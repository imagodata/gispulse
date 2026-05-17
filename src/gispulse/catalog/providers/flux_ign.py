"""IGN Géoplateforme flux catalog — WMS/WMTS/WFS services."""

from __future__ import annotations

from gispulse.catalog.models import CatalogDomain, FluxEntry, FluxProtocol
from gispulse.catalog.providers.base import CatalogProvider
from gispulse.catalog.registry import register_provider

_BASE_WMTS = "https://data.geopf.fr/wmts"
_BASE_WMS = "https://data.geopf.fr/wms-r"
_BASE_WFS = "https://data.geopf.fr/wfs"
_BASE_OGC_FEATURES = "https://data.geopf.fr/wfs/ows"

_IGN_FLUX: list[dict] = [
    {
        "id": "ign-ortho-wmts",
        "name": "Orthophotos HR",
        "description": "Photographies aériennes haute résolution — France entière",
        "service_url": f"{_BASE_WMTS}?SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0&LAYER=ORTHOIMAGERY.ORTHOPHOTOS&STYLE=normal&FORMAT=image/jpeg&TILEMATRIXSET=PM&TILEMATRIX={{z}}&TILEROW={{y}}&TILECOL={{x}}",
        "protocol": "wmts",
        "layer_name": "ORTHOIMAGERY.ORTHOPHOTOS",
        "tags": ["imagery", "ortho", "france"],
    },
    {
        "id": "ign-plan-wmts",
        "name": "Plan IGN v2",
        "description": "Carte topographique vectorielle IGN",
        "service_url": f"{_BASE_WMTS}?SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0&LAYER=GEOGRAPHICALGRIDSYSTEMS.PLANIGNV2&STYLE=normal&FORMAT=image/png&TILEMATRIXSET=PM&TILEMATRIX={{z}}&TILEROW={{y}}&TILECOL={{x}}",
        "protocol": "wmts",
        "layer_name": "GEOGRAPHICALGRIDSYSTEMS.PLANIGNV2",
        "tags": ["topo", "plan", "france"],
    },
    {
        "id": "ign-cadastre-wmts",
        "name": "Cadastre (parcellaire)",
        "description": "Parcelles cadastrales PCI vecteur",
        "service_url": f"{_BASE_WMTS}?SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0&LAYER=CADASTRALPARCELS.PARCELLAIRE_EXPRESS&STYLE=PCI vecteur&FORMAT=image/png&TILEMATRIXSET=PM&TILEMATRIX={{z}}&TILEROW={{y}}&TILECOL={{x}}",
        "protocol": "wmts",
        "layer_name": "CADASTRALPARCELS.PARCELLAIRE_EXPRESS",
        "tags": ["cadastre", "parcelle", "france"],
    },
    {
        "id": "ign-bdtopo-wfs",
        "name": "BD TOPO (WFS)",
        "description": "Base de données topographique — bâtiments, routes, hydro, etc.",
        "service_url": f"{_BASE_WFS}?SERVICE=WFS&VERSION=2.0.0",
        "protocol": "wfs",
        "layer_name": "BDTOPO_V3:batiment",
        "tags": ["bdtopo", "bâtiments", "france", "vector"],
    },
    {
        "id": "ign-admin-wfs",
        "name": "Admin Express (WFS)",
        "description": "Limites administratives — communes, départements, régions",
        "service_url": f"{_BASE_WFS}?SERVICE=WFS&VERSION=2.0.0",
        "protocol": "wfs",
        "layer_name": "ADMINEXPRESS-COG-CARTO.LATEST:commune",
        "tags": ["admin", "commune", "france", "vector"],
    },
    {
        "id": "ign-rpg-wfs",
        "name": "RPG (Registre Parcellaire Graphique)",
        "description": "Îlots et parcelles agricoles — PAC",
        "service_url": f"{_BASE_WFS}?SERVICE=WFS&VERSION=2.0.0",
        "protocol": "wfs",
        "layer_name": "RPG.LATEST:parcelles_graphiques",
        "tags": ["agriculture", "rpg", "france", "vector"],
    },
    {
        "id": "ign-gpu-zone-urba-wfs",
        "name": "GPU — zones d'urbanisme (WFS)",
        "description": "Géoportail de l'Urbanisme — zones d'urbanisme",
        "service_url": f"{_BASE_OGC_FEATURES}?SERVICE=WFS&VERSION=2.0.0",
        "protocol": "wfs",
        "layer_name": "wfs_du:zone_urba",
        "tags": ["gpu", "plu", "urbanisme", "zonage", "france", "vector"],
    },
    {
        "id": "ign-gpu-prescription-surf-wfs",
        "name": "GPU — prescriptions surfaciques (WFS)",
        "description": "Géoportail de l'Urbanisme — prescriptions surfaciques",
        "service_url": f"{_BASE_OGC_FEATURES}?SERVICE=WFS&VERSION=2.0.0",
        "protocol": "wfs",
        "layer_name": "wfs_du:prescription_surf",
        "tags": ["gpu", "plu", "urbanisme", "prescription", "france", "vector"],
    },
    {
        "id": "ign-gpu-doc-urba-wfs",
        "name": "GPU — documents d'urbanisme (WFS)",
        "description": "Géoportail de l'Urbanisme — documents d'urbanisme",
        "service_url": f"{_BASE_OGC_FEATURES}?SERVICE=WFS&VERSION=2.0.0",
        "protocol": "wfs",
        "layer_name": "wfs_du:doc_urba",
        "tags": ["gpu", "plu", "urbanisme", "document", "france", "vector"],
    },
    {
        "id": "ign-gpu-prescription-lin-wfs",
        "name": "GPU — prescriptions linéaires (WFS)",
        "description": "Géoportail de l'Urbanisme — prescriptions linéaires",
        "service_url": f"{_BASE_OGC_FEATURES}?SERVICE=WFS&VERSION=2.0.0",
        "protocol": "wfs",
        "layer_name": "wfs_du:prescription_lin",
        "tags": ["gpu", "plu", "urbanisme", "prescription", "lineaire", "france", "vector"],
    },
    {
        "id": "ign-gpu-prescription-pct-wfs",
        "name": "GPU — prescriptions ponctuelles (WFS)",
        "description": "Géoportail de l'Urbanisme — prescriptions ponctuelles",
        "service_url": f"{_BASE_OGC_FEATURES}?SERVICE=WFS&VERSION=2.0.0",
        "protocol": "wfs",
        "layer_name": "wfs_du:prescription_pct",
        "tags": ["gpu", "plu", "urbanisme", "prescription", "ponctuel", "france", "vector"],
    },
    {
        "id": "ign-gpu-info-surf-wfs",
        "name": "GPU — informations surfaciques (WFS)",
        "description": "Géoportail de l'Urbanisme — informations surfaciques",
        "service_url": f"{_BASE_OGC_FEATURES}?SERVICE=WFS&VERSION=2.0.0",
        "protocol": "wfs",
        "layer_name": "wfs_du:info_surf",
        "tags": ["gpu", "plu", "urbanisme", "information", "surfacique", "france", "vector"],
    },
    {
        "id": "ign-gpu-info-lin-wfs",
        "name": "GPU — informations linéaires (WFS)",
        "description": "Géoportail de l'Urbanisme — informations linéaires",
        "service_url": f"{_BASE_OGC_FEATURES}?SERVICE=WFS&VERSION=2.0.0",
        "protocol": "wfs",
        "layer_name": "wfs_du:info_lin",
        "tags": ["gpu", "plu", "urbanisme", "information", "lineaire", "france", "vector"],
    },
    {
        "id": "ign-gpu-info-pct-wfs",
        "name": "GPU — informations ponctuelles (WFS)",
        "description": "Géoportail de l'Urbanisme — informations ponctuelles",
        "service_url": f"{_BASE_OGC_FEATURES}?SERVICE=WFS&VERSION=2.0.0",
        "protocol": "wfs",
        "layer_name": "wfs_du:info_pct",
        "tags": ["gpu", "plu", "urbanisme", "information", "ponctuel", "france", "vector"],
    },
    {
        "id": "ign-gpu-secteur-cc-wfs",
        "name": "GPU — secteurs carte communale (WFS)",
        "description": "Géoportail de l'Urbanisme — secteurs de carte communale",
        "service_url": f"{_BASE_OGC_FEATURES}?SERVICE=WFS&VERSION=2.0.0",
        "protocol": "wfs",
        "layer_name": "wfs_du:secteur_cc",
        "tags": ["gpu", "carte-communale", "urbanisme", "secteur", "france", "vector"],
    },
    {
        "id": "ign-sup-servitude-wfs",
        "name": "SUP — servitudes (WFS)",
        "description": "Géoportail de l'Urbanisme — servitudes d'utilité publique",
        "service_url": f"{_BASE_OGC_FEATURES}?SERVICE=WFS&VERSION=2.0.0",
        "protocol": "wfs",
        "layer_name": "wfs_sup:servitude",
        "tags": ["sup", "servitude", "urbanisme", "france", "vector"],
    },
    {
        "id": "ign-sup-assiette-s-wfs",
        "name": "SUP — assiettes surfaciques (WFS)",
        "description": "Géoportail de l'Urbanisme — assiettes surfaciques de servitudes",
        "service_url": f"{_BASE_OGC_FEATURES}?SERVICE=WFS&VERSION=2.0.0",
        "protocol": "wfs",
        "layer_name": "wfs_sup:assiette_sup_s",
        "tags": ["sup", "servitude", "urbanisme", "assiette", "surfacique", "france", "vector"],
    },
    {
        "id": "ign-sup-assiette-l-wfs",
        "name": "SUP — assiettes linéaires (WFS)",
        "description": "Géoportail de l'Urbanisme — assiettes linéaires de servitudes",
        "service_url": f"{_BASE_OGC_FEATURES}?SERVICE=WFS&VERSION=2.0.0",
        "protocol": "wfs",
        "layer_name": "wfs_sup:assiette_sup_l",
        "tags": ["sup", "servitude", "urbanisme", "assiette", "lineaire", "france", "vector"],
    },
    {
        "id": "ign-sup-assiette-p-wfs",
        "name": "SUP — assiettes ponctuelles (WFS)",
        "description": "Géoportail de l'Urbanisme — assiettes ponctuelles de servitudes",
        "service_url": f"{_BASE_OGC_FEATURES}?SERVICE=WFS&VERSION=2.0.0",
        "protocol": "wfs",
        "layer_name": "wfs_sup:assiette_sup_p",
        "tags": ["sup", "servitude", "urbanisme", "assiette", "ponctuel", "france", "vector"],
    },
    {
        "id": "ign-sup-generateur-s-wfs",
        "name": "SUP — générateurs surfaciques (WFS)",
        "description": "Géoportail de l'Urbanisme — générateurs surfaciques de servitudes",
        "service_url": f"{_BASE_OGC_FEATURES}?SERVICE=WFS&VERSION=2.0.0",
        "protocol": "wfs",
        "layer_name": "wfs_sup:generateur_sup_s",
        "tags": ["sup", "servitude", "urbanisme", "generateur", "surfacique", "france", "vector"],
    },
    {
        "id": "ign-sup-generateur-l-wfs",
        "name": "SUP — générateurs linéaires (WFS)",
        "description": "Géoportail de l'Urbanisme — générateurs linéaires de servitudes",
        "service_url": f"{_BASE_OGC_FEATURES}?SERVICE=WFS&VERSION=2.0.0",
        "protocol": "wfs",
        "layer_name": "wfs_sup:generateur_sup_l",
        "tags": ["sup", "servitude", "urbanisme", "generateur", "lineaire", "france", "vector"],
    },
    {
        "id": "ign-sup-generateur-p-wfs",
        "name": "SUP — générateurs ponctuels (WFS)",
        "description": "Géoportail de l'Urbanisme — générateurs ponctuels de servitudes",
        "service_url": f"{_BASE_OGC_FEATURES}?SERVICE=WFS&VERSION=2.0.0",
        "protocol": "wfs",
        "layer_name": "wfs_sup:generateur_sup_p",
        "tags": ["sup", "servitude", "urbanisme", "generateur", "ponctuel", "france", "vector"],
    },
    {
        "id": "ign-sup-acte-wfs",
        "name": "SUP — actes (WFS)",
        "description": "Géoportail de l'Urbanisme — actes de servitudes d'utilité publique",
        "service_url": f"{_BASE_OGC_FEATURES}?SERVICE=WFS&VERSION=2.0.0",
        "protocol": "wfs",
        "layer_name": "wfs_sup:acte_sup",
        "tags": ["sup", "servitude", "urbanisme", "acte", "document", "france", "vector"],
    },
    {
        "id": "ign-altitude-wms",
        "name": "MNT (altitude WMS)",
        "description": "Modèle numérique de terrain — ombrage et altitude",
        "service_url": f"{_BASE_WMS}?SERVICE=WMS&VERSION=1.3.0",
        "protocol": "wms",
        "layer_name": "ELEVATION.ELEVATIONGRIDCOVERAGE.HIGHRES",
        "tags": ["altitude", "mnt", "raster", "france"],
    },
    # --- OGC API Features ---
    {
        "id": "ign-bdtopo-ogc",
        "name": "BD TOPO (OGC Features)",
        "description": "Base de données topographique via OGC API Features",
        "service_url": "https://data.geopf.fr/wfs/ows?service=WFS&version=2.0.0&request=GetCapabilities",
        "protocol": "ogc-features",
        "layer_name": "BDTOPO_V3:batiment",
        "tags": ["bdtopo", "ogc", "features", "vector", "france"],
    },
    {
        "id": "ign-admin-ogc",
        "name": "Admin Express (OGC Features)",
        "description": "Communes, départements, régions via OGC API Features",
        "service_url": "https://data.geopf.fr/wfs/ows?service=WFS&version=2.0.0&request=GetCapabilities",
        "protocol": "ogc-features",
        "layer_name": "ADMINEXPRESS-COG-CARTO.LATEST:commune",
        "tags": ["admin", "commune", "ogc", "features", "vector", "france"],
    },
]


class IGNFluxProvider(CatalogProvider):
    name = "ign"
    domain = CatalogDomain.FLUX
    description = "IGN Géoplateforme — WMS, WMTS, WFS services"

    def __init__(self) -> None:
        self._entries: dict[str, FluxEntry] = {}
        for item in _IGN_FLUX:
            entry_id = f"flux:ign:{item['id']}"
            self._entries[entry_id] = FluxEntry(
                id=entry_id,
                domain=CatalogDomain.FLUX,
                provider="ign",
                name=item["name"],
                description=item.get("description", ""),
                tags=item.get("tags", []),
                service_url=item["service_url"],
                protocol=FluxProtocol(item["protocol"]),
                layer_name=item.get("layer_name", ""),
                attribution="&copy; IGN Géoplateforme",
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
                or q in e.layer_name.lower()
                or any(q in t for t in e.tags)
            ]
        if tags:
            entries = [e for e in entries if any(t in e.tags for t in tags)]
        return entries[offset : offset + limit]

    def get_entry(self, entry_id):
        return self._entries.get(entry_id)


register_provider(IGNFluxProvider())
