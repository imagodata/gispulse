"""OpenStreetMap (Geofabrik) DataSource.

This plugin declares Geofabrik thematic shapefile extracts. A single vector
layer of the ``.shp.zip`` archive is read in place via the DOWNLOAD protocol +
``archive_member`` (GDAL ``/vsizip/``) — no full extraction. GISPulse core owns
HTTP download / VSI streaming; harmonisation/scoring stays OUTSIDE the source
plugin (frontière ETL/métier).

⚠️ The Geofabrik *free* shapefiles carry road TYPE (``fclass``) but **not** the
OSM ``surface=*`` tag — geometry + coarse classification, not exact pavement.
Generic by design: a new region or layer is a new entry, no code change.
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

_TARGET_CRS = "EPSG:31370"
_SOURCE_CRS = "EPSG:4326"  # OSM/Geofabrik native

GEOFABRIK_BE_SHP = "https://download.geofabrik.de/europe/belgium-latest-free.shp.zip"
GEOFABRIK_BE_PBF = "https://download.geofabrik.de/europe/belgium-latest.osm.pbf"

# Geofabrik *free* shapefile roads layer (gis_osm_roads_free_1) attributes.
# Confirmé sur la doc/exports Geofabrik : osm_id, code, fclass, name, ref, oneway,
# maxspeed, layer, bridge, tunnel. PAS de `surface` (uniquement dans le .pbf complet
# ou les shapefiles « tous attributs » payants) — `fclass`/`code` (type de voie) sont
# le seul signal de revêtement disponible dans l'extrait gratuit.
_ROADS_SCHEMA = {
    "source": "str",
    "osm_id": "str",
    "code": "int",
    "fclass": "str",
    "name": "str",
    "ref": "str",
    "oneway": "str",
    "maxspeed": "int",
    "layer": "int",
    "bridge": "str",
    "tunnel": "str",
    "geometry": f"geometry[{_TARGET_CRS}]",
}

_ROADS_FIELD_MAP = {
    "source": "literal:OSM",
    "osm_id": "osm_id",
    "code": "code",
    "fclass": "fclass",
    "name": "name",
    "ref": "ref",
    "oneway": "oneway",
    "maxspeed": "maxspeed",
    "layer": "layer",
    "bridge": "bridge",
    "tunnel": "tunnel",
    "geometry": "geometry",
}

_ENTRIES: dict[str, dict[str, Any]] = {
    "osm-roads-be": {
        "label": "OSM roads — Belgium (Geofabrik)",
        "provider": "Geofabrik / OpenStreetMap",
        "platform": "Geofabrik download server (thematic shapefile)",
        "region": "BE",
        "endpoint": GEOFABRIK_BE_SHP,
        "access": AccessSpec(
            protocol=AccessProtocol.DOWNLOAD,
            endpoint=GEOFABRIK_BE_SHP,
            params={
                "archive_member": "gis_osm_roads_free_1.shp",
                "archive_format": "zip",
                "source_crs": _SOURCE_CRS,
                "target_crs": _TARGET_CRS,
            },
            format="application/zip",
        ),
        "schema": _ROADS_SCHEMA,
        "canonical_field_map": _ROADS_FIELD_MAP,
        "source_crs": _SOURCE_CRS,
    },
    "osm-pbf-be": {
        "label": "OSM full extract — Belgium (Geofabrik PBF)",
        "provider": "Geofabrik / OpenStreetMap",
        "platform": "Geofabrik download server (PBF)",
        "region": "BE",
        "endpoint": GEOFABRIK_BE_PBF,
        "access": AccessSpec(
            protocol=AccessProtocol.DOWNLOAD,
            endpoint=GEOFABRIK_BE_PBF,
            params={
                "source_crs": _SOURCE_CRS,
                "target_crs": _TARGET_CRS,
                "reader": "duckdb.ST_ReadOSM",
            },
            format="application/x-osm-pbf",
        ),
        "schema": {
            "id": "int",
            "highway": "str",
            "surface": "str",
            "geometry": f"geometry[{_TARGET_CRS}]",
        },
        "canonical_field_map": {
            "id": "id",
            "highway": "tags.highway",
            "surface": "tags.surface",
            "geometry": "geom",
        },
        "source_crs": _SOURCE_CRS,
    },
}

_COMMON_METADATA = {
    "license": "ODbL (OpenStreetMap contributors)",
    "jurisdiction": "BE",
    "target_crs": _TARGET_CRS,
}


def _copy_params(params: dict[str, Any]) -> dict[str, Any]:
    return dict(params)


class OsmSource(DeclarativeSource):
    """OpenStreetMap Geofabrik thematic extracts (vector)."""

    name = "osm"
    domain = SourceDomain.BASE
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
                "schema_columns": tuple(spec["schema"]),
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
        return dict(_ENTRIES[entry_id]["schema"])

    def revision(self, entry_id: str) -> str | None:
        self._entry(entry_id)  # validates the id
        return None
