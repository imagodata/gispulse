"""Selection / row-level capabilities — sort, deduplicate, sample, top_n.

These manipulate the *row dimension* of a layer: ordering, deduplicating,
sampling. Geometry and attributes are passed through unchanged.
"""

from __future__ import annotations

import re as _re

import geopandas as gpd

from gispulse.capabilities.base import Capability
from gispulse.capabilities.registry import register


_IDENT_RE = _re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")


def _validate_ident(name: str) -> str:
    if not isinstance(name, str) or not _IDENT_RE.match(name):
        raise ValueError(
            f"Invalid field name '{name}'. Must match [A-Za-z_][A-Za-z0-9_]{{0,62}}.",
        )
    return name


# ---------------------------------------------------------------------------
# sort — order rows by one or more columns
# ---------------------------------------------------------------------------


@register
class SortCapability(Capability):
    """Orders features by one or more attribute columns.

    Example::

        {"by": ["population", "name"], "ascending": [false, true]}
    """

    name = "sort"
    description = "Orders features by one or more attribute columns."

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        by: list[str] | str | None = None,
        ascending: list[bool] | bool = True,
        na_position: str = "last",
        **_,
    ) -> gpd.GeoDataFrame:
        if not by:
            return gdf.copy()
        if na_position not in {"first", "last"}:
            raise ValueError("na_position must be 'first' or 'last'.")
        cols = [by] if isinstance(by, str) else list(by)
        for c in cols:
            _validate_ident(c)
            if c not in gdf.columns:
                raise KeyError(f"sort column '{c}' not in layer.")
        return gdf.sort_values(
            by=cols,
            ascending=ascending,
            na_position=na_position,
        ).reset_index(drop=True)

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "by": {
                    "type": ["array", "string"],
                    "items": {"type": "string"},
                    "description": "Column(s) to sort by.",
                },
                "ascending": {
                    "type": ["array", "boolean"],
                    "items": {"type": "boolean"},
                    "default": True,
                    "description": "Sort direction. Scalar applies to all keys.",
                },
                "na_position": {
                    "type": "string",
                    "enum": ["first", "last"],
                    "default": "last",
                },
            },
            "required": ["by"],
        }


# ---------------------------------------------------------------------------
# deduplicate — drop duplicate rows by attribute keys (with tie-break)
# ---------------------------------------------------------------------------


@register
class DeduplicateCapability(Capability):
    """Drops duplicate rows by attribute key(s), keeping one per group.

    For tie-breaking, set ``order_by`` (sorts before dedup) and ``keep``
    (``"first"`` or ``"last"``).

    Example::

        {"keys": ["code_insee"], "order_by": ["updated_at"], "keep": "last"}
    """

    name = "deduplicate"
    description = "Drops duplicate features by attribute key(s)."

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        keys: list[str] | str | None = None,
        keep: str = "first",
        order_by: list[str] | str | None = None,
        ascending: bool | list[bool] = True,
        **_,
    ) -> gpd.GeoDataFrame:
        if not keys:
            raise ValueError("deduplicate requires 'keys'.")
        if keep not in {"first", "last"}:
            raise ValueError("keep must be 'first' or 'last'.")
        key_cols = [keys] if isinstance(keys, str) else list(keys)
        for c in key_cols:
            _validate_ident(c)
            if c not in gdf.columns:
                raise KeyError(f"dedup key '{c}' not in layer.")

        working = gdf
        if order_by is not None:
            order_cols = [order_by] if isinstance(order_by, str) else list(order_by)
            for c in order_cols:
                _validate_ident(c)
                if c not in gdf.columns:
                    raise KeyError(f"order_by '{c}' not in layer.")
            working = gdf.sort_values(by=order_cols, ascending=ascending)

        return working.drop_duplicates(subset=key_cols, keep=keep).reset_index(drop=True)

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "keys": {
                    "type": ["array", "string"],
                    "items": {"type": "string"},
                    "description": "Column(s) defining duplicate identity.",
                },
                "keep": {
                    "type": "string",
                    "enum": ["first", "last"],
                    "default": "first",
                },
                "order_by": {
                    "type": ["array", "string", "null"],
                    "items": {"type": "string"},
                    "description": "Optional sort applied before dedup (controls which row 'first'/'last' picks).",
                },
                "ascending": {
                    "type": ["array", "boolean"],
                    "default": True,
                },
            },
            "required": ["keys"],
        }


# ---------------------------------------------------------------------------
# random_sample — sample N or fraction of features
# ---------------------------------------------------------------------------


@register
class RandomSampleCapability(Capability):
    """Returns a random sample of features.

    Pass either ``n`` (absolute count) OR ``fraction`` (0-1). ``seed`` makes
    sampling deterministic across runs.

    Example::

        {"fraction": 0.1, "seed": 42}
    """

    name = "random_sample"
    description = "Returns a random sample of features (n or fraction)."

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        n: int | None = None,
        fraction: float | None = None,
        seed: int | None = None,
        replace: bool = False,
        **_,
    ) -> gpd.GeoDataFrame:
        if n is None and fraction is None:
            raise ValueError("random_sample requires 'n' or 'fraction'.")
        if n is not None and fraction is not None:
            raise ValueError("random_sample takes either 'n' or 'fraction', not both.")
        if gdf.empty:
            return gdf.copy()
        if n is not None:
            if n < 0:
                raise ValueError("n must be >= 0.")
            # Cap at len(gdf) to avoid pandas raising when not replacing.
            effective_n = min(n, len(gdf)) if not replace else n
            return gdf.sample(
                n=effective_n, random_state=seed, replace=replace,
            ).reset_index(drop=True)
        if not 0 < fraction <= 1 and not replace:
            raise ValueError("fraction must be in (0, 1] when replace=False.")
        return gdf.sample(
            frac=fraction, random_state=seed, replace=replace,
        ).reset_index(drop=True)

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "n": {
                    "type": ["integer", "null"],
                    "minimum": 0,
                    "description": "Number of features to draw.",
                },
                "fraction": {
                    "type": ["number", "null"],
                    "minimum": 0.0,
                    "maximum": 1.0,
                    "description": "Fraction of features to draw (0-1).",
                },
                "seed": {
                    "type": ["integer", "null"],
                    "description": "Random seed for reproducibility.",
                },
                "replace": {
                    "type": "boolean",
                    "default": False,
                },
            },
        }


# ---------------------------------------------------------------------------
# top_n — keep N first features by an ordering
# ---------------------------------------------------------------------------


@register
class TopNCapability(Capability):
    """Keeps the top-N features by a column value.

    Example::

        {"n": 10, "by": "population", "ascending": false}
    """

    name = "top_n"
    description = "Keeps the top-N features ordered by a column."

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        n: int = 10,
        by: str | list[str] | None = None,
        ascending: bool | list[bool] = False,
        **_,
    ) -> gpd.GeoDataFrame:
        if n is None or n < 0:
            raise ValueError("top_n requires n >= 0.")
        if gdf.empty:
            return gdf.copy()
        if by is None:
            return gdf.head(n).reset_index(drop=True)
        cols = [by] if isinstance(by, str) else list(by)
        for c in cols:
            _validate_ident(c)
            if c not in gdf.columns:
                raise KeyError(f"top_n column '{c}' not in layer.")
        # P1-4 (beta-test 2026-04-24): mergesort is stable, so ties are broken
        # by original input order — making the result deterministic regardless
        # of how the data arrived.
        return (
            gdf.sort_values(by=cols, ascending=ascending, kind="mergesort")
            .head(n)
            .reset_index(drop=True)
        )

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "n": {
                    "type": "integer",
                    "minimum": 0,
                    "default": 10,
                },
                "by": {
                    "type": ["string", "array", "null"],
                    "items": {"type": "string"},
                    "description": "Column(s) to order by. None preserves input order (head).",
                },
                "ascending": {
                    "type": ["boolean", "array"],
                    "default": False,
                    "description": "Sort direction. Default false → top values.",
                },
            },
            "required": ["n"],
        }


# ---------------------------------------------------------------------------
# ELT Lot 2 (#245) — DuckDB / PostGIS SQL push-down strategies
# ---------------------------------------------------------------------------

from gispulse.capabilities import _attribute_sql as _asql  # noqa: E402
from gispulse.capabilities.sql_pushdown import attach_sql_pushdown  # noqa: E402

attach_sql_pushdown(
    SortCapability,
    _asql.build_sort,
    gate=lambda p: bool(p.get("by")),
)
attach_sql_pushdown(
    TopNCapability,
    _asql.build_top_n,
    gate=lambda p: bool(p.get("by")),
)
attach_sql_pushdown(
    DeduplicateCapability,
    _asql.build_deduplicate,
    gate=lambda p: bool(p.get("keys")) and bool(p.get("order_by")),
)
