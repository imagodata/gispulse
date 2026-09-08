"""Raw UrbIS road surfaces, axes and structures from the official vector WFS."""

from __future__ import annotations

from gispulse.plugins.api import (
    AccessProtocol,
    AccessSpec,
    DeclarativeSource,
    Payload,
    SourceDomain,
    SourceEntryRef,
)

ENDPOINT = "https://geoservices-vector.irisnet.be/geoserver/urbisvector/wfs"
ENTRIES = {
    "urbis-street-surfaces-bxl": ("StreetSurfaces", "Polygon"),
    "urbis-street-axes-bxl": ("StreetAxes", "LineString"),
    "urbis-bridges-bxl": ("Bridges", "Polygon"),
    "urbis-tunnels-bxl": ("Tunnels", "Polygon"),
}


def _access(layer: str) -> AccessSpec:
    try:
        from gispulse.adapters.ogc.counted_wfs import fetch_counted_wfs  # noqa: F401
    except ImportError as exc:
        raise RuntimeError(
            "URBIS_PAGINATION_REQUIRED: install core with counted WFS support"
        ) from exc
    return AccessSpec(
        protocol=AccessProtocol.WFS,
        endpoint=ENDPOINT,
        format="application/json",
        params={
            "typename": f"urbisvector:{layer}",
            "version": "2.0.0",
            "crs": "EPSG:31370",
            "require_extent": True,
            "sortBy": "INSPIRE_ID A",
            "pagination": {
                "mode": "offset",
                "cursor_policy": "returned_count",
                "offset_param": "startIndex",
                "limit_param": "count",
                "page_size": 2000,
                "max_pages": 1000,
                "max_features": 1_000_000,
                "id_field": "INSPIRE_ID",
                "count_key": "numberMatched",
                "count_query": {"count": 1, "startIndex": 0},
            },
        },
    )


class UrbisSource(DeclarativeSource):
    """WFS extent in EPSG:31370; preserve TYPE/LVL without material or grade inference."""

    name = "urbis"
    domain = SourceDomain.BASE
    payload = Payload.VECTOR
    jurisdiction = "BE"

    def entries(self) -> list[SourceEntryRef]:
        return [
            SourceEntryRef(
                id=key,
                name=f"UrbIS {layer}",
                access=_access(layer),
                revision_token=None,
                domain=self.domain,
                payload=self.payload,
                jurisdiction=self.jurisdiction,
                metadata={
                    "provider": "Paradigm Brussels",
                    "source": "UrbIS",
                    "layer": layer,
                    "geometry_type": geometry,
                    "target_crs": "EPSG:31370",
                    "raw_fields_preserved": True,
                    "snapshot_revision": None,
                },
            )
            for key, (layer, geometry) in ENTRIES.items()
        ]

    def access_for(self, entry_id: str) -> AccessSpec:
        self._entry(entry_id)
        return _access(ENTRIES[entry_id][0])

    def schema(self, entry_id: str) -> dict[str, str]:
        self._entry(entry_id)
        return {
            "INSPIRE_ID": "str",
            "TYPE": "str",
            "LVL": "int",
            "geometry": "geometry[EPSG:31370]",
        }

    def revision(self, entry_id: str) -> str | None:
        self._entry(entry_id)
        return None
