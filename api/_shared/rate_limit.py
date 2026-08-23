"""Best-effort per-IP rate limiting for the /api inference functions.

Each of these endpoints can trigger multiple XGBoost model calls plus live
weather/sensor lookups per request — cheap to hammer, not cheap to serve.
Vercel's Fluid Compute reuses warm function instances across concurrent
requests (see Context/Chunks/heatroute.md), so this in-memory counter is not
a no-op the way it would be under classic one-request-per-instance
serverless — it actually throttles repeat callers on the same warm instance.
It resets on a cold start and is not shared across instances, so it is a
mitigation against casual abuse/cost spikes, not a hard security boundary —
add a real edge-level limiter (e.g. Vercel Firewall) before this matters for
a production launch.
"""

from __future__ import annotations

import time

_WINDOW_SECONDS = 60.0
_MAX_REQUESTS_PER_WINDOW = 30

# client_ip -> (window_start_epoch_s, count_in_window)
_buckets: dict[str, tuple[float, int]] = {}


def client_ip(headers) -> str:
    forwarded = headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return headers.get("X-Real-IP", "unknown")


def check(headers) -> bool:
    """Returns True if the request should proceed, False if it should be
    rejected (429). Prunes old buckets opportunistically so this doesn't
    grow unbounded on a long-lived warm instance."""
    ip = client_ip(headers)
    now = time.time()
    if len(_buckets) > 5000:
        _buckets.clear()

    window_start, count = _buckets.get(ip, (now, 0))
    if now - window_start >= _WINDOW_SECONDS:
        window_start, count = now, 0

    count += 1
    _buckets[ip] = (window_start, count)
    return count <= _MAX_REQUESTS_PER_WINDOW
