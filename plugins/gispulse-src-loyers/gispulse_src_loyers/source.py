"""French rent indicators and rental tension DataSource.

The source exposes the latest verified data.gouv.fr CSV files for the 2025
``Carte des loyers`` release, plus the 2025 TLV zoning file for the
regulatory rental-tension signal. It is intentionally declarative:
GISPulse TABLE_FILE fetchers own HTTP, materialization and lazy scans.
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

_LOYERS_SOURCE_PAGE = (
    "https://www.data.gouv.fr/datasets/"
    "carte-des-loyers-indicateurs-de-loyers-dannonce-par-commune-en-2025"
)
_TLV_SOURCE_PAGE = (
    "https://www.data.gouv.fr/datasets/liste-des-communes-selon-le-zonage-tlv-1"
)

_TABLE_PARAMS = {"table_format": "csv", "delimiter": ";", "decimal": ","}
_TLV_TABLE_PARAMS = {"table_format": "csv", "delimiter": ";"}

_LOYERS_SCHEMA = {
    "id_zone": "str",
    "INSEE_C": "str",
    "LIBGEO": "str",
    "EPCI": "str",
    "DEP": "str",
    "REG": "str",
    "loypredm2": "float",
    "lwr.IPm2": "float",
    "upr.IPm2": "float",
    "TYPPRED": "str",
    "nbobs_com": "int",
    "nbobs_mail": "int",
    "R2_adj": "float",
}

_TLV_SCHEMA = {
    "CODGEO25": "str",
    "DEP": "str",
    "LIBGEO": "str",
    "Code EPCI": "str",
    "Libell\u00e9 EPCI": "str",
    "Zonage TLV 2013": "str",
    "Zonage TLV 2023": "str",
    "Zonage TLV post d\u00e9cret 22/12/2025": "str",
}


@dataclass(frozen=True)
class _EntrySpec:
    label: str
    endpoint: str
    revision: str
    resource_id: str
    resource_title: str
    source_page: str
    source_dataset: str
    millesime: str
    last_modified: str
    schema: dict[str, str]
    segment: str | None = None
    domain: SourceDomain = SourceDomain.STATISTIQUE
    params: dict[str, str] | None = None
    license: str = "License Not Specified (data.gouv.fr)"
    attribution: str | None = (
        "Estimations ANIL, a partir des donnees du Groupe SeLoger et de leboncoin"
    )
    join_key: str = "INSEE_C"


_LOYERS_BASE = (
    "https://static.data.gouv.fr/resources/"
    "carte-des-loyers-indicateurs-de-loyers-dannonce-par-commune-en-2025"
)
_TLV_BASE = (
    "https://static.data.gouv.fr/resources/liste-des-communes-selon-le-zonage-tlv-1"
)

_ENTRY_SPECS: dict[str, _EntrySpec] = {
    "loyers_appartement_2025": _EntrySpec(
        label="Carte des loyers 2025 - appartement",
        endpoint=f"{_LOYERS_BASE}/20251211-145010/pred-app-mef-dhup.csv",
        revision=(
            "data-gouv-loyers-2025-55b34088-0964-415f-9df7-d87dd98a09be-"
            "2025-12-11T14:50:11"
        ),
        resource_id="55b34088-0964-415f-9df7-d87dd98a09be",
        resource_title="Indicateurs de loyer appartement",
        source_page=_LOYERS_SOURCE_PAGE,
        source_dataset="Carte des loyers 2025",
        millesime="2025",
        last_modified="2025-12-11T14:50:11",
        schema=_LOYERS_SCHEMA,
        segment="appartement",
    ),
    "loyers_appartement_t1_t2_2025": _EntrySpec(
        label="Carte des loyers 2025 - appartement 1 ou 2 pieces",
        endpoint=f"{_LOYERS_BASE}/20251211-144934/pred-app12-mef-dhup.csv",
        revision=(
            "data-gouv-loyers-2025-14a1fe11-b2d1-49b3-9f6b-83d12df9482c-"
            "2025-12-11T14:58:19"
        ),
        resource_id="14a1fe11-b2d1-49b3-9f6b-83d12df9482c",
        resource_title="Indicateur de loyer appartement de 1 ou 2 pieces",
        source_page=_LOYERS_SOURCE_PAGE,
        source_dataset="Carte des loyers 2025",
        millesime="2025",
        last_modified="2025-12-11T14:58:19",
        schema=_LOYERS_SCHEMA,
        segment="appartement_t1_t2",
    ),
    "loyers_appartement_t3_plus_2025": _EntrySpec(
        label="Carte des loyers 2025 - appartement 3 pieces ou plus",
        endpoint=f"{_LOYERS_BASE}/20251211-144951/pred-app3-mef-dhup.csv",
        revision=(
            "data-gouv-loyers-2025-5e3b28a4-cf56-43a3-ae79-43cceeb27f8c-"
            "2025-12-11T14:49:52"
        ),
        resource_id="5e3b28a4-cf56-43a3-ae79-43cceeb27f8c",
        resource_title="Indicateur de loyer appartement de 3 pieces ou plus",
        source_page=_LOYERS_SOURCE_PAGE,
        source_dataset="Carte des loyers 2025",
        millesime="2025",
        last_modified="2025-12-11T14:49:52",
        schema=_LOYERS_SCHEMA,
        segment="appartement_t3_plus",
    ),
    "loyers_maison_2025": _EntrySpec(
        label="Carte des loyers 2025 - maison",
        endpoint=f"{_LOYERS_BASE}/20251211-145039/pred-mai-mef-dhup.csv",
        revision=(
            "data-gouv-loyers-2025-129f764d-b613-44e4-952c-5ff50a8c9b73-"
            "2025-12-11T14:50:40"
        ),
        resource_id="129f764d-b613-44e4-952c-5ff50a8c9b73",
        resource_title="Indicateurs de loyer maison",
        source_page=_LOYERS_SOURCE_PAGE,
        source_dataset="Carte des loyers 2025",
        millesime="2025",
        last_modified="2025-12-11T14:50:40",
        schema=_LOYERS_SCHEMA,
        segment="maison",
    ),
    "zone_tendue_tlv_2025": _EntrySpec(
        label="Zonage TLV 2025 - communes tendues et non tendues",
        endpoint=f"{_TLV_BASE}/20251230-094759/zonage-tlv-decret-22-dec-2025.csv",
        revision=(
            "data-gouv-tlv-2025-efe71da1-15f8-4526-bcb8-5b9a9419c58c-"
            "2025-12-30T09:48:00"
        ),
        resource_id="efe71da1-15f8-4526-bcb8-5b9a9419c58c",
        resource_title="Zonage TLV",
        source_page=_TLV_SOURCE_PAGE,
        source_dataset="Liste des communes selon le zonage TLV",
        millesime="2025",
        last_modified="2025-12-30T09:48:00",
        schema=_TLV_SCHEMA,
        segment=None,
        domain=SourceDomain.REGLEMENTAIRE,
        params=_TLV_TABLE_PARAMS,
        license="Licence Ouverte 2.0",
        attribution=None,
        join_key="CODGEO25",
    ),
}


class LoyersSource(DeclarativeSource):
    """Rent level indicators and rental tension tables by commune."""

    name = "loyers"
    domain = SourceDomain.STATISTIQUE
    payload = Payload.TABLE
    jurisdiction = "FR"

    def entries(self) -> list[SourceEntryRef]:
        return [
            SourceEntryRef(
                id=entry_id,
                name=spec.label,
                access=AccessSpec(
                    protocol=AccessProtocol.TABLE_FILE,
                    endpoint=spec.endpoint,
                    params=dict(spec.params or _TABLE_PARAMS),
                    format="text/csv",
                ),
                revision_token=spec.revision,
                domain=spec.domain,
                payload=Payload.TABLE,
                jurisdiction=self.jurisdiction,
                metadata=self._metadata(spec),
            )
            for entry_id, spec in _ENTRY_SPECS.items()
        ]

    def schema(self, entry_id: str) -> dict[str, str]:
        self._entry(entry_id)  # validates the id
        return dict(_ENTRY_SPECS[entry_id].schema)

    def revision(self, entry_id: str) -> str | None:
        self._entry(entry_id)  # validates the id
        return _ENTRY_SPECS[entry_id].revision

    @staticmethod
    def _metadata(spec: _EntrySpec) -> dict[str, object]:
        metadata: dict[str, object] = {
            "provider": "Ministere de la Transition ecologique / ANIL",
            "platform": "data.gouv.fr static resources",
            "dataset": spec.source_dataset,
            "resource_title": spec.resource_title,
            "resource_id": spec.resource_id,
            "source_page": spec.source_page,
            "license": spec.license,
            "millesime": spec.millesime,
            "last_modified": spec.last_modified,
            "update_cadence": "annual",
            "table_format": "csv",
            "delimiter": ";",
            "join_key": spec.join_key,
            "geography_date": "2025-01-01",
        }
        if spec.segment:
            metadata.update(
                {
                    "segment": spec.segment,
                    "metric": "loypredm2",
                    "lower_interval_field": "lwr.IPm2",
                    "upper_interval_field": "upr.IPm2",
                    "quality_fields": ("TYPPRED", "nbobs_com", "nbobs_mail", "R2_adj"),
                    "scope": "France hors Mayotte",
                }
            )
        else:
            metadata.update(
                {
                    "signal": "zone_tendue",
                    "decret": "Decret n. 2025-1267 du 22 decembre 2025",
                    "current_zoning_field": "Zonage TLV post d\u00e9cret 22/12/2025",
                    "previous_zoning_fields": ("Zonage TLV 2013", "Zonage TLV 2023"),
                }
            )
        if spec.attribution:
            metadata["attribution"] = spec.attribution
        return metadata
