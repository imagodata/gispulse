"""GIPOD DataSource — travaux planifiés et occupations du domaine public (Flandre/BE).

GIPOD (Generiek InformatiePlatform Openbaar Domein) est la plateforme d'information
du domaine public de la région flamande (Belgique). Le service OGC publié par
Digitaal Vlaanderen expose les occupations du domaine public et les perturbations
de mobilité planifiées, valables aujourd'hui et sur les 6 prochains mois.

Les deux entrées couvertes par ce plugin :

* **inname** — occupations du domaine public (surfaces/polygones). Comprend
  grondwerken (travaux de génie civil), événements et autres types d'occupation.
  Co-tranchée (GroundworkPartOfTrenchSynergy) inclus.
* **hinder** — perturbations de mobilité planifiées (surfaces). Contient les
  zones de restriction de trafic associées aux occupations.

Endpoints confirmés (2026-06) via GetCapabilities WFS et collection OGC API :
  WFS    : https://geo.api.vlaanderen.be/GIPOD/wfs
  OGC AF : https://geo.api.vlaanderen.be/GIPOD/ogc/features/v1

CRS natif : EPSG:31370 (Belgian Lambert 72). WFS version 2.0 supporte la
demande en EPSG:4326 via le paramètre ``srsname``; le fetcher core utilise
le ``crs`` des AccessSpec.params (cf. guide WfsFetcher).

Licence : Modellicentie Gratis Hergebruik — Vlaamse overheid
(https://www.vlaanderen.be/digitaal-vlaanderen/onze-oplossingen/geografische-webdiensten/
gebruiksrechten-en-privacyverklaring-geografische-webdiensten)
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

# WFS endpoint (Digitaal Vlaanderen — confirmé via GetCapabilities 2026-06).
_WFS_ENDPOINT = "https://geo.api.vlaanderen.be/GIPOD/wfs"

# URL GetCapabilities — sonde de fraîcheur pour revision() (HEAD).
_WFS_CAPABILITIES = (
    f"{_WFS_ENDPOINT}?SERVICE=WFS&VERSION=2.0.0&REQUEST=GetCapabilities"
)

# OGC API Features endpoint (alternatif au WFS, même données).
# Utilisé ici via AccessProtocol.OGC_FEATURES ; collection key = "INNAME".
_OGC_FEATURES_BASE = "https://geo.api.vlaanderen.be/GIPOD/ogc/features/v1"

# Namespace WFS (confirmé via FeatureType/@Name dans GetCapabilities).
_NS = "GIPOD"

_COMMON_METADATA = {
    "provider": "Digitaal Vlaanderen / Vlaamse overheid",
    "platform": "geo.api.vlaanderen.be/GIPOD",
    "license": "Modellicentie Gratis Hergebruik — Vlaamse overheid",
    "region": "Vlaanderen (Flandre)",
    "crs_native": "EPSG:31370",
    "temporal_window": "aujourd'hui + 6 mois",
    "note": (
        "Horizon temporel : occupations/travaux planifiés ou en cours "
        "aujourd'hui et durant les 6 prochains mois."
    ),
}

# Schéma des propriétés retournées par l'endpoint INNAME (OGC API, GeoJSON).
# Noms vérifiés sur un item réel via GET .../INNAME/items?limit=1 (2026-06).
_SCHEMA_INNAME = {
    "GipodId": "int",
    "Uri": "str",
    "Description": "str",
    "Reference": "str",
    "Type": "str",
    "TypeId": "int",
    "PublicDomainOccupancyTypes": "list[json]",
    "Status": "str",
    "StatusId": "int",
    "Start": "datetime",
    "End": "datetime",
    "TimeSchedule": "str",
    "Owner": "str",
    "OwnerId": "int",
    "ContactOrganisations": "list[json]",
    "MobilityHindrances": "list[json]",
    "GroundworkCategory": "str",
    "GroundworkCategoryId": "int",
    "GroundworkSpecification": "str",
    "GroundworkPartOfTrenchSynergy": "bool",
    "CreatedOn": "datetime",
    "LastModifiedOn": "datetime",
    "geometry": "geometry",
}

# Schéma des perturbations de mobilité (HINDER) — champs déduits du modèle GIPOD.
# Les champs MobilityHindrance-spécifiques (HinderId, OccupancyIds, …) sont
# documentés dans la page OGC Services de Digitaal Vlaanderen.
_SCHEMA_HINDER = {
    "GipodId": "int",
    "Uri": "str",
    "Description": "str",
    "Start": "datetime",
    "End": "datetime",
    "Status": "str",
    "StatusId": "int",
    "Owner": "str",
    "OwnerId": "int",
    "CreatedOn": "datetime",
    "LastModifiedOn": "datetime",
    "geometry": "geometry",
}

_SCHEMAS: dict[str, dict[str, str]] = {
    "inname": _SCHEMA_INNAME,
    "hinder": _SCHEMA_HINDER,
}


def _probe_revision_head(url: str) -> str | None:
    """Retourne un token de fraîcheur par HEAD sur l'URL de capabilities.

    Renvoie ``None`` si le service est inaccessible ou n'expose pas
    d'en-tête de fraîcheur — le watcher traite ``None`` comme "inconnu"
    et ne déclenche pas de faux positif.
    """
    import httpx  # import local — le module ne fait aucun réseau à l'import

    try:
        resp = httpx.head(url, timeout=8.0, follow_redirects=True)
    except Exception:  # noqa: BLE001 — toute erreur réseau → inconnu
        return None
    etag = resp.headers.get("etag")
    if etag:
        return etag.strip('"')
    last_modified = resp.headers.get("last-modified")
    if last_modified:
        return last_modified
    return None


class GipodBeSource(DeclarativeSource):
    """GIPOD (Flandre/BE) : occupations domaine public et perturbations de mobilité."""

    name = "gipod_be"
    domain = SourceDomain.REGLEMENTAIRE  # occupation = autorisation/permis d'occuper
    payload = Payload.VECTOR
    jurisdiction = "BE"

    def entries(self) -> list[SourceEntryRef]:
        return [
            SourceEntryRef(
                id="inname",
                name="GIPOD - inname openbaar domein (occupations du domaine public)",
                access=AccessSpec(
                    protocol=AccessProtocol.WFS,
                    endpoint=_WFS_ENDPOINT,
                    params={
                        "typename": f"{_NS}:INNAME",
                        "version": "2.0.0",
                        # CRS demandé en sortie : 4326 (WGS84 GeoJSON).
                        # Le CRS natif du service est EPSG:31370 (Lambert 72) ;
                        # passer "EPSG:4326" ici déclenche la reprojection côté WFS.
                        "crs": "EPSG:4326",
                    },
                    format="application/json",
                ),
                revision_token=None,
                domain=self.domain,
                payload=self.payload,
                jurisdiction=self.jurisdiction,
                metadata={
                    **_COMMON_METADATA,
                    "layer": "INNAME",
                    "layer_title": "GIPOD - inname openbaar domein",
                    "description": (
                        "Occupations du domaine public (zones/polygones) : "
                        "travaux de génie civil (grondwerken), événements et "
                        "autres occupations planifiées ou en cours. "
                        "Inclut GroundworkPartOfTrenchSynergy (co-tranchée)."
                    ),
                    "identity_key": "GipodId",
                    "date_fields": ("Start", "End"),
                    "cotrench_key": "GroundworkPartOfTrenchSynergy",
                    "wfs_typename": f"{_NS}:INNAME",
                    "ogc_features_collection": "INNAME",
                    "ogc_features_url": f"{_OGC_FEATURES_BASE}/collections/INNAME/items",
                },
            ),
            SourceEntryRef(
                id="hinder",
                name="GIPOD - geplande mobiliteitshinder (perturbations de mobilité planifiées)",
                access=AccessSpec(
                    protocol=AccessProtocol.WFS,
                    endpoint=_WFS_ENDPOINT,
                    params={
                        "typename": f"{_NS}:HINDER",
                        "version": "2.0.0",
                        "crs": "EPSG:4326",
                    },
                    format="application/json",
                ),
                revision_token=None,
                domain=self.domain,
                payload=self.payload,
                jurisdiction=self.jurisdiction,
                metadata={
                    **_COMMON_METADATA,
                    "layer": "HINDER",
                    "layer_title": "GIPOD - geplande mobiliteitshinder",
                    "description": (
                        "Perturbations de mobilité planifiées (zones/polygones) "
                        "associées aux occupations GIPOD. Comprend restrictions "
                        "de circulation et fermetures de voirie validées par les "
                        "autorités locales."
                    ),
                    "identity_key": "GipodId",
                    "date_fields": ("Start", "End"),
                    "wfs_typename": f"{_NS}:HINDER",
                    "ogc_features_collection": "HINDER",
                    "ogc_features_url": f"{_OGC_FEATURES_BASE}/collections/HINDER/items",
                },
            ),
        ]

    def schema(self, entry_id: str) -> dict[str, str]:
        """Schéma des attributs de l'entrée GIPOD."""
        self._entry(entry_id)  # valide l'id
        return dict(_SCHEMAS[entry_id])

    def revision(self, entry_id: str) -> str | None:
        """Token de fraîcheur via HEAD sur GetCapabilities WFS.

        Retourne ``None`` si le service est inaccessible — le watcher
        considère cela comme "inconnu" et ne déclenche pas de faux positif.
        """
        self._entry(entry_id)  # valide l'id
        return _probe_revision_head(_WFS_CAPABILITIES)
