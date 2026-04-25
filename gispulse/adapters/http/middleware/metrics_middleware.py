"""
HTTP request metrics middleware for GISPulse.

Automatically records request count, duration, and status code distribution
using the core MetricsCollector. Exposed via ``GET /metrics`` in Prometheus
text format.

Metrics emitted:
    gispulse_http_requests_total      — Counter by method, path, status
    gispulse_http_request_duration_seconds — Histogram of request latencies
    gispulse_http_requests_in_flight  — Gauge of currently active requests
"""

from __future__ import annotations

import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from core.observability import MetricsCollector


def _normalize_path(path: str) -> str:
    """Collapse UUID and numeric path segments to reduce cardinality.

    Examples:
        /datasets/3fa85f64-5717-4562-b3fc-2c963f66afa6 → /datasets/{id}
        /tiles/col/3/4/5.mvt → /tiles/{collection}/{z}/{x}/{y}.mvt
    """
    import re

    # Replace UUIDs
    path = re.sub(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
        "{id}",
        path,
    )
    # Replace pure numeric segments (tile coords, pagination IDs)
    path = re.sub(r"/\d+(?=/|$|\.\w+$)", "/{n}", path)
    return path


class MetricsMiddleware(BaseHTTPMiddleware):
    """Collect HTTP request metrics into MetricsCollector."""

    async def dispatch(self, request: Request, call_next):
        metrics = MetricsCollector.get()
        method = request.method
        path = _normalize_path(request.url.path)

        metrics.inc("gispulse_http_requests_in_flight")
        start = time.perf_counter()

        try:
            response = await call_next(request)
        except Exception:
            metrics.inc(f"gispulse_http_requests_total{{method=\"{method}\",path=\"{path}\",status=\"500\"}}")
            raise
        finally:
            duration = time.perf_counter() - start
            metrics.observe("gispulse_http_request_duration_seconds", duration)
            metrics.inc("gispulse_http_requests_in_flight", -1)

        status = response.status_code
        metrics.inc(f"gispulse_http_requests_total{{method=\"{method}\",path=\"{path}\",status=\"{status}\"}}")

        return response
