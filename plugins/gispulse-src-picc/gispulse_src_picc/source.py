"""Declarative PICC vector sources; transport and pagination remain in core adapters.

Raw SPW fields are retained, including NIVEAU. Its interpretation, geometric
matching of axes and footprints, and all client costing are separate concerns.
"""

from __future__ import annotations


from gispulse.plugins.api import (
    AccessProtocol,
    AccessSpec,
    DeclarativeSource,
    Payload,
    SourceDomain,
    SourceEntryRef,
)

PICC_ENDPOINT = (
    "https://geoservices.wallonie.be/arcgis/rest/services/TOPOGRAPHIE/PICC_VDIFF/MapServer"
)
_ENTRIES = {
    "picc-road-surfaces-wa": (24, "PICC Voirie — Surface", "Polygon"),
    "picc-road-axes-wa": (21, "PICC Voirie — Axe", "LineString"),
}
_FIELDS = {
    "OBJECTID": "int",
    "GEOREF_ID": "str",
    "NATUR_CODE": "str",
    "NATUR_DESC": "str",
    "TYPE_CODE": "str",
    "TYPE_DESC": "str",
    "PRECIS_XY": "str",
    "PRECIS_Z": "str",
    "TECH_LEVE": "str",
    "DATE_CREAT": "datetime",
    "DATE_MODIF": "datetime",
    "DATE_TRANS": "datetime",
    "SOURCE": "str",
    "NIVEAU": "int",
    "geometry": "geometry[EPSG:4326]",
}


def _access(layer: int) -> AccessSpec:
    try:
        from gispulse.adapters.rest.offset_pages import OffsetPagination
    except ImportError as exc:
        raise RuntimeError(
            "PICC_PAGINATION_REQUIRED: install GISPulse with counted-offset REST support"
        ) from exc
    access = AccessSpec(
        protocol=AccessProtocol.REST_API,
        endpoint=f"{PICC_ENDPOINT}/{layer}/query",
        format="application/geo+json",
        params={
            "where": "1=1",
            "outFields": "*",
            "outSR": "4326",
            "returnZ": "true",
            "returnGeometry": "true",
            "f": "geojson",
            "orderByFields": "OBJECTID ASC",
            "inSR": "4326",
            "geometryType": "esriGeometryEnvelope",
            "spatialRel": "esriSpatialRelIntersects",
            "bbox_param": "geometry",
            "require_extent": True,
            "pagination": {
                "mode": "offset",
                "cursor_policy": "arcgis_page_window",
                "offset_param": "resultOffset",
                "limit_param": "resultRecordCount",
                "page_size": 2000,
                "max_pages": 1000,
                "max_features": 1_000_000,
                "id_field": "OBJECTID",
                "count_key": "count",
                "count_query": {"f": "json", "returnCountOnly": "true", "returnGeometry": "false"},
            },
        },
    )

    OffsetPagination.from_params(access.params["pagination"])
    return access


class PiccSource(DeclarativeSource):
    """Wallonia road footprints and axes, bounded by an explicit WGS84 extent."""

    name = "picc"
    domain = SourceDomain.BASE
    payload = Payload.VECTOR
    jurisdiction = "BE"

    def entries(self) -> list[SourceEntryRef]:
        return [self._entry_ref(key) for key in _ENTRIES]

    def _entry_ref(self, entry_id: str) -> SourceEntryRef:
        layer, label, geometry_type = _ENTRIES[entry_id]
        return SourceEntryRef(
            id=entry_id,
            name=label,
            access=_access(layer),
            revision_token=None,
            domain=self.domain,
            payload=self.payload,
            jurisdiction=self.jurisdiction,
            metadata={
                "provider": "Service public de Wallonie",
                "source": "PICC",
                "endpoint": f"{PICC_ENDPOINT}/{layer}",
                "layer_id": layer,
                "geometry_type": geometry_type,
                "transport_crs": "EPSG:4326",
                "native_crs": "EPSG:3812",
                "normalization_target_crs": "EPSG:31370",
                "raw_fields_preserved": True,
                "snapshot_revision": None,
                "requires_counted_offset_pagination": True,
            },
        )

    def access_for(self, entry_id: str) -> AccessSpec:
        """Build fresh nested params on every request (caller overrides cannot leak)."""
        self._entry(entry_id)
        return _access(_ENTRIES[entry_id][0])

    def schema(self, entry_id: str) -> dict[str, str]:
        """Published raw fields, without assigning a material or a crossing decision."""
        self._entry(entry_id)
        return dict(_FIELDS)

    def revision(self, entry_id: str) -> str | None:
        self._entry(entry_id)
        return None
