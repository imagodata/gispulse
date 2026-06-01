"""DPE DataSource — diagnostics de performance énergétique (ADEME).

A :class:`DeclarativeSource` over :attr:`AccessProtocol.REST_TABLE` for
the ADEME data-fair API (``data.ademe.fr``). Two entries are declared:

* ``logements-existants`` — DPE logements existants depuis juillet 2021.
* ``logements-neufs``     — DPE logements neufs depuis juillet 2021.

Each entry filters the ADEME dataset at commune or département granularity
via a ``qs`` Lucene query appended to the ``/lines`` endpoint. The runtime
spatial key (``code_insee_ban`` or ``code_departement_ban``) is supplied
per call by the ingestion orchestrator through
:meth:`DpeSource.access_for`.

Endpoint documentation
----------------------
Base: ``https://data.ademe.fr/data-fair/api/v1/datasets``
Lines: ``{base}/{dataset_id}/lines?size=10000&qs=code_insee_ban:{code}``
Metadata: ``{base}/{dataset_id}`` — ``dataUpdatedAt`` carries the freshness
token for :meth:`revision`.

Upstream churn warning: ADEME announces that DPE "Depuis juillet 2021" dataset
URLs/API URLs are being replaced; keep this plugin's dataset IDs under watch:
``https://data.ademe.fr/pages/dpe``.

Pagination: ``{"total": N, "results": [...], "next": "<url>"}``
→ ``pagination = {"data_key": "results", "next_key": "next"}``

Spatial filter: ADEME data-fair uses a Lucene ``qs`` query string.
``access_for`` builds ``params["query"]["qs"] = "code_insee_ban:{code}"``
so the :class:`RestTableFetcher` appends it as ``?qs=code_insee_ban:{code}``.

Key columns (verified live 2026-05-29)
---------------------------------------
Identity:
  ``numero_dpe``                — identifiant DPE unique
  ``date_etablissement_dpe``    — date d'émission
  ``date_fin_validite_dpe``     — date d'expiration (DPE valide 10 ans)

Performance:
  ``etiquette_dpe``             — étiquette DPE (A–G)
  ``etiquette_ges``             — étiquette GES (A–G)
  ``conso_5_usages_ep``         — consommation énergie primaire (kWh EP/an)
  ``conso_5_usages_par_m2_ep``  — conso EP par m² (kWh EP/m²/an)
  ``conso_5_usages_ef``         — consommation énergie finale (kWh EF/an)
  ``emission_ges_5_usages``     — émissions GES (kg CO₂eq/an)
  ``emission_ges_5_usages_par_m2`` — émissions GES par m² (kg CO₂eq/m²/an)

Logement:
  ``surface_habitable_logement`` — surface en m²
  ``type_batiment``             — maison / appartement / immeuble
  ``annee_construction``        — année de construction
  ``periode_construction``      — tranche de construction

Géolocalisation / liaison foncier:
  ``code_insee_ban``            — code commune INSEE (clé de filtre spatiale)
  ``code_departement_ban``      — code département (filtre alternatif)
  ``adresse_ban``               — adresse complète BAN
  ``identifiant_ban``           — identifiant BAN (liaison possible → adresse)
  ``coordonnee_cartographique_x_ban`` — X Lambert-93 (EPSG:2154)
  ``coordonnee_cartographique_y_ban`` — Y Lambert-93 (EPSG:2154)
  ``statut_geocodage``          — qualité du géocodage BAN

.. note::
   Les DPE n'incluent pas de référence cadastrale directe (pas d'``id_parcelle``).
   La liaison parcelle foncier se fait en aval via code_insee_ban + adresse BAN
   (jointure sur le référentiel adresses BAN → cadastre) ou via les coordonnées
   Lambert-93 si le géocodage est de bonne qualité.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import re
from typing import Any

from gispulse.plugins.api import (
    AccessProtocol,
    AccessSpec,
    DeclarativeSource,
    Payload,
    SourceDomain,
    SourceEntryRef,
)

# ADEME data-fair base URL (open-data, no auth required).
_ADEME_BASE_URL = "https://data.ademe.fr/data-fair/api/v1/datasets"
_REVISION_TIMEOUT_S = 8.0
_REST_TIMEOUT_S = 20.0
_REST_MAX_PAGES = 1000
_REST_MAX_TOTAL_SECONDS_S = 600.0
_REST_RETRY_ATTEMPTS = 4
_REST_RETRY_BACKOFF_S = 1.0
_REST_RETRY_BACKOFF_FACTOR = 2.0
_REST_RETRY_STATUSES = [429, 500, 502, 503, 504]

# ADEME data-fair dataset IDs (stable, verified 2026-05-29).
_DATASET_EXISTANTS = "meg-83tjwtg8dyz4vv7h1dqe"
_DATASET_NEUFS = "g3cgx7jb3cmys5voxz1mrm22"

_METROPOLITAN_DEPARTMENT_PATTERN = r"(?:0[1-9]|1\d|2[1-9]|[3-8]\d|9[0-5])"
_CORSICA_DEPARTMENT_PATTERN = r"2[AB]"
_OVERSEAS_DEPARTMENT_PATTERN = r"97[1-8]"

_CODE_INSEE_RE = re.compile(
    rf"(?:{_METROPOLITAN_DEPARTMENT_PATTERN}\d{{3}}"
    rf"|{_CORSICA_DEPARTMENT_PATTERN}\d{{3}}"
    rf"|{_OVERSEAS_DEPARTMENT_PATTERN}\d{{2}})"
)
_CODE_DEPARTEMENT_RE = re.compile(
    rf"(?:{_METROPOLITAN_DEPARTMENT_PATTERN}"
    rf"|{_CORSICA_DEPARTMENT_PATTERN}"
    rf"|{_OVERSEAS_DEPARTMENT_PATTERN})"
)

# Entries: entry_id → dataset config.
# ``filter_field`` names the ADEME column used to filter at commune scope.
# ``dept_field`` is the column to filter at département scope.
_ENTRIES: dict[str, dict[str, Any]] = {
    "logements-existants": {
        "label": "DPE logements existants (depuis juillet 2021)",
        "dataset_id": _DATASET_EXISTANTS,
        "filter_field": "code_insee_ban",
        "dept_field": "code_departement_ban",
        "page_size": 10000,
    },
    "logements-neufs": {
        "label": "DPE logements neufs (depuis juillet 2021)",
        "dataset_id": _DATASET_NEUFS,
        "filter_field": "code_insee_ban",
        "dept_field": "code_departement_ban",
        "page_size": 10000,
    },
}

_METADATA_COMMON = {
    "provider": "ADEME",
    "platform": "data.ademe.fr (data-fair / koumoul)",
    "license": "Licence Ouverte 2.0",
    "license_url": "https://www.etalab.gouv.fr/licence-ouverte-open-licence",
    "api_version": "v1",
    "geo_join_key": "code_insee_ban",
    "geo_join_note": (
        "Pas de référence cadastrale directe. "
        "Liaison foncier en aval : code_insee_ban + adresse BAN → cadastre, "
        "ou coordonnées Lambert-93 (coordonnee_cartographique_x/y_ban) "
        "si le statut_geocodage est satisfaisant."
    ),
    "geometry": {
        "type": "point",
        "x_field": "coordonnee_cartographique_x_ban",
        "y_field": "coordonnee_cartographique_y_ban",
        "crs": "EPSG:2154",
        "quality_field": "statut_geocodage",
        "recommended_quality_value": "adresse géocodée ban à l'adresse",
        "missing_coordinates": "preserve_row_without_geometry",
    },
    "upstream_churn_risk": {
        "status": "decommissioning-announced",
        "notice_url": "https://data.ademe.fr/pages/dpe",
        "risk": (
            "ADEME indique que les dataset URLs/API URLs DPE depuis juillet "
            "2021 changent avec les nouveaux jeux de données; surveiller les "
            "nouveaux identifiants avant décommissionnement des anciens."
        ),
    },
}

# Raw upstream fields per entry, limited to the columns most consumed
# downstream (foncier DPE scoring, permis-check, atlas). The upstream
# schema carries ~200 columns; only the key fields are listed here as
# schema contract — callers may widen the projection themselves.
_SCHEMAS: dict[str, dict[str, str]] = {
    "logements-existants": {
        # identity
        "numero_dpe": "str",
        "numero_dpe_immeuble_associe": "str",
        "date_etablissement_dpe": "str",
        "date_reception_dpe": "str",
        "date_fin_validite_dpe": "str",
        "date_derniere_modification_dpe": "str",
        "modele_dpe": "str",
        "version_dpe": "float",
        "methode_application_dpe": "str",
        "id_rnb": "str",
        "provenance_id_rnb": "str",
        # performance labels
        "etiquette_dpe": "str",
        "etiquette_ges": "str",
        # energy consumption — primary energy
        "conso_5_usages_ep": "float",
        "conso_5_usages_par_m2_ep": "float",
        # energy consumption — final energy
        "conso_5_usages_ef": "float",
        "conso_5_usages_par_m2_ef": "float",
        # GES emissions
        "emission_ges_5_usages": "float",
        "emission_ges_5_usages_par_m2": "float",
        # logement characteristics
        "surface_habitable_logement": "float",
        "surface_habitable_immeuble": "float",
        "type_batiment": "str",
        "annee_construction": "int",
        "periode_construction": "str",
        "nombre_niveau_logement": "int",
        "nombre_niveau_immeuble": "int",
        "nombre_appartement": "int",
        # geography / spatial join
        "code_insee_ban": "str",
        "code_departement_ban": "str",
        "code_region_ban": "str",
        "code_postal_ban": "str",
        "nom_commune_ban": "str",
        "adresse_ban": "str",
        "adresse_complete_brut": "str",
        "identifiant_ban": "str",
        "score_ban": "float",
        "coordonnee_cartographique_x_ban": "float",
        "coordonnee_cartographique_y_ban": "float",
        "_geopoint": "str",
        "statut_geocodage": "str",
    },
    "logements-neufs": {
        # identity
        "numero_dpe": "str",
        "numero_dpe_immeuble_associe": "str",
        "date_etablissement_dpe": "str",
        "date_reception_dpe": "str",
        "date_fin_validite_dpe": "str",
        "date_derniere_modification_dpe": "str",
        "modele_dpe": "str",
        "version_dpe": "float",
        "methode_application_dpe": "str",
        "id_rnb": "str",
        "provenance_id_rnb": "str",
        # performance labels
        "etiquette_dpe": "str",
        "etiquette_ges": "str",
        # energy consumption — primary energy
        "conso_5_usages_ep": "float",
        "conso_5_usages_par_m2_ep": "float",
        # energy consumption — final energy
        "conso_5_usages_ef": "float",
        "conso_5_usages_par_m2_ef": "float",
        # GES emissions
        "emission_ges_5_usages": "float",
        "emission_ges_5_usages_par_m2": "float",
        # logement characteristics
        "surface_habitable_logement": "float",
        "surface_habitable_immeuble": "float",
        "type_batiment": "str",
        "annee_construction": "int",
        "periode_construction": "str",
        "nombre_niveau_logement": "int",
        "nombre_niveau_immeuble": "int",
        "nombre_appartement": "int",
        # geography / spatial join
        "code_insee_ban": "str",
        "code_departement_ban": "str",
        "code_region_ban": "str",
        "code_postal_ban": "str",
        "nom_commune_ban": "str",
        "adresse_ban": "str",
        "adresse_complete_brut": "str",
        "identifiant_ban": "str",
        "score_ban": "float",
        "coordonnee_cartographique_x_ban": "float",
        "coordonnee_cartographique_y_ban": "float",
        "_geopoint": "str",
        "statut_geocodage": "str",
    },
}


def _normalise_code(value: object, *, field: str, pattern: re.Pattern[str]) -> str:
    """Normalise a trusted spatial code before embedding it in a Lucene ``qs``."""
    text = str(value).strip().upper()
    if not pattern.fullmatch(text):
        raise ValueError(f"Invalid {field}: {value!r}")
    return text


def _probe_revision(dataset_id: str) -> str | None:
    """Return ``dataUpdatedAt`` from the ADEME data-fair dataset metadata API.

    Issues a single GET against ``{base}/{dataset_id}`` and extracts the
    ``dataUpdatedAt`` timestamp. Returns ``None`` on any network error, non-2xx
    response, malformed JSON, or missing field — the source watcher treats that
    as "freshness unknown" and skips rather than emitting a spurious change.
    """
    import httpx

    url = f"{_ADEME_BASE_URL}/{dataset_id}"
    try:
        resp = httpx.get(
            url,
            timeout=_REVISION_TIMEOUT_S,
            follow_redirects=True,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception:  # noqa: BLE001 — any transport/parse error ⇒ unknown
        return None
    token = data.get("dataUpdatedAt") if isinstance(data, dict) else None
    return token if isinstance(token, str) and token else None


class DpeSource(DeclarativeSource):
    """ADEME DPE (diagnostics de performance énergétique) GISPulse source.

    Two entries: ``logements-existants`` and ``logements-neufs``. Both use
    ``AccessProtocol.REST_TABLE`` over the ADEME data-fair ``/lines`` API.
    The orchestrator supplies the spatial key at runtime via
    :meth:`access_for`.
    """

    name = "dpe"
    domain = SourceDomain.STATISTIQUE
    payload = Payload.TABLE
    jurisdiction = "FR"

    def entries(self) -> list[SourceEntryRef]:
        return [self._entry_ref(entry_id) for entry_id in _ENTRIES]

    def _entry_ref(self, entry_id: str) -> SourceEntryRef:
        spec = _ENTRIES[entry_id]
        dataset_id = spec["dataset_id"]
        return SourceEntryRef(
            id=entry_id,
            name=spec["label"],
            access=AccessSpec(
                protocol=AccessProtocol.REST_TABLE,
                endpoint=f"{_ADEME_BASE_URL}/{dataset_id}/lines",
                params={
                    "query": {"size": spec["page_size"]},
                    "pagination": {
                        "data_key": "results",
                        "next_key": "next",
                        "max_pages": _REST_MAX_PAGES,
                        "max_total_seconds": _REST_MAX_TOTAL_SECONDS_S,
                    },
                    "timeout": _REST_TIMEOUT_S,
                    "retry": {
                        "max_attempts": _REST_RETRY_ATTEMPTS,
                        "backoff_seconds": _REST_RETRY_BACKOFF_S,
                        "backoff_factor": _REST_RETRY_BACKOFF_FACTOR,
                        "statuses": _REST_RETRY_STATUSES,
                    },
                },
                format="application/json",
            ),
            # revision() probes the ADEME dataset metadata API live.
            revision_token=None,
            domain=self.domain,
            payload=self.payload,
            jurisdiction=self.jurisdiction,
            metadata={
                **deepcopy(_METADATA_COMMON),
                "dataset_id": dataset_id,
                "dataset_url": f"https://data.ademe.fr/datasets/{dataset_id}",
                "filter_field": spec["filter_field"],
                "dept_field": spec["dept_field"],
            },
        )

    def access_for(
        self,
        entry_id: str,
        *,
        code_insee: str | None = None,
        code_departement: str | None = None,
        local_path: str | None = None,
        s3_uri: str | None = None,
        s3_key: str | None = None,
    ) -> AccessSpec:
        """Build a per-query :class:`AccessSpec` for one spatial unit.

        The declarative entry carries the endpoint and its static query;
        this helper folds in the runtime spatial key as a Lucene ``qs``
        filter (``code_insee_ban:{code}`` or ``code_departement_ban:{dept}``),
        which is the filter mechanism of the ADEME data-fair ``/lines`` API.

        At least one of ``code_insee`` or ``code_departement`` must be provided.
        ``code_insee`` takes precedence when both are given.

        No network — it only shapes the :class:`AccessSpec` the orchestrator
        hands to the :class:`RestTableFetcher`.
        """
        entry = self._entry(entry_id)
        spec = _ENTRIES[entry_id]

        if code_insee is not None:
            code = _normalise_code(
                code_insee, field="code_insee", pattern=_CODE_INSEE_RE
            )
            qs_filter = f"{spec['filter_field']}:{code}"
        elif code_departement is not None:
            dept = _normalise_code(
                code_departement,
                field="code_departement",
                pattern=_CODE_DEPARTEMENT_RE,
            )
            qs_filter = f"{spec['dept_field']}:{dept}"
        else:
            raise ValueError(
                f"entry {entry_id!r}: pass code_insee=<code> or "
                "code_departement=<dept>"
            )

        # Deep-copy mutable nested REST_TABLE params to avoid mutating the
        # shared declarative entry as destinations and qs filters are added.
        params = deepcopy(entry.access.params)

        params["query"] = {**params.get("query", {}), "qs": qs_filter}

        if local_path is not None:
            params["local_path"] = local_path
        if s3_uri is not None:
            params["s3_uri"] = s3_uri
        if s3_key is not None:
            params["s3_key"] = s3_key

        return replace(entry.access, params=params)

    def schema(self, entry_id: str) -> dict[str, str]:
        """Key upstream field schema for one DPE entry.

        Only the most-consumed downstream columns are listed. The upstream
        dataset carries ~200 columns; callers that need the full schema
        should query the ADEME data-fair API directly:
        ``{base}/{dataset_id}/schema``.
        """
        self._entry(entry_id)  # validates the id
        return dict(_SCHEMAS[entry_id])

    def revision(self, entry_id: str) -> str | None:
        """Cheap freshness token via the ADEME data-fair dataset metadata API.

        Issues a single GET against ``{base}/{dataset_id}`` and returns the
        ``dataUpdatedAt`` ISO-8601 timestamp — never a full ``fetch()``.
        Returns ``None`` when the endpoint is unreachable or the field is
        absent — the watcher treats that as "unknown" and skips silently.
        """
        entry = self._entry(entry_id)  # validates the id
        dataset_id = entry.metadata["dataset_id"]
        return _probe_revision(dataset_id)
