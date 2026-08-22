#!/usr/bin/env python3
"""Finalize an immutable, bounded traffic-recovery Parquet set.

This utility is intentionally separate from ``build_traffic_dataset.py``.  It
does not open raw ZIP archives or rebuild SCATS.  It validates the completed
Parquet recovery partitions, excludes every 2023 source, performs one bounded
DuckDB external sort, and publishes a complete canonical target plus a
deterministic manifest.  The manifest is the readiness marker consumed by
``build_traffic_training_datasets.py``.

The default contract is the August 2026 recovery window: Transport Activity
and SCATS sources from 2024 through 31 July 2026, with only the publisher
SCATS date gaps explicitly allowlisted by ``expected_coverage.json``.  The
date/year arguments are deliberately explicit so a test or a later bounded
recovery can use a narrower contract without changing the recovery inputs.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date, timedelta
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
from typing import Any, Mapping, Sequence

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RECOVERY_DIR = ROOT / "ml" / "traffic" / "processed" / ".traffic-recovery-20260822"
DEFAULT_OUTPUT_DIR = ROOT / "ml" / "traffic" / "processed"
DEFAULT_EXPECTED_CONFIG = ROOT / "ml" / "traffic" / "config" / "expected_coverage.json"
DEFAULT_START_DATE = date(2024, 1, 1)
DEFAULT_START_YEAR = 2024
DEFAULT_END_YEAR = 2026
DEFAULT_MEMORY_LIMIT = "6GB"
DEFAULT_THREADS = 6
FINALIZER_VERSION = "traffic-recovery-finalizer-v1"
EXCLUDED_YEARS = frozenset({2023})
SELECTED_MIN_YEAR = 2024
SELECTED_MAX_YEAR = 2026
SELECTED_MIN_DATE = date(2024, 1, 1)
SELECTED_MAX_DATE = date(2026, 12, 31)

# The cleaner owns the public schema and natural-key definition.  Importing
# those constants keeps this bounded finalization boundary aligned with the
# source cleaner without importing or executing its raw-archive pipeline.
SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
from build_traffic_dataset import (  # noqa: E402
    NATURAL_KEY,
    OUTPUT_SCHEMA,
    _schema_descriptor,
    _write_atomic_parquet_batches,
)


class FinalizationError(RuntimeError):
    """A recovery set failed a publication invariant."""


@dataclass(frozen=True)
class RecoveryPartition:
    """A validated, single-source recovery partition."""

    path: Path
    source_dataset_id: str
    label_source: str
    rows: int
    min_date: date
    max_date: date
    date_column: str
    sha256: str

    @property
    def kind(self) -> str:
        return self.label_source


def _json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _sql_string(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _parquet_relation(paths: Sequence[Path]) -> str:
    if not paths:
        raise FinalizationError("no recovery Parquet partitions were selected")
    literals = ", ".join(_sql_string(path) for path in paths)
    return f"read_parquet([{literals}], union_by_name=false)"


def _date_literal(value: date) -> str:
    return f"DATE '{value.isoformat()}'"


def _parse_date(value: str, label: str) -> date:
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError as error:
        raise FinalizationError(f"{label} must be an ISO date: {value!r}") from error


def _as_date(value: Any, label: str) -> date:
    if value is None:
        raise FinalizationError(f"{label} contains a null date")
    if isinstance(value, date):
        return value
    return _parse_date(str(value), label)


def _source_year(source_dataset_id: str) -> int | None:
    match = re.search(r"(?<!\d)(20\d{2})(?!\d)", source_dataset_id)
    return int(match.group(1)) if match else None


def _load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FinalizationError(f"expected coverage config is not a file: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise FinalizationError(f"unable to read expected coverage config: {path}") from error
    if not isinstance(payload, dict):
        raise FinalizationError("expected coverage config must be a JSON object")
    return payload


def _source_values(value: Any, label: str) -> list[str]:
    if not isinstance(value, (list, tuple)) or not value:
        raise FinalizationError(f"{label} must be a non-empty list")
    values: list[str] = []
    for item in value:
        if isinstance(item, Mapping):
            item = item.get("source_dataset_id", item.get("id"))
        if item is None or not str(item).strip():
            raise FinalizationError(f"{label} contains an empty source id")
        values.append(str(item).strip())
    if len(values) != len(set(values)):
        raise FinalizationError(f"{label} contains duplicate source ids")
    return values


def _resolved_contract(
    payload: Mapping[str, Any],
    *,
    start_year: int,
    end_year: int,
    start_date: date,
    end_date: date | None,
) -> dict[str, Any]:
    if start_year < 1 or end_year < start_year:
        raise FinalizationError("source year bounds are invalid")
    if start_year < SELECTED_MIN_YEAR or end_year > SELECTED_MAX_YEAR:
        raise FinalizationError(
            "selected recovery years must stay inside the explicit 2024-2026 boundary"
        )
    if start_date < SELECTED_MIN_DATE or start_date > SELECTED_MAX_DATE:
        raise FinalizationError(
            "selected recovery start date must stay inside the explicit 2024-2026 boundary"
        )
    if start_date.year != start_year:
        raise FinalizationError("start date must be in --start-year")

    ta_values = _source_values(
        payload.get("transport_activity_sources"),
        "transport_activity_sources",
    )
    scats_payload = payload.get("scats")
    if not isinstance(scats_payload, Mapping):
        raise FinalizationError("expected coverage config is missing the scats object")
    scats_values = _source_values(scats_payload.get("source_datasets"), "scats.source_datasets")

    configured_end = scats_payload.get("expected_end")
    resolved_end = end_date or _parse_date(
        str(configured_end), "scats.expected_end"
    )
    if resolved_end < start_date:
        raise FinalizationError("finalization date bounds are invalid")
    if resolved_end > SELECTED_MAX_DATE or resolved_end.year > end_year:
        raise FinalizationError("end date is outside --end-year")

    allowed_missing_raw = scats_payload.get("allowed_missing_dates", [])
    if not isinstance(allowed_missing_raw, (list, tuple, set)):
        raise FinalizationError("scats.allowed_missing_dates must be a list")
    allowed_missing = sorted(
        {
            _parse_date(str(item), "scats.allowed_missing_dates")
            for item in allowed_missing_raw
        }
    )
    allowed_missing = [
        item for item in allowed_missing if start_date <= item <= resolved_end
    ]

    def selected(values: Sequence[str]) -> list[str]:
        selected_values: list[str] = []
        for source_id in values:
            year = _source_year(source_id)
            if year is None:
                raise FinalizationError(f"source id has no four-digit year: {source_id}")
            if start_year <= year <= end_year:
                selected_values.append(source_id)
        return sorted(selected_values)

    selected_ta = selected(ta_values)
    selected_scats = selected(scats_values)
    if not selected_ta or not selected_scats:
        raise FinalizationError("coverage config has no selected 2024-2026 source ids")

    return {
        "transport_activity_sources": selected_ta,
        "scats_source_datasets": selected_scats,
        "all_transport_activity_sources": sorted(ta_values),
        "all_scats_source_datasets": sorted(scats_values),
        "scats_expected_start": start_date.isoformat(),
        "scats_expected_end": resolved_end.isoformat(),
        "allowed_missing_scats_dates": [item.isoformat() for item in allowed_missing],
        "excluded_years": sorted(EXCLUDED_YEARS),
        "selected_year_boundary": {
            "min": SELECTED_MIN_YEAR,
            "max": SELECTED_MAX_YEAR,
        },
    }


def _assert_schema(path: Path) -> None:
    try:
        actual = pq.read_schema(path)
    except Exception as error:
        raise FinalizationError(f"recovery partition is not readable Parquet: {path}") from error
    if actual.names != list(OUTPUT_SCHEMA.names) or not actual.equals(
        OUTPUT_SCHEMA, check_metadata=False
    ):
        raise FinalizationError(
            "recovery partition schema does not exactly match the canonical traffic schema: "
            f"{path.name}"
        )


def _duckdb_connection(
    temp_dir: Path,
    memory_limit: str,
    threads: int,
) -> duckdb.DuckDBPyConnection:
    try:
        connection = duckdb.connect(database=":memory:")
        connection.execute(f"PRAGMA memory_limit={_sql_string(memory_limit)}")
        connection.execute(f"SET threads={int(threads)}")
        connection.execute("SET preserve_insertion_order=false")
        connection.execute("SET temp_directory = ?", [str(temp_dir)])
        return connection
    except Exception as error:
        raise FinalizationError("unable to initialize bounded DuckDB finalizer") from error


def _inspect_partition(connection: duckdb.DuckDBPyConnection, path: Path, recovery_dir: Path) -> RecoveryPartition:
    _assert_schema(path)
    relation = _parquet_relation([path])
    rows = connection.execute(
        f"""
        SELECT
            source_dataset_id,
            label_source,
            COUNT(*) AS row_count,
            MIN(local_date) AS min_local_date,
            MAX(local_date) AS max_local_date,
            MIN(scats_source_date_local) AS min_scats_date,
            MAX(scats_source_date_local) AS max_scats_date,
            COUNT(*) FILTER (
                WHERE source_dataset_id IS NULL
                   OR observation_unit_id IS NULL
                   OR label_source IS NULL
                   OR measurement_scope IS NULL
                   OR hour_start_utc IS NULL
                   OR vehicle_count IS NULL
                   OR vehicle_count < 0
                   OR vehicle_count != FLOOR(vehicle_count)
            ) AS invalid_rows,
            COUNT(*) FILTER (
                WHERE EXTRACT(MINUTE FROM hour_start_utc) <> 0
                   OR EXTRACT(SECOND FROM hour_start_utc) <> 0
            ) AS non_hourly_rows
            ,COUNT(*) FILTER (
                WHERE hour_start_utc IS NULL
                   OR local_timestamp IS NULL
                   OR local_date IS NULL
                   OR local_hour IS NULL
                   OR year IS NULL
                   OR month IS NULL
                   OR day IS NULL
                   OR (label_source = 'scats' AND (
                       source_date_local IS NULL
                       OR scats_source_date_local IS NULL
                       OR source_timestamp_utc IS NULL
                   ))
            ) AS null_temporal_rows
            ,COUNT(*) FILTER (
                WHERE (label_source = 'transport_activity' AND (
                    measurement_scope IS DISTINCT FROM 'countline'
                    OR traffic_eligible IS DISTINCT FROM TRUE
                    OR lower(coalesce(review_status, '')) IS DISTINCT FROM 'approved'
                    OR source_date_local IS NOT NULL
                    OR scats_source_date_local IS NOT NULL
                    OR source_timezone_name IS DISTINCT FROM 'Australia/Melbourne'
                ))
                OR (label_source = 'scats' AND (
                    measurement_scope IS DISTINCT FROM 'intersection'
                    OR traffic_eligible IS NOT NULL
                    OR review_status IS NOT NULL
                    OR source_date_local IS NULL
                    OR scats_source_date_local IS NULL
                    OR source_timezone_name IS DISTINCT FROM 'UTC+10'
                    OR source_timezone_offset_minutes IS DISTINCT FROM 600
                ))
            ) AS invalid_source_semantics
        FROM {relation}
        GROUP BY source_dataset_id, label_source
        ORDER BY source_dataset_id, label_source
        """
    ).fetchall()
    if len(rows) != 1:
        raise FinalizationError(
            f"recovery partition must contain exactly one source and label: {path.name}"
        )
    (
        source_id,
        label_source,
        row_count,
        min_local,
        max_local,
        min_scats,
        max_scats,
        invalid,
        non_hourly,
        null_temporal,
        invalid_source_semantics,
    ) = rows[0]
    if invalid or non_hourly or null_temporal or invalid_source_semantics:
        raise FinalizationError(
            f"recovery partition failed row invariants: {path.name} "
            f"invalid={invalid}, non_hourly={non_hourly}, "
            f"null_temporal={null_temporal}, "
            f"source_semantics={invalid_source_semantics}"
        )
    source_id = str(source_id)
    label_source = str(label_source)
    if label_source not in {"transport_activity", "scats"}:
        raise FinalizationError(f"unsupported recovery label_source {label_source!r}: {path.name}")
    if label_source == "scats":
        if min_scats is None or max_scats is None:
            raise FinalizationError(f"SCATS recovery partition has no source dates: {path.name}")
        min_date, max_date, date_column = (
            _as_date(min_scats, f"{path.name}.scats_source_date_local"),
            _as_date(max_scats, f"{path.name}.scats_source_date_local"),
            "scats_source_date_local",
        )
    else:
        if min_local is None or max_local is None:
            raise FinalizationError(f"Transport Activity recovery partition has no local dates: {path.name}")
        min_date, max_date, date_column = (
            _as_date(min_local, f"{path.name}.local_date"),
            _as_date(max_local, f"{path.name}.local_date"),
            "local_date",
        )
    if int(row_count) <= 0:
        raise FinalizationError(f"recovery partition is empty: {path.name}")
    parquet_rows = pq.ParquetFile(path).metadata.num_rows
    if int(row_count) != int(parquet_rows):
        raise FinalizationError(
            f"recovery partition row count disagrees with Parquet metadata: {path.name}"
        )
    return RecoveryPartition(
        path=path,
        source_dataset_id=source_id,
        label_source=label_source,
        rows=int(row_count),
        min_date=min_date,
        max_date=max_date,
        date_column=date_column,
        sha256=_sha256_file(path),
    )


def _validate_partition_inventory(
    partitions: Sequence[RecoveryPartition],
    contract: Mapping[str, Any],
    *,
    start_date: date,
    end_date: date,
) -> tuple[list[RecoveryPartition], list[RecoveryPartition]]:
    all_known = set(contract["all_transport_activity_sources"]) | set(
        contract["all_scats_source_datasets"]
    )
    selected_ids = set(contract["transport_activity_sources"]) | set(
        contract["scats_source_datasets"]
    )
    by_source: dict[str, list[RecoveryPartition]] = {}
    for partition in partitions:
        if partition.source_dataset_id not in all_known:
            raise FinalizationError(
                f"recovery partition contains an unconfigured source id: {partition.source_dataset_id}"
            )
        by_source.setdefault(partition.source_dataset_id, []).append(partition)

    duplicates = sorted(source for source, values in by_source.items() if len(values) != 1)
    if duplicates:
        raise FinalizationError(f"recovery source ids are not represented exactly once: {duplicates}")

    missing = sorted(selected_ids - set(by_source))
    if missing:
        raise FinalizationError(f"required 2024-2026 recovery sources are missing: {missing}")

    expected_ta = set(contract["transport_activity_sources"])
    expected_scats = set(contract["scats_source_datasets"])
    for partition in partitions:
        expected_label = (
            "transport_activity"
            if partition.source_dataset_id in expected_ta or partition.source_dataset_id in contract["all_transport_activity_sources"]
            else "scats"
        )
        if partition.label_source != expected_label:
            raise FinalizationError(
                f"source family mismatch for {partition.source_dataset_id}: "
                f"expected {expected_label}, got {partition.label_source}"
            )
        source_year = _source_year(partition.source_dataset_id)
        if source_year in EXCLUDED_YEARS:
            continue
        if partition.source_dataset_id not in selected_ids:
            raise FinalizationError(
                f"non-2023 recovery source is outside the selected contract: {partition.source_dataset_id}"
            )
        if partition.min_date < start_date or partition.max_date > end_date:
            raise FinalizationError(
                f"{partition.source_dataset_id} contains dates outside the bounded contract: "
                f"{partition.min_date}..{partition.max_date}"
            )

    selected = sorted(
        (partition for partition in partitions if partition.source_dataset_id in selected_ids),
        key=lambda item: (item.label_source, item.source_dataset_id, item.path.name),
    )
    excluded = sorted(
        (partition for partition in partitions if partition.source_dataset_id not in selected_ids),
        key=lambda item: (item.label_source, item.source_dataset_id, item.path.name),
    )
    if not excluded:
        # The exclusion is a hard policy even when the caller has already
        # removed the 2023 files from a copied recovery set.
        excluded = []
    return selected, excluded


def _date_range(start: date, end: date) -> list[date]:
    return [start + timedelta(days=offset) for offset in range((end - start).days + 1)]


def _coverage(
    connection: duckdb.DuckDBPyConnection,
    relation: str,
    contract: Mapping[str, Any],
    *,
    start_date: date,
    end_date: date,
    input_rows: int,
) -> dict[str, Any]:
    expected_dates = _date_range(start_date, end_date)
    observed_rows = connection.execute(
        f"""
        SELECT DISTINCT CAST(scats_source_date_local AS DATE)
        FROM {relation}
        WHERE label_source = 'scats'
        ORDER BY 1
        """
    ).fetchall()
    observed_dates = sorted({_as_date(row[0], "SCATS coverage") for row in observed_rows})
    observed_set = set(observed_dates)
    expected_set = set(expected_dates)
    publisher_missing_set = {
        _parse_date(item, "allowed_missing_scats_dates")
        for item in contract["allowed_missing_scats_dates"]
    }
    zero_eligible_set = {
        _parse_date(item, "allowed_zero_eligible_scats_dates")
        for item in contract.get("allowed_zero_eligible_scats_dates", [])
    }
    allowed_set = publisher_missing_set | zero_eligible_set
    missing_set = expected_set - observed_set
    allowed_missing = sorted(missing_set & allowed_set)
    unexpected_missing = sorted(missing_set - allowed_set)
    if unexpected_missing:
        raise FinalizationError(
            "SCATS coverage has unexpected missing dates: "
            f"{[item.isoformat() for item in unexpected_missing[:20]]}"
        )
    if not observed_set <= expected_set:
        raise FinalizationError("SCATS coverage contains dates outside the bounded contract")

    source_rows = connection.execute(
        f"""
        SELECT source_dataset_id, label_source, COUNT(*) AS row_count,
               MIN(CASE WHEN label_source = 'scats' THEN scats_source_date_local ELSE local_date END),
               MAX(CASE WHEN label_source = 'scats' THEN scats_source_date_local ELSE local_date END)
        FROM {relation}
        GROUP BY source_dataset_id, label_source
        ORDER BY source_dataset_id
        """
    ).fetchall()
    observed_sources = {str(row[0]) for row in source_rows}
    expected_sources = set(contract["transport_activity_sources"]) | set(
        contract["scats_source_datasets"]
    )
    if observed_sources != expected_sources:
        raise FinalizationError(
            f"final source inventory mismatch: observed={sorted(observed_sources)}, "
            f"expected={sorted(expected_sources)}"
        )
    output_rows = int(connection.execute(f"SELECT COUNT(*) FROM {relation}").fetchone()[0])
    if output_rows != input_rows:
        raise FinalizationError(
            f"final row count disagrees with selected recovery rows: {output_rows} != {input_rows}"
        )
    return {
        "partial": False,
        "expected_scats_start": start_date.isoformat(),
        "expected_scats_end": end_date.isoformat(),
        "expected_scats_date_count": len(expected_dates),
        "observed_scats_date_count": len(observed_dates),
        "missing_scats_dates": [item.isoformat() for item in sorted(missing_set)],
        "missing_scats_date_count": len(missing_set),
        "allowed_missing_scats_dates": [item.isoformat() for item in allowed_missing],
        "allowed_missing_scats_date_count": len(allowed_missing),
        "publisher_missing_scats_dates": [
            item.isoformat() for item in sorted(missing_set & publisher_missing_set)
        ],
        "publisher_missing_scats_date_count": len(missing_set & publisher_missing_set),
        "zero_eligible_scats_dates": [
            item.isoformat() for item in sorted(missing_set & zero_eligible_set)
        ],
        "zero_eligible_scats_date_count": len(missing_set & zero_eligible_set),
        "unexpected_missing_scats_dates": [],
        "unexpected_missing_scats_date_count": 0,
        "expected_sources": sorted(expected_sources),
        "observed_sources": sorted(observed_sources),
        "source_rows": [
            {
                "source_dataset_id": str(source_id),
                "label_source": str(label_source),
                "rows": int(rows),
                "min_source_date": _as_date(min_date, "source coverage").isoformat(),
                "max_source_date": _as_date(max_date, "source coverage").isoformat(),
            }
            for source_id, label_source, rows, min_date, max_date in source_rows
        ],
    }


def _validate_final_relation(
    connection: duckdb.DuckDBPyConnection,
    relation: str,
    *,
    start_date: date,
    end_date: date,
    expected_rows: int,
) -> dict[str, Any]:
    (
        row_count,
        null_key_rows,
        invalid_target_rows,
        non_hourly_rows,
        out_of_range_rows,
        selected_year_rows,
        year_2023_rows,
        null_temporal_rows,
        temporal_consistency_rows,
        source_semantic_rows,
    ) = connection.execute(
        f"""
        WITH canonical AS (
            SELECT
                *,
                hour_start_utc AT TIME ZONE 'Australia/Melbourne' AS melbourne_local,
                hour_start_utc AT TIME ZONE 'Etc/GMT-10' AS scats_fixed_local
            FROM {relation}
        )
        SELECT
            COUNT(*),
            COUNT(*) FILTER (
                WHERE source_dataset_id IS NULL OR observation_unit_id IS NULL
                   OR hour_start_utc IS NULL
            ),
            COUNT(*) FILTER (
                WHERE vehicle_count IS NULL OR vehicle_count < 0
                   OR vehicle_count != FLOOR(vehicle_count)
                   OR NOT isfinite(CAST(vehicle_count AS DOUBLE))
            ),
            COUNT(*) FILTER (
                WHERE EXTRACT(MINUTE FROM hour_start_utc) <> 0
                   OR EXTRACT(SECOND FROM hour_start_utc) <> 0
            ),
            COUNT(*) FILTER (
                WHERE (label_source = 'scats' AND (
                    scats_source_date_local < {_date_literal(start_date)}
                    OR scats_source_date_local > {_date_literal(end_date)}
                ))
                   OR (label_source = 'transport_activity' AND (
                    local_date < {_date_literal(start_date)}
                    OR local_date > {_date_literal(end_date)}
                ))
            ),
            COUNT(*) FILTER (
                WHERE year < {SELECTED_MIN_YEAR} OR year > {SELECTED_MAX_YEAR}
            ),
            COUNT(*) FILTER (WHERE year = 2023),
            COUNT(*) FILTER (
                WHERE hour_start_utc IS NULL
                   OR local_timestamp IS NULL
                   OR local_date IS NULL
                   OR local_hour IS NULL
                   OR year IS NULL
                   OR month IS NULL
                   OR day IS NULL
                   OR (label_source = 'scats' AND (
                       source_date_local IS NULL
                       OR scats_source_date_local IS NULL
                       OR source_timestamp_utc IS NULL
                   ))
            ),
            COUNT(*) FILTER (
                WHERE local_timestamp IS DISTINCT FROM hour_start_utc
                   OR local_date IS DISTINCT FROM CAST(melbourne_local AS DATE)
                   OR local_hour IS DISTINCT FROM EXTRACT(HOUR FROM melbourne_local)
                   OR year IS DISTINCT FROM EXTRACT(YEAR FROM melbourne_local)
                   OR month IS DISTINCT FROM EXTRACT(MONTH FROM melbourne_local)
                   OR day IS DISTINCT FROM EXTRACT(DAY FROM melbourne_local)
                   OR (label_source = 'scats' AND (
                       source_date_local IS DISTINCT FROM scats_source_date_local
                       OR scats_source_date_local IS DISTINCT FROM CAST(scats_fixed_local AS DATE)
                       OR source_timestamp_utc IS DISTINCT FROM hour_start_utc
                   ))
            ),
            COUNT(*) FILTER (
                WHERE label_source IS NULL
                   OR label_source NOT IN ('scats', 'transport_activity')
                   OR (label_source = 'transport_activity' AND (
                       measurement_scope IS DISTINCT FROM 'countline'
                       OR traffic_eligible IS DISTINCT FROM TRUE
                       OR lower(coalesce(review_status, '')) IS DISTINCT FROM 'approved'
                       OR source_date_local IS NOT NULL
                       OR scats_source_date_local IS NOT NULL
                       OR source_timezone_name IS DISTINCT FROM 'Australia/Melbourne'
                   ))
                   OR (label_source = 'scats' AND (
                       measurement_scope IS DISTINCT FROM 'intersection'
                       OR traffic_eligible IS NOT NULL
                       OR review_status IS NOT NULL
                       OR source_date_local IS NULL
                       OR scats_source_date_local IS NULL
                       OR source_timezone_name IS DISTINCT FROM 'UTC+10'
                       OR source_timezone_offset_minutes IS DISTINCT FROM 600
                   ))
            )
        FROM canonical
        """
    ).fetchone()
    duplicate_groups = int(
        connection.execute(
            f"""
            SELECT COUNT(*) FROM (
                SELECT source_dataset_id, observation_unit_id, hour_start_utc
                FROM {relation}
                GROUP BY source_dataset_id, observation_unit_id, hour_start_utc
                HAVING COUNT(*) > 1
            ) AS duplicates
            """
        ).fetchone()[0]
    )
    stats = {
        "rows": int(row_count),
        "expected_rows": int(expected_rows),
        "null_natural_key_rows": int(null_key_rows),
        "invalid_target_rows": int(invalid_target_rows),
        "non_hourly_rows": int(non_hourly_rows),
        "out_of_range_rows": int(out_of_range_rows),
        "selected_year_boundary_rows": int(selected_year_rows),
        "year_2023_rows": int(year_2023_rows),
        "null_temporal_rows": int(null_temporal_rows),
        "temporal_consistency_rows": int(temporal_consistency_rows),
        "source_semantic_rows": int(source_semantic_rows),
        "duplicate_natural_key_groups": duplicate_groups,
    }
    if row_count != expected_rows or any(
        stats[key]
        for key in (
            "null_natural_key_rows",
            "invalid_target_rows",
            "non_hourly_rows",
            "out_of_range_rows",
            "selected_year_boundary_rows",
            "year_2023_rows",
            "null_temporal_rows",
            "temporal_consistency_rows",
            "source_semantic_rows",
            "duplicate_natural_key_groups",
        )
    ):
        raise FinalizationError(f"final canonical relation failed validation: {stats}")
    return stats


def _validate_sorted(path: Path) -> None:
    previous: tuple[Any, ...] | None = None
    for batch in pq.ParquetFile(path).iter_batches(columns=NATURAL_KEY, batch_size=100_000):
        for row in pa.Table.from_batches([batch]).to_pylist():
            current = tuple(row[column] for column in NATURAL_KEY)
            if previous is not None and current <= previous:
                raise FinalizationError(
                    f"final canonical target is not strictly ordered by {NATURAL_KEY}"
                )
            previous = current


def _assert_selected_recovery_unchanged(
    selected: Sequence[RecoveryPartition],
    *,
    phase: str,
) -> None:
    """Reject a recovery input that changed after it was inspected.

    DuckDB reads the selected paths directly.  Rehashing them after the read
    makes the published provenance fail closed if a producer or another
    process replaced or edited a recovery partition during finalization.
    """

    changed: list[str] = []
    for partition in selected:
        if partition.path.is_symlink() or not partition.path.is_file():
            changed.append(f"{partition.path.name} (missing or symlinked)")
            continue
        try:
            current_sha256 = _sha256_file(partition.path)
        except OSError as error:
            raise FinalizationError(
                f"unable to rehash selected recovery partition during {phase}: "
                f"{partition.path.name}"
            ) from error
        if current_sha256 != partition.sha256:
            changed.append(
                f"{partition.path.name} ({partition.sha256} -> {current_sha256})"
            )
    if changed:
        raise FinalizationError(
            f"selected recovery partition mutation detected {phase}: "
            + ", ".join(changed)
        )


def _publish_exclusive(source: Path, destination: Path) -> int:
    """Publish one staged file without ever replacing an existing path."""

    try:
        # The staging directory is inside output_dir, so this is an atomic
        # same-filesystem no-clobber publication.  Unlike os.replace(), it
        # cannot overwrite a file created by a concurrent finalizer.
        os.link(source, destination)
    except FileExistsError as error:
        raise FileExistsError(
            f"refusing to overwrite existing finalization output: {destination}"
        ) from error
    return os.stat(destination).st_ino


def _input_descriptor(partition: RecoveryPartition, recovery_dir: Path) -> dict[str, Any]:
    return {
        "path": partition.path.relative_to(recovery_dir).as_posix(),
        "source_dataset_id": partition.source_dataset_id,
        "label_source": partition.label_source,
        "rows": partition.rows,
        "min_source_date": partition.min_date.isoformat(),
        "max_source_date": partition.max_date.isoformat(),
        "sha256": partition.sha256,
    }


def _content_hash(
    selected: Sequence[RecoveryPartition],
    recovery_dir: Path,
    contract: Mapping[str, Any],
    *,
    start_date: date,
    end_date: date,
) -> str:
    payload = {
        "finalizer": FINALIZER_VERSION,
        "inputs": sorted(
            (_input_descriptor(partition, recovery_dir) for partition in selected),
            key=lambda item: (item["label_source"], item["source_dataset_id"], item["path"]),
        ),
        "contract": dict(contract),
        "date_bounds": {
            "start": start_date.isoformat(),
            "end": end_date.isoformat(),
        },
        "natural_key": list(NATURAL_KEY),
        "schema": _schema_descriptor(),
        "ordering": list(NATURAL_KEY),
    }
    return hashlib.sha256(_json_bytes(payload)).hexdigest()


def _manifest(
    *,
    content_hash: str,
    recovery_dir: Path,
    expected_config: Path,
    expected_payload: Mapping[str, Any],
    contract: Mapping[str, Any],
    selected: Sequence[RecoveryPartition],
    excluded: Sequence[RecoveryPartition],
    coverage: Mapping[str, Any],
    validation: Mapping[str, Any],
    parquet_name: str,
    parquet_bytes: int,
    parquet_sha256: str,
    memory_limit: str,
    threads: int,
) -> dict[str, Any]:
    schema = _schema_descriptor()
    selected_rows = sum(item.rows for item in selected)
    return {
        "schema_version": 1,
        "builder": "finalize_traffic_recovery.py",
        "finalizer_version": FINALIZER_VERSION,
        "content_hash": content_hash,
        "artifact_status": "complete",
        "measure": "hourly vehicle traffic target; Transport Activity countline or SCATS intersection",
        "natural_key": list(NATURAL_KEY),
        "target_columns": {"raw": "vehicle_count", "transformed": "log1p_vehicle_count"},
        "timezone": "Australia/Melbourne",
        "scats_source_timezone": "UTC+10",
        "recovery": {
            "directory_name": recovery_dir.name,
            "selected_partition_count": len(selected),
            "excluded_partition_count": len(excluded),
            "excluded_years": sorted(EXCLUDED_YEARS),
            "excluded_source_dataset_ids": sorted(item.source_dataset_id for item in excluded),
            "raw_rebuild_performed": False,
        },
        "inputs": [_input_descriptor(item, recovery_dir) for item in selected],
        "excluded_inputs": [_input_descriptor(item, recovery_dir) for item in excluded],
        "expected_coverage": {
            "config": {
                "path": expected_config.name,
                "bytes": expected_config.stat().st_size,
                "sha256": _sha256_file(expected_config),
                "payload": expected_payload,
            },
            "resolved": dict(contract),
        },
        "coverage": dict(coverage),
        "row_counts": {
            "selected_recovery_rows": selected_rows,
            "canonical_rows": int(validation["rows"]),
            "per_source": coverage["source_rows"],
        },
        "staging": {
            "strategy": "read-only recovery partitions then bounded DuckDB external global sort",
            "memory_limit": memory_limit,
            "threads": int(threads),
            "global_ordered_by": list(NATURAL_KEY),
        },
        "schema": schema,
        "schema_hash": hashlib.sha256(_json_bytes(schema)).hexdigest(),
        "outputs": {
            "parquet": {
                "path": parquet_name,
                "rows": int(validation["rows"]),
                "bytes": int(parquet_bytes),
                "sha256": parquet_sha256,
            },
            "manifest": {"path": f"{Path(parquet_name).stem}_manifest.json"},
        },
        "feature_builder_compatibility": {
            "artifact_status": "complete",
            "canonical_manifest_partial": False,
            "required_target_columns": [
                "source_dataset_id",
                "observation_unit_id",
                "hour_start_utc",
                "vehicle_count",
                "measurement_scope",
                "label_source",
            ],
            "observation_key": "derived from the canonical natural key when absent",
        },
        "assertions": {
            "recovery_partitions_read_only": True,
            "raw_scats_rebuild_performed": False,
            "all_2023_rows_excluded": True,
            "natural_key_unique": True,
            "null_targets_allowed": False,
            "negative_targets_allowed": False,
            "hour_start_utc_hourly": True,
            "global_ordered_by": list(NATURAL_KEY),
            "canonical_target_complete": True,
            "target_rows_preserved": True,
            "feature_builder_partial_manifest_rejected": False,
        },
    }


def finalize_recovery(args: argparse.Namespace) -> dict[str, Any]:
    recovery_dir = args.recovery_dir.expanduser().resolve()
    output_dir = args.output_dir.expanduser().resolve()
    expected_config = args.expected_config.expanduser().resolve()
    if not recovery_dir.is_dir():
        raise FinalizationError(f"recovery directory is not a directory: {recovery_dir}")
    if output_dir == recovery_dir or recovery_dir in output_dir.parents:
        raise FinalizationError(
            "output directory must be outside the immutable recovery directory"
        )
    if args.threads < 1 or args.threads > 10:
        raise FinalizationError("--threads must be between 1 and 10")
    start_date = _parse_date(args.start_date, "--start-date")
    contract_payload = _load_json(expected_config)
    contract = _resolved_contract(
        contract_payload,
        start_year=args.start_year,
        end_year=args.end_year,
        start_date=start_date,
        end_date=_parse_date(args.end_date, "--end-date") if args.end_date else None,
    )
    end_date = _parse_date(contract["scats_expected_end"], "resolved scats end date")
    zero_eligible_dates = sorted(
        {
            _parse_date(value, "--allow-zero-eligible-scats-date")
            for value in args.allow_zero_eligible_scats_date
        }
    )
    if any(value < start_date or value > end_date for value in zero_eligible_dates):
        raise FinalizationError(
            "--allow-zero-eligible-scats-date must fall inside the bounded contract"
        )
    publisher_missing_dates = {
        _parse_date(value, "allowed_missing_scats_dates")
        for value in contract["allowed_missing_scats_dates"]
    }
    if publisher_missing_dates & set(zero_eligible_dates):
        raise FinalizationError(
            "a SCATS date cannot be both publisher-missing and zero-eligible"
        )
    contract = {
        **contract,
        "allowed_zero_eligible_scats_dates": [
            value.isoformat() for value in zero_eligible_dates
        ],
    }

    output_dir_existed = output_dir.exists()
    output_dir.mkdir(parents=True, exist_ok=True)
    parquet_files = sorted(recovery_dir.glob("*.parquet"))
    if not parquet_files:
        raise FinalizationError(f"recovery directory has no Parquet partitions: {recovery_dir}")
    if any(path.is_symlink() for path in parquet_files):
        raise FinalizationError("symlinked recovery partitions are not accepted")
    connection: duckdb.DuckDBPyConnection | None = None
    temp_dir = Path(tempfile.mkdtemp(prefix=".traffic-recovery-finalize-", dir=output_dir))
    published_files: list[tuple[Path, int]] = []
    try:
        connection = _duckdb_connection(
            temp_dir / "duckdb-tmp",
            args.memory_limit,
            args.threads,
        )
        partitions = [
            _inspect_partition(connection, path, recovery_dir)
            for path in parquet_files
        ]
        selected, excluded = _validate_partition_inventory(
            partitions,
            contract,
            start_date=start_date,
            end_date=end_date,
        )
        if not selected:
            raise FinalizationError("no 2024-2026 recovery partitions selected")
        input_rows = sum(item.rows for item in selected)
        selected_relation = _parquet_relation([item.path for item in selected])
        final_relation = (
            f"SELECT * FROM {selected_relation} "
            f"WHERE ((label_source = 'scats' AND scats_source_date_local BETWEEN "
            f"{_date_literal(start_date)} AND {_date_literal(end_date)}) OR "
            f"(label_source = 'transport_activity' AND local_date BETWEEN "
            f"{_date_literal(start_date)} AND {_date_literal(end_date)}))"
        )
        selected_count = int(connection.execute(f"SELECT COUNT(*) FROM ({final_relation}) AS selected").fetchone()[0])
        if selected_count != input_rows:
            raise FinalizationError(
                f"date-bounded recovery rows changed: {selected_count} != {input_rows}"
            )

        content_hash = _content_hash(
            selected,
            recovery_dir,
            contract,
            start_date=start_date,
            end_date=end_date,
        )
        parquet_name = f"traffic_training_v1_complete_{content_hash}.parquet"
        manifest_name = f"traffic_training_v1_complete_{content_hash}_manifest.json"
        parquet_path = output_dir / parquet_name
        manifest_path = output_dir / manifest_name
        existing = [path for path in (parquet_path, manifest_path) if path.exists()]
        if existing:
            raise FileExistsError(
                f"deterministic finalization outputs already exist; refusing to overwrite: {existing[0]}"
            )

        sorted_parquet = temp_dir / f"sorted-{parquet_name}"
        staged_parquet = temp_dir / parquet_name
        order = ", ".join(f'"{column}" ASC' for column in NATURAL_KEY)
        connection.execute(
            f"COPY (SELECT * FROM ({final_relation}) AS canonical ORDER BY {order}) "
            f"TO {_sql_string(sorted_parquet)} "
            "(FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 100000)"
        )
        _assert_selected_recovery_unchanged(
            selected,
            phase="after DuckDB consumption",
        )
        written_rows = _write_atomic_parquet_batches(
            staged_parquet,
            pq.ParquetFile(sorted_parquet).iter_batches(batch_size=100_000),
        )
        if written_rows != input_rows:
            raise FinalizationError(
                f"schema-preserving writer changed row count: {written_rows} != {input_rows}"
            )
        staged_relation = _parquet_relation([staged_parquet])
        validation = _validate_final_relation(
            connection,
            staged_relation,
            start_date=start_date,
            end_date=end_date,
            expected_rows=input_rows,
        )
        _assert_schema(staged_parquet)
        _validate_sorted(staged_parquet)
        coverage = _coverage(
            connection,
            staged_relation,
            contract,
            start_date=start_date,
            end_date=end_date,
            input_rows=input_rows,
        )
        staged_manifest = temp_dir / manifest_name
        manifest = _manifest(
            content_hash=content_hash,
            recovery_dir=recovery_dir,
            expected_config=expected_config,
            expected_payload=contract_payload,
            contract=contract,
            selected=selected,
            excluded=excluded,
            coverage=coverage,
            validation=validation,
            parquet_name=parquet_name,
            parquet_bytes=staged_parquet.stat().st_size,
            parquet_sha256=_sha256_file(staged_parquet),
            memory_limit=args.memory_limit,
            threads=args.threads,
        )
        staged_manifest.write_bytes(
            (json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")
        )
        # Recheck immediately before publication as well as immediately after
        # DuckDB consumption.  The exclusive links below then make the two
        # deterministic destinations no-clobber even if another finalizer
        # races this process after the earlier existence check.
        _assert_selected_recovery_unchanged(
            selected,
            phase="immediately before publication",
        )
        parquet_inode = _publish_exclusive(staged_parquet, parquet_path)
        published_files.append((parquet_path, parquet_inode))
        try:
            manifest_inode = _publish_exclusive(staged_manifest, manifest_path)
            published_files.append((manifest_path, manifest_inode))
        except Exception:
            for published_path, inode in reversed(published_files):
                try:
                    if os.stat(published_path).st_ino == inode:
                        published_path.unlink()
                except FileNotFoundError:
                    pass
            raise
        return {
            "parquet": parquet_path,
            "manifest": manifest_path,
            "rows": validation["rows"],
            "content_hash": content_hash,
            "coverage": coverage,
        }
    finally:
        if connection is not None:
            connection.close()
        shutil.rmtree(temp_dir, ignore_errors=True)
        if not output_dir_existed:
            try:
                output_dir.rmdir()
            except OSError:
                pass


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--recovery-dir", type=Path, default=DEFAULT_RECOVERY_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--expected-config", type=Path, default=DEFAULT_EXPECTED_CONFIG)
    parser.add_argument("--start-year", type=int, default=DEFAULT_START_YEAR)
    parser.add_argument("--end-year", type=int, default=DEFAULT_END_YEAR)
    parser.add_argument("--start-date", default=DEFAULT_START_DATE.isoformat())
    parser.add_argument("--end-date", help="override scats.expected_end from the config")
    parser.add_argument(
        "--allow-zero-eligible-scats-date",
        action="append",
        default=[],
        help=(
            "SCATS source date known to contain no sites inside the configured "
            "canonical spatial filter; repeat for multiple dates"
        ),
    )
    parser.add_argument(
        "--memory-limit",
        default=DEFAULT_MEMORY_LIMIT,
        help="bounded DuckDB memory budget (default: 6GB)",
    )
    parser.add_argument(
        "--threads",
        type=int,
        default=DEFAULT_THREADS,
        help="DuckDB sort threads (default: 6; maximum: 10)",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    try:
        result = finalize_recovery(parse_args(argv))
    except (FinalizationError, FileExistsError, OSError, ValueError, duckdb.Error) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(f"Parquet: {result['parquet']}")
    print(f"Manifest: {result['manifest']}")
    print(f"Rows: {result['rows']}")
    print(f"Content hash: {result['content_hash']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
