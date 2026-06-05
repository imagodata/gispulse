"""French protected natural areas via IGN API Carto Nature.

This source is intentionally declarative: each entry points at an API
Carto Nature REST endpoint, and the core ``RestGeoJsonFetcher`` injects
the runtime extent as a GeoJSON polygon in the ``geom`` query parameter.
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

_API_CARTO_NATURE = "https://apicarto.ign.fr/api/nature"
_METADATA = {
    "provider": "IGN / INPN",
    "platform": "API Carto Nature",
    "license": "Licence Ouverte 2.0",
}

# Entry-id -> (display label, API Carto Nature path).
_ENTRIES: dict[str, tuple[str, str]] = {
    "natura-habitat": (
        "Natura 2000 directive Habitat",
        "/natura-habitat",
    ),
    "natura-oiseaux": (
        "Natura 2000 directive Oiseaux",
        "/natura-oiseaux",
    ),
    "znieff1": (
        "ZNIEFF type 1",
        "/znieff1",
    ),
    "znieff2": (
        "ZNIEFF type 2",
        "/znieff2",
    ),
}

_RAW_SCHEMA = {
    "gml_id": "str",
    "id": "str",
    "nom": "str",
    "sitename": "str",
    "sitecode": "str",
    "geometry": "geometry",
}


class NatureSource(DeclarativeSource):
    """Protected natural areas from IGN / INPN API Carto Nature."""

    name = "nature"
    domain = SourceDomain.ENVIRONNEMENT
    payload = Payload.VECTOR
    jurisdiction = "FR"

    def entries(self) -> list[SourceEntryRef]:
        return [
            SourceEntryRef(
                id=entry_id,
                name=label,
                access=AccessSpec(
                    protocol=AccessProtocol.REST_API,
                    endpoint=f"{_API_CARTO_NATURE}{path}",
                    params={"geom_param": "geom"},
                    format="application/json",
                ),
                revision_token=None,
                domain=self.domain,
                payload=self.payload,
                jurisdiction=self.jurisdiction,
                metadata=dict(_METADATA),
            )
            for entry_id, (label, path) in _ENTRIES.items()
        ]

    def schema(self, entry_id: str) -> dict:
        """Expose raw upstream fields without domain-level normalisation."""
        self._entry(entry_id)
        return dict(_RAW_SCHEMA)

    def revision(self, entry_id: str) -> str | None:
        """No cheap freshness probe exists for geometry-filtered runtime calls."""
        self._entry(entry_id)
        return None
