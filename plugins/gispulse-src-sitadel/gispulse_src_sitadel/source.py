"""Sitadel DataSource - French urban planning authorisations (SDES / MTES).

Sitadel (Systeme d'Information et de Traitement Automatise des Donnees
Elementaires sur les Logements et les locaux) collects all building-permit
decisions made by local authorities since 2013. The SDES (Service des
Donnees et Etudes Statistiques) publishes the bulk CSV files monthly via
the DiDo open-data platform and mirrors them on data.gouv.fr.

This source is intentionally declarative: it exposes the latest published
CSV endpoints and leaves HTTP, materialisation and lazy scans to the
registered GISPulse TABLE_FILE fetcher.

Endpoint pattern: data.gouv.fr redirect URLs are stable per resource_id
and always resolve to the current monthly millésime file on the DiDo
platform (dido.api.v1 — permanent download links).

Confirmed resource_ids (from data.gouv.fr API, dataset 689c42fa521ccf80ce954f83,
last verified 2026-06-01, updated 2026-05-30):
- autorisations_logements : 65a9e264-7a20-46a9-9d98-66becb817bc3
- autorisations_locaux    : 8f23d65f-7142-4ac5-94c1-077b028255bf
- permis_amenager        : 9db13a09-72a9-4871-b430-13872b4890b3
- permis_demolir         : 8f73cf2d-7bc4-4b5a-b912-718d6991f0a0

Column names verified against:
- SDES official variable dictionary (2021-06, XLS)
  statistiques.developpement-durable.gouv.fr/sites/default/files/2021-06/
  dictionnaire_variables_logements_permis_construire.xls
- IDF Smart Services open dataset (field list, 2026)
  data.smartidf.services/explore/dataset/
  liste-autorisations-urbanisme-creant-logements-sitadel-gps-maj-trimestrielle
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

# Stable data.gouv.fr redirect URLs — each resource_id always resolves to
# the current monthly file on the DiDo platform (permanent pointers).
_DATAGOUV_BASE = "https://www.data.gouv.fr/api/1/datasets/r"
_DATASET_ID = "689c42fa521ccf80ce954f83"
_DATASET_PAGE = f"https://www.data.gouv.fr/datasets/{_DATASET_ID}"

# Column delimiter used by the DiDo CSV export.
_TABLE_PARAMS: dict[str, str] = {"table_format": "csv", "delimiter": ";"}

# Schema for "autorisations créant des logements" (PC + DP logements).
# Field names are UPPERCASE as published in the SDES dictionary.
_SCHEMA_LOGEMENTS: dict[str, str] = {
    # --- geography ---
    "REG": "str",
    "DEP": "str",
    "COMM": "str",
    # --- dossier ---
    "Num_DAU": "str",
    "Type_DAU": "str",
    "Etat_DAU": "str",
    # --- dates ---
    "DATE_REELLE_AUTORISATION": "str",  # YYYY-MM-DD
    "DATE_REELLE_DOC": "str",
    "DATE_REELLE_DAACT": "str",
    # --- demandeur ---
    "CAT_DEM": "str",
    "SIREN_DEM": "str",
    "SIRET_DEM": "str",
    "DENOM_DEM": "str",
    # --- terrain ---
    "SUPERFICIE_TERRAIN": "float",
    # --- project nature ---
    "NATURE_PROJET": "str",
    "UTILISATION": "str",
    "I_EXTENSION": "str",
    # --- logements (key metrics) ---
    "NB_LGT_TOT_CREES": "int",
    "NB_LGT_IND_CREES": "int",
    "NB_LGT_COL_CREES": "int",
    "NB_LGT_DEMOLIS": "int",
    # --- surfaces ---
    "SURF_HAB_AVANT": "float",
    "SURF_HAB_CREEE": "float",
    "SURF_HAB_DEMOLIE": "float",
    # --- social housing ---
    "NB_LGT_PRET_LOC_SOCIAL": "int",
    "NB_LGT_ACC_SOC_HORS_PTZ": "int",
    "NB_LGT_PTZ": "int",
}

# Schema for "autorisations créant des locaux non résidentiels" (PC + DP locaux).
# Source: same SDES variable dictionary, locaux sheet.
_SCHEMA_LOCAUX: dict[str, str] = {
    # --- geography ---
    "REG": "str",
    "DEP": "str",
    "COMM": "str",
    # --- dossier ---
    "Num_DAU": "str",
    "Type_DAU": "str",
    "Etat_DAU": "str",
    # --- dates ---
    "DATE_REELLE_AUTORISATION": "str",
    "DATE_REELLE_DOC": "str",
    "DATE_REELLE_DAACT": "str",
    # --- demandeur ---
    "CAT_DEM": "str",
    "SIREN_DEM": "str",
    "DENOM_DEM": "str",
    # --- terrain ---
    "SUPERFICIE_TERRAIN": "float",
    # --- project ---
    "NATURE_PROJET": "str",
    "UTILISATION": "str",
    "I_EXTENSION": "str",
    # --- surface non résidentielle (key metric) ---
    "SURF_LOC_AVANT": "float",
    "SURF_LOC_CREEE": "float",
    "SURF_LOC_DEMOLIE": "float",
}


@dataclass(frozen=True)
class _EntrySpec:
    label: str
    resource_id: str
    schema: dict[str, str]
    millesime: str
    last_modified: str
    join_key: str = "COMM"


_MILLESIME = "2026-05"  # last published millésime (monthly, updated 2026-05-30)
_LAST_MODIFIED = "2026-05-30"

_ENTRY_SPECS: dict[str, _EntrySpec] = {
    "autorisations_logements": _EntrySpec(
        label=("Sitadel - liste des autorisations d'urbanisme creant des logements (PC+DP)"),
        resource_id="65a9e264-7a20-46a9-9d98-66becb817bc3",
        schema=_SCHEMA_LOGEMENTS,
        millesime=_MILLESIME,
        last_modified=_LAST_MODIFIED,
    ),
    "autorisations_locaux": _EntrySpec(
        label=(
            "Sitadel - liste des autorisations d'urbanisme creant des locaux "
            "non residentiels (PC+DP)"
        ),
        resource_id="8f23d65f-7142-4ac5-94c1-077b028255bf",
        schema=_SCHEMA_LOCAUX,
        millesime=_MILLESIME,
        last_modified=_LAST_MODIFIED,
        join_key="COMM",
    ),
}


class SitadelSource(DeclarativeSource):
    """Sitadel building-permit and urban-authorisation open data (SDES/MTES)."""

    name = "sitadel"
    domain = SourceDomain.FONCIER
    payload = Payload.TABLE
    jurisdiction = "FR"

    def entries(self) -> list[SourceEntryRef]:
        return [_make_entry_ref(entry_id, spec) for entry_id, spec in _ENTRY_SPECS.items()]

    def schema(self, entry_id: str) -> dict[str, str]:
        self._entry(entry_id)  # validates the id
        return dict(_ENTRY_SPECS[entry_id].schema)

    def revision(self, entry_id: str) -> str | None:
        self._entry(entry_id)  # validates the id
        spec = _ENTRY_SPECS[entry_id]
        return f"sitadel-{spec.millesime}-{spec.resource_id}"


def _make_entry_ref(entry_id: str, spec: _EntrySpec) -> SourceEntryRef:
    endpoint = f"{_DATAGOUV_BASE}/{spec.resource_id}"
    metadata: dict[str, object] = {
        "provider": "SDES - Ministere de la Transition ecologique",
        "dataset": "Liste des permis de construire et autres autorisations d'urbanisme",
        "dataset_id": _DATASET_ID,
        "resource_id": spec.resource_id,
        "source_page": _DATASET_PAGE,
        "platform": "DiDo / data.gouv.fr",
        "license": "Licence Ouverte 2.0",
        "millesime": spec.millesime,
        "last_modified": spec.last_modified,
        "update_cadence": "mensuel",
        "table_format": "csv",
        "delimiter": ";",
        "join_key": spec.join_key,
        "geography_scope": "France metropolitaine + DROM (depuis 2013)",
    }
    return SourceEntryRef(
        id=entry_id,
        name=spec.label,
        access=AccessSpec(
            protocol=AccessProtocol.TABLE_FILE,
            endpoint=endpoint,
            params=dict(_TABLE_PARAMS),
            format="text/csv",
        ),
        revision_token=f"sitadel-{spec.millesime}-{spec.resource_id}",
        domain=SourceDomain.FONCIER,
        payload=Payload.TABLE,
        jurisdiction="FR",
        metadata=metadata,
    )
