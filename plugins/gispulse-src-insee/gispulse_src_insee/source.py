"""French INSEE DataSource — statistical units and IRIS tables.

A :class:`~gispulse.plugins.api.DeclarativeSource`: the plugin only
declares its access specs; WFS, bulk vector downloads and table downloads are
delegated to the registered protocol fetchers. This package ships zero network
code besides a HEAD probe for the WFS contour revision.

The first entries are IRIS (Ilots Regroupés pour l'Information Statistique)
contours: a live WFS declaration plus an IGN/Géoplateforme per-département
bulk archive. Additional entries expose official INSEE IRIS-level
sociodemographic CSV ZIP downloads.
"""

from __future__ import annotations

from dataclasses import dataclass

from gispulse.plugins.api import (
    AccessProtocol,
    AccessSpec,
    DeclarativeSource,
    Payload,
    SourceDomain,
    SourceEntryRef,
)

# IGN Géoplateforme — public WFS endpoint for statistical units.
_GEOPLATEFORME_WFS = "https://data.geopf.fr/wfs/ows"
_WFS_CAPABILITIES = (
    f"{_GEOPLATEFORME_WFS}?SERVICE=WFS&VERSION=2.0.0&REQUEST=GetCapabilities"
)
_REVISION_TIMEOUT_S = 8.0

_IRIS_TYPENAME = "STATISTICALUNITS.IRIS:contours_iris"
_IRIS_BULK_RESOURCE = "IRIS-GE"
_IRIS_BULK_MILLESIME = "2026-01-01"
_IRIS_BULK_SUBRESOURCE_TEMPLATE = (
    f"{_IRIS_BULK_RESOURCE}_3-0__GPKG_LAMB93_{{zone}}_{_IRIS_BULK_MILLESIME}"
)
_IRIS_BULK_ENDPOINT = (
    f"https://data.geopf.fr/telechargement/download/{_IRIS_BULK_RESOURCE}/"
    f"{_IRIS_BULK_SUBRESOURCE_TEMPLATE}/{_IRIS_BULK_SUBRESOURCE_TEMPLATE}.7z"
)
_IRIS_BULK_REVISION = "ign-iris-ge-gpkg-lamb93-2026-01-01"

_ENTRIES: dict[str, tuple[str, str]] = {
    "iris": (
        "IRIS — découpage infra-communal INSEE",
        _IRIS_TYPENAME,
    ),
}


@dataclass(frozen=True)
class _TableEntrySpec:
    label: str
    endpoint: str
    archive_member: str
    revision: str
    source_page: str
    millesime: str
    geography_date: str
    schema: dict[str, str]


_INSEE_FILE_BASE = "https://www.insee.fr/fr/statistiques/fichier"

_RP_IRIS_COMMON_SCHEMA = {
    "IRIS": "str",
    "COM": "str",
    "TYP_IRIS": "str",
    "LAB_IRIS": "str",
}

_FILOSOFI_IRIS_COMMON_SCHEMA = {
    "IRIS": "str",
}

_TABLE_ENTRIES: dict[str, _TableEntrySpec] = {
    "iris_population_2022": _TableEntrySpec(
        label="IRIS — population 2022",
        endpoint=f"{_INSEE_FILE_BASE}/8647014/base-ic-evol-struct-pop-2022_csv.zip",
        archive_member="base-ic-evol-struct-pop-2022.CSV",
        revision="insee-rp-iris-population-2022-geo-2024-01-01",
        source_page="https://www.insee.fr/fr/statistiques/8647014",
        millesime="2022",
        geography_date="2024-01-01",
        schema={
            **_RP_IRIS_COMMON_SCHEMA,
            "P22_POP": "float",
            "P22_POP0014": "float",
            "P22_POP1529": "float",
            "P22_POP3044": "float",
            "P22_POP4559": "float",
            "P22_POP6074": "float",
            "P22_POP75P": "float",
        },
    ),
    "iris_logement_2022": _TableEntrySpec(
        label="IRIS — logement 2022",
        endpoint=f"{_INSEE_FILE_BASE}/8647012/base-ic-logement-2022_csv.zip",
        archive_member="base-ic-logement-2022.CSV",
        revision="insee-rp-iris-logement-2022-geo-2024-01-01",
        source_page="https://www.insee.fr/fr/statistiques/8647012",
        millesime="2022",
        geography_date="2024-01-01",
        schema={
            **_RP_IRIS_COMMON_SCHEMA,
            "P22_LOG": "float",
            "P22_RP": "float",
            "P22_LOGVAC": "float",
            "P22_RP_PROP": "float",
            "P22_RP_LOC": "float",
        },
    ),
    "iris_menages_2022": _TableEntrySpec(
        label="IRIS — couples, familles, menages 2022",
        endpoint=(
            f"{_INSEE_FILE_BASE}/8647008/"
            "base-ic-couples-familles-menages-2022_csv.zip"
        ),
        archive_member="base-ic-couples-familles-menages-2022.CSV",
        revision="insee-rp-iris-menages-2022-geo-2024-01-01",
        source_page="https://www.insee.fr/fr/statistiques/8647008",
        millesime="2022",
        geography_date="2024-01-01",
        schema={
            **_RP_IRIS_COMMON_SCHEMA,
            "C22_MEN": "float",
            "C22_PMEN": "float",
            "C22_MENPSEUL": "float",
            "C22_MENFAM": "float",
        },
    ),
    "iris_activite_2022": _TableEntrySpec(
        label="IRIS — activite des residents 2022",
        endpoint=f"{_INSEE_FILE_BASE}/8647006/base-ic-activite-residents-2022_csv.zip",
        archive_member="base-ic-activite-residents-2022.CSV",
        revision="insee-rp-iris-activite-2022-geo-2024-01-01",
        source_page="https://www.insee.fr/fr/statistiques/8647006",
        millesime="2022",
        geography_date="2024-01-01",
        schema={
            **_RP_IRIS_COMMON_SCHEMA,
            "P22_ACT1564": "float",
            "P22_CHOM1564": "float",
            "P22_INACT1564": "float",
        },
    ),
    "iris_diplomes_2022": _TableEntrySpec(
        label="IRIS — diplomes et formation 2022",
        endpoint=f"{_INSEE_FILE_BASE}/8647010/base-ic-diplomes-formation-2022_csv.zip",
        archive_member="base-ic-diplomes-formation-2022.CSV",
        revision="insee-rp-iris-diplomes-2022-geo-2024-01-01",
        source_page="https://www.insee.fr/fr/statistiques/8647010",
        millesime="2022",
        geography_date="2024-01-01",
        schema={
            **_RP_IRIS_COMMON_SCHEMA,
            "P22_NSCOL15P": "float",
            "P22_NSCOL15P_DIPLMIN": "float",
            "P22_NSCOL15P_SUP2": "float",
        },
    ),
    "iris_filosofi_revenus_declares_2021": _TableEntrySpec(
        label="IRIS — Filosofi revenus declares 2021",
        endpoint=f"{_INSEE_FILE_BASE}/8229323/BASE_TD_FILO_IRIS_2021_DEC_CSV.zip",
        archive_member="BASE_TD_FILO_IRIS_2021_DEC.csv",
        revision="insee-filosofi-iris-revenus-declares-2021-geo-2022-01-01",
        source_page="https://www.insee.fr/fr/statistiques/8229323",
        millesime="2021",
        geography_date="2022-01-01",
        schema={
            **_FILOSOFI_IRIS_COMMON_SCHEMA,
            "DEC_MED21": "float",
            "DEC_TP6021": "float",
            "DEC_RD21": "float",
        },
    ),
    "iris_filosofi_revenus_disponibles_2021": _TableEntrySpec(
        label="IRIS — Filosofi revenus disponibles 2021",
        endpoint=f"{_INSEE_FILE_BASE}/8229323/BASE_TD_FILO_IRIS_2021_DISP_CSV.zip",
        archive_member="BASE_TD_FILO_IRIS_2021_DISP.csv",
        revision="insee-filosofi-iris-revenus-disponibles-2021-geo-2022-01-01",
        source_page="https://www.insee.fr/fr/statistiques/8229323",
        millesime="2021",
        geography_date="2022-01-01",
        schema={
            **_FILOSOFI_IRIS_COMMON_SCHEMA,
            "DISP_MED21": "float",
            "DISP_TP6021": "float",
            "DISP_RD21": "float",
        },
    ),
}


def _probe_revision(url: str) -> str | None:
    """Return a freshness token for ``url`` via a single HTTP HEAD.

    Derives the token from the ``ETag`` (preferred) / ``Last-Modified``
    response header. Returns ``None`` on any network error or when the
    endpoint exposes neither header, so the source watcher skips it
    rather than emitting a spurious change.
    """
    import httpx  # local import — keeps module import network-free

    try:
        resp = httpx.head(url, timeout=_REVISION_TIMEOUT_S, follow_redirects=True)
    except Exception:  # noqa: BLE001 — any transport error ⇒ unknown
        return None
    etag = resp.headers.get("etag")
    if etag:
        return etag.strip('"')
    last_modified = resp.headers.get("last-modified")
    if last_modified:
        return last_modified
    return None


class InseeSource(DeclarativeSource):
    """INSEE statistical units and IRIS-level sociodemographic tables."""

    name = "insee"
    domain = SourceDomain.STATISTIQUE
    payload = Payload.VECTOR
    jurisdiction = "FR"

    def entries(self) -> list[SourceEntryRef]:
        entries = [
            SourceEntryRef(
                id=entry_id,
                name=label,
                access=AccessSpec(
                    protocol=AccessProtocol.WFS,
                    endpoint=_GEOPLATEFORME_WFS,
                    params={"typename": typename},
                    format="application/json",
                ),
                revision_token=None,
                domain=self.domain,
                payload=self.payload,
                jurisdiction=self.jurisdiction,
                metadata={
                    "provider": "IGN / INSEE",
                    "platform": "WFS Géoplateforme",
                    "license": "Licence Ouverte 2.0",
                    "update_cadence": "annuel",
                    "typename": typename,
                },
            )
            for entry_id, (label, typename) in _ENTRIES.items()
        ]
        entries.append(
            SourceEntryRef(
                id="iris_bulk",
                name="IRIS GE — contours bulk IGN GeoPackage (par département)",
                access=AccessSpec(
                    protocol=AccessProtocol.DOWNLOAD,
                    endpoint=_IRIS_BULK_ENDPOINT,
                    params={
                        "zone": "D075",
                        "layer": "iris_ge",
                        "source_crs": "EPSG:2154",
                        "target_crs": "EPSG:4326",
                    },
                    format="application/x-7z-compressed",
                ),
                revision_token=None,
                domain=self.domain,
                payload=self.payload,
                jurisdiction=self.jurisdiction,
                metadata={
                    "provider": "IGN / INSEE",
                    "platform": "Géoplateforme téléchargement",
                    "license": "Licence Ouverte 2.0",
                    "update_cadence": "annuel",
                    "resource": _IRIS_BULK_RESOURCE,
                    "format": "GPKG",
                    "archive_format": "7z",
                    "source_projection": "LAMB93",
                    "source_crs": "EPSG:2154",
                    "projection": "EPSG:4326",
                    "millesime": _IRIS_BULK_MILLESIME,
                    "zone_format": "D{code_departement:0>3}",
                    "join_key": "code_iris",
                },
            )
        )
        entries.extend(
            SourceEntryRef(
                id=entry_id,
                name=spec.label,
                access=AccessSpec(
                    protocol=AccessProtocol.TABLE_FILE,
                    endpoint=spec.endpoint,
                    params={
                        "archive_format": "zip",
                        "table_format": "csv",
                        "archive_member": spec.archive_member,
                    },
                    format="application/zip",
                ),
                revision_token=spec.revision,
                domain=self.domain,
                payload=Payload.TABLE,
                jurisdiction=self.jurisdiction,
                metadata={
                    "provider": "INSEE",
                    "platform": "insee.fr fichiers statistiques",
                    "license": "Licence Ouverte 2.0",
                    "update_cadence": "annuel",
                    "millesime": spec.millesime,
                    "geography_date": spec.geography_date,
                    "source_page": spec.source_page,
                    "archive_format": "zip",
                    "table_format": "csv",
                },
            )
            for entry_id, spec in _TABLE_ENTRIES.items()
        )
        return entries

    def revision(self, entry_id: str) -> str | None:
        """Cheap freshness token for the source watcher.

        One HTTP HEAD against the Géoplateforme WFS GetCapabilities. The
        IRIS millésime is service-wide for this WFS declaration, so the
        entry id is only validated here.
        """
        self._entry(entry_id)  # validate the id
        if entry_id == "iris_bulk":
            return _IRIS_BULK_REVISION
        if entry_id in _TABLE_ENTRIES:
            return _TABLE_ENTRIES[entry_id].revision
        return _probe_revision(_WFS_CAPABILITIES)

    def schema(self, entry_id: str) -> dict:
        """Raw upstream fields exposed by the selected INSEE entry."""
        self._entry(entry_id)  # validates the id
        if entry_id in _TABLE_ENTRIES:
            return dict(_TABLE_ENTRIES[entry_id].schema)
        return {
            "code_iris": "str",
            "nom_iris": "str",
            "insee_com": "str",
            "nom_com": "str",
            "type_iris": "str",
            "geometry": "geometry",
        }
