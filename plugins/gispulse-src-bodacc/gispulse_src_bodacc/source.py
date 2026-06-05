"""BODACC DataSource over the official DILA OpenDataSoft API.

The source stays declarative: it exposes BODACC commercial-notice rows from
``annonces-commerciales`` as raw table records. Normalising those rows into
Marchand'biens scoring features remains downstream.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from typing import Any

from gispulse.plugins.api import (
    AccessProtocol,
    AccessSpec,
    DeclarativeSource,
    Payload,
    SourceDomain,
    SourceEntryRef,
)

BODACC_DATASET_ID = "annonces-commerciales"
BODACC_DATASET_ENDPOINT = (
    "https://bodacc-datadila.opendatasoft.com/api/explore/v2.1/"
    f"catalog/datasets/{BODACC_DATASET_ID}"
)
BODACC_RECORDS_ENDPOINT = f"{BODACC_DATASET_ENDPOINT}/records"
_DEFAULT_LIMIT = 100
_MAX_LIMIT = 100
_REVISION_TIMEOUT_S = 8.0

_COMMON_METADATA = {
    "provider": "DILA",
    "platform": "bodacc-datadila.opendatasoft.com API v2.1",
    "dataset_id": BODACC_DATASET_ID,
    "license": "Licence Ouverte 2.0",
    "ods_pagination": "limit-offset",
    "pagination_scope": "single_ods_page",
    "page_size_parameter": "limit",
    "page_offset_parameter": "offset",
    "total_count_key": "total_count",
    "core_fetcher_note": (
        "BODACC REST_TABLE entries intentionally materialize one ODS page."
    ),
    "orchestration_note": (
        "Loop explicitly by calling access_for(..., offset=offset+limit) "
        "until total_count is reached or the page is empty."
    ),
    "filter_fields": (
        "registre",
        "ville",
        "cp",
        "numerodepartement",
        "dateparution",
    ),
    "civil_notices_note": (
        "Data.gouv states civil succession notices are not included in this "
        "database for personal-data protection reasons."
    ),
}

_ENTRIES: dict[str, dict[str, str | None]] = {
    "annonces-commerciales": {
        "label": "BODACC annonces commerciales",
        "familleavis": None,
        "familleavis_lib": None,
    },
    "ventes-cessions": {
        "label": "BODACC ventes et cessions",
        "familleavis": "vente",
        "familleavis_lib": "Ventes et cessions",
    },
    "procedures-collectives": {
        "label": "BODACC procedures collectives",
        "familleavis": "collective",
        "familleavis_lib": "Procedures collectives",
    },
    "depots-comptes": {
        "label": "BODACC depots des comptes",
        "familleavis": "dpc",
        "familleavis_lib": "Depots des comptes",
    },
    "immatriculations": {
        "label": "BODACC immatriculations",
        "familleavis": "immatriculation",
        "familleavis_lib": "Immatriculations",
    },
    "creations-etablissements": {
        "label": "BODACC creations d'etablissements",
        "familleavis": "creation",
        "familleavis_lib": "Creations",
    },
    "modifications": {
        "label": "BODACC modifications generales",
        "familleavis": "modification",
        "familleavis_lib": "Modifications diverses",
    },
    "radiations": {
        "label": "BODACC radiations",
        "familleavis": "radiation",
        "familleavis_lib": "Radiations",
    },
    "conciliations": {
        "label": "BODACC procedures de conciliation",
        "familleavis": "conciliation",
        "familleavis_lib": "Procedures de conciliation",
    },
    "retablissements-professionnels": {
        "label": "BODACC retablissements professionnels",
        "familleavis": "retablissement_professionnel",
        "familleavis_lib": "Procedures de retablissement professionnel",
    },
    "annonces-diverses": {
        "label": "BODACC annonces diverses",
        "familleavis": "divers",
        "familleavis_lib": "Annonces diverses",
    },
    "famille-inconnue": {
        "label": "BODACC famille inconnue",
        "familleavis": "inconnue",
        "familleavis_lib": None,
    },
}

_SCHEMA = {
    "id": "str",
    "publicationavis": "str",
    "parution": "str",
    "dateparution": "date",
    "numeroannonce": "int",
    "typeavis": "str",
    "typeavis_lib": "str",
    "familleavis": "str",
    "familleavis_lib": "str",
    "numerodepartement": "str",
    "departement_nom_officiel": "str",
    "region_code": "int",
    "region_nom_officiel": "str",
    "tribunal": "str",
    "commercant": "str",
    "ville": "str",
    "registre": "list[str]",
    "cp": "str",
    "listepersonnes": "json-string",
    "listeetablissements": "json-string",
    "jugement": "json-string",
    "acte": "json-string",
    "modificationsgenerales": "json-string",
    "radiationaurcs": "json-string",
    "depot": "json-string",
    "listeprecedentexploitant": "json-string",
    "listeprecedentproprietaire": "json-string",
    "divers": "json-string",
    "parutionavisprecedent": "str",
    "url_complete": "str",
}


def _quote(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _digits(value: str) -> str:
    return "".join(ch for ch in value if ch.isdigit())


def _date_literal(value: str) -> str:
    parsed = date.fromisoformat(value)
    return f"date'{parsed.isoformat()}'"


def _and_where(clauses: list[str]) -> str:
    return " AND ".join(clause for clause in clauses if clause)


def _probe_dataset_revision() -> str | None:
    """Return the OpenDataSoft dataset freshness token from a tiny JSON probe."""
    import httpx

    try:
        resp = httpx.get(
            BODACC_DATASET_ENDPOINT,
            timeout=_REVISION_TIMEOUT_S,
            follow_redirects=True,
        )
        resp.raise_for_status()
    except Exception:  # noqa: BLE001 - unreachable source means unknown freshness
        return None

    try:
        default_metas = resp.json().get("metas", {}).get("default", {})
    except ValueError:
        default_metas = {}
    data_processed = default_metas.get("data_processed")
    if data_processed:
        return str(data_processed)
    metadata_processed = default_metas.get("metadata_processed")
    if metadata_processed:
        return str(metadata_processed)
    last_modified = resp.headers.get("last-modified")
    if last_modified:
        return last_modified
    return None


class BodaccSource(DeclarativeSource):
    """BODACC legal notices exposed as a GISPulse declarative source."""

    name = "bodacc"
    domain = SourceDomain.STATISTIQUE
    payload = Payload.TABLE
    jurisdiction = "FR"

    def entries(self) -> list[SourceEntryRef]:
        return [self._entry_ref(entry_id) for entry_id in _ENTRIES]

    def _entry_ref(self, entry_id: str) -> SourceEntryRef:
        spec = _ENTRIES[entry_id]
        familleavis = spec["familleavis"]
        query: dict[str, Any] = {"limit": _DEFAULT_LIMIT, "offset": 0}
        if familleavis:
            query["where"] = f"familleavis={_quote(familleavis)}"
        return SourceEntryRef(
            id=entry_id,
            name=str(spec["label"]),
            access=AccessSpec(
                protocol=AccessProtocol.REST_TABLE,
                endpoint=BODACC_RECORDS_ENDPOINT,
                params={
                    "query": query,
                    "pagination": {
                        "data_key": "results",
                        "max_pages": 1,
                        "max_rows": _DEFAULT_LIMIT,
                    },
                },
                format="application/json",
            ),
            revision_token=None,
            domain=self.domain,
            payload=self.payload,
            jurisdiction=self.jurisdiction,
            metadata={
                **_COMMON_METADATA,
                "familleavis": familleavis,
                "familleavis_lib": spec["familleavis_lib"],
            },
        )

    def access_for(
        self,
        entry_id: str,
        *,
        siren: str | None = None,
        siret: str | None = None,
        commune: str | None = None,
        code_postal: str | None = None,
        departement: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        offset: int = 0,
        limit: int = _DEFAULT_LIMIT,
        local_path: str | None = None,
        s3_uri: str | None = None,
        s3_key: str | None = None,
    ) -> AccessSpec:
        """Build a filtered BODACC API AccessSpec.

        BODACC exposes SIREN values in the top-level ``registre`` field.
        SIRET is not a top-level column in the OpenDataSoft dataset, so the
        SIRET filter searches the raw record text and also includes the SIREN
        prefix through ``registre``.
        """
        if limit < 1 or limit > _MAX_LIMIT:
            raise ValueError(f"limit must be between 1 and {_MAX_LIMIT}")
        if offset < 0:
            raise ValueError("offset must be positive or zero")

        entry = self._entry(entry_id)
        params = dict(entry.access.params)
        for nested_key in ("query", "pagination"):
            nested = params.get(nested_key)
            if isinstance(nested, dict):
                params[nested_key] = dict(nested)

        query = dict(params.get("query") or {})
        clauses: list[str] = []
        static_where = query.get("where")
        if static_where:
            clauses.append(str(static_where))
        if siren:
            siren_digits = _digits(siren)
            if len(siren_digits) != 9:
                raise ValueError("siren must contain exactly 9 digits")
            clauses.append(f"registre={_quote(siren_digits)}")
        if siret:
            siret_digits = _digits(siret)
            if len(siret_digits) != 14:
                raise ValueError("siret must contain exactly 14 digits")
            siren_prefix = siret_digits[:9]
            clauses.append(
                f"(search({_quote(siret_digits)}) OR registre={_quote(siren_prefix)})"
            )
        if commune:
            clauses.append(f"ville={_quote(commune)}")
        if code_postal:
            clauses.append(f"cp={_quote(code_postal)}")
        if departement:
            clauses.append(f"numerodepartement={_quote(departement)}")
        if date_from:
            clauses.append(f"dateparution>={_date_literal(date_from)}")
        if date_to:
            clauses.append(f"dateparution<={_date_literal(date_to)}")

        query["limit"] = limit
        query["offset"] = offset
        where = _and_where(clauses)
        if where:
            query["where"] = where
        else:
            query.pop("where", None)
        params["query"] = query
        params["pagination"]["max_rows"] = limit

        if local_path is not None:
            params["local_path"] = local_path
        if s3_uri is not None:
            params["s3_uri"] = s3_uri
        if s3_key is not None:
            params["s3_key"] = s3_key
        return replace(entry.access, params=params)

    def schema(self, entry_id: str) -> dict[str, str]:
        self._entry(entry_id)  # validates the id
        return dict(_SCHEMA)

    def revision(self, entry_id: str) -> str | None:
        self._entry(entry_id)  # validates the id
        return _probe_dataset_revision()
