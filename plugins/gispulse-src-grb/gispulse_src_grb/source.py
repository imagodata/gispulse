"""GRB Flanders topographic source, with raw functional classifications.

WBN describes the whole road corridor; WGA describes ancillary road structures,
not sidewalk polygons. WGO road subdivision boundaries require upstream
geometric reconstruction before use as areas. WFS dispatch belongs to core;
client material mapping and all prices stay outside this plugin.

Output CRS is EPSG:31370. TYPE/LBLTYPE describe function, not paving material.
PICC Wallonia and UrbIS Brussels are separate regional source plugins.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from gispulse.plugins.api import (
    AccessProtocol,
    AccessSpec,
    DeclarativeSource,
    Payload,
    SourceDomain,
    SourceEntryRef,
)

GRB_WFS_ENDPOINT = "https://geo.api.vlaanderen.be/GRB/wfs"
_TARGET_CRS = "EPSG:31370"  # GRB WFS native — no reprojection

_SCHEMA = {
    "source": "str",
    "oidn": "int",
    "uidn": "int",
    "type": "int",
    "label_type": "str",
    "geometry": f"geometry[{_TARGET_CRS}]",
}

_FIELD_MAP = {
    "source": "literal:GRB",
    "oidn": "OIDN",
    "uidn": "UIDN",
    "type": "TYPE",
    "label_type": "LBLTYPE",
    "geometry": "geometry",
}


def _wfs_access(typename: str) -> AccessSpec:
    try:
        from gispulse.adapters.ogc.counted_wfs import _get_wfs_hits  # noqa: F401
    except ImportError as exc:
        raise RuntimeError(
            "GRB_COUNTED_WFS_REQUIRED: install core with XML hits and native INTERSECTS support"
        ) from exc
    return AccessSpec(
        protocol=AccessProtocol.WFS,
        endpoint=GRB_WFS_ENDPOINT,
        params={
            "typename": typename,
            "version": "2.0.0",
            "crs": _TARGET_CRS,
            "native_crs": _TARGET_CRS,
            "require_extent": True,
            "bbox_filter": "intersects",
            "geometry_field": "SHAPE",
            "sortBy": "OIDN A",
            "count_format": "wfs_hits_xml",
            "pagination": {
                "mode": "offset",
                "offset_param": "startIndex",
                "limit_param": "count",
                "page_size": 2000,
                "max_pages": 1000,
                "max_features": 1_000_000,
                "id_field": "OIDN",
                "count_key": "count",
                "count_query": {"resultType": "hits"},
            },
        },
        format="application/json",
    )


_ENTRIES: dict[str, dict[str, Any]] = {
    "grb-wegbaan-vl": {
        "label": "GRB wegbaan (corridor routier) — Flandre",
        "typename": "GRB:WBN",
        "kind": "road_corridor",
    },
    "grb-wegopdeling-vl": {
        "label": "GRB limites fonctionnelles — Flandre",
        "typename": "GRB:WGO",
        "kind": "functional_boundaries",
        "raw_fields": ["TYPE", "LBLTYPE"],
    },
    "grb-wegsegment-vl": {
        "label": "GRB axes de voirie — Flandre",
        "typename": "GRB:Wegsegment",
        "kind": "road_axes",
        "raw_fields": [
            "WS_OIDN",
            "WS_UIDN",
            "MORF",
            "LBLMORF",
            "VERH",
            "LBLVERH",
            "STATUS",
            "LBLSTATUS",
            "METHODE",
            "LBLMETHODE",
        ],
    },
    "grb-wegknoop-vl": {
        "label": "GRB nœuds de voirie — Flandre",
        "typename": "GRB:Wegknoop",
        "kind": "road_nodes",
        "raw_fields": ["WK_OIDN", "WK_UIDN", "TYPE", "LBLTYPE"],
    },
    "grb-kunstwerk-vl": {
        "label": "GRB ouvrages — Flandre",
        "typename": "GRB:KNW",
        "kind": "structures",
        "raw_fields": ["TYPE", "LBLTYPE", "VORM", "LBLVORM"],
    },
    "grb-aanhorigheid-vl": {
        "label": "GRB wegaanhorigheid (constructions annexes) — Flandre",
        "typename": "GRB:WGA",
        "kind": "appurtenance",
    },
}


def _raw_schema(spec: dict[str, Any]) -> dict[str, str]:
    columns = {"OIDN": "int", "UIDN": "int", "VERSIE": "int", "VERSDATUM": "date"}
    for field in spec["raw_fields"]:
        columns[field] = "str" if field.startswith("LBL") or field.endswith("UIDN") else "int"
    columns["geometry"] = f"geometry[{_TARGET_CRS}]"
    return columns


_COMMON_METADATA = {
    "license": "Gratis Open Data Licentie Vlaanderen",
    "provider": "Digitaal Vlaanderen",
    "platform": "GRB WFS (geo.api.vlaanderen.be)",
    "jurisdiction": "BE",
    "region": "VL",
    "target_crs": _TARGET_CRS,
    "source_crs": _TARGET_CRS,
    "schema_columns": tuple(_SCHEMA),
    "canonical_field_map": dict(_FIELD_MAP),
}


def _copy_params(params: dict[str, Any]) -> dict[str, Any]:
    return dict(params)


class GrbSource(DeclarativeSource):
    """GRB Flanders road corridors, internal boundaries, axes and structures."""

    name = "grb"
    domain = SourceDomain.BASE
    payload = Payload.VECTOR
    jurisdiction = "BE"

    def entries(self) -> list[SourceEntryRef]:
        return [self._entry_ref(entry_id) for entry_id in _ENTRIES]

    def _entry_ref(self, entry_id: str) -> SourceEntryRef:
        spec = _ENTRIES[entry_id]
        access = _wfs_access(spec["typename"])
        metadata = dict(_COMMON_METADATA)
        if "raw_fields" in spec:
            metadata.pop("canonical_field_map")
            metadata["schema_columns"] = tuple(_raw_schema(spec))
        return SourceEntryRef(
            id=entry_id,
            name=spec["label"],
            access=access,
            revision_token=None,
            domain=self.domain,
            payload=self.payload,
            jurisdiction=self.jurisdiction,
            metadata={
                **metadata,
                "raw_fields_preserved": True,
                "extent_crs": _TARGET_CRS,
                "counted_pagination": True,
                "typename": spec["typename"],
                "kind": spec["kind"],
                "endpoint": GRB_WFS_ENDPOINT,
            },
        )

    def access_for(self, entry_id: str) -> AccessSpec:
        """Return the declared AccessSpec without network access."""
        self._entry(entry_id)  # valide l'id (lève l'erreur canonique si inconnu)
        access = _wfs_access(_ENTRIES[entry_id]["typename"])
        return replace(access, params=_copy_params(access.params))

    def schema(self, entry_id: str) -> dict[str, str]:
        self._entry(entry_id)  # validates the id
        spec = _ENTRIES[entry_id]
        return _raw_schema(spec) if "raw_fields" in spec else dict(_SCHEMA)

    def revision(self, entry_id: str) -> str | None:
        self._entry(entry_id)  # validates the id
        return None
