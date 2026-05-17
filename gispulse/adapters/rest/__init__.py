"""REST service adapter — generic GeoJSON-over-HTTP fetcher.

Importing this package self-registers the core REST GeoJSON transport
adapter in :data:`core.sources.PROTOCOLS` (issue #192), so the ETL fetch
path has a real ``rest-api`` fetcher to dispatch to.
"""

from __future__ import annotations

# Side-effect import: RestGeoJsonFetcher registers itself on import.
from gispulse.adapters.rest import rest_fetcher  # noqa: F401

__all__ = ["rest_fetcher"]
