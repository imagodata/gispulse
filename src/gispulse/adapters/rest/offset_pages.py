"""Bounded, counted offset pagination for GeoJSON REST services.

A changing remote dataset is not a snapshot: counts before and after, stable
identifiers and transfer-limit checks detect common incompleteness, not updates
that preserve the count. No partial collection is returned on failure.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class OffsetPagination:
    """Explicit recipe; no server-specific pagination guessed by the fetcher."""

    offset_param: str
    limit_param: str
    page_size: int
    max_pages: int
    max_features: int
    id_field: str
    count_query: dict[str, Any]
    count_key: str = "count"
    cursor_policy: str = "returned_count"

    @classmethod
    def from_params(cls, raw: Mapping[str, Any]) -> OffsetPagination:
        if not isinstance(raw, Mapping) or raw.get("mode") != "offset":
            raise ValueError("REST_PAGINATION_INVALID: mode must be offset")
        integers = {}
        for name in ("page_size", "max_pages", "max_features"):
            value = raw.get(name)
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise ValueError(f"REST_PAGINATION_INVALID: positive integer {name} required")
            integers[name] = value
        names = {}
        for name in ("offset_param", "limit_param", "id_field", "count_key"):
            value = raw.get(name, "count" if name == "count_key" else None)
            if not isinstance(value, str) or not value:
                raise ValueError(f"REST_PAGINATION_INVALID: {name} required")
            names[name] = value
        count_query = raw.get("count_query")
        if not isinstance(count_query, dict) or not count_query:
            raise ValueError("REST_PAGINATION_INVALID: explicit count_query required")
        if names["offset_param"] == names["limit_param"]:
            raise ValueError("REST_PAGINATION_INVALID: offset and limit must differ")
        policy = raw.get("cursor_policy", "returned_count")
        if policy not in ("returned_count", "arcgis_page_window"):
            raise ValueError("REST_PAGINATION_INVALID: unsupported cursor_policy")
        return cls(**names, **integers, count_query=dict(count_query), cursor_policy=policy)


def _count(payload: dict[str, Any], key: str) -> int:
    if not isinstance(payload, dict):
        raise ValueError("REST_COUNT_INVALID: object payload required")
    count = payload.get(key)
    if "error" in payload or not isinstance(count, int) or isinstance(count, bool) or count < 0:
        raise ValueError("REST_COUNT_INVALID: nonnegative count required; inspect service error")
    return count


def collect_offset_pages(
    spec: OffsetPagination,
    query: dict[str, Any],
    request: Callable[[dict[str, Any]], dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Collect only a complete counted response, or fail without publishing a prefix."""
    if spec.offset_param in query or spec.limit_param in query:
        raise ValueError("REST_PAGINATION_INVALID: offset/limit belong in pagination recipe")
    count_query = {**query, **spec.count_query}
    expected = _count(request(count_query), spec.count_key)
    if expected > spec.max_features:
        raise ValueError("REST_FEATURE_LIMIT: narrow extent or explicitly raise max_features")
    features: list[dict[str, Any]] = []
    seen: set[str | int] = set()
    pages = 0
    more = expected > 0
    while more:
        if pages >= spec.max_pages:
            raise ValueError("REST_PAGE_LIMIT: narrow extent or explicitly raise max_pages")
        payload = request(
            {
                **query,
                spec.offset_param: (
                    pages * spec.page_size
                    if spec.cursor_policy == "arcgis_page_window"
                    else len(features)
                ),
                spec.limit_param: spec.page_size,
            }
        )
        pages += 1
        if not isinstance(payload, dict):
            raise ValueError("REST_PAGE_INVALID: object payload required")
        page = payload.get("features")
        if (
            payload.get("type") != "FeatureCollection"
            or not isinstance(page, list)
            or "error" in payload
        ):
            raise ValueError("REST_PAGE_INVALID: GeoJSON FeatureCollection required")
        if len(page) > spec.page_size or len(features) + len(page) > expected:
            raise ValueError("REST_COUNT_CHANGED: response exceeds declared count or page size")
        for feature in page:
            if not isinstance(feature, dict) or not isinstance(feature.get("properties"), dict):
                raise ValueError("REST_FEATURE_INVALID: feature properties required")
            identifier = feature["properties"].get(spec.id_field)
            if (
                not isinstance(identifier, (str, int))
                or isinstance(identifier, bool)
                or identifier == ""
            ):
                raise ValueError("REST_ID_MISSING: stable per-feature identity required")
            if identifier in seen:
                raise ValueError("REST_DUPLICATE_ID: overlapping/repeated pages; inspect ordering")
            seen.add(identifier)
            features.append(feature)
        properties = payload.get("properties")
        if properties is not None and not isinstance(properties, dict):
            raise ValueError("REST_TRANSFER_FLAG_INVALID: object properties required")
        flags = [
            payload.get("exceededTransferLimit"),
            (properties or {}).get("exceededTransferLimit"),
        ]
        flags = [flag for flag in flags if flag is not None]
        if any(not isinstance(flag, bool) for flag in flags) or len(set(flags)) > 1:
            raise ValueError("REST_TRANSFER_FLAG_INVALID: inconsistent completion metadata")
        if spec.cursor_policy == "arcgis_page_window":
            # Spatial-index filtering may yield short or empty windows. Advance
            # by the requested window and use the service completion flag.
            more = bool(flags and flags[0])
        else:
            more = len(features) < expected
            if not page or (flags and flags[0] != more):
                raise ValueError(
                    "REST_INCOMPLETE: transfer limit disagrees with counted collection"
                )
    if len(features) != expected:
        raise ValueError("REST_INCOMPLETE: final collection differs from expected count")
    if _count(request(count_query), spec.count_key) != expected:
        raise ValueError("REST_COUNT_CHANGED: source changed during pagination; retry")
    return features, {
        "page_count": pages,
        "expected_count": expected,
        "complete": True,
        "consistency": "counts_and_unique_ids_verified_not_snapshot",
    }
