"""GRB Flanders (Grootschalig Referentiebestand) DataSource.

GRB is the Flemish large-scale topographic reference. This plugin declares its
public WFS layers used for civil-works surface classification: ``WBN`` (chaussée
/ wegbaan) and ``WGA`` (accotements / wegaanhorigheid). GISPulse core owns the
WFS dispatch; harmonisation (GRB type → gc_surface) stays OUTSIDE this plugin.

WFS native CRS is EPSG:31370 (Lambert 72) — no reprojection needed. Polygons
carry a functional ``TYPE``/``LBLTYPE`` (carriageway zone, sidewalk…), the basis
for the BOM cost tiers (sous revêtement / trottoir). Flanders only (PICC Wallonie
has no WFS, UrbIS Brussels is a separate datastore).
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
    return AccessSpec(
        protocol=AccessProtocol.WFS,
        endpoint=GRB_WFS_ENDPOINT,
        params={
            "typename": typename,
            "version": "2.0.0",
            "crs": _TARGET_CRS,
        },
        format="application/json",
    )


_ENTRIES: dict[str, dict[str, Any]] = {
    "grb-wegbaan-vl": {
        "label": "GRB wegbaan (chaussée) — Flandre",
        "typename": "GRB:WBN",
        "kind": "carriageway",
    },
    "grb-aanhorigheid-vl": {
        "label": "GRB wegaanhorigheid (accotements) — Flandre",
        "typename": "GRB:WGA",
        "kind": "appurtenance",
    },
}

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
    """GRB Flanders WFS layers (carriageway + appurtenances)."""

    name = "grb"
    domain = SourceDomain.BASE
    payload = Payload.VECTOR
    jurisdiction = "BE"

    def entries(self) -> list[SourceEntryRef]:
        return [self._entry_ref(entry_id) for entry_id in _ENTRIES]

    def _entry_ref(self, entry_id: str) -> SourceEntryRef:
        spec = _ENTRIES[entry_id]
        access = _wfs_access(spec["typename"])
        return SourceEntryRef(
            id=entry_id,
            name=spec["label"],
            access=access,
            revision_token=None,
            domain=self.domain,
            payload=self.payload,
            jurisdiction=self.jurisdiction,
            metadata={
                **_COMMON_METADATA,
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
        return dict(_SCHEMA)

    def revision(self, entry_id: str) -> str | None:
        self._entry(entry_id)  # validates the id
        return None
