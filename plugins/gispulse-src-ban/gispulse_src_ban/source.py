"""Base Adresse Nationale DataSource.

BAN is the French address identity pivot: address text, coordinates, commune
codes and BAN identifiers. Runtime lookup uses the Geoplateforme geocoding API,
while bulk ingest uses the official address CSV exports by department.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
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

BAN_GEOCODING_BASE_URL = "https://data.geopf.fr/geocodage"
BAN_SEARCH_ENDPOINT = f"{BAN_GEOCODING_BASE_URL}/search/"
BAN_REVERSE_ENDPOINT = f"{BAN_GEOCODING_BASE_URL}/reverse/"
BAN_BULK_CSV_ENDPOINT = (
    "https://adresse.data.gouv.fr/data/ban/adresses/latest/csv/"
    "adresses-{departement}.csv.gz"
)

_DEFAULT_DEPARTEMENT = "63"
_DEFAULT_CITYCODE = "63113"
_DEFAULT_ADDRESS_QUERY = "1 rue abbe Girard Clermont-Ferrand"
_DEFAULT_LON = 3.087025
_DEFAULT_LAT = 45.777222
_DEFAULT_SEARCH_LIMIT = 5
_DEFAULT_REVERSE_LIMIT = 1
_MAX_API_LIMIT = 20

_API_PAGINATION = {"data_key": "features", "max_pages": 1, "max_rows": 5}
_BULK_PARAMS = {"departement": _DEFAULT_DEPARTEMENT, "table_format": "csv", "delimiter": ";"}
_DEPARTMENT_RE = re.compile(
    r"^(?:0[1-9]|[1-8][0-9]|9[0-5]|2A|2B|97[1-8])$"
)

_COMMON_METADATA = {
    "provider": "Base Adresse Nationale",
    "platform": "data.geopf.fr/geocodage + adresse.data.gouv.fr exports",
    "license": "Licence Ouverte 2.0",
    "default_departement": _DEFAULT_DEPARTEMENT,
    "default_citycode": _DEFAULT_CITYCODE,
    "identity_source": "address",
    "join_keys": ("properties.id", "properties.banId", "properties.citycode"),
    "api_host": "data.geopf.fr/geocodage",
    "legacy_api_host": "api-adresse.data.gouv.fr",
    "legacy_api_status": "deprecated; use Geoplateforme host",
}

_FEATURE_SCHEMA = {
    "type": "str",
    "geometry.type": "str",
    "geometry.coordinates": "list[float]",
    "properties.label": "str",
    "properties.score": "float",
    "properties.housenumber": "str",
    "properties.id": "str",
    "properties.banId": "str",
    "properties.name": "str",
    "properties.postcode": "str",
    "properties.citycode": "str",
    "properties.x": "float",
    "properties.y": "float",
    "properties.city": "str",
    "properties.context": "str",
    "properties.type": "str",
    "properties.importance": "float",
    "properties.depcode": "str",
    "properties.street": "str",
    "properties.distance": "float",
}

_BULK_SCHEMA = {
    "id": "str",
    "id_fantoir": "str",
    "numero": "str",
    "rep": "str",
    "nom_voie": "str",
    "code_postal": "str",
    "code_insee": "str",
    "nom_commune": "str",
    "code_insee_ancienne_commune": "str",
    "nom_ancienne_commune": "str",
    "x": "float",
    "y": "float",
    "lon": "float",
    "lat": "float",
    "type_position": "str",
    "alias": "str",
    "nom_ld": "str",
    "libelle_acheminement": "str",
    "nom_afnor": "str",
    "source_position": "str",
    "source_nom_voie": "str",
    "certification_commune": "bool",
    "cad_parcelles": "str",
}


def _copy_params(params: Mapping[str, Any]) -> dict[str, Any]:
    copied = dict(params)
    for nested_key in ("query", "pagination"):
        nested = copied.get(nested_key)
        if isinstance(nested, dict):
            copied[nested_key] = dict(nested)
    return copied


def _coerce_limit(limit: int) -> int:
    if limit < 1 or limit > _MAX_API_LIMIT:
        raise ValueError(f"limit must be between 1 and {_MAX_API_LIMIT}")
    return int(limit)


def _normalise_departement(value: object | None) -> str:
    code = str(value or "").strip().upper()
    if code.isdecimal() and len(code) == 1:
        code = f"0{code}"
    if not _DEPARTMENT_RE.match(code):
        raise ValueError(f"department code is not supported by BAN exports: {value!r}")
    return code


def _apply_materialization(
    params: dict[str, Any],
    *,
    local_path: str | None,
    s3_uri: str | None,
    s3_key: str | None,
) -> None:
    if local_path is not None:
        params["local_path"] = local_path
    if s3_uri is not None:
        params["s3_uri"] = s3_uri
    if s3_key is not None:
        params["s3_key"] = s3_key


def _api_entry(
    *,
    entry_id: str,
    label: str,
    endpoint: str,
    query: dict[str, Any],
    limit: int,
    metadata: dict[str, Any],
) -> SourceEntryRef:
    return SourceEntryRef(
        id=entry_id,
        name=label,
        access=AccessSpec(
            protocol=AccessProtocol.REST_TABLE,
            endpoint=endpoint,
            params={
                "query": dict(query),
                "pagination": {
                    **_API_PAGINATION,
                    "max_rows": limit,
                },
            },
            format="application/json",
        ),
        revision_token=None,
        domain=SourceDomain.BASE,
        payload=Payload.TABLE,
        jurisdiction="FR",
        metadata={**_COMMON_METADATA, **metadata},
    )


class BanSource(DeclarativeSource):
    """BAN address identity records exposed as a GISPulse source."""

    name = "ban"
    domain = SourceDomain.BASE
    payload = Payload.TABLE
    jurisdiction = "FR"

    def entries(self) -> list[SourceEntryRef]:
        return [
            _api_entry(
                entry_id="addresses-search",
                label="BAN address search",
                endpoint=BAN_SEARCH_ENDPOINT,
                query={
                    "q": _DEFAULT_ADDRESS_QUERY,
                    "citycode": _DEFAULT_CITYCODE,
                    "limit": _DEFAULT_SEARCH_LIMIT,
                },
                limit=_DEFAULT_SEARCH_LIMIT,
                metadata={
                    "query_kind": "search",
                    "default_query": _DEFAULT_ADDRESS_QUERY,
                    "filter_fields": (
                        "q",
                        "citycode",
                        "postcode",
                        "depcode",
                        "type",
                        "lat",
                        "lon",
                    ),
                },
            ),
            _api_entry(
                entry_id="addresses-reverse",
                label="BAN reverse geocoding",
                endpoint=BAN_REVERSE_ENDPOINT,
                query={
                    "lon": _DEFAULT_LON,
                    "lat": _DEFAULT_LAT,
                    "limit": _DEFAULT_REVERSE_LIMIT,
                },
                limit=_DEFAULT_REVERSE_LIMIT,
                metadata={
                    "query_kind": "reverse",
                    "default_lon": _DEFAULT_LON,
                    "default_lat": _DEFAULT_LAT,
                    "filter_fields": ("lon", "lat", "type"),
                },
            ),
            SourceEntryRef(
                id="addresses-departement",
                name="BAN address CSV export by department",
                access=AccessSpec(
                    protocol=AccessProtocol.TABLE_FILE,
                    endpoint=BAN_BULK_CSV_ENDPOINT,
                    params=dict(_BULK_PARAMS),
                    format="text/csv",
                ),
                revision_token=None,
                domain=self.domain,
                payload=self.payload,
                jurisdiction=self.jurisdiction,
                metadata={
                    "provider": "Base Adresse Nationale",
                    "platform": "adresse.data.gouv.fr static exports",
                    "license": "Licence Ouverte 2.0",
                    "default_departement": _DEFAULT_DEPARTEMENT,
                    "granularity": "departement",
                    "format": "csv",
                    "compression": "gzip",
                    "delimiter": ";",
                    "join_keys": ("id", "code_insee", "cad_parcelles"),
                    "geometry_fields": ("lon", "lat", "x", "y"),
                    "endpoint_template": BAN_BULK_CSV_ENDPOINT,
                    "source_page": "https://adresse.data.gouv.fr/data/ban/adresses/latest/csv/",
                },
            ),
        ]

    def access_for(
        self,
        entry_id: str,
        *,
        q: str | None = None,
        citycode: str | None = None,
        postcode: str | None = None,
        depcode: str | None = None,
        address_type: str | None = None,
        lon: float | None = None,
        lat: float | None = None,
        departement: str | None = None,
        limit: int | None = None,
        local_path: str | None = None,
        s3_uri: str | None = None,
        s3_key: str | None = None,
    ) -> AccessSpec:
        """Build a filtered BAN AccessSpec without network access."""
        entry = self._entry(entry_id)
        params = _copy_params(entry.access.params)
        query_kind = entry.metadata.get("query_kind")

        if query_kind == "search":
            params["query"] = self._search_query(
                params["query"],
                q=q,
                citycode=citycode,
                postcode=postcode,
                depcode=depcode,
                address_type=address_type,
                lon=lon,
                lat=lat,
                limit=limit,
            )
            params["pagination"]["max_rows"] = params["query"]["limit"]
        elif query_kind == "reverse":
            params["query"] = self._reverse_query(
                params["query"],
                lon=lon,
                lat=lat,
                address_type=address_type,
                limit=limit,
            )
            params["pagination"]["max_rows"] = params["query"]["limit"]
        elif entry_id == "addresses-departement":
            if departement is not None:
                params["departement"] = _normalise_departement(departement)
        else:  # pragma: no cover - defensive for future local entries
            raise ValueError(f"unsupported BAN entry: {entry_id!r}")

        _apply_materialization(
            params,
            local_path=local_path,
            s3_uri=s3_uri,
            s3_key=s3_key,
        )
        return replace(entry.access, params=params)

    @staticmethod
    def _search_query(
        base_query: Mapping[str, Any],
        *,
        q: str | None,
        citycode: str | None,
        postcode: str | None,
        depcode: str | None,
        address_type: str | None,
        lon: float | None,
        lat: float | None,
        limit: int | None,
    ) -> dict[str, Any]:
        query = dict(base_query)
        if q is not None:
            query["q"] = q
        if citycode is not None:
            query["citycode"] = citycode
        if postcode is not None:
            query["postcode"] = postcode
        if depcode is not None:
            query["depcode"] = depcode
        if address_type is not None:
            query["type"] = address_type
        if lon is not None:
            query["lon"] = float(lon)
        if lat is not None:
            query["lat"] = float(lat)
        query["limit"] = _coerce_limit(limit or int(query["limit"]))
        return query

    @staticmethod
    def _reverse_query(
        base_query: Mapping[str, Any],
        *,
        lon: float | None,
        lat: float | None,
        address_type: str | None,
        limit: int | None,
    ) -> dict[str, Any]:
        query = dict(base_query)
        if lon is not None:
            query["lon"] = float(lon)
        if lat is not None:
            query["lat"] = float(lat)
        if address_type is not None:
            query["type"] = address_type
        query["limit"] = _coerce_limit(limit or int(query["limit"]))
        return query

    def schema(self, entry_id: str) -> dict[str, str]:
        entry = self._entry(entry_id)
        if entry.access.protocol is AccessProtocol.TABLE_FILE:
            return dict(_BULK_SCHEMA)
        return dict(_FEATURE_SCHEMA)

    def revision(self, entry_id: str) -> str | None:
        self._entry(entry_id)  # validates the id
        return None
