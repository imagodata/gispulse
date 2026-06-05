"""BDNB DataSource - French national building database.

The BDNB open-data distribution is a set of large downloadable archives:
France-wide exports plus department archives. This source deliberately stays
declarative: it exposes the current public department ZIP archives and leaves
download/materialisation to the registered GISPulse fetchers.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from gispulse.plugins.api import (
    AccessProtocol,
    AccessSpec,
    DeclarativeSource,
    Payload,
    SourceDomain,
    SourceEntryRef,
)

_MILLESIME_LABEL = "2026-02.a"
_MILLESIME_PATH = "2026-02-a"
_REVISION = "bdnb-2026-02-a"
_DEFAULT_DEPARTEMENT = "63"
_BDNB_S3_BASE = (
    f"https://open-data.s3.fr-par.scw.cloud/bdnb_millesime_{_MILLESIME_PATH}"
)

_PUBLISHED_DEPARTEMENTS = (
    *(f"{value:02d}" for value in range(1, 20)),
    *(f"{value:02d}" for value in range(21, 96)),
    "2a",
    "2b",
)
_VALID_DEPARTEMENTS = set(_PUBLISHED_DEPARTEMENTS)
_BUILDING_GROUP_TABLE = "batiment_groupe"
_BUILDING_GROUP_ARCHIVE_MEMBER = f"csv/{_BUILDING_GROUP_TABLE}.csv"


@dataclass(frozen=True)
class _EntrySpec:
    label: str
    protocol: AccessProtocol
    payload: Payload
    archive_kind: str
    data_format: str
    params: dict[str, str]


_ENTRY_SPECS: dict[str, _EntrySpec] = {
    "batiments": _EntrySpec(
        label="BDNB - batiments, archive GPKG departementale",
        protocol=AccessProtocol.DOWNLOAD,
        payload=Payload.VECTOR,
        archive_kind="gpkg",
        data_format="gpkg",
        params={
            "departement": _DEFAULT_DEPARTEMENT,
            "archive_format": "zip",
            "data_format": "gpkg",
        },
    ),
    "batiments_tables": _EntrySpec(
        label="BDNB - batiment_groupe, table CSV departementale",
        protocol=AccessProtocol.TABLE_FILE,
        payload=Payload.TABLE,
        archive_kind="csv",
        data_format="csv",
        params={
            "departement": _DEFAULT_DEPARTEMENT,
            "archive_format": "zip",
            "table_format": "csv",
            "archive_member": _BUILDING_GROUP_ARCHIVE_MEMBER,
        },
    ),
}

_SOURCE_TABLES = (
    _BUILDING_GROUP_TABLE,
)

_SCHEMA = {
    "batiment_groupe_id": "str",
    "code_departement_insee": "str",
    "code_commune_insee": "str",
    "parcelle_unifiee_id": "str",
    "s_geom_groupe": "float",
    "hauteur_mean": "float",
    "dpe_arrete_2021_annee_construction_dpe": "int",
    "dpe_arrete_2021_periode_construction_dpe": "str",
    "dpe_arrete_2021_classe_conso_energie": "str",
    "geometry": "geometry",
}


def _department_archive_endpoint(kind: str) -> str:
    return (
        f"{_BDNB_S3_BASE}/millesime_{_MILLESIME_PATH}_dep{{departement}}/"
        f"open_data_millesime_{_MILLESIME_PATH}_dep{{departement}}_{kind}.zip"
    )


def _normalise_departement(raw: object | None) -> str:
    value = str(raw or "").strip()
    if value.upper() in {"2A", "2B"}:
        value = value.lower()
    if value.isdigit() and len(value) <= 2:
        value = value.zfill(2)
    if value in _VALID_DEPARTEMENTS:
        return value
    raise ValueError(f"invalid BDNB department code: {raw!r}")


class BdnbSource(DeclarativeSource):
    """BDNB building-level open-data archives."""

    name = "bdnb"
    domain = SourceDomain.FONCIER
    payload = Payload.VECTOR
    jurisdiction = "FR"

    def entries(self) -> list[SourceEntryRef]:
        return [
            self._entry_ref(entry_id, spec)
            for entry_id, spec in _ENTRY_SPECS.items()
        ]

    def access_for(
        self,
        entry_id: str,
        *,
        departement: str | None = None,
        department: str | None = None,
        code_departement: str | None = None,
        local_path: str | None = None,
        s3_uri: str | None = None,
        s3_key: str | None = None,
    ) -> AccessSpec:
        """Return a per-department AccessSpec without mutating the catalog."""
        entry = self._entry(entry_id)
        selected = (
            departement
            if departement is not None
            else department
            if department is not None
            else code_departement
        )
        params = dict(entry.access.params)
        params["departement"] = _normalise_departement(
            selected or params.get("departement")
        )
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
        return _REVISION

    @staticmethod
    def _entry_ref(entry_id: str, spec: _EntrySpec) -> SourceEntryRef:
        metadata = {
            "provider": "CSTB",
            "dataset": "Base de Donnees Nationale des Batiments",
            "platform": "bdnb.io / data.gouv.fr",
            "license": "Licence Ouverte 2.0",
            "millesime": _MILLESIME_LABEL,
            "update_cadence": "semestrial",
            "archive_format": "zip",
            "data_format": spec.data_format,
            "archive_scope": (
                "single_member"
                if "archive_member" in spec.params
                else "full_archive"
            ),
            "department_param": "departement",
            "default_departement": _DEFAULT_DEPARTEMENT,
            "published_departements": _PUBLISHED_DEPARTEMENTS,
            "join_key": "batiment_groupe_id",
            "geometry_key": "geometry",
        }
        if "archive_member" in spec.params:
            metadata.update(
                {
                    "archive_member": spec.params["archive_member"],
                    "source_tables": _SOURCE_TABLES,
                    "table_name": _BUILDING_GROUP_TABLE,
                }
            )
        return SourceEntryRef(
            id=entry_id,
            name=spec.label,
            access=AccessSpec(
                protocol=spec.protocol,
                endpoint=_department_archive_endpoint(spec.archive_kind),
                params=dict(spec.params),
                format="application/zip",
            ),
            revision_token=_REVISION,
            domain=SourceDomain.FONCIER,
            payload=spec.payload,
            jurisdiction="FR",
            metadata=metadata,
        )
