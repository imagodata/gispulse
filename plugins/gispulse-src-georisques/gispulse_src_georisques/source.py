"""Géorisques DataSource — natural/technological risk signals (#196).

A :class:`DeclarativeSource` over :attr:`AccessProtocol.REST_TABLE` for the
runtime APIs plus :attr:`AccessProtocol.DOWNLOAD` for confirmed bulk datasets.
Each API endpoint remains one declarative entry; the endpoint and its static
query (page size, radius) are fixed here, but the **runtime spatial key** —
``code_insee`` for the communal endpoints, ``latlon`` for the point endpoints
— is supplied per call by the ingestion orchestrator through
:meth:`GeorisquesSource.access_for`.

The plugin is intentionally *raw*: ``schema`` describes the upstream fields,
not a normalised ``radon_class`` / ``RiskConstraint`` shape. Normalisation
stays in the consuming product (permis service, foncier dbt) — keeping this
source from becoming a disguised business client.
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

GEORISQUES_BASE_URL = "https://www.georisques.gouv.fr"
GEORISQUES_DOWNLOAD_SERVICE = (
    "https://www.georisques.gouv.fr/webappReport/ws/telechargements"
)

# entry_id -> declarative spec. ``query_key`` names the runtime spatial
# parameter; ``scope`` is the granularity it filters at. ``static_query``
# is folded into every request; ``pagination`` is the REST_TABLE recipe.
_ENTRIES: dict[str, dict[str, Any]] = {
    "gaspar-risques": {
        "label": "Risques recensés GASPAR (commune)",
        "path": "/api/v1/gaspar/risques",
        "query_key": "code_insee",
        "scope": "commune",
        "static_query": {"page_size": 100},
        "pagination": {"data_key": "data", "next_key": "next"},
    },
    "radon": {
        "label": "Potentiel radon (commune)",
        "path": "/api/v1/radon",
        "query_key": "code_insee",
        "scope": "commune",
        "static_query": {},
        "pagination": {"data_key": "data"},
    },
    "sismicite": {
        "label": "Zonage sismique (commune)",
        "path": "/api/v1/zonage_sismique",
        "query_key": "code_insee",
        "scope": "commune",
        "static_query": {"page_size": 10},
        "pagination": {"data_key": "data"},
    },
    "rga": {
        "label": "Retrait-gonflement des argiles (point)",
        "path": "/api/v1/rga",
        "query_key": "latlon",
        "scope": "point",
        "static_query": {},
        # 2024+ API answers a top-level object, not {"data": [...]}.
        "pagination": {"row_source": "body", "empty_body_is_empty": True},
    },
    "tri-zonage": {
        "label": "Territoire à risque important d'inondation (point)",
        "path": "/api/v1/tri_zonage",
        "query_key": "latlon",
        "scope": "point",
        "static_query": {},
        # absence of a TRI is served as 404 — treat it as an empty result.
        "pagination": {"data_key": "data", "empty_statuses": [404]},
    },
    "ssp": {
        "label": "Sites et sols pollués à proximité (point)",
        "path": "/api/v1/ssp",
        "query_key": "latlon",
        "scope": "point",
        "static_query": {"rayon": 500, "page_size": 5},
        # 2024+ API nests the count under {"casias": {...}} — keep the raw body.
        "pagination": {"row_source": "body"},
    },
}

_BULK_ENTRIES: dict[str, dict[str, Any]] = {
    "rga-bulk": {
        "label": "Retrait-gonflement des argiles 2026 (bulk, département)",
        "endpoint": (
            "https://files.georisques.fr/argiles/2025/"
            "AleaRG_2025_{departement}_L93.zip"
        ),
        "payload": Payload.VECTOR,
        "base_key": "alearg_25",
        "format": "zip",
        "archive_format": "zip",
        "data_format": "shapefile",
        "echelle": "departementale",
        "department_param": "codeDepartement",
        "params": {"departement": "69"},
        "revision": "georisques-alearg_25-zip",
    },
    "tri-bulk": {
        "label": "TRI rapportage 2020 (bulk, département)",
        "endpoint": (
            "https://files.georisques.fr/di_2020/"
            "tri_2020_sig_di_{departement}.zip"
        ),
        "payload": Payload.VECTOR,
        "base_key": "tri_2020",
        "format": "zip",
        "archive_format": "zip",
        "data_format": "shapefile",
        "echelle": "departementale",
        "department_param": "codeDepartement",
        "params": {"departement": "69"},
        "revision": "georisques-tri_2020-zip",
    },
    "sis-bulk": {
        "label": "Secteurs d'informations sur les sols (bulk CSV)",
        "endpoint": (
            "https://mapsref.brgm.fr/wxs/georisques/georisques_dl?"
            "&service=wfs&version=2.0.0&request=getfeature"
            "&typename=classification&outputformat=CSVTEXT"
        ),
        "payload": Payload.TABLE,
        "base_key": "sis",
        "format": "csv",
        "archive_format": None,
        "data_format": "csv",
        "echelle": "nationale",
        "department_param": None,
        "params": {},
        "revision": "georisques-sis-csv",
    },
    "gaspar-bulk": {
        "label": "Procédures administratives GASPAR (bulk ZIP)",
        "endpoint": "https://files.georisques.fr/GASPAR/gaspar.zip",
        "payload": Payload.TABLE,
        "base_key": "gaspar",
        "format": "zip",
        "archive_format": "zip",
        "data_format": "csv",
        "echelle": "nationale",
        "department_param": None,
        "params": {},
        "revision": "georisques-gaspar-zip",
    },
}

_METADATA_COMMON = {
    "provider": "Géorisques / BRGM-MTE",
    "platform": "georisques.gouv.fr API v1",
    "license": "Licence Ouverte 2.0",
}

# Raw upstream fields per entry — what the REST_TABLE rows carry. The
# normalisation to radon_class / rga_class / RiskConstraint lives downstream.
_SCHEMAS: dict[str, dict[str, str]] = {
    "gaspar-risques": {
        "code_insee": "str",
        "risques_detail": "json",
    },
    "radon": {
        "code_insee": "str",
        "classe_potentiel": "str",
    },
    "sismicite": {
        "code_insee": "str",
        "code_zone": "str",
        "libelle_commune": "str",
        "zone_sismicite": "str",
        "libelle_zone": "str",
    },
    "rga": {
        "codeExposition": "str",
        "exposition": "str",
        "alea": "str",
    },
    "tri-zonage": {
        "code_national_tri": "str",
        "libelle_tri": "str",
    },
    "ssp": {
        "casias": "json",
        "instructions": "json",
        "conclusions_sis": "json",
        "conclusions_sup": "json",
    },
    "rga-bulk": {
        "codeExposition": "str",
        "exposition": "str",
        "alea": "str",
        "geometry": "geometry",
    },
    "tri-bulk": {
        "code_national_tri": "str",
        "libelle_tri": "str",
        "geometry": "geometry",
    },
    "sis-bulk": {
        "classification": "str",
        "identifiant": "str",
        "code_insee": "str",
    },
    "gaspar-bulk": {
        "code_insee": "str",
        "risques_detail": "json",
    },
}


class GeorisquesSource(DeclarativeSource):
    """Géorisques risk signals exposed as a GISPulse declarative source."""

    name = "georisques"
    domain = SourceDomain.ENVIRONNEMENT
    payload = Payload.TABLE
    jurisdiction = "FR"

    def entries(self) -> list[SourceEntryRef]:
        return [
            *(self._entry_ref(entry_id) for entry_id in _ENTRIES),
            *(self._bulk_entry_ref(entry_id) for entry_id in _BULK_ENTRIES),
        ]

    def _entry_ref(self, entry_id: str) -> SourceEntryRef:
        spec = _ENTRIES[entry_id]
        params: dict[str, Any] = {"pagination": dict(spec["pagination"])}
        if spec["static_query"]:
            params["query"] = dict(spec["static_query"])
        return SourceEntryRef(
            id=entry_id,
            name=spec["label"],
            access=AccessSpec(
                protocol=AccessProtocol.REST_TABLE,
                endpoint=f"{GEORISQUES_BASE_URL}{spec['path']}",
                params=params,
                format="application/json",
            ),
            # The data depends on a runtime spatial key, so there is no
            # source-wide revision token (see revision()).
            revision_token=None,
            domain=self.domain,
            payload=self.payload,
            jurisdiction=self.jurisdiction,
            metadata={
                **_METADATA_COMMON,
                "query_key": spec["query_key"],
                "query_scope": spec["scope"],
            },
        )

    def _bulk_entry_ref(self, entry_id: str) -> SourceEntryRef:
        spec = _BULK_ENTRIES[entry_id]
        base_key = spec["base_key"]
        fmt = spec["format"]
        echelle = spec["echelle"]
        department_param = spec["department_param"]
        catalog_endpoint = (
            f"{GEORISQUES_DOWNLOAD_SERVICE}/formats/{fmt}/{base_key}"
            f"?echelle={echelle}"
        )
        if department_param:
            default_dept = spec["params"]["departement"]
            catalog_endpoint = (
                f"{catalog_endpoint}&{department_param}={default_dept}"
            )
        return SourceEntryRef(
            id=entry_id,
            name=spec["label"],
            access=AccessSpec(
                protocol=AccessProtocol.DOWNLOAD,
                endpoint=spec["endpoint"],
                params=dict(spec["params"]),
                format="application/zip" if fmt == "zip" else "text/csv",
            ),
            revision_token=None,
            domain=self.domain,
            payload=spec["payload"],
            jurisdiction=self.jurisdiction,
            metadata={
                "provider": "Géorisques / BRGM-MTE",
                "platform": "Géorisques téléchargement",
                "download_service": GEORISQUES_DOWNLOAD_SERVICE,
                "download_index_endpoint": catalog_endpoint,
                "base_key": base_key,
                "format": fmt,
                "archive_format": spec["archive_format"],
                "data_format": spec["data_format"],
                "echelle": echelle,
                "department_param": department_param,
            },
        )

    def access_for(
        self,
        entry_id: str,
        *,
        code_insee: str | None = None,
        latlon: str | None = None,
        local_path: str | None = None,
        s3_uri: str | None = None,
        s3_key: str | None = None,
    ) -> AccessSpec:
        """Build a per-query :class:`AccessSpec` for one spatial unit.

        The declarative entry carries the endpoint and its static query; this
        helper folds in the runtime spatial key (``code_insee`` *or*
        ``latlon``, whichever the entry declares) and optional materialisation
        destinations (local JSONL path or S3/Garage object). No network — it
        only shapes the spec the orchestrator hands to ``RestTableFetcher``.
        """
        entry = self._entry(entry_id)
        query_key = entry.metadata["query_key"]
        value = code_insee if query_key == "code_insee" else latlon
        if value is None:
            raise ValueError(
                f"entry {entry_id!r} filters by {query_key!r}; "
                f"pass {query_key}=<value>"
            )

        params = dict(entry.access.params)
        for nested_key in ("query", "pagination"):
            nested = params.get(nested_key)
            if isinstance(nested, dict):
                params[nested_key] = dict(nested)
        params["query"] = {**params.get("query", {}), query_key: value}
        if local_path is not None:
            params["local_path"] = local_path
        if s3_uri is not None:
            params["s3_uri"] = s3_uri
        if s3_key is not None:
            params["s3_key"] = s3_key
        return replace(entry.access, params=params)

    def schema(self, entry_id: str) -> dict[str, str]:
        self._entry(entry_id)  # validates the id
        return dict(_SCHEMAS[entry_id])

    def revision(self, entry_id: str) -> str | None:
        """No cheap source-wide freshness token.

        Géorisques has no dataset-level ``last_modified`` and the data is
        keyed on a runtime spatial parameter, so there is nothing to probe
        without a full per-unit fetch. The guide says ``revision()`` must
        stay cheap, so it returns ``None`` (= freshness unknown) and the
        source watcher skips it.
        """
        self._entry(entry_id)  # validates the id
        if entry_id in _BULK_ENTRIES:
            return str(_BULK_ENTRIES[entry_id]["revision"])
        return None
