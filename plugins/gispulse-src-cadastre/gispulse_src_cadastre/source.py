"""French cadastre DataSource — parcels, communes and buildings.

A :class:`~gispulse.plugins.api.DeclarativeSource`: the plugin only
*declares* the available entries and their :class:`AccessSpec`; the
actual WFS request is delegated to the registered protocol adapter, so
this package ships zero network code.

Data: IGN Géoplateforme WFS, ``CADASTRALPARCELS.PARCELLAIRE_EXPRESS``.
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

# IGN Géoplateforme — public WFS endpoint (no API key required).
_GEOPLATEFORME_WFS = "https://data.geopf.fr/wfs/ows"
_LAYER_PREFIX = "CADASTRALPARCELS.PARCELLAIRE_EXPRESS"

# Millésime token surfaced by revision() — drives the source watcher
# (issue #187). Parcellaire Express is refreshed twice a year.
_MILLESIME = "2026-01"


class CadastreSource(DeclarativeSource):
    """French cadastre (Parcellaire Express) exposed as a GISPulse source."""

    name = "cadastre"
    domain = SourceDomain.FONCIER
    payload = Payload.VECTOR
    jurisdiction = "FR"

    def entries(self) -> list[SourceEntryRef]:
        return [
            self._entry_ref("parcelles", "Parcelles cadastrales", "parcelle"),
            self._entry_ref("communes", "Communes cadastrales", "commune"),
            self._entry_ref("batiments", "Bâtiments cadastraux", "batiment"),
        ]

    @staticmethod
    def _entry_ref(entry_id: str, label: str, layer: str) -> SourceEntryRef:
        return SourceEntryRef(
            id=entry_id,
            name=label,
            access=AccessSpec(
                protocol=AccessProtocol.WFS,
                endpoint=_GEOPLATEFORME_WFS,
                params={"typename": f"{_LAYER_PREFIX}:{layer}"},
                format="application/json",
            ),
            revision_token=_MILLESIME,
            metadata={"provider": "IGN", "dataset": "Parcellaire Express"},
        )

    def schema(self, entry_id: str) -> dict:
        """Normalised attribute schema of a cadastre layer."""
        self._entry(entry_id)  # validates the id
        common = {"idu": "str", "geometry": "geometry"}
        if entry_id == "parcelles":
            return {**common, "commune": "str", "section": "str", "numero": "str",
                    "contenance": "int"}
        if entry_id == "communes":
            return {**common, "nom": "str", "code_insee": "str"}
        return {**common, "nature": "str"}  # batiments
