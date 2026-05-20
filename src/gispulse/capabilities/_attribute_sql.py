"""SQL builders for the attribute-only capabilities (ELT Lot 2, #245).

One builder per ``schema`` / ``selection`` capability. Each turns
``(dialect, gdf, params, tables)`` into a ``SELECT`` statement, or raises
:class:`~gispulse.capabilities.sql_pushdown.Untranslatable` to defer to
the Python implementation.

Wiring lives at the bottom of ``schema.py`` / ``selection.py`` /
``vector/calculate.py`` via :func:`attach_sql_pushdown` — kept out of
those files so the capability modules stay readable.
"""

from __future__ import annotations

import geopandas as gpd

from gispulse.capabilities.sql_pushdown import (
    Untranslatable,
    attr_columns,
    qi,
    sql_literal,
    translate_expression,
)
from gispulse.persistence.sql_dialect import SQLDialect

# --- dtype → SQL type -------------------------------------------------------

# Maps a pandas dtype (as resolved by schema._resolve_dtype) to the SQL
# column type for each engine.
_SQL_TYPES: dict[str, dict[str, str]] = {
    "duckdb": {
        "int": "BIGINT",
        "float": "DOUBLE",
        "string": "VARCHAR",
        "boolean": "BOOLEAN",
        "datetime": "TIMESTAMP",
    },
    "postgis": {
        "int": "BIGINT",
        "float": "DOUBLE PRECISION",
        "string": "TEXT",
        "boolean": "BOOLEAN",
        "datetime": "TIMESTAMP",
    },
}


def _type_family(resolved_dtype: str) -> str:
    """Collapse a resolved pandas dtype to a SQL type family."""
    d = resolved_dtype.lower()
    if d.startswith("int"):
        return "int"
    if d.startswith("float"):
        return "float"
    if d in ("string", "object", "str"):
        return "string"
    if d in ("boolean", "bool"):
        return "boolean"
    if d.startswith("datetime"):
        return "datetime"
    raise Untranslatable(f"dtype {resolved_dtype!r} has no SQL type mapping")


def _sql_type(dialect: SQLDialect, resolved_dtype: str) -> str:
    return _SQL_TYPES[dialect.name][_type_family(resolved_dtype)]


# --- projection helpers -----------------------------------------------------


def _geom_reg(dialect: SQLDialect, gdf: gpd.GeoDataFrame) -> str:
    """Name of the geometry column in the *registered* table."""
    if dialect.name == "duckdb":
        return dialect.geom_column  # always __wkb
    return gdf.geometry.name  # PostGIS keeps the GeoDataFrame's name


def _ordered_projection(
    dialect: SQLDialect,
    gdf: gpd.GeoDataFrame,
    *,
    rename: dict[str, str] | None = None,
    drop: set[str] | None = None,
    keep: set[str] | None = None,
) -> str:
    """Build a column projection that preserves the input column order.

    The geometry column is always kept (under its registered name).
    *rename* / *drop* / *keep* act on attribute columns only.
    """
    rename = rename or {}
    drop = drop or set()
    geom_name = gdf.geometry.name
    geom_reg = _geom_reg(dialect, gdf)
    parts: list[str] = []
    for col in gdf.columns:
        if col == geom_name:
            parts.append(qi(geom_reg))
            continue
        if col in drop:
            continue
        if keep is not None and col not in keep:
            continue
        if col in rename:
            parts.append(f"{qi(col)} AS {qi(rename[col])}")
        else:
            parts.append(qi(col))
    return ", ".join(parts)


# ===========================================================================
# schema.py builders
# ===========================================================================


def build_select_columns(dialect, gdf, params, tables) -> str:
    fields = [c for c in (params.get("fields") or []) if c in attr_columns(gdf)]
    proj = _ordered_projection(dialect, gdf, keep=set(fields))
    return f"SELECT {proj} FROM {tables['input']}"


def build_drop_field(dialect, gdf, params, tables) -> str:
    geom_name = gdf.geometry.name
    drop: set[str] = set()
    for col in params.get("fields") or []:
        if col == geom_name:
            raise Untranslatable("drop_field cannot drop the geometry column")
        if col in attr_columns(gdf):
            drop.add(col)
    proj = _ordered_projection(dialect, gdf, drop=drop)
    return f"SELECT {proj} FROM {tables['input']}"


def build_rename_field(dialect, gdf, params, tables) -> str:
    mapping = params.get("mapping") or {}
    geom_name = gdf.geometry.name
    cols = set(attr_columns(gdf))
    valid: dict[str, str] = {}
    for old, new in mapping.items():
        if old == geom_name or new == geom_name:
            raise Untranslatable("rename_field cannot touch the geometry column")
        if old not in cols:
            continue  # ignore_missing default — Python handles errors=raise
        if new in cols and new != old:
            raise Untranslatable(f"rename target {new!r} collides")
        valid[old] = new
    proj = _ordered_projection(dialect, gdf, rename=valid)
    return f"SELECT {proj} FROM {tables['input']}"


def build_add_field(dialect, gdf, params, tables) -> str:
    from gispulse.capabilities.schema import _resolve_dtype

    fields = params.get("fields") or []
    existing = set(gdf.columns)
    geom_name = gdf.geometry.name
    additions: list[str] = []
    for spec in fields:
        name = spec.get("name", "")
        if name == geom_name:
            raise Untranslatable("add_field cannot overwrite the geometry column")
        if name in existing:
            # overwrite / skip semantics — leave to Python.
            raise Untranslatable(f"add_field target {name!r} already exists")
        sql_type = _sql_type(dialect, _resolve_dtype(spec.get("dtype", "string")))
        lit = sql_literal(spec.get("default"))
        additions.append(f"CAST({lit} AS {sql_type}) AS {qi(name)}")
    if not additions:
        raise Untranslatable("add_field has no new columns")
    return f"SELECT *, {', '.join(additions)} FROM {tables['input']}"


def build_cast_field(dialect, gdf, params, tables) -> str:
    from gispulse.capabilities.schema import _resolve_dtype

    casts = params.get("casts") or {}
    errors = params.get("errors", "raise")
    if errors == "ignore":
        raise Untranslatable("cast_field errors='ignore' is not SQL-expressible")
    if errors == "coerce" and dialect.name != "duckdb":
        raise Untranslatable("cast_field errors='coerce' needs DuckDB TRY_CAST")
    cast_fn = "TRY_CAST" if errors == "coerce" else "CAST"
    geom_name = gdf.geometry.name
    cols = set(attr_columns(gdf))
    cast_map: dict[str, str] = {}
    for col, dtype_spec in casts.items():
        if col == geom_name:
            raise Untranslatable("cast_field cannot cast the geometry column")
        if col not in cols:
            raise Untranslatable(f"cast_field column {col!r} absent")
        cast_map[col] = _sql_type(dialect, _resolve_dtype(dtype_spec))
    parts: list[str] = []
    for col in gdf.columns:
        if col == geom_name:
            parts.append(qi(_geom_reg(dialect, gdf)))
        elif col in cast_map:
            parts.append(f"{cast_fn}({qi(col)} AS {cast_map[col]}) AS {qi(col)}")
        else:
            parts.append(qi(col))
    return f"SELECT {', '.join(parts)} FROM {tables['input']}"


def build_coalesce_fields(dialect, gdf, params, tables) -> str:
    sources = params.get("sources") or []
    target = params.get("target_col", "")
    cols = set(attr_columns(gdf))
    for c in sources:
        if c not in cols:
            raise Untranslatable(f"coalesce source {c!r} absent")
    if target in gdf.columns:
        raise Untranslatable(f"coalesce target {target!r} already exists")
    coalesced = "COALESCE(" + ", ".join(qi(c) for c in sources) + ")"
    return f"SELECT *, {coalesced} AS {qi(target)} FROM {tables['input']}"


def build_case_when(dialect, gdf, params, tables) -> str:
    target = params.get("target_col", "")
    cases = params.get("cases") or []
    if target in gdf.columns:
        raise Untranslatable(f"case_when target {target!r} already exists")
    whens: list[str] = []
    for case in cases:
        when = case.get("when", "")
        if not when:
            raise Untranslatable("case_when: empty 'when'")
        cond = translate_expression(when)
        then = sql_literal(case.get("then"))
        whens.append(f"WHEN ({cond}) THEN {then}")
    else_sql = sql_literal(params.get("else_"))
    case_expr = "CASE " + " ".join(whens) + f" ELSE {else_sql} END"
    return f"SELECT *, {case_expr} AS {qi(target)} FROM {tables['input']}"


def build_attribute_join(dialect, gdf, params, tables) -> str:
    left_on = params.get("left_on", "")
    right_on = params.get("right_on") or left_on
    how = params.get("how", "left")
    columns = params.get("columns")
    prefix = params.get("prefix", "")
    suffix = params.get("suffix", "")
    ref = params.get("ref_gdf")
    if ref is None or not left_on:
        raise Untranslatable("attribute_join missing ref/left_on")
    if left_on not in gdf.columns:
        raise Untranslatable(f"attribute_join left_on {left_on!r} absent")
    if right_on not in getattr(ref, "columns", []):
        raise Untranslatable(f"attribute_join right_on {right_on!r} absent")
    if prefix or suffix:
        for ch in (prefix, suffix):
            if ch and not all(c.isalnum() or c == "_" for c in ch):
                raise Untranslatable("attribute_join prefix/suffix not identifier-safe")
    _JOIN_SQL = {
        "left": "LEFT JOIN",
        "right": "RIGHT JOIN",
        "inner": "INNER JOIN",
        "outer": "FULL OUTER JOIN",
    }
    if how not in _JOIN_SQL:
        raise Untranslatable(f"attribute_join how={how!r} unsupported")

    ref_attr = [c for c in attr_columns(ref) if c != right_on]
    if columns:
        ref_attr = [c for c in ref_attr if c in columns]
    imported: list[str] = []
    for c in ref_attr:
        alias = f"{prefix}{c}{suffix}"
        if alias in gdf.columns:
            raise Untranslatable(f"attribute_join column {alias!r} collides")
        imported.append(f"r.{qi(c)} AS {qi(alias)}")
    proj = "i.*" + ("" if not imported else ", " + ", ".join(imported))
    on = f"i.{qi(left_on)} = r.{qi(right_on)}"
    return (
        f"SELECT {proj} FROM {tables['input']} i "
        f"{_JOIN_SQL[how]} {tables['ref']} r ON {on}"
    )


# ===========================================================================
# selection.py builders
# ===========================================================================


def _order_by_clause(by, ascending, na_position="last") -> str:
    cols = [by] if isinstance(by, str) else list(by)
    if isinstance(ascending, bool):
        asc = [ascending] * len(cols)
    else:
        asc = list(ascending)
        if len(asc) != len(cols):
            raise Untranslatable("ascending length mismatch")
    if na_position not in ("first", "last"):
        raise Untranslatable(f"na_position {na_position!r} invalid")
    nulls = "NULLS FIRST" if na_position == "first" else "NULLS LAST"
    terms = [
        f"{qi(c)} {'ASC' if a else 'DESC'} {nulls}"
        for c, a in zip(cols, asc)
    ]
    return ", ".join(terms)


def build_sort(dialect, gdf, params, tables) -> str:
    by = params.get("by")
    if not by:
        raise Untranslatable("sort without 'by' is a no-op")
    order = _order_by_clause(
        by, params.get("ascending", True), params.get("na_position", "last")
    )
    return f"SELECT * FROM {tables['input']} ORDER BY {order}"


def build_top_n(dialect, gdf, params, tables) -> str:
    n = params.get("n", 10)
    by = params.get("by")
    if n is None or n < 0:
        raise Untranslatable("top_n needs n >= 0")
    if not by:
        # head(n) = input order; SQL has no stable input order — defer.
        raise Untranslatable("top_n without 'by' relies on input order")
    order = _order_by_clause(by, params.get("ascending", False))
    return f"SELECT * FROM {tables['input']} ORDER BY {order} LIMIT {int(n)}"


def build_deduplicate(dialect, gdf, params, tables) -> str:
    keys = params.get("keys")
    keep = params.get("keep", "first")
    order_by = params.get("order_by")
    if not keys:
        raise Untranslatable("deduplicate needs 'keys'")
    if not order_by:
        # 'first'/'last' without order_by means input order — not stable in SQL.
        raise Untranslatable("deduplicate push-down requires 'order_by'")
    if keep not in ("first", "last"):
        raise Untranslatable(f"deduplicate keep={keep!r} invalid")
    key_cols = [keys] if isinstance(keys, str) else list(keys)
    for c in key_cols:
        if c not in gdf.columns:
            raise Untranslatable(f"deduplicate key {c!r} absent")
    # keep='last' ⇔ reverse the order so row 1 is the last per group.
    asc = params.get("ascending", True)
    if keep == "last":
        if isinstance(asc, bool):
            asc = not asc
        else:
            asc = [not a for a in asc]
    order = _order_by_clause(order_by, asc)
    partition = ", ".join(qi(c) for c in key_cols)
    geom_name = gdf.geometry.name
    cols = ", ".join(
        qi(_geom_reg(dialect, gdf)) if c == geom_name else qi(c)
        for c in gdf.columns
    )
    return (
        f"SELECT {cols} FROM ("
        f"SELECT *, ROW_NUMBER() OVER (PARTITION BY {partition} "
        f"ORDER BY {order}) AS _gp_rn FROM {tables['input']}"
        f") _gp_sub WHERE _gp_rn = 1"
    )


# ===========================================================================
# vector/calculate.py builder
# ===========================================================================


def build_calculate(dialect, gdf, params, tables) -> str:
    expressions = params.get("expressions") or {}
    geom_name = gdf.geometry.name
    additions: list[str] = []
    for col_name, expr in expressions.items():
        if col_name == geom_name:
            raise Untranslatable("calculate cannot write the geometry column")
        if col_name in gdf.columns:
            # Overwriting an existing column would duplicate it under
            # `SELECT *` — leave that case to the Python implementation.
            raise Untranslatable(f"calculate target {col_name!r} already exists")
        additions.append(f"({translate_expression(expr)}) AS {qi(col_name)}")
    if not additions:
        raise Untranslatable("calculate has no expressions")
    return f"SELECT *, {', '.join(additions)} FROM {tables['input']}"
