"""Schema / attribute manipulation capabilities.

These capabilities operate primarily on the *table* (attribute) part of a
GeoDataFrame — adding, dropping, renaming, casting columns or joining
non-spatial reference tables. The geometry column is preserved unchanged.
"""

from __future__ import annotations

import re as _re

import geopandas as gpd
import numpy as np
import pandas as pd

from capabilities.base import Capability
from capabilities.registry import register


_IDENT_RE = _re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")

_DTYPE_ALIASES: dict[str, str] = {
    "int": "Int64",
    "integer": "Int64",
    "int32": "Int32",
    "int64": "Int64",
    "bigint": "Int64",
    "float": "float64",
    "float32": "float32",
    "float64": "float64",
    "double": "float64",
    "real": "float32",
    "str": "string",
    "string": "string",
    "text": "string",
    "varchar": "string",
    "bool": "boolean",
    "boolean": "boolean",
    "datetime": "datetime64[ns]",
    "timestamp": "datetime64[ns]",
    "date": "datetime64[ns]",
}


def _validate_ident(name: str, *, kind: str = "field") -> str:
    if not isinstance(name, str) or not _IDENT_RE.match(name):
        raise ValueError(
            f"Invalid {kind} name '{name}'. Must match [A-Za-z_][A-Za-z0-9_]{{0,62}}.",
        )
    return name


def _resolve_dtype(spec: str) -> str:
    key = spec.strip().lower()
    if key in _DTYPE_ALIASES:
        return _DTYPE_ALIASES[key]
    # Allow direct pandas dtype strings.
    return spec


# ---------------------------------------------------------------------------
# add_field — append a column with a constant or null value
# ---------------------------------------------------------------------------


@register
class AddFieldCapability(Capability):
    """Adds one or more columns initialised with a constant value.

    Use ``calculate`` for derived/computed values; ``add_field`` is for
    blank columns that downstream rules will populate.

    Example::

        {"fields": [
            {"name": "status", "dtype": "string", "default": "pending"},
            {"name": "score", "dtype": "float64"}
        ]}
    """

    name = "add_field"
    description = "Adds one or more attribute columns with a default value."

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        fields: list[dict] | None = None,
        overwrite: bool = False,
        **_,
    ) -> gpd.GeoDataFrame:
        if not fields:
            return gdf.copy()
        result = gdf.copy()
        geom_col = result.geometry.name if hasattr(result, "geometry") else None
        for spec in fields:
            col = _validate_ident(spec.get("name", ""))
            if col == geom_col:
                raise ValueError(f"Cannot overwrite the geometry column '{col}'.")
            if col in result.columns and not overwrite:
                continue
            dtype = _resolve_dtype(spec.get("dtype", "string"))
            default = spec.get("default")
            try:
                series = pd.Series([default] * len(result), index=result.index, dtype=dtype)
            except (TypeError, ValueError):
                # dtype not compatible with default — fall back to object then cast.
                series = pd.Series([default] * len(result), index=result.index).astype(dtype)
            result[col] = series
        return result

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "fields": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "dtype": {"type": "string", "default": "string"},
                            "default": {},
                        },
                        "required": ["name"],
                    },
                    "description": "List of fields to create.",
                },
                "overwrite": {
                    "type": "boolean",
                    "default": False,
                    "description": "Overwrite existing columns instead of skipping.",
                },
            },
            "required": ["fields"],
        }


# ---------------------------------------------------------------------------
# drop_field — remove columns
# ---------------------------------------------------------------------------


@register
class DropFieldCapability(Capability):
    """Drops one or more attribute columns. Geometry column is protected.

    Example::

        {"fields": ["scratch_a", "scratch_b"], "ignore_missing": true}
    """

    name = "drop_field"
    description = "Drops one or more attribute columns (geometry is protected)."

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        fields: list[str] | None = None,
        ignore_missing: bool = True,
        **_,
    ) -> gpd.GeoDataFrame:
        if not fields:
            return gdf.copy()
        geom_col = gdf.geometry.name
        to_drop: list[str] = []
        for col in fields:
            _validate_ident(col)
            if col == geom_col:
                raise ValueError(f"Cannot drop the geometry column '{col}'.")
            if col not in gdf.columns:
                if ignore_missing:
                    continue
                raise KeyError(f"Field '{col}' not in layer.")
            to_drop.append(col)
        if not to_drop:
            return gdf.copy()
        return gdf.drop(columns=to_drop)

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "fields": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Columns to drop.",
                },
                "ignore_missing": {
                    "type": "boolean",
                    "default": True,
                    "description": "Skip columns that don't exist instead of raising.",
                },
            },
            "required": ["fields"],
        }


# ---------------------------------------------------------------------------
# select_columns — keep only the listed columns (+ geometry)
# ---------------------------------------------------------------------------


@register
class SelectColumnsCapability(Capability):
    """Keeps only the listed columns. Geometry column is always preserved.

    Inverse of ``drop_field`` — useful to slim a layer before export.

    Example::

        {"fields": ["id", "name", "population"]}
    """

    name = "select_columns"
    description = "Keeps only the listed attribute columns (geometry preserved)."

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        fields: list[str] | None = None,
        **_,
    ) -> gpd.GeoDataFrame:
        if not fields:
            return gdf.copy()
        geom_col = gdf.geometry.name
        keep = [geom_col] + [
            _validate_ident(c) for c in fields if c != geom_col and c in gdf.columns
        ]
        # Preserve order: geometry stays where it was.
        ordered = [c for c in gdf.columns if c in keep]
        return gdf[ordered].copy()

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "fields": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Columns to keep (geometry is always kept).",
                },
            },
            "required": ["fields"],
        }


# ---------------------------------------------------------------------------
# rename_field — rename one or more columns
# ---------------------------------------------------------------------------


@register
class RenameFieldCapability(Capability):
    """Renames one or more attribute columns.

    Example::

        {"mapping": {"pop": "population", "nm": "name"}}
    """

    name = "rename_field"
    description = "Renames attribute columns via a {old: new} mapping."

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        mapping: dict[str, str] | None = None,
        ignore_missing: bool = True,
        **_,
    ) -> gpd.GeoDataFrame:
        if not mapping:
            return gdf.copy()
        geom_col = gdf.geometry.name
        valid: dict[str, str] = {}
        for old, new in mapping.items():
            _validate_ident(old, kind="field")
            _validate_ident(new, kind="field")
            if old == geom_col or new == geom_col:
                raise ValueError(f"Cannot rename the geometry column '{geom_col}'.")
            if old not in gdf.columns:
                if ignore_missing:
                    continue
                raise KeyError(f"Field '{old}' not in layer.")
            if new in gdf.columns and new != old:
                raise ValueError(f"Target name '{new}' collides with existing column.")
            valid[old] = new
        if not valid:
            return gdf.copy()
        return gdf.rename(columns=valid)

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "mapping": {
                    "type": "object",
                    "additionalProperties": {"type": "string"},
                    "description": "Mapping of {old_name: new_name}.",
                },
                "ignore_missing": {
                    "type": "boolean",
                    "default": True,
                },
            },
            "required": ["mapping"],
        }


# ---------------------------------------------------------------------------
# cast_field — change a column's dtype
# ---------------------------------------------------------------------------


@register
class CastFieldCapability(Capability):
    """Casts attribute columns to a new dtype.

    Uses pandas-friendly aliases (``int``, ``float``, ``string``, ``boolean``,
    ``datetime``) plus direct pandas dtype strings (``Int64``, ``float32``…).
    Failed conversions raise unless ``errors='coerce'`` is set, in which case
    invalid values become NA.

    Example::

        {"casts": {"id": "int", "score": "float64", "active": "boolean"},
         "errors": "coerce"}
    """

    name = "cast_field"
    description = "Casts one or more attribute columns to a target dtype."

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        casts: dict[str, str] | None = None,
        errors: str = "raise",
        **_,
    ) -> gpd.GeoDataFrame:
        if not casts:
            return gdf.copy()
        if errors not in ("raise", "coerce", "ignore"):
            raise ValueError("errors must be one of 'raise', 'coerce', 'ignore'.")
        geom_col = gdf.geometry.name
        result = gdf.copy()
        for col, dtype_spec in casts.items():
            _validate_ident(col)
            if col == geom_col:
                raise ValueError(f"Cannot cast the geometry column '{col}'.")
            if col not in result.columns:
                if errors == "raise":
                    raise KeyError(f"Field '{col}' not in layer.")
                continue
            target = _resolve_dtype(dtype_spec)
            try:
                if target.startswith("datetime"):
                    result[col] = pd.to_datetime(result[col], errors=errors)
                elif target in {"Int64", "Int32", "Int16", "Int8"}:
                    result[col] = pd.to_numeric(result[col], errors=errors).astype(target)
                elif target in {"float64", "float32"}:
                    result[col] = pd.to_numeric(result[col], errors=errors).astype(target)
                else:
                    result[col] = result[col].astype(target)
            except (TypeError, ValueError):
                if errors == "raise":
                    raise
                if errors == "coerce":
                    result[col] = pd.Series([pd.NA] * len(result), index=result.index, dtype=target)
                # errors == 'ignore' → leave the column untouched.
        return result

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "casts": {
                    "type": "object",
                    "additionalProperties": {"type": "string"},
                    "description": "Mapping of {column: target_dtype}.",
                },
                "errors": {
                    "type": "string",
                    "enum": ["raise", "coerce", "ignore"],
                    "default": "raise",
                },
            },
            "required": ["casts"],
        }


# ---------------------------------------------------------------------------
# attribute_join — non-spatial table join with a reference layer
# ---------------------------------------------------------------------------


_JOIN_HOWS = {"left", "right", "inner", "outer"}


@register
class AttributeJoinCapability(Capability):
    """Non-spatial join with a reference layer on a key column.

    Sister capability of ``spatial_join``: enriches the primary layer with
    columns from a reference table (e.g. a CSV-loaded INSEE referential)
    matched on an attribute key. Geometry of the primary layer is preserved.

    Example::

        {"ref_layer": "insee_communes",
         "left_on": "code_insee", "right_on": "INSEE_COM",
         "columns": ["nom", "population"], "prefix": "insee_"}
    """

    name = "attribute_join"
    description = (
        "Non-spatial join with a reference layer on a key column "
        "(left/right/inner/outer)."
    )

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        ref_gdf: gpd.GeoDataFrame | pd.DataFrame | None = None,
        left_on: str = "",
        right_on: str | None = None,
        how: str = "left",
        columns: list[str] | None = None,
        prefix: str = "",
        suffix: str = "",
        **_,
    ) -> gpd.GeoDataFrame:
        if ref_gdf is None:
            raise ValueError("attribute_join requires a reference layer (ref_layer).")
        if not left_on:
            raise ValueError("attribute_join requires 'left_on'.")
        if how not in _JOIN_HOWS:
            raise ValueError(f"how must be one of {sorted(_JOIN_HOWS)}.")
        right_key = right_on or left_on
        _validate_ident(left_on)
        _validate_ident(right_key)
        if left_on not in gdf.columns:
            raise KeyError(f"left_on '{left_on}' missing from primary layer.")
        if right_key not in ref_gdf.columns:
            raise KeyError(f"right_on '{right_key}' missing from reference layer.")

        # Drop reference geometry if present — attribute join only.
        ref_attrs: pd.DataFrame
        if isinstance(ref_gdf, gpd.GeoDataFrame):
            ref_attrs = pd.DataFrame(ref_gdf.drop(columns=[ref_gdf.geometry.name]))
        else:
            ref_attrs = pd.DataFrame(ref_gdf).copy()

        # Restrict to requested columns + the key.
        if columns:
            for c in columns:
                _validate_ident(c)
            keep = [right_key] + [c for c in columns if c in ref_attrs.columns and c != right_key]
            ref_attrs = ref_attrs[keep]

        # Apply prefix/suffix to imported columns (excluding the key itself).
        if prefix or suffix:
            renames = {
                c: f"{prefix}{c}{suffix}"
                for c in ref_attrs.columns
                if c != right_key
            }
            ref_attrs = ref_attrs.rename(columns=renames)

        merged = gdf.merge(
            ref_attrs,
            how=how,
            left_on=left_on,
            right_on=right_key,
            suffixes=("", "_ref"),
        )
        # If the right_on key differs from left_on, drop the duplicate after merge.
        if right_key != left_on and right_key in merged.columns:
            merged = merged.drop(columns=[right_key])
        # Re-wrap as GeoDataFrame only when the primary input was a GeoDataFrame
        # — accept plain DataFrame inputs for non-spatial attribute enrichment
        # (P0-2 from the 2026-04-24 beta-test).
        if isinstance(gdf, gpd.GeoDataFrame):
            return gpd.GeoDataFrame(merged, geometry=gdf.geometry.name, crs=gdf.crs)
        return merged

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
                    "description": "Key column in the primary layer.",
                },
                "right_on": {
                    "type": ["string", "null"],
                    "description": "Key column in the reference layer (defaults to left_on).",
                },
                "how": {
                    "type": "string",
                    "enum": sorted(_JOIN_HOWS),
                    "default": "left",
                },
                "columns": {
                    "type": ["array", "null"],
                    "items": {"type": "string"},
                    "description": "Columns to import from the reference layer (defaults to all).",
                },
                "prefix": {"type": "string", "default": ""},
                "suffix": {"type": "string", "default": ""},
            },
            "required": ["left_on"],
        }


# ---------------------------------------------------------------------------
# pivot — long → wide reshape on attribute columns
# ---------------------------------------------------------------------------


_AGG_FUNCS = {"first", "last", "mean", "sum", "min", "max", "count", "median"}


@register
class PivotCapability(Capability):
    """Reshapes long-format rows into wide format (one column per category).

    ``geom_strategy`` controls how the per-group geometry is picked when
    several rows in the same index group have different geometries:
      - ``"first"`` (default): first occurrence; backward-compatible
      - ``"union"``: union of all per-group geometries
      - ``"raise_if_differs"``: raise ValueError when geometries diverge

    Example::

        # rows = (parcel_id, year, value) → cols = (parcel_id, value_2020, value_2021…)
        {"index": ["parcel_id"], "columns": "year", "values": "value",
         "aggfunc": "sum", "fill_value": 0, "geom_strategy": "raise_if_differs"}
    """

    name = "pivot"
    description = "Reshapes long-format rows into wide format on a category column."

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        index: list[str] | str | None = None,
        columns: str = "",
        values: str | list[str] = "",
        aggfunc: str = "first",
        fill_value=None,
        geom_strategy: str = "first",
        **_,
    ) -> gpd.GeoDataFrame:
        if not index:
            raise ValueError("pivot requires 'index'.")
        if not columns:
            raise ValueError("pivot requires 'columns'.")
        if not values:
            raise ValueError("pivot requires 'values'.")
        if aggfunc not in _AGG_FUNCS:
            raise ValueError(f"aggfunc must be one of {sorted(_AGG_FUNCS)}.")
        if geom_strategy not in {"first", "union", "raise_if_differs"}:
            raise ValueError(
                "geom_strategy must be 'first', 'union', or 'raise_if_differs'.",
            )

        idx_cols = [index] if isinstance(index, str) else list(index)
        val_cols = [values] if isinstance(values, str) else list(values)
        for c in idx_cols + val_cols + [columns]:
            _validate_ident(c)
            if c not in gdf.columns:
                raise KeyError(f"pivot column '{c}' not in layer.")

        geom_col = gdf.geometry.name

        # P1-3 (beta-test 2026-04-24): explicit geometry strategy per group.
        if geom_strategy == "raise_if_differs":
            divergent = (
                gdf.assign(_geom_wkb=gdf.geometry.apply(lambda g: g.wkb if g else None))
                .groupby(idx_cols)["_geom_wkb"]
                .nunique()
            )
            offenders = divergent[divergent > 1]
            if not offenders.empty:
                raise ValueError(
                    f"pivot: index group {tuple(offenders.index[0])!r} "
                    f"contains divergent geometries — set geom_strategy='first' "
                    f"or 'union' to allow.",
                )
            geom_lookup = (
                gdf[idx_cols + [geom_col]]
                .drop_duplicates(subset=idx_cols, keep="first")
                .set_index(idx_cols)
            )
        elif geom_strategy == "union":
            geom_lookup = (
                gdf[idx_cols + [geom_col]]
                .dissolve(by=idx_cols, as_index=True)
            )
        else:  # "first"
            geom_lookup = (
                gdf[idx_cols + [geom_col]]
                .drop_duplicates(subset=idx_cols, keep="first")
                .set_index(idx_cols)
            )

        wide = pd.pivot_table(
            pd.DataFrame(gdf.drop(columns=[geom_col])),
            index=idx_cols,
            columns=columns,
            values=val_cols,
            aggfunc=aggfunc,
            fill_value=fill_value,
        )
        # Flatten MultiIndex columns when several values were aggregated.
        if isinstance(wide.columns, pd.MultiIndex):
            wide.columns = [
                f"{val}_{cat}" if val else str(cat)
                for val, cat in wide.columns.to_flat_index()
            ]
        else:
            wide.columns = [str(c) for c in wide.columns]

        wide = wide.reset_index()
        merged = wide.merge(geom_lookup, on=idx_cols, how="left")
        return gpd.GeoDataFrame(merged, geometry=geom_col, crs=gdf.crs)

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "index": {
                    "type": ["array", "string"],
                    "items": {"type": "string"},
                    "description": "Column(s) identifying each output row.",
                },
                "columns": {
                    "type": "string",
                    "description": "Column whose unique values become new columns.",
                },
                "values": {
                    "type": ["array", "string"],
                    "items": {"type": "string"},
                    "description": "Column(s) supplying the cell values.",
                },
                "aggfunc": {
                    "type": "string",
                    "enum": sorted(_AGG_FUNCS),
                    "default": "first",
                },
                "fill_value": {
                    "description": "Value used to fill missing combinations.",
                },
                "geom_strategy": {
                    "type": "string",
                    "enum": ["first", "union", "raise_if_differs"],
                    "default": "first",
                    "description": (
                        "How to pick the per-group geometry when rows in the "
                        "same index group have divergent geometries."
                    ),
                },
            },
            "required": ["index", "columns", "values"],
        }


# ---------------------------------------------------------------------------
# unpivot — wide → long reshape (a.k.a. melt)
# ---------------------------------------------------------------------------


@register
class UnpivotCapability(Capability):
    """Reshapes wide-format columns into a long-format (variable, value) pair.

    Example::

        {"id_vars": ["parcel_id"], "value_vars": ["pop_2020", "pop_2021", "pop_2022"],
         "var_name": "year", "value_name": "population"}
    """

    name = "unpivot"
    description = "Reshapes wide-format columns into long-format (variable, value)."

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        id_vars: list[str] | str | None = None,
        value_vars: list[str] | str | None = None,
        var_name: str = "variable",
        value_name: str = "value",
        **_,
    ) -> gpd.GeoDataFrame:
        if id_vars is None:
            raise ValueError("unpivot requires 'id_vars' (use [] to melt all).")
        _validate_ident(var_name)
        _validate_ident(value_name)

        idv = [id_vars] if isinstance(id_vars, str) else list(id_vars)
        vv = (
            [value_vars] if isinstance(value_vars, str)
            else (list(value_vars) if value_vars else None)
        )
        for c in idv + (vv or []):
            _validate_ident(c)
            if c not in gdf.columns:
                raise KeyError(f"unpivot column '{c}' not in layer.")

        geom_col = gdf.geometry.name
        # Always keep the geometry as an id_var so we can re-wrap below.
        keep_idv = list(dict.fromkeys(idv + [geom_col]))
        long = pd.melt(
            pd.DataFrame(gdf),
            id_vars=keep_idv,
            value_vars=vv,
            var_name=var_name,
            value_name=value_name,
        )
        return gpd.GeoDataFrame(long, geometry=geom_col, crs=gdf.crs)

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "id_vars": {
                    "type": ["array", "string"],
                    "items": {"type": "string"},
                    "description": "Column(s) preserved as identifiers across melted rows.",
                },
                "value_vars": {
                    "type": ["array", "string", "null"],
                    "items": {"type": "string"},
                    "description": "Columns to melt (defaults to all non-id columns).",
                },
                "var_name": {"type": "string", "default": "variable"},
                "value_name": {"type": "string", "default": "value"},
            },
            "required": ["id_vars"],
        }


# ---------------------------------------------------------------------------
# lookup_table — value mapping with default fallback
# ---------------------------------------------------------------------------


@register
class LookupTableCapability(Capability):
    """Maps values of a source column through a static lookup dictionary.

    Output goes into ``target_col`` (defaults to overwrite ``source_col``).
    Unmatched values fall back to ``default``: when ``default`` is the literal
    string ``"__source__"``, the original value is kept (passthrough).

    Example::

        # Map INSEE department code → region name; keep code if unknown
        {"source_col": "dep", "target_col": "region",
         "mapping": {"75": "IDF", "13": "PACA", "69": "ARA"},
         "default": "Unknown"}
    """

    name = "lookup_table"
    description = "Maps a column's values through a lookup dict with a default fallback."

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        source_col: str = "",
        mapping: dict | None = None,
        target_col: str | None = None,
        default=None,
        **_,
    ) -> gpd.GeoDataFrame:
        if not source_col:
            raise ValueError("lookup_table requires 'source_col'.")
        if not mapping:
            raise ValueError("lookup_table requires a non-empty 'mapping'.")
        _validate_ident(source_col)
        target = target_col or source_col
        _validate_ident(target)
        if source_col not in gdf.columns:
            raise KeyError(f"source_col '{source_col}' not in layer.")

        result = gdf.copy()
        # Coerce mapping keys to strings for stable lookup against mixed dtypes,
        # but also allow direct dtype match for numeric keys.
        mapped = result[source_col].map(mapping)
        if default == "__source__":
            mapped = mapped.where(mapped.notna(), result[source_col])
        elif default is not None:
            mapped = mapped.where(mapped.notna(), default)
        result[target] = mapped
        return result

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "source_col": {"type": "string"},
                "target_col": {
                    "type": ["string", "null"],
                    "description": "Output column (defaults to source_col, overwriting).",
                },
                "mapping": {
                    "type": "object",
                    "description": "Mapping dict {source_value: target_value}.",
                },
                "default": {
                    "description": (
                        "Fallback for unmatched values. Use the literal string "
                        "'__source__' to keep the original value."
                    ),
                },
            },
            "required": ["source_col", "mapping"],
        }


# ---------------------------------------------------------------------------
# coalesce_fields — first non-null value across columns
# ---------------------------------------------------------------------------


@register
class CoalesceFieldsCapability(Capability):
    """Picks the first non-null value across a list of source columns.

    SQL ``COALESCE`` semantics. Result goes into ``target_col``.

    Example::

        {"sources": ["preferred_name", "official_name", "code"],
         "target_col": "display_name"}
    """

    name = "coalesce_fields"
    description = "Returns the first non-null value across a list of columns."

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        sources: list[str] | None = None,
        target_col: str = "",
        **_,
    ) -> gpd.GeoDataFrame:
        if not sources:
            raise ValueError("coalesce_fields requires 'sources'.")
        if not target_col:
            raise ValueError("coalesce_fields requires 'target_col'.")
        _validate_ident(target_col)
        for c in sources:
            _validate_ident(c)
            if c not in gdf.columns:
                raise KeyError(f"source column '{c}' not in layer.")

        geom_col = gdf.geometry.name
        if target_col == geom_col:
            raise ValueError(f"Cannot overwrite the geometry column '{geom_col}'.")

        result = gdf.copy()
        # bfill across selected columns row-wise picks the first non-null.
        result[target_col] = result[sources].bfill(axis=1).iloc[:, 0]
        return result

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "sources": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Columns to coalesce, in priority order.",
                },
                "target_col": {"type": "string"},
            },
            "required": ["sources", "target_col"],
        }


# ---------------------------------------------------------------------------
# case_when — conditional field computation
# ---------------------------------------------------------------------------


@register
class CaseWhenCapability(Capability):
    """Conditional field computation — SQL CASE WHEN equivalent.

    Each ``case`` is ``{"when": "<pandas-query expression>", "then": <value>}``.
    Cases are evaluated top-to-bottom; the first matching ``when`` wins.
    Rows matching no case fall back to ``else_``.

    Expressions go through the same AST validator as ``calculate`` to block
    arbitrary code execution. Constants in ``then`` / ``else_`` are written
    verbatim (they are never executed as code).

    Example::

        {"target_col": "tier",
         "cases": [
             {"when": "population > 100000", "then": "large"},
             {"when": "population > 10000",  "then": "medium"}
         ],
         "else_": "small"}
    """

    name = "case_when"
    description = "Conditional field computation (SQL CASE WHEN)."

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        target_col: str = "",
        cases: list[dict] | None = None,
        else_=None,
        **_,
    ) -> gpd.GeoDataFrame:
        if not target_col:
            raise ValueError("case_when requires 'target_col'.")
        if not cases:
            raise ValueError("case_when requires at least one 'cases' entry.")
        _validate_ident(target_col)

        geom_col = gdf.geometry.name
        if target_col == geom_col:
            raise ValueError(f"Cannot overwrite the geometry column '{geom_col}'.")

        # Local import to keep schema.py free of vector.py at import time.
        from capabilities.vector import _validate_query_expression  # type: ignore

        result = gdf.copy()
        # Initialise with else_ so unmatched rows get the fallback.
        out = pd.Series([else_] * len(result), index=result.index, dtype=object)
        # Track which rows are still un-assigned (priority: first match wins).
        assigned = pd.Series(False, index=result.index)

        for case in cases:
            when = case.get("when", "")
            if not when:
                raise ValueError("Each case requires a non-empty 'when' expression.")
            _validate_query_expression(when)
            then_value = case.get("then")
            try:
                mask = result.eval(when)
            except Exception as exc:
                raise ValueError(f"Invalid 'when' expression '{when}': {exc}") from exc
            if not isinstance(mask, pd.Series):
                # Constant expression like "True" — broadcast.
                mask = pd.Series(bool(mask), index=result.index)
            mask = mask.fillna(False).astype(bool)
            new_match = mask & ~assigned
            out.loc[new_match] = then_value
            assigned |= new_match

        result[target_col] = out
        return result

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "target_col": {"type": "string"},
                "cases": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "when": {"type": "string"},
                            "then": {},
                        },
                        "required": ["when"],
                    },
                    "minItems": 1,
                },
                "else_": {
                    "description": "Fallback value for rows matching no case.",
                },
            },
            "required": ["target_col", "cases"],
        }
