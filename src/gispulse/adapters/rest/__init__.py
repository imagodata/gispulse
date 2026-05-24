"""REST service adapters — GeoJSON and tabular-JSON over HTTP.

Importing this package self-registers the core REST transport adapters in
:data:`core.sources.PROTOCOLS`, so the ETL fetch path has real fetchers to
dispatch to: ``rest-api`` (GeoJSON FeatureCollection, #192) and
``rest-table`` (paginated tabular JSON, #196).
"""

from __future__ import annotations

# Side-effect imports: each fetcher registers itself on import.
from gispulse.adapters.rest import rest_fetcher  # noqa: F401
from gispulse.adapters.rest import rest_table_fetcher  # noqa: F401

__all__ = ["rest_fetcher", "rest_table_fetcher"]
