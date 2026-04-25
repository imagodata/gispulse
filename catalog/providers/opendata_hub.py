"""Hub'Eau and other French environmental API providers."""

from __future__ import annotations

from catalog.models import CatalogDomain, FluxEntry, FluxProtocol
from catalog.providers.base import CatalogProvider
from catalog.registry import register_provider

_HUBEAU_FLUX: list[dict] = [
    {
        "id": "hubeau-stations-hydro",
        "name": "Stations hydrométriques",
        "description": "Stations de mesure du réseau hydrométrique français (Hub'Eau)",
        "service_url": "https://hubeau.eaufrance.fr/api/v1/hydrometrie/referentiel/stations",
        "protocol": "ogc-features",
        "layer_name": "stations",
        "tags": ["hydro", "stations", "eau", "france", "environnement"],
    },
    {
        "id": "hubeau-qualite-nappes",
        "name": "Qualité des nappes",
        "description": "Stations de surveillance qualité des eaux souterraines (Hub'Eau)",
        "service_url": "https://hubeau.eaufrance.fr/api/v1/qualite_nappes/stations",
        "protocol": "ogc-features",
        "layer_name": "stations",
        "tags": ["nappes", "eau", "qualité", "france", "environnement"],
    },
    {
        "id": "hubeau-prelevements",
        "name": "Prélèvements en eau",
        "description": "Points de prélèvement d'eau — volumes et usages (Hub'Eau)",
        "service_url": "https://hubeau.eaufrance.fr/api/v1/prelevements/referentiel/ouvrages",
        "protocol": "ogc-features",
        "layer_name": "ouvrages",
        "tags": ["prélèvements", "eau", "ressource", "france"],
    },
]

_EU_FLUX: list[dict] = [
    {
        "id": "eu-inspire-cadastre",
        "name": "INSPIRE Cadastral (EU)",
        "description": "Service WFS INSPIRE parcelles cadastrales — couverture européenne",
        "service_url": "https://inspire.ec.europa.eu/download/CP/CadastralParcel",
        "protocol": "wfs",
        "layer_name": "CP:CadastralParcel",
        "tags": ["cadastre", "inspire", "eu", "vector"],
    },
    {
        "id": "eu-copernicus-clc",
        "name": "CORINE Land Cover (WFS)",
        "description": "Occupation du sol européenne CORINE — Copernicus Land Monitoring",
        "service_url": "https://image.discomap.eea.europa.eu/arcgis/services/Corine/CLC2018_WM/MapServer/WFSServer",
        "protocol": "wfs",
        "layer_name": "CLC2018_WM",
        "tags": ["corine", "occupation-du-sol", "eu", "vector", "environnement"],
    },
]

_ALL_FLUX = _HUBEAU_FLUX + _EU_FLUX


class HubEauFluxProvider(CatalogProvider):
    name = "hubeau"
    domain = CatalogDomain.FLUX
    description = "Hub'Eau — APIs données eau France + services INSPIRE EU"

    def __init__(self) -> None:
        self._entries: dict[str, FluxEntry] = {}
        for item in _ALL_FLUX:
            entry_id = f"flux:hubeau:{item['id']}"
            self._entries[entry_id] = FluxEntry(
                id=entry_id,
                domain=CatalogDomain.FLUX,
                provider="hubeau",
                name=item["name"],
                description=item.get("description", ""),
                tags=item.get("tags", []),
                service_url=item["service_url"],
                protocol=FluxProtocol(item["protocol"]),
                layer_name=item.get("layer_name", ""),
                attribution="© Hub'Eau / Eau France / Copernicus",
                default_crs="EPSG:4326",
            )

    def list_entries(self, search=None, tags=None, limit=50, offset=0):
        entries = list(self._entries.values())
        if search:
            q = search.lower()
            entries = [
                e
                for e in entries
                if q in e.name.lower()
                or q in e.description.lower()
                or any(q in t for t in e.tags)
            ]
        if tags:
            entries = [e for e in entries if any(t in e.tags for t in tags)]
        return entries[offset : offset + limit]

    def get_entry(self, entry_id):
        return self._entries.get(entry_id)


register_provider(HubEauFluxProvider())
