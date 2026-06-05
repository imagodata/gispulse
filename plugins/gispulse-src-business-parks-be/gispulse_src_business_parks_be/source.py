"""Belgian business parks DataSource.

This plugin only declares the three upstream inventories used for Belgian
business parks. GISPulse core fetchers own WFS, HTTP download and
materialization; harmonisation/scoring stays outside the source plugin.
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

VLAIO_WFS_ENDPOINT = "https://geo.api.vlaanderen.be/Bedrijventerreinen/wfs"
SPW_PRE_GEOJSON_ENDPOINT = (
    "https://www.odwb.be/api/explore/v2.1/catalog/datasets/"
    "perimetres-de-reconnaissance-economique-wallonie/exports/geojson"
    "?lang=fr&timezone=Europe%2FBrussels"
)
WALSPACE_GEOJSON_ENDPOINT = (
    "https://www.odwb.be/api/explore/v2.1/catalog/datasets/"
    "walspace/exports/geojson?lang=fr&timezone=Europe%2FBrussels"
)

_TARGET_CRS = "EPSG:31370"

_SCHEMA = {
    "source": "str",
    "region": "str",
    "park_id": "str",
    "name": "str",
    "municipality": "str",
    "use": "str",
    "area_ha": "float",
    "geometry": f"geometry[{_TARGET_CRS}]",
}

_COMMON_METADATA = {
    "license": "open data",
    "jurisdiction": "BE",
    "target_crs": _TARGET_CRS,
    "schema_columns": tuple(_SCHEMA),
}

_ENTRIES: dict[str, dict[str, Any]] = {
    "vlaio-bedrijventerreinen": {
        "label": "VLAIO GIS-Bedrijventerreinen",
        "provider": "VLAIO",
        "platform": "Digitaal Vlaanderen WFS Bedrijventerreinen",
        "region": "VL",
        "endpoint": VLAIO_WFS_ENDPOINT,
        "access": AccessSpec(
            protocol=AccessProtocol.WFS,
            endpoint=VLAIO_WFS_ENDPOINT,
            params={
                "typename": "Bedrijventerreinen:Bedrter",
                "version": "2.0.0",
                "crs": _TARGET_CRS,
            },
            format="application/json",
        ),
        "canonical_field_map": {
            "source": "literal:VLAIO",
            "region": "literal:VL",
            "park_id": "IDBEDR",
            "name": "NAAM",
            "municipality": "GEMEENTE",
            "use": "TYPE",
            "area_ha": "geometry.area / 10000",
            "geometry": "geometry",
        },
        "source_crs": _TARGET_CRS,
    },
    "spw-economic-perimeters": {
        "label": "SPW perimetres de reconnaissance economique",
        "provider": "SPW",
        "platform": "Open Data Wallonie-Bruxelles",
        "region": "WAL",
        "endpoint": SPW_PRE_GEOJSON_ENDPOINT,
        "access": AccessSpec(
            protocol=AccessProtocol.DOWNLOAD,
            endpoint=SPW_PRE_GEOJSON_ENDPOINT,
            params={
                "source_crs": "EPSG:4326",
                "target_crs": _TARGET_CRS,
            },
            format="application/geo+json",
        ),
        "canonical_field_map": {
            "source": "literal:SPW",
            "region": "literal:WAL",
            "park_id": "codecarto",
            "name": "libelle",
            "municipality": "commune",
            "use": "affectatio",
            "area_ha": "superfha",
            "geometry": "geometry",
        },
        "source_crs": "EPSG:4326",
    },
    "walspace": {
        "label": "WalSpace business park points",
        "provider": "WalSpace",
        "platform": "Open Data Wallonie-Bruxelles",
        "region": "WAL",
        "endpoint": WALSPACE_GEOJSON_ENDPOINT,
        "access": AccessSpec(
            protocol=AccessProtocol.DOWNLOAD,
            endpoint=WALSPACE_GEOJSON_ENDPOINT,
            params={
                "source_crs": "EPSG:4326",
                "target_crs": _TARGET_CRS,
            },
            format="application/geo+json",
        ),
        "canonical_field_map": {
            "source": "literal:WalSpace",
            "region": "literal:WAL",
            "park_id": "pae_id",
            "name": "pae_denom",
            "municipality": "pae_commune",
            "use": "pae_theme",
            "area_ha": "pae_superf_utile",
            "geometry": "pae_geo",
        },
        "source_crs": "EPSG:4326",
    },
}


def _copy_params(params: dict[str, Any]) -> dict[str, Any]:
    return dict(params)


class BusinessParksBeSource(DeclarativeSource):
    """Belgian business park upstream inventories."""

    name = "business_parks_be"
    domain = SourceDomain.FONCIER
    payload = Payload.VECTOR
    jurisdiction = "BE"

    def entries(self) -> list[SourceEntryRef]:
        return [self._entry_ref(entry_id) for entry_id in _ENTRIES]

    def _entry_ref(self, entry_id: str) -> SourceEntryRef:
        spec = _ENTRIES[entry_id]
        access = spec["access"]
        return SourceEntryRef(
            id=entry_id,
            name=spec["label"],
            access=replace(access, params=_copy_params(access.params)),
            revision_token=None,
            domain=self.domain,
            payload=self.payload,
            jurisdiction=self.jurisdiction,
            metadata={
                **_COMMON_METADATA,
                "provider": spec["provider"],
                "platform": spec["platform"],
                "region": spec["region"],
                "source_crs": spec["source_crs"],
                "canonical_field_map": dict(spec["canonical_field_map"]),
                "endpoint": spec["endpoint"],
            },
        )

    def access_for(self, entry_id: str) -> AccessSpec:
        """Return the declared AccessSpec without network access."""
        entry = self._entry(entry_id)
        return replace(entry.access, params=_copy_params(entry.access.params))

    def schema(self, entry_id: str) -> dict[str, str]:
        self._entry(entry_id)  # validates the id
        return dict(_SCHEMA)

    def revision(self, entry_id: str) -> str | None:
        self._entry(entry_id)  # validates the id
        return None
