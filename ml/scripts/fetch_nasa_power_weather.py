#!/usr/bin/env python3
"""Fetch a reproducible Melbourne hourly weather table from NASA POWER.

The POWER hourly point API is queried once per calendar year, beginning at
``2009-05-01`` and ending at the inclusive date supplied with ``--end-date``.
The yearly responses are normalised into one UTC, hourly CSV.  Both the CSV
and its JSON provenance record are written with an atomic replace so an
interrupted run cannot leave a partially written final file.

Only Python's standard library is required.  For example::

    python ml/scripts/fetch_nasa_power_weather.py \
        --end-date 2024-12-31 \
        --output ml/data/raw/nasa_power_melbourne_hourly.csv

``PRECTOTCORR`` is the default precipitation variable.  If a POWER endpoint
reports that it does not support that variable, the script retries that year
with ``PRECTOT`` and keeps the logical precipitation column named
``PRECTOTCORR`` while recording the fallback in provenance.  A different
parameter list can be supplied with ``--parameters``.
"""

from __future__ import annotations

import argparse
import csv
import datetime as datetime_module
import hashlib
import http.client
import json
import math
import os
import re
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


SCRIPT_DIR = Path(__file__).resolve().parent
ROOT_DIR = SCRIPT_DIR.parents[1]
DEFAULT_OUTPUT = ROOT_DIR / "ml" / "data" / "raw" / "nasa_power_melbourne_hourly.csv"
DEFAULT_API_URL = "https://power.larc.nasa.gov/api/temporal/hourly/point"
POWER_DOCUMENTATION_URL = "https://power.larc.nasa.gov/docs/services/api/temporal/hourly/"

START_DATE = datetime_module.date(2009, 5, 1)
DEFAULT_LATITUDE = -37.8136
DEFAULT_LONGITUDE = 144.9631
DEFAULT_COMMUNITY = "AG"
DEFAULT_TIME_STANDARD = "UTC"
DEFAULT_PARAMETERS = (
    "T2M",
    "T2MDEW",
    "RH2M",
    "WS10M",
    "WD10M",
    "PS",
    "PRECTOTCORR",
    "ALLSKY_SFC_SW_DWN",
)
PRECIPITATION_PARAMETER = "PRECTOTCORR"
PRECIPITATION_FALLBACK = "PRECTOT"
TIMESTAMP_COLUMN = "timestamp_utc"
DEFAULT_TIMEOUT = 120.0
DEFAULT_RETRIES = 3
DEFAULT_BACKOFF_SECONDS = 1.0
USER_AGENT = "HeatRoute-NASA-POWER-fetcher/1.0"
MAX_ERROR_BODY = 2000
PARAMETER_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")


class FetchError(RuntimeError):
    """An actionable error while requesting or normalising POWER data."""


class HTTPFetchError(FetchError):
    """A failed HTTP request with enough metadata to decide whether to retry."""

    def __init__(
        self,
        message: str,
        *,
        url: str,
        status: int | None = None,
        body: str = "",
        attempts: int = 1,
    ) -> None:
        super().__init__(message)
        self.url = url
        self.status = status
        self.body = body
        self.attempts = attempts


def utc_now() -> str:
    """Return a stable, explicit UTC timestamp for provenance records."""

    return datetime_module.datetime.now(datetime_module.timezone.utc).isoformat().replace(
        "+00:00", "Z"
    )


def parse_iso_date(value: str) -> datetime_module.date:
    """Parse an ISO date and give callers a concise validation error."""

    try:
        parsed = datetime_module.date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise FetchError(f"invalid date {value!r}; expected YYYY-MM-DD") from exc
    if parsed.isoformat() != value:
        raise FetchError(f"invalid date {value!r}; expected YYYY-MM-DD")
    return parsed


def parse_parameters(value: str | Iterable[str]) -> list[str]:
    """Parse comma-separated POWER parameter names, preserving input order."""

    if isinstance(value, str):
        values: Iterable[str] = value.split(",")
    else:
        values = value

    parameters: list[str] = []
    seen: set[str] = set()
    for raw_value in values:
        parameter = str(raw_value).strip().upper()
        if not parameter:
            continue
        if not PARAMETER_NAME_RE.fullmatch(parameter):
            raise FetchError(
                f"invalid POWER parameter {raw_value!r}; use names such as T2M or PRECTOTCORR"
            )
        if parameter not in seen:
            seen.add(parameter)
            parameters.append(parameter)
    if not parameters:
        raise FetchError("at least one POWER parameter is required")
    return parameters


def parse_coordinate(value: float | str, *, name: str, minimum: float, maximum: float) -> float:
    """Validate a finite latitude or longitude."""

    try:
        coordinate = float(value)
    except (TypeError, ValueError) as exc:
        raise FetchError(f"{name} must be a number") from exc
    if not math.isfinite(coordinate) or not minimum <= coordinate <= maximum:
        raise FetchError(f"{name} must be between {minimum:g} and {maximum:g}")
    return coordinate


def format_coordinate(value: float) -> str:
    """Format coordinates deterministically without unnecessary zero padding."""

    return f"{value:.8f}".rstrip("0").rstrip(".")


def year_ranges(
    start_date: datetime_module.date, end_date: datetime_module.date
) -> list[tuple[datetime_module.date, datetime_module.date]]:
    """Split an inclusive date interval into calendar-year request intervals."""

    if end_date < start_date:
        raise FetchError(
            f"end date {end_date.isoformat()} is before start date {start_date.isoformat()}"
        )

    ranges: list[tuple[datetime_module.date, datetime_module.date]] = []
    current = start_date
    while current <= end_date:
        year_end = datetime_module.date(current.year, 12, 31)
        interval_end = min(year_end, end_date)
        ranges.append((current, interval_end))
        current = interval_end + datetime_module.timedelta(days=1)
    return ranges


def build_request_url(
    base_url: str,
    start_date: datetime_module.date,
    end_date: datetime_module.date,
    parameters: Sequence[str],
    *,
    latitude: float = DEFAULT_LATITUDE,
    longitude: float = DEFAULT_LONGITUDE,
    community: str = DEFAULT_COMMUNITY,
    time_standard: str = DEFAULT_TIME_STANDARD,
) -> str:
    """Build a deterministic POWER hourly point URL for one date interval."""

    if end_date < start_date:
        raise FetchError("request end date cannot be before request start date")
    if time_standard.upper() != "UTC":
        raise FetchError("this fetcher writes UTC timestamps; --time-standard must be UTC")
    parameter_list = parse_parameters(parameters)
    query: list[tuple[str, str]] = [
        ("parameters", ",".join(parameter_list)),
        ("community", str(community)),
        ("longitude", format_coordinate(longitude)),
        ("latitude", format_coordinate(latitude)),
        ("start", start_date.strftime("%Y%m%d")),
        ("end", end_date.strftime("%Y%m%d")),
        ("format", "JSON"),
        ("time-standard", "UTC"),
    ]
    separator = "&" if "?" in base_url else "?"
    return base_url.rstrip("&") + separator + urlencode(query)


def expected_timestamps(
    start_date: datetime_module.date, end_date: datetime_module.date
) -> list[datetime_module.datetime]:
    """Return all expected UTC hour instants for an inclusive date interval."""

    start = datetime_module.datetime.combine(
        start_date, datetime_module.time.min, tzinfo=datetime_module.timezone.utc
    )
    end_exclusive = datetime_module.datetime.combine(
        end_date + datetime_module.timedelta(days=1),
        datetime_module.time.min,
        tzinfo=datetime_module.timezone.utc,
    )
    count = int((end_exclusive - start).total_seconds() // 3600)
    return [start + datetime_module.timedelta(hours=offset) for offset in range(count)]


def parse_power_timestamp(value: Any) -> datetime_module.datetime:
    """Convert a POWER ``YYYYMMDDHH`` key to an aware UTC datetime."""

    text = str(value).strip()
    formats = ("%Y%m%d%H", "%Y%m%d%H%M")
    parsed: datetime_module.datetime | None = None
    for date_format in formats:
        try:
            parsed = datetime_module.datetime.strptime(text, date_format)
            break
        except ValueError:
            continue
    if parsed is None:
        raise FetchError(f"invalid POWER hourly timestamp key {value!r}")
    if parsed.minute != 0 or parsed.second != 0 or parsed.microsecond != 0:
        raise FetchError(f"POWER timestamp is not on an hour boundary: {value!r}")
    return parsed.replace(tzinfo=datetime_module.timezone.utc)


def _response_body(error: HTTPError) -> str:
    try:
        body = error.read(MAX_ERROR_BODY + 1)
    except Exception:  # pragma: no cover - defensive for unusual HTTP handlers.
        return ""
    if isinstance(body, bytes):
        return body[:MAX_ERROR_BODY].decode("utf-8", errors="replace")
    return str(body)[:MAX_ERROR_BODY]


def _close_response(response: Any) -> None:
    close = getattr(response, "close", None)
    if callable(close):
        close()


def request_json(
    url: str,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    retries: int = DEFAULT_RETRIES,
    backoff_seconds: float = DEFAULT_BACKOFF_SECONDS,
    opener: Callable[..., Any] = urlopen,
    sleeper: Callable[[float], None] = time.sleep,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Fetch and decode JSON with deterministic exponential retry/backoff.

    ``retries`` is the number of retries after the initial attempt.  HTTP 429,
    HTTP 5xx, connection errors, timeouts, and incomplete reads are retried;
    other HTTP statuses fail immediately.
    """

    if timeout <= 0:
        raise FetchError("timeout must be greater than zero")
    if retries < 0:
        raise FetchError("retries cannot be negative")
    if backoff_seconds < 0:
        raise FetchError("backoff must be zero or greater")

    total_attempts = retries + 1
    last_error: HTTPFetchError | None = None
    for attempt in range(1, total_attempts + 1):
        request = Request(
            url,
            headers={
                "Accept": "application/json",
                "Accept-Encoding": "identity",
                "User-Agent": USER_AGENT,
            },
        )
        try:
            response = opener(request, timeout=timeout)
            try:
                raw_body = response.read()
                status = getattr(response, "status", None) or getattr(response, "code", None) or 200
                headers = getattr(response, "headers", None)
                content_type = headers.get("Content-Type") if headers is not None else None
            finally:
                _close_response(response)
            if isinstance(raw_body, bytes):
                text_body = raw_body.decode("utf-8-sig")
            else:
                text_body = str(raw_body)
            try:
                payload = json.loads(text_body)
            except json.JSONDecodeError as exc:
                raise FetchError(f"POWER returned invalid JSON from {url}: {exc}") from exc
            if not isinstance(payload, dict):
                raise FetchError(f"POWER returned a non-object JSON response from {url}")
            return payload, {
                "http_status": int(status),
                "content_type": content_type,
                "attempts": attempt,
            }
        except HTTPError as exc:
            body = _response_body(exc)
            status = int(exc.code)
            retryable = status == 429 or status >= 500
            last_error = HTTPFetchError(
                f"HTTP {status} from NASA POWER ({url})",
                url=url,
                status=status,
                body=body,
                attempts=attempt,
            )
            if not retryable or attempt >= total_attempts:
                raise last_error from exc
        except (URLError, TimeoutError, OSError, http.client.IncompleteRead) as exc:
            last_error = HTTPFetchError(
                f"could not reach NASA POWER ({url}): {exc}",
                url=url,
                body=str(exc),
                attempts=attempt,
            )
            if attempt >= total_attempts:
                raise last_error from exc

        delay = backoff_seconds * (2 ** (attempt - 1))
        if delay:
            sleeper(delay)

    # The loop either returns or raises, but retaining this guard keeps static
    # type checkers and future edits honest.
    if last_error is not None:  # pragma: no cover
        raise last_error
    raise FetchError(f"request failed without a response: {url}")  # pragma: no cover


def _properties_parameter_map(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    properties = payload.get("properties")
    if not isinstance(properties, Mapping):
        raise FetchError("POWER response is missing an object-valued properties field")
    parameter_map = properties.get("parameter")
    if parameter_map is None:
        # Accept the plural spelling used by a few POWER-compatible fixtures.
        parameter_map = properties.get("parameters")
    if not isinstance(parameter_map, Mapping):
        raise FetchError("POWER response is missing properties.parameter time series")
    return parameter_map


def _provider_fill_values(payload: Mapping[str, Any]) -> set[float]:
    """Return numeric fill values advertised by a POWER response."""

    values: set[float] = set()
    header = payload.get("header")
    if isinstance(header, Mapping):
        raw_fill_value = header.get("fill_value")
        try:
            fill_value = float(raw_fill_value)
        except (TypeError, ValueError):
            fill_value = math.nan
        if math.isfinite(fill_value):
            values.add(fill_value)
    return values


def _is_missing_value(value: Any, fill_values: Iterable[Any] = ()) -> bool:
    """Recognise missing values, including NASA's ``-999`` sentinel family."""

    if value is None:
        return True
    if isinstance(value, bool):
        return False
    if isinstance(value, str) and not value.strip():
        return True
    try:
        numeric_value = float(value)
    except (TypeError, ValueError):
        return False
    if not math.isfinite(numeric_value) or numeric_value <= -900:
        return True
    for raw_fill_value in fill_values:
        try:
            if numeric_value == float(raw_fill_value):
                return True
        except (TypeError, ValueError):
            continue
    return False


def _csv_value(value: Any, fill_values: Iterable[Any] = ()) -> str:
    """Render a POWER scalar, converting provider fill values to blank cells."""

    if _is_missing_value(value, fill_values):
        return ""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, (int, str)):
        return str(value)
    return str(value)


def normalise_response(
    payload: Mapping[str, Any],
    start_date: datetime_module.date,
    end_date: datetime_module.date,
    parameters: Sequence[str],
    *,
    source_parameter_map: Mapping[str, str] | None = None,
) -> list[dict[str, str]]:
    """Normalise one POWER response into ordered hourly rows.

    ``source_parameter_map`` allows a logical ``PRECTOTCORR`` column to be
    populated from a supported ``PRECTOT`` response when the corrected series
    is unavailable.  Missing values for an otherwise valid hour remain blank;
    timestamps themselves must cover every expected hour.
    """

    logical_parameters = parse_parameters(parameters)
    source_parameter_map = dict(source_parameter_map or {})
    raw_parameters = _properties_parameter_map(payload)
    provider_fill_values = _provider_fill_values(payload)
    expected = expected_timestamps(start_date, end_date)
    expected_set = set(expected)
    values_by_parameter: dict[str, dict[datetime_module.datetime, Any]] = {}

    for logical_parameter in logical_parameters:
        source_parameter = source_parameter_map.get(logical_parameter, logical_parameter)
        series = raw_parameters.get(source_parameter)
        if not isinstance(series, Mapping):
            raise FetchError(
                f"POWER response is missing requested parameter {source_parameter!r}"
            )
        values: dict[datetime_module.datetime, Any] = {}
        for raw_timestamp, value in series.items():
            timestamp = parse_power_timestamp(raw_timestamp)
            if timestamp in values:
                raise FetchError(
                    f"duplicate hourly timestamp {timestamp.isoformat()} in {source_parameter}"
                )
            if timestamp not in expected_set:
                raise FetchError(
                    f"POWER returned {timestamp.isoformat()} outside requested interval "
                    f"{start_date.isoformat()} through {end_date.isoformat()}"
                )
            values[timestamp] = value
        values_by_parameter[logical_parameter] = values

    # A valid response must expose every requested hour in at least one series;
    # the final table validator also checks the complete union and duplicates.
    available_timestamps: set[datetime_module.datetime] = set()
    for values in values_by_parameter.values():
        available_timestamps.update(values)
    missing_timestamps = expected_set - available_timestamps
    if missing_timestamps:
        first_missing = min(missing_timestamps).isoformat()
        raise FetchError(f"POWER response is missing hourly timestamp {first_missing}")

    rows: list[dict[str, str]] = []
    for timestamp in expected:
        row: dict[str, str] = {
            TIMESTAMP_COLUMN: timestamp.strftime("%Y-%m-%dT%H:00:00Z")
        }
        for parameter in logical_parameters:
            row[parameter] = _csv_value(
                values_by_parameter[parameter].get(timestamp), provider_fill_values
            )
        rows.append(row)
    return rows


def validate_hourly_rows(
    rows: Sequence[Mapping[str, Any]],
    start_date: datetime_module.date,
    end_date: datetime_module.date,
    expected_columns: Sequence[str],
    *,
    fill_values: Iterable[Any] = (),
) -> dict[str, Any]:
    """Validate schema/coverage and report parameter-level missingness."""

    columns = list(expected_columns)
    if not columns or columns[0] != TIMESTAMP_COLUMN:
        raise FetchError(f"expected columns must begin with {TIMESTAMP_COLUMN!r}")
    if len(set(columns)) != len(columns):
        raise FetchError("expected columns contain duplicates")

    expected = expected_timestamps(start_date, end_date)
    fill_values = tuple(fill_values)
    parameter_columns = columns[1:]
    missing_counts = {parameter: 0 for parameter in parameter_columns}
    valid_max_timestamps: dict[str, str | None] = {
        parameter: None for parameter in parameter_columns
    }
    seen: set[datetime_module.datetime] = set()
    parsed_timestamps: list[datetime_module.datetime] = []
    for row_number, row in enumerate(rows, start=2):
        actual_columns = list(row.keys())
        if actual_columns != columns:
            if set(actual_columns) != set(columns):
                missing = sorted(set(columns) - set(actual_columns))
                extra = sorted(set(actual_columns) - set(columns))
                raise FetchError(
                    f"row {row_number} columns do not match expected columns; "
                    f"missing={missing}, extra={extra}"
                )
            raise FetchError(f"row {row_number} columns are in the wrong order")
        raw_timestamp = row.get(TIMESTAMP_COLUMN)
        if not isinstance(raw_timestamp, str) or not raw_timestamp.endswith("Z"):
            raise FetchError(f"row {row_number} has a non-UTC timestamp {raw_timestamp!r}")
        try:
            timestamp = datetime_module.datetime.strptime(
                raw_timestamp, "%Y-%m-%dT%H:%M:%SZ"
            ).replace(tzinfo=datetime_module.timezone.utc)
        except ValueError as exc:
            raise FetchError(f"row {row_number} has invalid timestamp {raw_timestamp!r}") from exc
        if timestamp in seen:
            raise FetchError(f"duplicate hourly timestamp {raw_timestamp}")
        seen.add(timestamp)
        parsed_timestamps.append(timestamp)
        for parameter in parameter_columns:
            value = row.get(parameter)
            if _is_missing_value(value, fill_values):
                missing_counts[parameter] += 1
            else:
                valid_max_timestamps[parameter] = raw_timestamp

    if parsed_timestamps != sorted(parsed_timestamps):
        raise FetchError("hourly timestamps are not in ascending UTC order")
    if parsed_timestamps != expected:
        missing = sorted(set(expected) - set(parsed_timestamps))
        extra = sorted(set(parsed_timestamps) - set(expected))
        raise FetchError(
            "hourly timestamp coverage does not match requested interval; "
            f"missing={len(missing)}, extra={len(extra)}"
        )
    quality = {
        parameter: {
            "missing_count": missing_counts[parameter],
            "valid_max_timestamp_utc": valid_max_timestamps[parameter],
        }
        for parameter in parameter_columns
    }
    return {
        "rows": len(rows),
        "expected_rows": len(expected),
        "timestamps_unique": len(seen) == len(rows),
        "hourly_contiguous": True,
        "columns": columns,
        "missing_counts": missing_counts,
        "valid_max_timestamp_utc": valid_max_timestamps,
        "parameter_quality": quality,
    }


def _atomic_path_write(path: Path, writer: Callable[[Any], None], mode: str) -> None:
    """Write a file in the destination directory and atomically replace it."""

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, mode, encoding="utf-8", newline="") as handle:
            writer(handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def atomic_csv_write(path: Path, columns: Sequence[str], rows: Sequence[Mapping[str, str]]) -> None:
    """Atomically write the normalised CSV with a stable newline convention."""

    def write(handle: Any) -> None:
        output = csv.DictWriter(
            handle, fieldnames=list(columns), extrasaction="raise", lineterminator="\n"
        )
        output.writeheader()
        output.writerows(rows)

    _atomic_path_write(path, write, "w")


def atomic_json_write(path: Path, payload: Mapping[str, Any]) -> None:
    """Atomically write indented, sorted JSON provenance."""

    def write(handle: Any) -> None:
        json.dump(payload, handle, indent=2, ensure_ascii=False, sort_keys=True)
        handle.write("\n")

    _atomic_path_write(path, write, "w")


def sha256_file(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
    return size, digest.hexdigest()


def _is_precipitation_unsupported(error: HTTPFetchError) -> bool:
    if error.status not in {400, 404, 422}:
        return False
    body = error.body.lower()
    return "prectotcorr" in body or ("precip" in body and "parameter" in body)


def _fallback_parameters(parameters: Sequence[str]) -> list[str] | None:
    if PRECIPITATION_PARAMETER not in parameters or PRECIPITATION_FALLBACK in parameters:
        return None
    return [
        PRECIPITATION_FALLBACK if parameter == PRECIPITATION_PARAMETER else parameter
        for parameter in parameters
    ]


def _provenance_path(output: Path, configured: Path | None) -> Path:
    return configured if configured is not None else output.with_name(output.name + ".provenance.json")


def fetch_weather(
    *,
    end_date: datetime_module.date,
    output: Path = DEFAULT_OUTPUT,
    provenance: Path | None = None,
    start_date: datetime_module.date = START_DATE,
    latitude: float = DEFAULT_LATITUDE,
    longitude: float = DEFAULT_LONGITUDE,
    community: str = DEFAULT_COMMUNITY,
    parameters: Sequence[str] = DEFAULT_PARAMETERS,
    time_standard: str = DEFAULT_TIME_STANDARD,
    base_url: str = DEFAULT_API_URL,
    timeout: float = DEFAULT_TIMEOUT,
    retries: int = DEFAULT_RETRIES,
    backoff_seconds: float = DEFAULT_BACKOFF_SECONDS,
    opener: Callable[..., Any] = urlopen,
    sleeper: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Fetch, validate, atomically write, and describe the weather table."""

    if start_date < START_DATE:
        raise FetchError(f"start date cannot be earlier than {START_DATE.isoformat()}")
    if end_date < start_date:
        raise FetchError("end date cannot be before start date")
    if time_standard.upper() != "UTC":
        raise FetchError("this fetcher only supports UTC output")
    latitude = parse_coordinate(latitude, name="latitude", minimum=-90, maximum=90)
    longitude = parse_coordinate(longitude, name="longitude", minimum=-180, maximum=180)
    logical_parameters = parse_parameters(parameters)
    if retries < 0 or timeout <= 0 or backoff_seconds < 0:
        raise FetchError("timeout must be > 0, retries >= 0, and backoff >= 0")

    rows: list[dict[str, str]] = []
    request_records: list[dict[str, Any]] = []
    provider_fill_values: set[float] = set()
    source_parameter_map: dict[str, str] = {}
    api_parameters = list(logical_parameters)

    for request_start, request_end in year_ranges(start_date, end_date):
        url = build_request_url(
            base_url,
            request_start,
            request_end,
            api_parameters,
            latitude=latitude,
            longitude=longitude,
            community=community,
            time_standard=time_standard,
        )
        try:
            payload, request_metadata = request_json(
                url,
                timeout=timeout,
                retries=retries,
                backoff_seconds=backoff_seconds,
                opener=opener,
                sleeper=sleeper,
            )
            request_record: dict[str, Any] = {
                "start_date": request_start.isoformat(),
                "end_date": request_end.isoformat(),
                "url": url,
                "parameters": list(api_parameters),
                **request_metadata,
            }
        except HTTPFetchError as error:
            fallback = _fallback_parameters(api_parameters)
            if fallback is None or not _is_precipitation_unsupported(error):
                raise
            api_parameters = fallback
            source_parameter_map[PRECIPITATION_PARAMETER] = PRECIPITATION_FALLBACK
            url = build_request_url(
                base_url,
                request_start,
                request_end,
                api_parameters,
                latitude=latitude,
                longitude=longitude,
                community=community,
                time_standard=time_standard,
            )
            payload, request_metadata = request_json(
                url,
                timeout=timeout,
                retries=retries,
                backoff_seconds=backoff_seconds,
                opener=opener,
                sleeper=sleeper,
            )
            request_record = {
                "start_date": request_start.isoformat(),
                "end_date": request_end.isoformat(),
                "url": url,
                "parameters": list(api_parameters),
                "fallback_from": PRECIPITATION_PARAMETER,
                "fallback_to": PRECIPITATION_FALLBACK,
                "initial_error": str(error),
                **request_metadata,
            }

        request_record["source_parameter_map"] = dict(source_parameter_map)
        request_records.append(request_record)
        provider_fill_values.update(_provider_fill_values(payload))
        rows.extend(
            normalise_response(
                payload,
                request_start,
                request_end,
                logical_parameters,
                source_parameter_map=source_parameter_map,
            )
        )

    columns = [TIMESTAMP_COLUMN, *logical_parameters]
    validation = validate_hourly_rows(
        rows,
        start_date,
        end_date,
        columns,
        fill_values=provider_fill_values,
    )
    output = Path(output)
    provenance_path = _provenance_path(output, provenance)
    if output.resolve() == provenance_path.resolve():
        raise FetchError("output and provenance paths must be different")
    atomic_csv_write(output, columns, rows)
    output_bytes, output_sha256 = sha256_file(output)
    retrieved_at = utc_now()
    source_urls = [str(record["url"]) for record in request_records]
    provenance_payload: dict[str, Any] = {
        "dataset": "nasa_power_melbourne_hourly",
        "retrieved_at_utc": retrieved_at,
        "provider": "NASA POWER",
        "documentation_url": POWER_DOCUMENTATION_URL,
        "source_url": source_urls[0] if len(source_urls) == 1 else None,
        "source_urls": source_urls,
        "location": {
            "latitude": latitude,
            "longitude": longitude,
            "community": community,
        },
        "time": {
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "time_standard": "UTC",
            "timestamp_column": TIMESTAMP_COLUMN,
        },
        "parameters": list(logical_parameters),
        "api_parameters": list(api_parameters),
        "source_parameter_map": dict(source_parameter_map),
        "missing_value_policy": {
            "blank_for_numeric_at_or_below": -900,
            "provider_fill_values": sorted(provider_fill_values),
        },
        "requests": request_records,
        "output": {
            "path": str(output.resolve()),
            "provenance_path": str(provenance_path.resolve()),
            "bytes": output_bytes,
            "sha256": output_sha256,
            "columns": columns,
        },
        "validation": validation,
        "fetcher": {
            "script": str(Path(__file__).resolve()),
            "user_agent": USER_AGENT,
            "retry_policy": {
                "retries_after_initial": retries,
                "backoff_seconds": backoff_seconds,
                "timeout_seconds": timeout,
            },
        },
    }
    atomic_json_write(provenance_path, provenance_payload)
    return {
        "output": output,
        "provenance": provenance_path,
        "rows": len(rows),
        "columns": columns,
        "requests": len(request_records),
        "sha256": output_sha256,
        "validation": validation,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--end-date",
        required=True,
        help="inclusive final date (YYYY-MM-DD); data starts at 2009-05-01",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"normalised CSV destination (default: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--provenance",
        type=Path,
        help="JSON sidecar destination (default: OUTPUT.provenance.json)",
    )
    parser.add_argument("--latitude", type=float, default=DEFAULT_LATITUDE)
    parser.add_argument("--longitude", type=float, default=DEFAULT_LONGITUDE)
    parser.add_argument("--community", default=DEFAULT_COMMUNITY)
    parser.add_argument(
        "--parameters",
        default=",".join(DEFAULT_PARAMETERS),
        help="comma-separated POWER hourly parameters",
    )
    parser.add_argument(
        "--time-standard",
        default=DEFAULT_TIME_STANDARD,
        choices=("UTC", "utc"),
        help="POWER time standard; UTC is required for the output contract",
    )
    parser.add_argument("--base-url", default=DEFAULT_API_URL, help=argparse.SUPPRESS)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument(
        "--retries",
        type=int,
        default=DEFAULT_RETRIES,
        help="retries after the initial request (default: 3)",
    )
    parser.add_argument(
        "--backoff",
        type=float,
        default=DEFAULT_BACKOFF_SECONDS,
        help="initial retry backoff in seconds; doubles per retry",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        end_date = parse_iso_date(args.end_date)
        result = fetch_weather(
            end_date=end_date,
            output=args.output,
            provenance=args.provenance,
            latitude=args.latitude,
            longitude=args.longitude,
            community=args.community,
            parameters=parse_parameters(args.parameters),
            time_standard=args.time_standard,
            base_url=args.base_url,
            timeout=args.timeout,
            retries=args.retries,
            backoff_seconds=args.backoff,
        )
    except (FetchError, OSError) as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 1

    print(
        f"[ok] NASA POWER weather: {result['rows']} rows, "
        f"{result['requests']} yearly request(s), sha256={result['sha256']}"
    )
    print(f"CSV: {result['output']}")
    print(f"Provenance: {result['provenance']}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("Interrupted; existing output files were not replaced.", file=sys.stderr)
        raise SystemExit(130)
