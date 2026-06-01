"""RNB DataSource over the public RNB building API.

The Referentiel National des Batiments (RNB) is the building identity pivot:
its API returns stable ``rnb_id`` values, geometry/point, status, addresses,
external identifiers and cadastral-plot intersections. This plugin stays
declarative and exposes query-scoped REST_TABLE entries; core fetchers own
HTTP, pagination and materialization.
"""

from __future__ import annotations

from collections.abc import Sequence
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

RNB_API_BASE_URL = "https://rnb-api.beta.gouv.fr"
RNB_BUILDINGS_ENDPOINT = f"{RNB_API_BASE_URL}/api/alpha/buildings/"
RNB_PLOT_ENDPOINT = f"{RNB_API_BASE_URL}/api/alpha/buildings/plot/{{plot_id}}/"
RNB_ADDRESS_ENDPOINT = f"{RNB_API_BASE_URL}/api/alpha/buildings/address/"

_DEFAULT_DEPARTEMENT = "63"
_DEFAULT_INSEE_CODE = "63113"
_DEFAULT_BBOX = "3.0885,45.7943,3.0895,45.7950"
_DEFAULT_PLOT_ID = "63113000MT0158"
_DEFAULT_BAN_KEY = "63113_2615_00089"
_DEFAULT_ADDRESS = "89 rue Lecuelle, 63100 Clermont-Ferrand"
_DEFAULT_LIMIT = 100
_MAX_LIMIT = 100

_PAGINATION = {
    "data_key": "results",
    "next_key": "next",
    "max_pages": 10,
    "max_rows": 1000,
}

_COMMON_METADATA = {
    "provider": "RNB / IGN - Fabrique des Geocommuns",
    "platform": "rnb-api.beta.gouv.fr API alpha",
    "license": "Licence Ouverte 2.0",
    "default_departement": _DEFAULT_DEPARTEMENT,
    "default_insee_code": _DEFAULT_INSEE_CODE,
    "identity_key": "rnb_id",
    "join_keys": ("rnb_id", "addresses.id", "plots.id"),
    "geometry_key": "shape",
    "point_key": "point",
}

_ENTRIES: dict[str, dict[str, Any]] = {
    "buildings-bbox": {
        "label": "RNB buildings by bounding box",
        "endpoint": RNB_BUILDINGS_ENDPOINT,
        "query_kind": "bbox",
        "params": {
            "query": {
                "bbox": _DEFAULT_BBOX,
                "limit": _DEFAULT_LIMIT,
                "withPlots": 1,
            },
            "pagination": dict(_PAGINATION),
        },
        "metadata": {
            "filter_fields": (
                "bbox",
                "insee_code",
                "status",
                "cle_interop_ban",
                "withPlots",
            ),
            "default_bbox": _DEFAULT_BBOX,
        },
    },
    "buildings-parcelle": {
        "label": "RNB buildings by cadastral plot",
        "endpoint": RNB_PLOT_ENDPOINT,
        "query_kind": "parcelle",
        "params": {
            "plot_id": _DEFAULT_PLOT_ID,
            "query": {"limit": _DEFAULT_LIMIT},
            "pagination": dict(_PAGINATION),
        },
        "metadata": {
            "filter_fields": ("plot_id",),
            "default_plot_id": _DEFAULT_PLOT_ID,
            "plot_match_note": (
                "RNB ranks buildings by geometric cover ratio over the plot."
            ),
        },
    },
    "buildings-address": {
        "label": "RNB buildings by BAN address key",
        "endpoint": RNB_ADDRESS_ENDPOINT,
        "query_kind": "address",
        "params": {
            "query": {
                "cle_interop_ban": _DEFAULT_BAN_KEY,
                "limit": _DEFAULT_LIMIT,
            },
            "pagination": dict(_PAGINATION),
        },
        "metadata": {
            "filter_fields": ("q", "cle_interop_ban", "min_score"),
            "default_address": _DEFAULT_ADDRESS,
            "default_cle_interop_ban": _DEFAULT_BAN_KEY,
        },
    },
}

_SCHEMA = {
    "rnb_id": "str",
    "status": "str",
    "point": "geojson-point",
    "shape": "geojson-geometry",
    "addresses": "list[json]",
    "addresses.id": "str",
    "addresses.source": "str",
    "addresses.street_number": "str",
    "addresses.street": "str",
    "addresses.city_zipcode": "str",
    "addresses.city_insee_code": "str",
    "ext_ids": "list[json]",
    "ext_ids.id": "str",
    "ext_ids.source": "str",
    "is_active": "bool",
    "plots": "list[json]",
    "plots.id": "str",
    "bdg_cover_ratio": "float",
    "marked_as_correct_by": "list[json]",
}


def _copy_params(params: dict[str, Any]) -> dict[str, Any]:
    copied = dict(params)
    for nested_key in ("query", "pagination"):
        nested = copied.get(nested_key)
        if isinstance(nested, dict):
            copied[nested_key] = dict(nested)
    return copied


def _coerce_limit(limit: int) -> int:
    if limit < 1 or limit > _MAX_LIMIT:
        raise ValueError(f"limit must be between 1 and {_MAX_LIMIT}")
    return int(limit)


def _normalise_bbox(value: str | Sequence[float | int | str]) -> str:
    if isinstance(value, str):
        parts = [part.strip() for part in value.split(",")]
    else:
        parts = [str(part).strip() for part in value]
    if len(parts) != 4 or any(not part for part in parts):
        raise ValueError("bbox must contain four comma-separated coordinates")
    return ",".join(parts)


def _normalise_plot_id(value: object | None) -> str:
    plot_id = str(value or "").strip().upper()
    if not plot_id:
        raise ValueError("plot_id must not be empty")
    return plot_id


def _status_query(status: str | Sequence[str]) -> str:
    if isinstance(status, str):
        return status
    return ",".join(str(item).strip() for item in status if str(item).strip())


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


class RnbSource(DeclarativeSource):
    """RNB building identity records exposed as a GISPulse source."""

    name = "rnb"
    domain = SourceDomain.FONCIER
    payload = Payload.TABLE
    jurisdiction = "FR"

    def entries(self) -> list[SourceEntryRef]:
        return [self._entry_ref(entry_id) for entry_id in _ENTRIES]

    def _entry_ref(self, entry_id: str) -> SourceEntryRef:
        spec = _ENTRIES[entry_id]
        return SourceEntryRef(
            id=entry_id,
            name=spec["label"],
            access=AccessSpec(
                protocol=AccessProtocol.REST_TABLE,
                endpoint=spec["endpoint"],
                params=_copy_params(spec["params"]),
                format="application/json",
            ),
            revision_token=None,
            domain=self.domain,
            payload=self.payload,
            jurisdiction=self.jurisdiction,
            metadata={
                **_COMMON_METADATA,
                **spec["metadata"],
                "query_kind": spec["query_kind"],
            },
        )

    def access_for(
        self,
        entry_id: str,
        *,
        bbox: str | Sequence[float | int | str] | None = None,
        insee_code: str | None = None,
        status: str | Sequence[str] | None = None,
        cle_interop_ban: str | None = None,
        q: str | None = None,
        plot_id: str | None = None,
        with_plots: bool | None = None,
        min_score: float | None = None,
        limit: int = _DEFAULT_LIMIT,
        local_path: str | None = None,
        s3_uri: str | None = None,
        s3_key: str | None = None,
    ) -> AccessSpec:
        """Build a filtered RNB API AccessSpec without network access."""
        limit = _coerce_limit(limit)
        entry = self._entry(entry_id)
        query_kind = entry.metadata["query_kind"]
        params = _copy_params(entry.access.params)
        query = dict(params.get("query") or {})

        if query_kind == "bbox":
            if bbox is not None:
                query["bbox"] = _normalise_bbox(bbox)
            if insee_code is not None:
                query["insee_code"] = insee_code
                if bbox is None:
                    query.pop("bbox", None)
            if cle_interop_ban is not None:
                query["cle_interop_ban"] = cle_interop_ban
                if bbox is None and insee_code is None:
                    query.pop("bbox", None)
            if status is not None:
                query["status"] = _status_query(status)
            if with_plots is not None:
                query["withPlots"] = 1 if with_plots else 0
            query["limit"] = limit
        elif query_kind == "parcelle":
            params["plot_id"] = _normalise_plot_id(plot_id or params.get("plot_id"))
            query = {"limit": limit}
        elif query_kind == "address":
            if cle_interop_ban:
                query.pop("q", None)
                query["cle_interop_ban"] = cle_interop_ban
            elif q:
                query.pop("cle_interop_ban", None)
                query["q"] = q
            if min_score is not None:
                query["min_score"] = float(min_score)
            query["limit"] = limit
        else:  # pragma: no cover - defensive for future local specs
            raise ValueError(f"unsupported RNB query kind: {query_kind!r}")

        params["query"] = query
        params["pagination"]["max_rows"] = limit
        _apply_materialization(
            params,
            local_path=local_path,
            s3_uri=s3_uri,
            s3_key=s3_key,
        )
        return replace(entry.access, params=params)

    def schema(self, entry_id: str) -> dict[str, str]:
        self._entry(entry_id)  # validates the id
        return dict(_SCHEMA)

    def revision(self, entry_id: str) -> str | None:
        self._entry(entry_id)  # validates the id
        return None
