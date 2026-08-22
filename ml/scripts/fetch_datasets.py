#!/usr/bin/env python3
"""Fetch the versioned HeatRoute dataset catalog with no third-party packages.

Direct HTTP downloads are written to ``.part`` files and atomically renamed only
after completion.  An interrupted HTTP download is resumed with a Range request
when the publisher supports it.  ArcGIS FeatureServer entries are fetched as
paginated GeoJSON clipped to the catalog bbox. Catalog entries may select a
domain-specific storage root; ``--raw-dir`` overrides those roots when supplied.
"""

from __future__ import annotations

import argparse
import datetime as datetime_module
import hashlib
import http.client
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen


SCRIPT_DIR = Path(__file__).resolve().parent
ML_DIR = SCRIPT_DIR.parent
DEFAULT_CATALOG = ML_DIR / "data" / "catalog.json"
DEFAULT_RAW_DIR = ML_DIR / "data" / "raw"
DEFAULT_PROVENANCE_DIR = ML_DIR / "data" / "provenance"
CHUNK_SIZE = 1024 * 1024
DEFAULT_TIMEOUT = 120
USER_AGENT = "HeatRoute-data-fetcher/1.0 (+https://github.com/HeatRoute)"


class FetchError(RuntimeError):
    """A user-facing, actionable fetch error."""


def utc_now() -> str:
    return datetime_module.datetime.now(datetime_module.timezone.utc).isoformat().replace(
        "+00:00", "Z"
    )


def load_catalog(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            catalog = json.load(handle)
    except FileNotFoundError as exc:
        raise FetchError(f"catalog not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise FetchError(f"catalog is not valid JSON ({path}): {exc}") from exc

    entries = catalog.get("datasets")
    if not isinstance(entries, list):
        raise FetchError("catalog must contain a datasets list")

    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict) or not entry.get("id"):
            raise FetchError("every catalog dataset must be an object with an id")
        dataset_id = str(entry["id"])
        if dataset_id in seen:
            raise FetchError(f"duplicate dataset id in catalog: {dataset_id}")
        seen.add(dataset_id)
        profile = entry.get("profile")
        if profile not in {"core", "extended", "manual"}:
            raise FetchError(
                f"{dataset_id}: profile must be core, extended, or manual (got {profile!r})"
            )
        storage = entry.get("storage")
        if storage is not None:
            storage_path = Path(str(storage))
            if (
                storage_path.is_absolute()
                or bool(storage_path.drive)
                or bool(storage_path.anchor)
                or not storage_path.parts
                or any(part in {".", ".."} for part in storage_path.parts)
            ):
                raise FetchError(
                    f"{dataset_id}: storage must be a safe path relative to ml/"
                )
        domains = entry.get("domains", [])
        if not isinstance(domains, list) or any(
            not isinstance(domain, str) or not domain.strip() for domain in domains
        ):
            raise FetchError(f"{dataset_id}: domains must be a list of non-empty strings")
    catalog["datasets"] = entries
    return catalog


def parse_dataset_filters(values: Iterable[str] | None) -> set[str]:
    result: set[str] = set()
    for value in values or []:
        result.update(part.strip() for part in value.split(",") if part.strip())
    return result


def select_entries(
    entries: list[dict[str, Any]], profile: str, dataset_filters: set[str]
) -> list[dict[str, Any]]:
    if dataset_filters:
        known = {str(entry["id"]) for entry in entries}
        unknown = sorted(dataset_filters - known)
        if unknown:
            raise FetchError(
                "unknown dataset id(s): "
                + ", ".join(unknown)
                + ". Use --list to see catalog ids."
            )
        return [entry for entry in entries if str(entry["id"]) in dataset_filters]
    return [entry for entry in entries if entry.get("profile") == profile]


def print_list(entries: list[dict[str, Any]]) -> None:
    print("ID\tPROFILE\tKIND\tDOMAINS\tSTORAGE\tTITLE")
    for entry in entries:
        print(
            "\t".join(
                [
                    str(entry["id"]),
                    str(entry.get("profile", "")),
                    str(entry.get("kind", "direct")),
                    ",".join(str(domain) for domain in entry.get("domains", [])),
                    str(entry.get("storage", "data/raw")),
                    str(entry.get("title", "")),
                ]
            )
        )


def parse_bbox(value: str | None) -> list[float] | None:
    if not value:
        return None
    try:
        parts = [float(part.strip()) for part in value.split(",")]
    except ValueError as exc:
        raise FetchError("--bbox must be min_lon,min_lat,max_lon,max_lat") from exc
    if len(parts) != 4 or parts[0] >= parts[2] or parts[1] >= parts[3]:
        raise FetchError("--bbox must be min_lon,min_lat,max_lon,max_lat with min < max")
    return parts


def request_url(url: str, headers: dict[str, str] | None = None):
    request_headers = {"User-Agent": USER_AGENT, "Accept-Encoding": "identity"}
    if headers:
        request_headers.update(headers)
    request = Request(url, headers=request_headers)
    try:
        return urlopen(request, timeout=DEFAULT_TIMEOUT)
    except HTTPError as exc:
        raise FetchError(f"HTTP {exc.code} from {url}") from exc
    except URLError as exc:
        raise FetchError(f"could not reach {url}: {exc.reason}") from exc
    except (TimeoutError, OSError) as exc:
        raise FetchError(f"could not reach {url}: {exc}") from exc


def response_metadata(response: Any) -> dict[str, Any]:
    headers = response.headers
    status = getattr(response, "status", None)
    content_length = headers.get("Content-Length")
    try:
        content_length_value: int | None = int(content_length) if content_length else None
    except ValueError:
        content_length_value = None
    return {
        "http_status": status,
        "content_type": headers.get("Content-Type"),
        "content_length": content_length_value,
        "etag": headers.get("ETag"),
        "last_modified": headers.get("Last-Modified"),
    }


def sha256_file(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(CHUNK_SIZE)
            if not chunk:
                break
            digest.update(chunk)
            size += len(chunk)
    return size, digest.hexdigest()


def atomic_json_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    os.replace(temporary, path)


def output_name(entry: dict[str, Any], url: str) -> str:
    configured = entry.get("output")
    if configured:
        name = Path(str(configured)).name
    else:
        name = Path(urlparse(url).path).name
    if not name or name in {".", ".."}:
        name = f"{entry['id']}.download"
    # Catalog-controlled names must never escape the per-dataset directory.
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", name)
    return name


def direct_download(url: str, destination: Path, force: bool) -> dict[str, Any]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    part_path = destination.with_name(destination.name + ".part")

    if destination.exists() and not force:
        size, digest = sha256_file(destination)
        return {"status": "exists", "bytes": size, "sha256": digest, "path": str(destination)}

    if force:
        destination.unlink(missing_ok=True)
        part_path.unlink(missing_ok=True)

    resume_from = part_path.stat().st_size if part_path.exists() else 0
    headers = {"Accept": "*/*"}
    if resume_from:
        headers["Range"] = f"bytes={resume_from}-"

    try:
        response = request_url(url, headers)
    except FetchError as exc:
        # A stale partial is not useful if a server rejects a range request.
        if resume_from and "HTTP 416" in str(exc):
            part_path.unlink(missing_ok=True)
            response = request_url(url, {"Accept": "*/*"})
            resume_from = 0
        else:
            raise

    metadata = response_metadata(response)
    content_range = response.headers.get("Content-Range", "")
    append = bool(resume_from and metadata.get("http_status") == 206 and content_range.startswith(f"bytes {resume_from}-"))
    if resume_from and not append:
        # The server ignored Range (200) or returned an incompatible range.
        part_path.unlink(missing_ok=True)
        resume_from = 0

    digest = hashlib.sha256()
    bytes_written = 0
    if append:
        with part_path.open("rb") as partial:
            while True:
                chunk = partial.read(CHUNK_SIZE)
                if not chunk:
                    break
                digest.update(chunk)
                bytes_written += len(chunk)

    mode = "ab" if append else "wb"
    try:
        with response, part_path.open(mode) as output:
            while True:
                chunk = response.read(CHUNK_SIZE)
                if not chunk:
                    break
                output.write(chunk)
                digest.update(chunk)
                bytes_written += len(chunk)
    except (OSError, URLError, TimeoutError, http.client.IncompleteRead) as exc:
        raise FetchError(f"failed while writing {url}: {exc}") from exc

    expected = metadata.get("content_length")
    if append and expected is not None:
        expected += resume_from
    if expected is not None and bytes_written != expected:
        raise FetchError(
            f"incomplete download for {url}: received {bytes_written} bytes, expected {expected}; "
            "rerun to resume"
        )

    os.replace(part_path, destination)
    return {
        "status": "resumed" if append else "downloaded",
        "bytes": bytes_written,
        "sha256": digest.hexdigest(),
        "path": str(destination),
        **metadata,
    }


def arcgis_download(
    entry: dict[str, Any], destination: Path, bbox: list[float] | None, force: bool
) -> dict[str, Any]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    part_path = destination.with_name(destination.name + ".part")
    if destination.exists() and not force:
        size, digest = sha256_file(destination)
        return {"status": "exists", "bytes": size, "sha256": digest, "path": str(destination)}
    destination.unlink(missing_ok=True)
    part_path.unlink(missing_ok=True)

    service_url = str(entry.get("url", "")).rstrip("/")
    query_url = service_url if service_url.endswith("/query") else service_url + "/query"
    page_size = int(entry.get("page_size", 1000))
    offset = 0
    feature_count = 0
    pages = 0
    first_feature = True
    digest = hashlib.sha256()

    try:
        with part_path.open("wb") as output:
            prefix = b'{"type":"FeatureCollection","features":['
            output.write(prefix)
            digest.update(prefix)
            while pages < 100000:
                params: dict[str, Any] = {
                    "where": entry.get("where", "1=1"),
                    "outFields": entry.get("out_fields", "*"),
                    "outSR": "4326",
                    "f": "geojson",
                    "returnGeometry": "true",
                    "resultOffset": offset,
                    "resultRecordCount": page_size,
                }
                if bbox:
                    params.update(
                        {
                            "geometry": ",".join(str(number) for number in bbox),
                            "geometryType": "esriGeometryEnvelope",
                            "inSR": "4326",
                            "spatialRel": "esriSpatialRelIntersects",
                        }
                    )
                page_url = query_url + "?" + urlencode(params)
                response = request_url(page_url, {"Accept": "application/geo+json,application/json"})
                metadata = response_metadata(response)
                try:
                    payload = json.load(response)
                finally:
                    response.close()
                if "error" in payload:
                    raise FetchError(f"ArcGIS error for {entry['id']}: {payload['error']}")
                features = payload.get("features")
                if not isinstance(features, list):
                    raise FetchError(f"ArcGIS response for {entry['id']} has no features list")
                for feature in features:
                    encoded = json.dumps(feature, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                    if not first_feature:
                        output.write(b",")
                        digest.update(b",")
                    output.write(encoded)
                    digest.update(encoded)
                    first_feature = False
                    feature_count += 1
                pages += 1
                if not features:
                    break
                offset += len(features)
                if len(features) < page_size and not payload.get("exceededTransferLimit"):
                    break
            else:
                raise FetchError(f"ArcGIS pagination exceeded safety limit for {entry['id']}")
            suffix = b"]}"
            output.write(suffix)
            digest.update(suffix)
    except OSError as exc:
        raise FetchError(f"failed while writing ArcGIS output for {entry['id']}: {exc}") from exc

    os.replace(part_path, destination)
    size = destination.stat().st_size
    return {
        "status": "downloaded",
        "bytes": size,
        "sha256": digest.hexdigest(),
        "path": str(destination),
        "pages": pages,
        "features": feature_count,
        "bbox": bbox,
    }


def fetch_entry(
    entry: dict[str, Any],
    raw_dir: Path | None,
    provenance_dir: Path,
    bbox: list[float] | None,
    force: bool,
) -> dict[str, Any]:
    dataset_id = str(entry["id"])
    started_at = utc_now()
    url = entry.get("url")
    kind = str(entry.get("kind", "direct"))
    record: dict[str, Any] = {
        "dataset_id": dataset_id,
        "profile": entry.get("profile"),
        "domains": entry.get("domains", []),
        "storage": entry.get("storage", "data/raw"),
        "title": entry.get("title"),
        "url": url,
        "page_url": entry.get("page_url"),
        "license": entry.get("license"),
        "license_url": entry.get("license_url"),
        "started_at": started_at,
    }
    try:
        if kind == "manual" or entry.get("downloadable") is False or not url:
            result = {
                "status": "manual",
                "message": entry.get("manual_reason") or entry.get("notes") or "manual retrieval required",
            }
        else:
            catalog_storage = entry.get("storage")
            storage_root = raw_dir or (
                ML_DIR / str(catalog_storage) if catalog_storage else DEFAULT_RAW_DIR
            )
            destination = storage_root / dataset_id / output_name(entry, str(url))
            if kind == "arcgis_geojson":
                result = arcgis_download(entry, destination, bbox, force)
            elif kind == "direct":
                result = direct_download(str(url), destination, force)
            else:
                raise FetchError(f"{dataset_id}: unsupported catalog kind {kind!r}")
        record.update(result)
    except FetchError as exc:
        record.update({"status": "error", "error": str(exc)})
    record["completed_at"] = utc_now()
    provenance_path = provenance_dir / f"{dataset_id}.json"
    atomic_json_write(provenance_path, record)
    return record


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG, help="catalog JSON path")
    parser.add_argument("--profile", choices=("core", "extended", "manual"), default="core")
    parser.add_argument("--dataset", action="append", help="dataset id; repeat or comma-separate")
    parser.add_argument("--list", action="store_true", help="list selected catalog entries and exit")
    parser.add_argument("--force", action="store_true", help="redownload existing files from scratch")
    parser.add_argument("--bbox", help="ArcGIS clip bbox: min_lon,min_lat,max_lon,max_lat")
    parser.add_argument(
        "--raw-dir",
        type=Path,
        help="override all catalog storage roots with one raw-data directory",
    )
    parser.add_argument("--provenance-dir", type=Path, default=DEFAULT_PROVENANCE_DIR)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        catalog = load_catalog(args.catalog)
        filters = parse_dataset_filters(args.dataset)
        entries = select_entries(catalog["datasets"], args.profile, filters)
        if args.list:
            print_list(entries)
            return 0
        catalog_bbox = catalog.get("city_of_melbourne_bbox", {}).get("value")
        bbox = parse_bbox(args.bbox) or parse_bbox(
            ",".join(str(number) for number in catalog_bbox) if catalog_bbox else None
        )
    except FetchError as exc:
        parser.error(str(exc))

    if args.raw_dir is not None:
        args.raw_dir.mkdir(parents=True, exist_ok=True)
    args.provenance_dir.mkdir(parents=True, exist_ok=True)
    failures = 0
    for entry in entries:
        dataset_id = str(entry["id"])
        result = fetch_entry(entry, args.raw_dir, args.provenance_dir, bbox, args.force)
        status = result.get("status")
        if status == "error":
            failures += 1
            print(f"[error] {dataset_id}: {result.get('error')}", file=sys.stderr)
        elif status == "manual":
            print(f"[manual] {dataset_id}: {result.get('message')}")
        else:
            print(
                f"[{status}] {dataset_id}: {result.get('bytes', 0)} bytes "
                f"sha256={result.get('sha256', 'n/a')}"
            )
    print(f"Processed {len(entries)} dataset(s); {failures} error(s).")
    return 1 if failures else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("Interrupted; any .part files are safe to resume.", file=sys.stderr)
        raise SystemExit(130)
