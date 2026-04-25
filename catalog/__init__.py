"""GIS Catalog — projections, basemaps, flux, and open data providers."""

from catalog import registry  # noqa: F401


def _auto_register():
    """Import providers to trigger self-registration."""
    from catalog.providers import projections as _projections  # noqa: F401
    from catalog.providers import basemaps as _basemaps  # noqa: F401
    from catalog.providers import flux_ign as _flux_ign  # noqa: F401
    from catalog.providers import flux_osm as _flux_osm  # noqa: F401
    from catalog.providers import opendata_datagouv as _opendata_datagouv  # noqa: F401
    from catalog.providers import opendata_ign as _opendata_ign  # noqa: F401
    from catalog.providers import opendata_hub as _opendata_hub  # noqa: F401


_auto_register()

# Discover third-party catalog providers via entry-points
from catalog.registry import _discover_providers  # noqa: E402

_discover_providers()
