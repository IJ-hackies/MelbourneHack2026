"""Assembles real request-time feature vectors from live data sources.

Per the user's explicit scope decision, the inference endpoints compute their
own features rather than accepting caller-supplied values — this module is
that lookup layer. It is deliberately separate from the offline ml/ pipeline
(ml/scripts/build_crowd_dataset.py etc.), which only produces static training
datasets and has no serving-time role.

Crowd: the City of Melbourne pedestrian counts dataset
(pedestrian-counting-system-monthly-counts-per-hour, see
ml/data/catalog.json's `com_pedestrian_counts_current` entry) is published on
an Opendatasoft Explore v2.1 portal, which supports a filtered `/records`
query — not just the bulk `/exports` used by the offline pipeline. That query
mode is what this module uses to fetch a sensor's recent hours live.

Traffic: ml/data/catalog.json shows SCATS and Transport Activity are only
published as periodic batch archives (monthly/annual ZIP downloads from
opendata.transport.vic.gov.au) with no live/recent-hours query endpoint.
There is nothing to fetch live, so `build_traffic_features` honestly reports
quality.status="unavailable" rather than fabricating a feature vector — this
is a real, documented data-availability gap, not an implementation shortcut.
"""

from __future__ import annotations

import math
import urllib.parse
import urllib.request
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

MELBOURNE_TZ = ZoneInfo("Australia/Melbourne")

PEDESTRIAN_DATASET = "pedestrian-counting-system-monthly-counts-per-hour"
PEDESTRIAN_RECORDS_URL = (
    f"https://data.melbourne.vic.gov.au/api/explore/v2.1/catalog/datasets/"
    f"{PEDESTRIAN_DATASET}/records"
)
SENSOR_LOCATIONS_DATASET = "pedestrian-counting-system-sensor-locations"
SENSOR_LOCATIONS_RECORDS_URL = (
    f"https://data.melbourne.vic.gov.au/api/explore/v2.1/catalog/datasets/"
    f"{SENSOR_LOCATIONS_DATASET}/records"
)
OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
# Melbourne CBD centroid, matching the regional NASA POWER point used at
# training time (ml/data/catalog.json: nasa_power_melbourne_hourly).
WEATHER_LAT, WEATHER_LON = -37.8136, 144.9631

# A sensor's raw history is reused across the 1h/24h/168h lookups within one
# request, and briefly across warm invocations of the same Fluid Compute
# instance, to avoid refetching the same recent window every call.
_history_cache: dict[str, tuple[datetime, list[dict]]] = {}
_HISTORY_CACHE_TTL = timedelta(minutes=5)

# Sensor locations change far less often than counts (relocation is rare and
# planned), so this is cached much longer — but still fetched live rather
# than frozen into a committed file, since STATE.md notes sensor metadata is
# not an effective-dated relocation history and a stale hardcoded snapshot
# could silently point at a decommissioned or relocated sensor.
_sensor_locations_cache: tuple[datetime, list[dict]] | None = None
_SENSOR_LOCATIONS_CACHE_TTL = timedelta(hours=1)


def _http_get_json(url: str, timeout: float = 8.0) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "LeafRoute/0.1 (hackathon project)"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


# Opendatasoft's Explore v2.1 API caps `limit` at 100 per request (a request
# for more is a 400, not a truncation) — this paginates with `offset` to get
# every row rather than silently capping at 100 and degrading coverage for
# whichever sensors/hours happen to sort past that point.
def _fetch_all_records(base_url: str, where: str | None, order_by: str, max_rows: int) -> list[dict]:
    records: list[dict] = []
    offset = 0
    page_size = 100
    while len(records) < max_rows:
        params = {"limit": page_size, "offset": offset, "order_by": order_by}
        if where:
            params["where"] = where
        url = f"{base_url}?{urllib.parse.urlencode(params)}"
        data = _http_get_json(url)
        page = [r["record"]["fields"] if "record" in r else r for r in data.get("results", [])]
        records.extend(page)
        if len(page) < page_size:
            break
        offset += page_size
    return records[:max_rows]


def _fetch_sensor_locations() -> list[dict]:
    """All known sensors with their current lat/lon, live from the City's
    sensor-locations dataset (not the offline ml/ mirror)."""
    global _sensor_locations_cache
    now = datetime.now(tz=MELBOURNE_TZ)
    if _sensor_locations_cache and now - _sensor_locations_cache[0] < _SENSOR_LOCATIONS_CACHE_TTL:
        return _sensor_locations_cache[1]

    # ~134 sensors total, well within a couple of paginated 100-row requests.
    records = _fetch_all_records(SENSOR_LOCATIONS_RECORDS_URL, None, "location_id", max_rows=500)
    _sensor_locations_cache = (now, records)
    return records


def list_sensor_locations() -> list[dict]:
    """Public wrapper around _fetch_sensor_locations for callers (e.g.
    route-planner.py's crowd-aware routing bias) that need every sensor's
    location, not just the single nearest one resolve_nearest_crowd_sensor
    returns."""
    try:
        return _fetch_sensor_locations()
    except Exception as exc:
        print(f"list_sensor_locations: sensor locations fetch failed: {exc!r}")
        return []


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


# Beyond this, "nearest sensor" is likely a different street/area entirely,
# not a meaningful proxy for pedestrian flow at the destination.
NEAREST_SENSOR_WARNING_KM = 0.5


def resolve_nearest_crowd_sensor(
    lat: float, lon: float
) -> tuple[str | None, float | None, list[str]]:
    """Finds the live pedestrian-counting sensor nearest to (lat, lon).

    Returns (sensor_id, distance_km, warnings). sensor_id is None if the
    live locations feed is unreachable or empty — callers must treat that as
    unavailable, never guess a sensor_id."""
    try:
        sensors = _fetch_sensor_locations()
    except Exception as exc:
        print(f"resolve_nearest_crowd_sensor: sensor locations fetch failed: {exc!r}")
        return None, None, ["live_sensor_locations_feed_unreachable"]

    best_id: str | None = None
    best_km: float | None = None
    for sensor in sensors:
        sensor_lat, sensor_lon = sensor.get("latitude"), sensor.get("longitude")
        location_id = sensor.get("location_id")
        if sensor_lat is None or sensor_lon is None or location_id is None:
            continue
        distance = _haversine_km(lat, lon, float(sensor_lat), float(sensor_lon))
        if best_km is None or distance < best_km:
            best_km, best_id = distance, str(location_id)

    if best_id is None:
        return None, None, ["no_sensors_in_live_locations_feed"]

    warnings = []
    if best_km is not None and best_km > NEAREST_SENSOR_WARNING_KM:
        warnings.append(f"nearest_sensor_distance_km_{round(best_km, 2)}_exceeds_proxy_radius")

    return best_id, best_km, warnings


def _fetch_sensor_history(sensor_id: str, as_of: datetime) -> list[dict]:
    """Recent hourly pedestrian counts for one sensor, most-recent first,
    covering at least the past 168 hours before `as_of`."""
    cached = _history_cache.get(sensor_id)
    now = datetime.now(tz=MELBOURNE_TZ)
    if cached and now - cached[0] < _HISTORY_CACHE_TTL:
        return cached[1]

    # 168+ hours of history needed for the flow_lag_168h feature; two
    # paginated requests comfortably cover that.
    records = _fetch_all_records(
        PEDESTRIAN_RECORDS_URL,
        f"location_id={sensor_id}",
        "sensing_date desc,hourday desc",
        max_rows=200,
    )
    _history_cache[sensor_id] = (now, records)
    return records


def _flow_at(records: list[dict], target: datetime) -> float | None:
    target_date = target.date().isoformat()
    for rec in records:
        if rec.get("sensing_date") == target_date and int(rec.get("hourday", -1)) == target.hour:
            count = rec.get("pedestriancount")
            return float(count) if count is not None else None
    return None


# Keyed by the target hour truncated to the day + hour Open-Meteo actually
# resolves against — a route-planner request scores multiple sample points
# for the same target_hour, and without this every sample re-hit Open-Meteo
# for an identical value (measured: this alone was most of a ~45s request).
_weather_cache: dict[str, dict[str, float] | None] = {}


def _fetch_weather(target_hour: datetime) -> dict[str, float] | None:
    cache_key = target_hour.strftime("%Y-%m-%dT%H")
    if cache_key in _weather_cache:
        return _weather_cache[cache_key]

    result = _fetch_weather_uncached(target_hour)
    _weather_cache[cache_key] = result
    return result


def _fetch_weather_uncached(target_hour: datetime) -> dict[str, float] | None:
    params = {
        "latitude": WEATHER_LAT,
        "longitude": WEATHER_LON,
        "hourly": "temperature_2m",
        "timezone": "Australia/Melbourne",
        "start_date": target_hour.date().isoformat(),
        "end_date": target_hour.date().isoformat(),
    }
    url = f"{OPEN_METEO_URL}?{urllib.parse.urlencode(params)}"
    try:
        data = _http_get_json(url)
        times = data["hourly"]["time"]
        temps = data["hourly"]["temperature_2m"]
        for t, temp in zip(times, temps):
            if t.startswith(target_hour.strftime("%Y-%m-%dT%H")):
                return {"temperature_c": temp}
    except Exception:
        return None
    return None


def current_temperature_c(target_hour: datetime) -> float | None:
    """Public wrapper around _fetch_weather for callers (e.g.
    route-planner.py's heat-adaptive shade bias) that just need the real
    current temperature, not a full crowd feature vector."""
    weather = _fetch_weather(target_hour)
    return weather["temperature_c"] if weather else None


def build_crowd_features(
    sensor_id: str, target_hour: datetime
) -> tuple[dict[str, float | int | str | bool | None], str, list[str]]:
    """Returns (features, quality_status, warnings) for crowd-inference/v1.

    feature_asof = target_hour - 1h, per ml/crowd/SOFTWARE_HANDOFF.md.
    """
    warnings: list[str] = []
    feature_asof = target_hour - timedelta(hours=1)

    try:
        records = _fetch_sensor_history(sensor_id, feature_asof)
    except Exception:
        return {}, "unavailable", ["live_pedestrian_feed_unreachable"]

    lag_1h = _flow_at(records, feature_asof - timedelta(hours=1))
    lag_24h = _flow_at(records, feature_asof - timedelta(hours=24))
    lag_168h = _flow_at(records, feature_asof - timedelta(hours=168))

    past_24h = [
        v
        for h in range(24)
        if (v := _flow_at(records, feature_asof - timedelta(hours=h))) is not None
    ]
    past_168h = [
        v
        for h in range(168)
        if (v := _flow_at(records, feature_asof - timedelta(hours=h))) is not None
    ]

    if lag_1h is None:
        warnings.append("missing_flow_lag_1h")
    if lag_24h is None:
        warnings.append("missing_flow_lag_24h")
    if lag_168h is None:
        warnings.append("missing_flow_lag_168h")

    weather = _fetch_weather(target_hour)
    if weather is None:
        warnings.append("missing_target_hour_weather")

    rolling_24h_mean = sum(past_24h) / len(past_24h) if past_24h else None
    rolling_168h_mean = sum(past_168h) / len(past_168h) if past_168h else None
    rolling_168h_std = (
        math.sqrt(sum((v - rolling_168h_mean) ** 2 for v in past_168h) / len(past_168h))
        if past_168h and rolling_168h_mean is not None
        else None
    )

    local_dt = target_hour.astimezone(MELBOURNE_TZ)
    is_dst = bool(local_dt.dst())

    features: dict[str, float | int | str | bool | None] = {
        "sensor_id": sensor_id,
        "local_hour": local_dt.hour,
        "prediction_horizon_hours": 1,
        "hour_sin": math.sin(2 * math.pi * local_dt.hour / 24),
        "hour_cos": math.cos(2 * math.pi * local_dt.hour / 24),
        "day_of_week_sin": math.sin(2 * math.pi * local_dt.weekday() / 7),
        "day_of_week_cos": math.cos(2 * math.pi * local_dt.weekday() / 7),
        "day_of_year_sin": math.sin(2 * math.pi * local_dt.timetuple().tm_yday / 365),
        "day_of_year_cos": math.cos(2 * math.pi * local_dt.timetuple().tm_yday / 365),
        "is_weekend": local_dt.weekday() >= 5,
        # Public holiday calendar isn't wired up for live lookups yet.
        "is_public_holiday": False,
        "is_dst": is_dst,
        "flow_lag_1h": lag_1h,
        "flow_lag_24h": lag_24h,
        "flow_lag_168h": lag_168h,
        "flow_rolling_past_24h_mean": rolling_24h_mean,
        "flow_rolling_past_168h_std": rolling_168h_std,
        "flow_rolling_past_168h_count": len(past_168h),
        # Not a model input (not in encoder.model_feature_columns, ignored by
        # _build_dmatrix) — kept here so callers can describe a prediction
        # relative to this sensor's own real recent history ("busier/quieter
        # than usual") instead of showing a raw count with no context.
        "flow_rolling_past_168h_mean": rolling_168h_mean,
        "nasa_temperature_c": weather["temperature_c"] if weather else None,
        "nasa_dewpoint_c": None,
        "nasa_relative_humidity_pct": None,
        "nasa_wind_speed_10m_m_s": None,
        "nasa_wind_direction_sin": None,
        "nasa_wind_direction_cos": None,
        "nasa_surface_pressure_kpa": None,
        "nasa_precipitation_corrected_mm_h": None,
        "nasa_surface_solar_radiation_w_m2": None,
        "nasa_weather_observation_count": 1 if weather else 0,
    }

    warnings.append("public_holiday_calendar_not_available")

    if lag_1h is None and lag_24h is None and lag_168h is None:
        status = "unavailable"
    elif warnings:
        status = "degraded"
    else:
        status = "ok"

    return features, status, warnings


def build_traffic_features(
    source_group: str, observation_unit_id: str, target_hour: datetime
) -> tuple[dict, str, list[str]]:
    """SCATS and Transport Activity are only published as periodic batch
    archives (see module docstring) — there is no live feed to query, so
    this honestly reports `unavailable` rather than fabricating a feature
    vector or zero-filling missing lags."""
    del source_group, observation_unit_id, target_hour  # nothing to look up yet
    return (
        {},
        "unavailable",
        [
            "no_live_traffic_feed",
            "scats_and_transport_activity_are_batch_only_no_live_query_endpoint",
        ],
    )
