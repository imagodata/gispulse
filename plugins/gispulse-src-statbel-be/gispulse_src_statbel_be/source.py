"""Statbel Belgium DataSource — statistical sectors (contours) and indicators.

Statbel (Belgian Statistical Office) publishes open-data files under the
Creative Commons Zero (CC0) licence. This plugin is **fully declarative**:
it only describes *where* data lives (``AccessSpec``) and *what shape* it
has (``schema()``). Core fetchers own the network code.

Geometry entries use ``AccessProtocol.DOWNLOAD`` (``HttpFileFetcher``).
Indicator entries also use ``DOWNLOAD`` — the upstream files are
delimited-text (TXT/CSV) or XLSX joined to the sector code.

CRS note
--------
Statbel vector data is natively in Belgian Lambert 72 (EPSG:31370).
The ``crs`` key in metadata is informational; the core fetcher does no
reprojection — the downstream pipeline is responsible for that.

URL status
----------
All URLs are the canonical Statbel open-data landing pages or their
direct-download variants as of June 2026.  Some direct-download URLs may
change between Statbel publication cycles.  Where the exact file URL was
uncertain, the dataset landing-page URL is used together with a comment
``# URL directe à vérifier``.  Maintainers should validate these URLs
against https://statbel.fgov.be/fr/open-data before each release.
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

# ---------------------------------------------------------------------------
# Upstream base URLs
# ---------------------------------------------------------------------------

# Statistical sectors — contours (GeoJSON, EPSG:31370)
# Direct GeoJSON download from Statbel open data.
# NOTE: Statbel publishes a new millésime each year; the URL below points to
# the 2023 release.  Check https://statbel.fgov.be/fr/open-data/secteurs-statistiques
# for the latest vintage.
_SECTORS_GEOJSON_URL = (
    "https://statbel.fgov.be/sites/default/files/files/opendata/statbel/"
    "secteurs-statistiques/sh_statbel_statistical_sectors_20230101.geojson"
    # URL directe à vérifier — landing page :
    # https://statbel.fgov.be/fr/open-data/secteurs-statistiques
)

# Population by statistical sector (TXT, delimiter "|")
# Landing: https://statbel.fgov.be/fr/open-data/population-par-secteur-statistique
_POPULATION_URL = (
    "https://statbel.fgov.be/sites/default/files/files/opendata/pop/"
    "TF_SOC_POP_STRUCT_2022.zip"
    # URL directe à vérifier — landing page :
    # https://statbel.fgov.be/fr/open-data/population-par-secteur-statistique
)

# Households by statistical sector (TXT, delimiter "|")
# Landing: https://statbel.fgov.be/fr/open-data/menages-par-secteur-statistique
_HOUSEHOLDS_URL = (
    "https://statbel.fgov.be/sites/default/files/files/opendata/menages/"
    "TF_SOC_MENAGES_2022.zip"
    # URL directe à vérifier — landing page :
    # https://statbel.fgov.be/fr/open-data/menages-par-secteur-statistique
)

# Average income by statistical sector (TXT, delimiter "|")
# Landing: https://statbel.fgov.be/fr/open-data/revenus-par-secteur-statistique
_INCOME_URL = (
    "https://statbel.fgov.be/sites/default/files/files/opendata/revenu/"
    "TF_INCOME_2021_SS.zip"
    # URL directe à vérifier — landing page :
    # https://statbel.fgov.be/fr/open-data/revenus-fiscaux-par-secteur-statistique
)

# Car fleet by statistical sector (TXT, delimiter "|")
# Landing: https://statbel.fgov.be/fr/open-data/parc-automobile-par-secteur-statistique
_CARS_URL = (
    "https://statbel.fgov.be/sites/default/files/files/opendata/vehicles/"
    "TF_VEHICLES_SS_2023.zip"
    # URL directe à vérifier — landing page :
    # https://statbel.fgov.be/fr/open-data/parc-automobile-par-secteur-statistique
)

# Building permits by municipality (XLSX) — Statbel does not publish permits
# at sector level; the finest grain is municipality (NIS code).
# Landing: https://statbel.fgov.be/fr/open-data/permis-de-batir
_PERMITS_URL = (
    "https://statbel.fgov.be/sites/default/files/files/opendata/permits/"
    "TF_PERM_BUILDING.xlsx"
    # URL directe à vérifier — landing page :
    # https://statbel.fgov.be/fr/open-data/permis-de-batir
)

_PROVIDER_META = {
    "provider": "Statbel — Direction générale Statistique, SPF Economie (BE)",
    "license": "Creative Commons Zero (CC0 1.0)",
    "jurisdiction": "BE",
    "join_key": "cd_sector",
    "crs": "EPSG:31370",  # Belgian Lambert 72 — for geometry entries
}

# ---------------------------------------------------------------------------
# Entry definitions
# ---------------------------------------------------------------------------

_ENTRIES: dict[str, dict] = {
    # ------------------------------------------------------------------
    # Geometry — statistical sector contours
    # ------------------------------------------------------------------
    "sectors-contours": {
        "label": "Statbel — statistical sector contours (EPSG:31370)",
        "payload": Payload.VECTOR,
        "access": AccessSpec(
            protocol=AccessProtocol.DOWNLOAD,
            endpoint=_SECTORS_GEOJSON_URL,
            params={},
            format="application/geo+json",
        ),
        "metadata": {
            **_PROVIDER_META,
            "dataset": "secteurs-statistiques",
            "geometry_key": "geometry",
            "sector_code_key": "cd_sector",
            "note": (
                "Annual vintage — check landing page for latest release: "
                "https://statbel.fgov.be/fr/open-data/secteurs-statistiques"
            ),
        },
    },
    # ------------------------------------------------------------------
    # Indicator tables — joinable by cd_sector
    # ------------------------------------------------------------------
    "indicators-population": {
        "label": "Statbel — population by statistical sector",
        "payload": Payload.TABLE,
        "access": AccessSpec(
            protocol=AccessProtocol.DOWNLOAD,
            endpoint=_POPULATION_URL,
            params={},
            format="text/plain",
        ),
        "metadata": {
            **_PROVIDER_META,
            "dataset": "population-par-secteur-statistique",
            "key_fields": ("cd_sector", "cd_sex", "cd_naty", "ms_pop"),
        },
    },
    "indicators-households": {
        "label": "Statbel — households by statistical sector",
        "payload": Payload.TABLE,
        "access": AccessSpec(
            protocol=AccessProtocol.DOWNLOAD,
            endpoint=_HOUSEHOLDS_URL,
            params={},
            format="text/plain",
        ),
        "metadata": {
            **_PROVIDER_META,
            "dataset": "menages-par-secteur-statistique",
            "key_fields": ("cd_sector", "cd_type_menage", "ms_menages"),
        },
    },
    "indicators-income": {
        "label": "Statbel — average income by statistical sector",
        "payload": Payload.TABLE,
        "access": AccessSpec(
            protocol=AccessProtocol.DOWNLOAD,
            endpoint=_INCOME_URL,
            params={},
            format="text/plain",
        ),
        "metadata": {
            **_PROVIDER_META,
            "dataset": "revenus-par-secteur-statistique",
            "key_fields": ("cd_sector", "ms_mean_total_net_taxable_income", "ms_nb"),
        },
    },
    "indicators-cars": {
        "label": "Statbel — car fleet by statistical sector",
        "payload": Payload.TABLE,
        "access": AccessSpec(
            protocol=AccessProtocol.DOWNLOAD,
            endpoint=_CARS_URL,
            params={},
            format="text/plain",
        ),
        "metadata": {
            **_PROVIDER_META,
            "dataset": "parc-automobile-par-secteur-statistique",
            "key_fields": ("cd_sector", "cd_type_fuel", "ms_nb_vehicles"),
        },
    },
    "indicators-building-permits": {
        "label": "Statbel — building permits (municipality level)",
        "payload": Payload.TABLE,
        "access": AccessSpec(
            protocol=AccessProtocol.DOWNLOAD,
            endpoint=_PERMITS_URL,
            params={},
            format="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ),
        "metadata": {
            **_PROVIDER_META,
            "dataset": "permis-de-batir",
            # Building permits are published at municipality (NIS) granularity,
            # not at statistical-sector level.
            "join_key": "cd_nis5",
            "key_fields": ("cd_nis5", "tx_adm_dstr_descr_fr", "ms_nb_permits"),
            "granularity": "municipality",
        },
    },
}

# ---------------------------------------------------------------------------
# Schema definitions
# ---------------------------------------------------------------------------

_SCHEMAS: dict[str, dict[str, str]] = {
    "sectors-contours": {
        "cd_sector": "str",
        "tx_sector_descr_fr": "str",
        "tx_sector_descr_nl": "str",
        "cd_nis5": "str",
        "tx_munty_descr_fr": "str",
        "tx_munty_descr_nl": "str",
        "cd_arr": "str",
        "cd_prov": "str",
        "cd_rgn_refnis": "str",
        "geometry": "geometry",
        "crs": "EPSG:31370",
    },
    "indicators-population": {
        "cd_sector": "str",
        "cd_refnis": "str",
        "cd_sex": "str",
        "cd_naty": "str",
        "cd_age": "int",
        "ms_pop": "int",
    },
    "indicators-households": {
        "cd_sector": "str",
        "cd_refnis": "str",
        "cd_type_menage": "str",
        "ms_menages": "int",
    },
    "indicators-income": {
        "cd_sector": "str",
        "cd_refnis": "str",
        "ms_nb": "int",
        "ms_mean_total_net_taxable_income": "float",
        "ms_median_total_net_taxable_income": "float",
    },
    "indicators-cars": {
        "cd_sector": "str",
        "cd_refnis": "str",
        "cd_type_fuel": "str",
        "ms_nb_vehicles": "int",
    },
    "indicators-building-permits": {
        "cd_nis5": "str",
        "tx_adm_dstr_descr_fr": "str",
        "tx_adm_dstr_descr_nl": "str",
        "cd_year": "int",
        "ms_nb_permits": "int",
    },
}


class StatbelBeSource(DeclarativeSource):
    """Statbel Belgium open statistical data exposed as a GISPulse source.

    Entries cover:
    - ``sectors-contours``: polygon contours of Belgium's statistical
      sectors in EPSG:31370 (Belgian Lambert 72).
    - ``indicators-population``: population by age/sex/nationality per
      statistical sector.
    - ``indicators-households``: household count by type per statistical
      sector.
    - ``indicators-income``: average net taxable income per statistical
      sector.
    - ``indicators-cars``: car fleet by fuel type per statistical sector.
    - ``indicators-building-permits``: building permits at municipality
      level (finest available granularity from Statbel).

    Indicator entries are joined to geometry via the ``cd_sector`` key
    (or ``cd_nis5`` for building permits).
    """

    name = "statbel_be"
    domain = SourceDomain.STATISTIQUE
    # Default payload — overridden per entry (VECTOR for contours, TABLE for
    # indicators). The per-entry payload is carried by SourceEntryRef.payload.
    payload = Payload.TABLE
    jurisdiction = "BE"

    def entries(self) -> list[SourceEntryRef]:
        return [
            SourceEntryRef(
                id=entry_id,
                name=spec["label"],
                access=spec["access"],
                revision_token=None,
                domain=self.domain,
                payload=spec["payload"],
                jurisdiction=self.jurisdiction,
                metadata=spec["metadata"],
            )
            for entry_id, spec in _ENTRIES.items()
        ]

    def schema(self, entry_id: str) -> dict[str, str]:
        """Return the known field names and types for ``entry_id``."""
        self._entry(entry_id)  # validate the id
        return dict(_SCHEMAS[entry_id])

    def revision(self, entry_id: str) -> str | None:
        """Return ``None`` — Statbel does not expose ETag/Last-Modified on
        its static file downloads; freshness is unknown."""
        self._entry(entry_id)  # validate the id
        return None
