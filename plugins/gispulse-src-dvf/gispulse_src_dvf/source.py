"""French DVF DataSource — Demandes de Valeurs Foncières (real-estate transactions).

A :class:`~gispulse.plugins.api.DeclarativeSource`: the plugin only
*declares* its access spec; the actual data read is delegated to the
registered :class:`Fetcher` for ``AccessSpec.protocol``. This package
ships a tiny DVF-specific DuckDB CSV fetcher because the former upstream
GeoParquet mirror has disappeared.

Data: Etalab DVF (a.k.a. "Demande de Valeurs Foncières") — every
real-estate transaction registered in France over a rolling 5-year
window, refreshed semestrially (April / October). Source-of-truth on
``data.gouv.fr``; geo-enriched CSV mirror published by Etalab at
``files.data.gouv.fr/geo-dvf/`` carries the latitude/longitude pair
this plugin's schema declares.

DVF mutations are *attribute rows* keyed on cadastral references, not
vector geometry — hence :data:`Payload.TABLE`. The downstream join to a
cadastral parcel is materialised on the canonical ``id_parcelle``. The
current CSV carries that key directly; the fetcher recreates the older
``prefixe_section`` / ``section`` / ``numero_plan`` columns from it.

.. note::
   :data:`AccessProtocol.REMOTE_TABLE` is still declared deliberately:
   DVF remains a tabular dataset read lazily by DuckDB. The live Etalab
   mirror now exposes gzip-compressed CSV files under
   ``latest/csv/{year}/``. The local fetcher below emits
   ``read_csv_auto`` scans over those files and applies bbox predicates
   against ``longitude`` / ``latitude``.
"""

from __future__ import annotations

import re
import tempfile
from collections.abc import Mapping, Sequence
from typing import Any, ClassVar

from gispulse.core.fetchers import DUCKDB_SCAN_KEY, LazyFetcher
from gispulse.core.plugin_model import FetchMode
from gispulse.plugins.api import (
    AccessProtocol,
    AccessSpec,
    DeclarativeSource,
    Payload,
    ProtocolRegistry,
    SourceDomain,
    SourceEntryRef,
    SourceResult,
)

__all__ = ["DvfSource", "dvf_registry", "resolve_dvf_scan"]

# Etalab geo-DVF mirror — live CSV export. ``latest/csv`` currently
# exposes a rolling 2021..2025 window (verified live on 2026-05-25) with
# both national yearly files and lighter department shards.
_DVF_GEO_CSV_BASE = "https://files.data.gouv.fr/geo-dvf/latest/csv"
_DVF_CSV_YEARS = ("2021", "2022", "2023", "2024", "2025")
_DVF_DEPARTMENT_ENDPOINT_TEMPLATE = (
    "{base}/{year}/departements/{departement}.csv.gz"
)

_DVF_STRING_COLUMNS = (
    "id_mutation",
    "numero_disposition",
    "nature_mutation",
    "adresse_numero",
    "adresse_suffixe",
    "adresse_nom_voie",
    "adresse_code_voie",
    "code_postal",
    "code_commune",
    "nom_commune",
    "code_departement",
    "ancien_code_commune",
    "ancien_nom_commune",
    "id_parcelle",
    "ancien_id_parcelle",
    "numero_volume",
    "lot1_numero",
    "lot2_numero",
    "lot3_numero",
    "lot4_numero",
    "lot5_numero",
    "type_local",
    "code_nature_culture",
    "nature_culture",
    "code_nature_culture_speciale",
    "nature_culture_speciale",
)

_DVF_DEPARTMENT_RE = re.compile(r"^[0-9A-Z]{2,3}$")

# Dataset metadata endpoint on data.gouv.fr — returns JSON with a
# top-level ``last_modified`` ISO-8601 timestamp updated on every
# resource refresh. Cheaper than crawling the resources list, and
# ``HEAD`` is not an option because the static.data.gouv.fr edge
# returns neither ``ETag`` nor ``Last-Modified`` for resource files
# (confirmed live on 2026-05-18).
_DVF_METADATA_API = (
    "https://www.data.gouv.fr/api/1/datasets/"
    "demandes-de-valeurs-foncieres/"
)
_REVISION_TIMEOUT_S = 8.0


def _sql_literal(value: object) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _sql_identifier(value: object) -> str:
    return '"' + str(value).replace('"', '""') + '"'


def _csv_numeric_expr(column: object) -> str:
    return (
        f"try_cast(replace(cast({_sql_identifier(column)} as varchar), ',', '.') "
        "as double)"
    )


def _normalise_years(raw: object) -> tuple[str, ...]:
    if raw is None:
        return _DVF_CSV_YEARS
    if isinstance(raw, str):
        years = (raw,)
    elif isinstance(raw, Sequence):
        years = tuple(str(year) for year in raw)
    else:
        years = (str(raw),)
    for year in years:
        if not (len(year) == 4 and year.isdecimal()):
            raise ValueError(f"invalid DVF CSV year: {year!r}")
    return years


def _normalise_departement(raw: object | None) -> str | None:
    if raw is None:
        return None
    code = str(raw).strip().upper()
    if not code:
        return None
    if code.isdecimal() and len(code) == 1:
        code = f"0{code}"
    if not _DVF_DEPARTMENT_RE.match(code):
        raise ValueError(f"invalid DVF department code: {raw!r}")
    return code


def _extent_parts(extent: Any | None) -> tuple[Any | None, str | None]:
    if isinstance(extent, Mapping):
        bbox = extent.get("bbox", extent.get("extent"))
        departement = (
            extent.get("departement")
            or extent.get("department")
            or extent.get("code_departement")
        )
        return bbox, _normalise_departement(departement)
    return extent, None


def _bbox_predicate(extent: Any | None, lon: object, lat: object) -> str | None:
    if not extent:
        return None
    minx, miny, maxx, maxy = (float(coord) for coord in extent)
    lon_col = _csv_numeric_expr(lon)
    lat_col = _csv_numeric_expr(lat)
    return (
        f"{lon_col} BETWEEN {minx} AND {maxx} "
        f"AND {lat_col} BETWEEN {miny} AND {maxy}"
    )


def _csv_source_sql(urls: Sequence[str]) -> str:
    quoted = [_sql_literal(url) for url in urls]
    if len(quoted) == 1:
        return quoted[0]
    return "[" + ", ".join(quoted) + "]"


class _DvfGeoCsvFetcher(LazyFetcher):
    """DVF-only remote table fetcher over the live geo-DVF CSV mirror."""

    protocol: ClassVar[AccessProtocol] = AccessProtocol.REMOTE_TABLE
    payload: ClassVar[Payload] = Payload.TABLE

    @staticmethod
    def _urls(access: AccessSpec, departement: str | None) -> tuple[str, ...]:
        base = access.endpoint.rstrip("/")
        years = _normalise_years(access.params.get("years"))
        if departement is None:
            raise ValueError(
                "DVF requires an explicit departement for national fan-out; "
                "refusing to fall back to full.csv.gz"
            )
        template = str(
            access.params.get(
                "department_endpoint_template",
                _DVF_DEPARTMENT_ENDPOINT_TEMPLATE,
            )
        )
        return tuple(
            template.format(base=base, year=year, departement=departement)
            for year in years
        )

    @staticmethod
    def _read_csv_auto(urls: Sequence[str]) -> str:
        source = _csv_source_sql(urls)
        return (
            f"read_csv_auto({source}, union_by_name=true, "
            "all_varchar=true)"
        )

    @staticmethod
    def _legacy_projection(scan: str) -> str:
        return (
            f"(SELECT *, "
            f'substr("id_parcelle", 6, 3) AS "prefixe_section", '
            f'substr("id_parcelle", 9, 2) AS "section", '
            f'substr("id_parcelle", 11, 4) AS "numero_plan" '
            f"FROM {scan})"
        )

    def _reference_scan(self, access: AccessSpec, extent: Any | None) -> str:
        bbox, extent_departement = _extent_parts(extent)
        departement = extent_departement or _normalise_departement(
            access.params.get("departement") or access.params.get("department")
        )
        scan = self._legacy_projection(
            self._read_csv_auto(self._urls(access, departement))
        )
        predicate = _bbox_predicate(
            bbox,
            access.params.get("lon", "longitude"),
            access.params.get("lat", "latitude"),
        )
        if predicate is None:
            return scan
        return f"(SELECT * FROM {scan} WHERE {predicate})"

    def _materialize(self, access: AccessSpec, extent: Any | None) -> SourceResult:
        from gispulse.persistence.duckdb_engine import DuckDBSession

        local_path = access.params.get("local_path")
        if not local_path:
            handle = tempfile.NamedTemporaryFile(suffix=".parquet", delete=False)
            handle.close()
            local_path = handle.name

        select = self._reference_scan(access, extent)
        dest = str(local_path).replace("'", "''")
        copy_sql = f"COPY (SELECT * FROM {select}) TO '{dest}' (FORMAT PARQUET)"
        with DuckDBSession() as session:
            session.conn.execute(copy_sql)
        return SourceResult(
            payload=self.payload,
            mode=FetchMode.MATERIALIZE,
            data=local_path,
            extent=tuple(extent) if extent and not isinstance(extent, Mapping) else None,
            metadata={"copy_sql": copy_sql, DUCKDB_SCAN_KEY: select},
        )


def dvf_registry() -> ProtocolRegistry:
    """Return a DVF-local registry whose REMOTE_TABLE slot is the CSV fetcher.

    This intentionally does not mutate the process-wide ``PROTOCOLS``
    registry: core owns the generic GeoParquet ``REMOTE_TABLE`` adapter,
    while DVF is now a single-format CSV source.
    """
    registry = ProtocolRegistry()
    registry.register(_DvfGeoCsvFetcher())
    return registry


def resolve_dvf_scan(
    entry: SourceEntryRef, *, extent: Any | None = None
) -> str:
    """Resolve a DVF entry to its DuckDB CSV scan through the local registry."""
    result = dvf_registry().dispatch_fetch(
        entry.access,
        extent=extent,
        mode=FetchMode.REFERENCE,
    )
    scan = result.metadata.get(DUCKDB_SCAN_KEY) if result.metadata else None
    if not isinstance(scan, str) or not scan:
        raise RuntimeError("DVF CSV fetcher did not return a DuckDB scan")
    return scan


def _probe_revision(api_url: str) -> str | None:
    """Return ``last_modified`` from the data.gouv.fr dataset API.

    Issues a single small GET against the JSON metadata endpoint and
    extracts the dataset-level ``last_modified`` timestamp. The payload
    is on the order of a few KB — comparable to a HEAD in network cost
    once TLS is amortised, and unlike HEAD it carries an actual
    freshness signal for this provider.

    Returns ``None`` — meaning "freshness unknown" — on any network
    error, non-2xx response, malformed JSON, or missing field, so the
    source watcher skips it rather than emitting a spurious change.
    """
    import httpx  # local import — keeps module import network-free

    try:
        resp = httpx.get(
            api_url, timeout=_REVISION_TIMEOUT_S, follow_redirects=True
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception:  # noqa: BLE001 — any transport error ⇒ unknown
        return None
    token = data.get("last_modified") if isinstance(data, dict) else None
    return token if isinstance(token, str) and token else None


class DvfSource(DeclarativeSource):
    """Etalab DVF (real-estate transactions) exposed as a GISPulse source."""

    name = "dvf"
    domain = SourceDomain.STATISTIQUE
    payload = Payload.TABLE
    jurisdiction = "FR"

    def __init__(self, registry: ProtocolRegistry | None = None) -> None:
        super().__init__(registry=registry or dvf_registry())

    def entries(self) -> list[SourceEntryRef]:
        return [
            self._entry_ref(
                "mutations",
                "Mutations DVF (transactions immobilières)",
            ),
        ]

    @staticmethod
    def _entry_ref(entry_id: str, label: str) -> SourceEntryRef:
        return SourceEntryRef(
            id=entry_id,
            name=label,
            access=AccessSpec(
                protocol=AccessProtocol.REMOTE_TABLE,
                endpoint=_DVF_GEO_CSV_BASE,
                params={
                    "years": _DVF_CSV_YEARS,
                    "department_endpoint_template": (
                        _DVF_DEPARTMENT_ENDPOINT_TEMPLATE
                    ),
                    "lat": "latitude",
                    "lon": "longitude",
                },
                format="text/csv",
            ),
            # revision() probes data.gouv.fr live (issue #198); the
            # declared token stays None so nothing hard-codes a stale
            # millésime.
            revision_token=None,
            # Per-entry classification axes (#227, EPIC #226) — repeat
            # the source-level domain/payload/jurisdiction so the
            # worldwide catalogue can index this entry directly without
            # a hop through ``DvfSource``.
            domain=SourceDomain.STATISTIQUE,
            payload=Payload.TABLE,
            jurisdiction="FR",
            metadata={
                "provider": "Etalab",
                "dataset": "Demandes de Valeurs Foncières",
                "mirror": "files.data.gouv.fr/geo-dvf",
                "mirror_format": "csv.gz",
                "update_cadence": "semestrial",
                "license": "Licence Ouverte 2.0",
            },
        )

    def revision(self, entry_id: str) -> str | None:
        """Cheap freshness token for the source watcher (issue #187/#198).

        Issues a single GET against the data.gouv.fr dataset metadata
        API and returns its top-level ``last_modified`` ISO-8601
        timestamp — never a full ``fetch()``. DVF releases are
        dataset-wide (semestrial), so every entry shares one probe;
        ``entry_id`` is only validated here.

        Returns ``None`` when the endpoint is unreachable, returns
        non-2xx, or omits the ``last_modified`` field — the watcher
        treats that as "unknown" and skips it rather than emit a
        spurious ``source.changed``.
        """
        self._entry(entry_id)  # validate the id
        return _probe_revision(_DVF_METADATA_API)

    def schema(self, entry_id: str) -> dict:
        """Normalised attribute schema of a DVF mutation row.

        Field names mirror the Etalab ``geo-dvf`` CSV header, plus the
        three legacy cadastral pivot fields reconstructed from
        ``id_parcelle`` for consumers that were written against the
        former parquet mirror.

        ``id_parcelle`` is the canonical cadastral join key. Downstream
        plugins like ``gispulse-permis`` can still consume the legacy
        split fields when needed; they are derived from ``id_parcelle``.
        """
        self._entry(entry_id)  # validates the id
        return {
            # Mutation identity
            "id_mutation": "str",
            "date_mutation": "date",
            "numero_disposition": "str",
            "nature_mutation": "str",
            # Economics
            "valeur_fonciere": "float",
            # Address
            "adresse_numero": "str",
            "adresse_suffixe": "str",
            "adresse_nom_voie": "str",
            "adresse_code_voie": "str",
            # Local typology
            "type_local": "str",
            "code_type_local": "int",
            "surface_reelle_bati": "float",
            "surface_terrain": "float",
            "nombre_pieces_principales": "int",
            # Lots
            "numero_volume": "str",
            "lot1_numero": "str",
            "lot1_surface_carrez": "float",
            "lot2_numero": "str",
            "lot2_surface_carrez": "float",
            "lot3_numero": "str",
            "lot3_surface_carrez": "float",
            "lot4_numero": "str",
            "lot4_surface_carrez": "float",
            "lot5_numero": "str",
            "lot5_surface_carrez": "float",
            "nombre_lots": "int",
            # Land nature
            "code_nature_culture": "str",
            "nature_culture": "str",
            "code_nature_culture_speciale": "str",
            "nature_culture_speciale": "str",
            # Geography — administrative
            "code_postal": "str",
            "code_commune": "str",
            "nom_commune": "str",
            "code_departement": "str",
            "ancien_code_commune": "str",
            "ancien_nom_commune": "str",
            # Cadastral pivot — the four raw fields and the canonical
            # join key downstream plugins consume.
            "prefixe_section": "str",
            "section": "str",
            "numero_plan": "str",
            "id_parcelle": "str",
            "ancien_id_parcelle": "str",
            # Geography — geocoded (geo-dvf mirror only)
            "longitude": "float",
            "latitude": "float",
        }
