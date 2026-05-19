"""Shared SQL push-down infrastructure for non-geometric capabilities.

ELT Lot 2 (issue #245, ADR 0005). The ~12 ``schema`` / ``selection``
capabilities are *attribute-only* — pure ``SELECT`` / ``CAST`` / ``CASE``
/ ``ORDER BY`` / ``DISTINCT`` / ``LIMIT``, no geometry maths. This module
gives them a single, generic DuckDB/PostGIS execution strategy so each
capability only describes *how to build its SELECT*, not how to register
data, dispatch, or decode the result.

Design — the push-down is **opportunistic**. A strategy declines (and
the proven Python/GeoPandas implementation runs) when:

- the active engine is neither DuckDB nor PostGIS;
- a capability-supplied ``gate`` rejects the params (missing required
  keys, an empty no-op call — handled by Python so the original
  ``ValueError`` / passthrough semantics are preserved);
- the builder raises :class:`Untranslatable` (e.g. a ``calculate``
  expression that leaves the SQL-pure subset).

Correctness never depends on the SQL path. The geometry column is
carried through untouched — every generated query keeps the dialect's
registered geometry column (``__wkb`` on DuckDB, ``geometry`` on PostGIS)
so the engine's own result decoder rebuilds the GeoDataFrame.
"""

from __future__ import annotations

import datetime as _dt
from typing import Callable

import geopandas as gpd

from gispulse.capabilities.strategy import (
    ExecutionContext,
    ExecutionStrategy,
    StrategyMode,
)
from gispulse.core.logging import get_logger
from gispulse.persistence.sql_dialect import SQLDialect, get_dialect

log = get_logger(__name__)

# PostGIS `register` writes to a prefixed temp table — keep in sync with
# gispulse.persistence.postgis._TMP_PREFIX.
_POSTGIS_TMP_PREFIX = "_gispulse_tmp_"

_PRIORITY = {"postgis": 100, "duckdb": 80}
_MODE = {"postgis": StrategyMode.POSTGIS, "duckdb": StrategyMode.DUCKDB}

__all__ = [
    "Untranslatable",
    "qi",
    "attr_columns",
    "sql_literal",
    "translate_expression",
    "attach_sql_pushdown",
]


class Untranslatable(Exception):
    """Raised by a builder when the operation cannot be expressed in SQL.

    Not an error: :class:`_SqlPushdownStrategy` catches it and runs the
    capability's Python implementation instead.
    """


# ---------------------------------------------------------------------------
# Identifier / literal helpers
# ---------------------------------------------------------------------------


def _registered_table(engine, name: str) -> str:
    """Return the table name an engine's ``register`` actually creates."""
    if getattr(engine, "backend_name", "") == "postgis":
        return f"{_POSTGIS_TMP_PREFIX}{name}"
    return name


def qi(identifier: str) -> str:
    """Quote a SQL identifier (column / table) with double quotes.

    The capability layer constrains identifiers to
    ``[A-Za-z_][A-Za-z0-9_]{0,62}``; quoting on top neutralises reserved
    words. A stray double quote is rejected outright.
    """
    if not isinstance(identifier, str) or not identifier or '"' in identifier:
        raise ValueError(f"Unsafe SQL identifier: {identifier!r}")
    return f'"{identifier}"'


def attr_columns(gdf: gpd.GeoDataFrame) -> list[str]:
    """Return *gdf*'s column names with the active geometry column removed."""
    geom = gdf.geometry.name if hasattr(gdf, "geometry") else None
    return [c for c in gdf.columns if c != geom]


def _as_registerable(frame) -> gpd.GeoDataFrame:
    """Coerce a join reference into something the engines can ``register``.

    A non-spatial reference table (``attribute_join`` accepts a plain
    ``DataFrame``) is wrapped with a throw-away null-geometry column so
    ``register`` — which assumes a GeoDataFrame — succeeds. The dummy
    column is never referenced by the generated JOIN.
    """
    if isinstance(frame, gpd.GeoDataFrame):
        return frame
    return gpd.GeoDataFrame(
        frame.copy(),
        geometry=gpd.GeoSeries([None] * len(frame), crs=None),
    )


def sql_literal(value: object) -> str:
    """Render a Python scalar as a SQL literal.

    Raises :class:`Untranslatable` for anything that cannot be safely
    inlined (lists, dicts, geometries, …).
    """
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        return "'" + value.replace("'", "''") + "'"
    if isinstance(value, (_dt.date, _dt.datetime)):
        return "'" + value.isoformat() + "'"
    raise Untranslatable(f"value {value!r} has no safe SQL literal form")


# ---------------------------------------------------------------------------
# pandas-query / eval expression → SQL
# ---------------------------------------------------------------------------

# Substrings that mean the expression leaves the SQL-pure subset.
_EXPR_REJECT = ("@", "`", "[", "]", "{", "}", ";", "--", "/*", "**", "~", "//")


def translate_expression(expr: str) -> str:
    """Translate a pandas ``query``/``eval`` expression to portable SQL.

    Handles the SQL-pure subset only — column names, numeric/string
    literals, arithmetic (``+ - * / %``), comparisons and the boolean
    connectives ``and`` / ``or`` / ``not``. Anything outside it (method
    calls, ``@`` variables, indexing, ``**`` / ``//``) raises
    :class:`Untranslatable`, so the caller falls back to Python.

    ``ST_*`` and other function calls are intentionally *not* supported —
    ``calculate`` / ``case_when`` push-down covers plain attribute maths.
    """
    raw = expr.strip()
    if not raw:
        raise Untranslatable("empty expression")
    for bad in _EXPR_REJECT:
        if bad in raw:
            raise Untranslatable(f"expression uses unsupported token {bad!r}")

    out: list[str] = []
    i, n = 0, len(raw)
    while i < n:
        ch = raw[i]
        if ch in ("'", '"'):  # string literal — re-quote to SQL single quotes
            j = i + 1
            while j < n and raw[j] != ch:
                j += 1
            if j >= n:
                raise Untranslatable("unterminated string literal")
            out.append("'" + raw[i + 1 : j].replace("'", "''") + "'")
            i = j + 1
            continue
        if ch == ".":  # legal only between digits (decimal literal)
            prev = raw[i - 1] if i > 0 else ""
            nxt = raw[i + 1] if i + 1 < n else ""
            if not (prev.isdigit() and nxt.isdigit()):
                raise Untranslatable("attribute access is not SQL-translatable")
            out.append(ch)
            i += 1
            continue
        if ch == "=" and i + 1 < n and raw[i + 1] == "=":
            out.append("=")
            i += 2
            continue
        if ch == "!" and i + 1 < n and raw[i + 1] == "=":
            out.append("<>")
            i += 2
            continue
        if ch == "(":  # a function call — `name(` — is out of scope
            if out and out[-1] not in ("(", "AND", "OR", "NOT", "", "+", "-", "*", "/", "%", "=", "<", ">", "<>", "<=", ">="):
                raise Untranslatable("function calls are not SQL-translatable")
            out.append(ch)
            i += 1
            continue
        if ch.isalpha() or ch == "_":
            j = i
            while j < n and (raw[j].isalnum() or raw[j] == "_"):
                j += 1
            word, lowered = raw[i:j], raw[i:j].lower()
            if lowered in ("and", "or", "not"):
                out.append(lowered.upper())
            elif lowered == "true":
                out.append("TRUE")
            elif lowered == "false":
                out.append("FALSE")
            elif lowered in ("none", "null"):
                out.append("NULL")
            else:
                out.append(qi(word))
            i = j
            continue
        if ch in "0123456789+-*/%)<> \t":
            out.append(ch)
            i += 1
            continue
        raise Untranslatable(f"unsupported character {ch!r} in expression")

    sql = "".join(out).strip()
    if not sql:
        raise Untranslatable("expression translated to empty SQL")
    return sql


# ---------------------------------------------------------------------------
# Generic strategy
# ---------------------------------------------------------------------------

# A builder turns (dialect, gdf, params, tables) into a SQL string. It may
# raise Untranslatable to decline. `tables` maps a logical name ("input",
# plus any declared extras) to the registered table name.
SqlBuilder = Callable[[SQLDialect, gpd.GeoDataFrame, dict, dict], str]
# An optional eligibility gate evaluated on params alone (no frame).
SqlGate = Callable[[dict], bool]


class _SqlPushdownStrategy(ExecutionStrategy):
    """Generic DuckDB/PostGIS strategy for one attribute-only capability."""

    def __init__(
        self,
        backend: str,
        capability,
        build: SqlBuilder,
        *,
        gate: SqlGate | None,
        extra_inputs: dict[str, str],
    ):
        self._backend = backend
        self._capability = capability
        self._build = build
        self._gate = gate
        self._extra_inputs = extra_inputs  # logical name -> param key
        self.mode = _MODE[backend]

    def can_execute(self, ctx: ExecutionContext) -> bool:
        if getattr(ctx.engine, "backend_name", "") != self._backend:
            return False
        if self._gate is not None and not self._gate(ctx.params):
            return False
        return True

    def execute(self, gdf: gpd.GeoDataFrame, ctx: ExecutionContext) -> gpd.GeoDataFrame:
        dialect = get_dialect(self._backend)
        engine = ctx.engine
        try:
            engine.register("_sqlin", gdf)
            tables = {"input": _registered_table(engine, "_sqlin")}
            for logical, param_key in self._extra_inputs.items():
                extra = ctx.params.get(param_key)
                if extra is None:
                    raise Untranslatable(f"missing extra input {param_key!r}")
                reg = f"_sql_{logical}"
                engine.register(reg, _as_registerable(extra))
                tables[logical] = _registered_table(engine, reg)
            sql = self._build(dialect, gdf, ctx.params, tables)
        except Untranslatable as exc:
            log.debug("sql_pushdown_declined", backend=self._backend, reason=str(exc))
            return self._python_fallback(gdf, ctx.params)
        return engine.sql_to_gdf(sql)

    def _python_fallback(self, gdf: gpd.GeoDataFrame, params: dict) -> gpd.GeoDataFrame:
        from gispulse.capabilities.base import safe_execute

        clean = {k: v for k, v in params.items() if not k.startswith("_")}
        return safe_execute(self._capability, gdf, **clean)

    @property
    def priority(self) -> int:
        return _PRIORITY[self._backend]


def attach_sql_pushdown(
    capability_cls: type,
    build: SqlBuilder,
    *,
    gate: SqlGate | None = None,
    extra_inputs: dict[str, str] | None = None,
) -> None:
    """Wire DuckDB + PostGIS push-down strategies onto a capability class.

    Call once, just after the class body. Builds a single capability
    instance shared by both strategies as the Python fallback target.

    Args:
        capability_cls: The :class:`~gispulse.capabilities.base.Capability`
            subclass to equip.
        build:          The SQL builder for this capability.
        gate:           Optional params-only eligibility predicate. Return
            ``False`` to defer to Python (e.g. required params absent).
        extra_inputs:   Logical-name → param-key map of additional
            GeoDataFrame inputs to register (e.g. ``{"ref": "ref_gdf"}``).
    """
    instance = capability_cls()
    extras = extra_inputs or {}
    capability_cls._strategies = [
        _SqlPushdownStrategy(
            "postgis", instance, build, gate=gate, extra_inputs=extras
        ),
        _SqlPushdownStrategy(
            "duckdb", instance, build, gate=gate, extra_inputs=extras
        ),
    ]
