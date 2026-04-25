"""Temporal capabilities — filter by time window, join by time key.

These operate on a time column of the layer (parsed to pandas datetimes).
Unlike spatial joins, temporal joins do NOT require geometry; they can
join attribute tables on time keys.
"""

from __future__ import annotations

import re as _re

import geopandas as gpd
import pandas as pd

from capabilities.base import Capability
from capabilities.registry import register


_IDENT_RE = _re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")


def _validate_ident(name: str) -> str:
    if not isinstance(name, str) or not _IDENT_RE.match(name):
        raise ValueError(
            f"Invalid column name '{name}'. Must match [A-Za-z_][A-Za-z0-9_]{{0,62}}.",
        )
    return name


def _to_datetime(series: pd.Series) -> pd.Series:
    """Parse a column to datetime, tolerating mixed string/datetime input."""
    if pd.api.types.is_datetime64_any_dtype(series):
        return series
    return pd.to_datetime(series, errors="coerce")


# ---------------------------------------------------------------------------
# temporal_filter — keep rows inside/outside a time window
# ---------------------------------------------------------------------------


@register
class TemporalFilterCapability(Capability):
    """Filters features by a time window on a datetime column.

    ``start`` / ``end`` are ISO-8601 strings (or anything pandas parses).
    ``include_start`` / ``include_end`` toggle inclusive bounds. Pass
    ``invert=True`` to *exclude* the window instead.

    Example::

        {"time_col": "captured_at", "start": "2025-01-01", "end": "2025-12-31"}
        {"time_col": "updated_at", "start": "2026-01-01", "invert": true}
    """

    name = "temporal_filter"
    description = "Filters features by a datetime window on a given column."

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        time_col: str = "",
        start: str | None = None,
        end: str | None = None,
        include_start: bool = True,
        include_end: bool = True,
        invert: bool = False,
        **_,
    ) -> gpd.GeoDataFrame:
        if not time_col:
            raise ValueError("temporal_filter requires 'time_col'.")
        _validate_ident(time_col)
        if time_col not in gdf.columns:
            raise KeyError(f"time_col '{time_col}' not in layer.")
        if start is None and end is None:
            raise ValueError("temporal_filter requires at least one of 'start' or 'end'.")
        if gdf.empty:
            return gdf.copy()

        times = _to_datetime(gdf[time_col])
        mask = pd.Series(True, index=gdf.index)
        if start is not None:
            start_ts = pd.to_datetime(start)
            mask &= times >= start_ts if include_start else times > start_ts
        if end is not None:
            end_ts = pd.to_datetime(end)
            mask &= times <= end_ts if include_end else times < end_ts

        # Rows with unparseable times become NaT → never match either bound,
        # so mask is already False for them. Invert preserves this (NaT stays out).
        if invert:
            mask = ~mask & times.notna()

        return gdf[mask].reset_index(drop=True)

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "time_col": {"type": "string", "description": "Column holding the datetimes."},
                "start": {
                    "type": ["string", "null"],
                    "description": "Lower bound (ISO-8601 or anything pandas parses).",
                },
                "end": {
                    "type": ["string", "null"],
                    "description": "Upper bound (ISO-8601 or anything pandas parses).",
                },
                "include_start": {"type": "boolean", "default": True},
                "include_end": {"type": "boolean", "default": True},
                "invert": {
                    "type": "boolean",
                    "default": False,
                    "description": "Exclude the window instead of keeping it.",
                },
            },
            "required": ["time_col"],
        }


# ---------------------------------------------------------------------------
# temporal_join — asof / exact join on a time key
# ---------------------------------------------------------------------------


_JOIN_STRATEGIES = {"exact", "nearest", "backward", "forward"}


@register
class TemporalJoinCapability(Capability):
    """Joins a reference table by time key — exact or as-of (nearest/backward/forward).

    ``strategy``:
      - ``"exact"``  : inner join on equal timestamps.
      - ``"nearest"``: asof merge, match closest in either direction.
      - ``"backward"``: asof match with the most recent prior timestamp (default for asof).
      - ``"forward"``: asof match with the next future timestamp.

    Optional ``by`` groups the asof match (e.g. match per sensor_id).
    ``tolerance`` bounds the max time delta for asof strategies (e.g. "1h", "30s").

    Example::

        {"ref_layer": "weather", "left_on": "captured_at", "right_on": "ts",
         "strategy": "backward", "tolerance": "1h"}
    """

    name = "temporal_join"
    description = (
        "Joins a reference table by time key — exact or as-of merge with "
        "optional tolerance and per-group matching."
    )

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        ref_gdf: gpd.GeoDataFrame | pd.DataFrame | None = None,
        left_on: str = "",
        right_on: str | None = None,
        strategy: str = "backward",
        by: str | list[str] | None = None,
        tolerance: str | None = None,
        columns: list[str] | None = None,
        **_,
    ) -> gpd.GeoDataFrame:
        if ref_gdf is None:
            raise ValueError("temporal_join requires a reference layer (ref_layer).")
        if not left_on:
            raise ValueError("temporal_join requires 'left_on'.")
        if strategy not in _JOIN_STRATEGIES:
            raise ValueError(f"strategy must be one of {sorted(_JOIN_STRATEGIES)}.")

        right_key = right_on or left_on
        _validate_ident(left_on)
        _validate_ident(right_key)
        if left_on not in gdf.columns:
            raise KeyError(f"left_on '{left_on}' missing from primary layer.")
        if right_key not in ref_gdf.columns:
            raise KeyError(f"right_on '{right_key}' missing from reference layer.")

        # Strip reference geometry — temporal join is attribute-only.
        if isinstance(ref_gdf, gpd.GeoDataFrame):
            ref = pd.DataFrame(ref_gdf.drop(columns=[ref_gdf.geometry.name]))
        else:
            ref = pd.DataFrame(ref_gdf).copy()

        # Slim reference before merging when columns is provided.
        if columns:
            for c in columns:
                _validate_ident(c)
            keep = [right_key]
            if by is not None:
                by_list = [by] if isinstance(by, str) else list(by)
                keep += [b for b in by_list if b not in keep]
            keep += [c for c in columns if c not in keep and c in ref.columns]
            ref = ref[keep]

        left_df = gdf.copy()
        left_df[left_on] = _to_datetime(left_df[left_on])
        ref[right_key] = _to_datetime(ref[right_key])

        if strategy == "exact":
            merged = left_df.merge(
                ref, how="left", left_on=left_on, right_on=right_key,
                suffixes=("", "_ref"),
            )
        else:
            # pandas.merge_asof requires both sides sorted by the key.
            left_sorted = left_df.sort_values(left_on, kind="mergesort")
            ref_sorted = ref.sort_values(right_key, kind="mergesort")
            merge_kwargs = dict(
                left_on=left_on,
                right_on=right_key,
                direction=strategy if strategy != "nearest" else "nearest",
                suffixes=("", "_ref"),
            )
            if by is not None:
                merge_kwargs["by"] = [by] if isinstance(by, str) else list(by)
            if tolerance is not None:
                merge_kwargs["tolerance"] = pd.Timedelta(tolerance)
            merged = pd.merge_asof(left_sorted, ref_sorted, **merge_kwargs)
            # Restore original row order.
            merged = merged.sort_index().reset_index(drop=True)

        # Drop the duplicate right-side key when names differ.
        if right_key != left_on and right_key in merged.columns:
            merged = merged.drop(columns=[right_key])

        return gpd.GeoDataFrame(merged, geometry=gdf.geometry.name, crs=gdf.crs)

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "ref_layer": {
                    "type": "string",
                    "description": "Reference layer alias (resolved to ref_gdf by engine).",
                },
                "left_on": {
                    "type": "string",
                    "description": "Time column in the primary layer.",
                },
                "right_on": {
                    "type": ["string", "null"],
                    "description": "Time column in the reference layer (defaults to left_on).",
                },
                "strategy": {
                    "type": "string",
                    "enum": sorted(_JOIN_STRATEGIES),
                    "default": "backward",
                },
                "by": {
                    "type": ["string", "array", "null"],
                    "items": {"type": "string"},
                    "description": "Optional group column(s) for per-group asof matching.",
                },
                "tolerance": {
                    "type": ["string", "null"],
                    "description": "Pandas Timedelta string ('1h', '30s') bounding asof match distance.",
                },
                "columns": {
                    "type": ["array", "null"],
                    "items": {"type": "string"},
                    "description": "Columns to import from the reference (defaults to all).",
                },
            },
            "required": ["left_on"],
        }
