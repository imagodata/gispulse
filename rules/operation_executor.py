"""Forge-style declarative spatial operations executor.

Executes BEFORE and AFTER operations defined in trigger conditions.operations[].
Each operation maps a source table/field to a spatial computation and optionally
propagates results to a distant table.

Architecture
------------
``OperationExecutor`` runs SQL **server-side on PostGIS**, against a live
connection. It is intentionally separate from :mod:`capabilities`, which
operates on Python ``GeoDataFrame`` objects loaded into memory:

  * Capabilities = analytical pipelines on full layers in Python.
  * OperationExecutor = per-row trigger handlers in SQL on a live DB.

Both must remain *conceptually* aligned (a ``st_within`` operation here
should match what ``IntersectsCapability`` would compute). The two registries
are not unified because executing a per-row trigger by streaming the whole
target table to Python defeats the latency/throughput properties triggers
need. New SQL operations live here; new in-memory operations live in
``capabilities/``.

BEFORE operations modify the current row before commit:
  - st_within / st_contains / st_intersects: parent-zone lookup
  - st_nearest / st_dwithin_startpoint / _endpoint: nearest neighbour
  - st_length / st_area: per-feature geometric measures
  - centroid: WKT-encoded centroid
  - custom_expression: arbitrary SQL (validated)

AFTER operations propagate to a distant table after commit:
  - count_st_contains / sum_st_contains
  - count_st_within / sum_st_within
  - count_st_intersects / sum_st_intersects
  - string_agg_st_intersects
  - custom_expression
"""
from __future__ import annotations

from typing import Any, Callable

from core.logging import get_logger
from core.sql_safety import validate_expression as _validate_expression
from core.sql_safety import validate_layer_name as _validate_identifier  # B-05

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Shared SQL fragments
# ---------------------------------------------------------------------------

# Bind-parameterised geometry literal — keeps WKT/SRID out of the SQL string
# itself so the operator names cannot collide with user-supplied values.
_GEOM_BIND = "ST_SetSRID(ST_GeomFromText(%s), %s)"


def _bind_geom(geom_wkt: str | None, srid: int) -> tuple[str | None, tuple]:
    """Return ``(geom_expr, params)`` for a parameterised geom literal."""
    if geom_wkt is None:
        return None, ()
    return _GEOM_BIND, (geom_wkt, srid)


# ---------------------------------------------------------------------------
# OperationExecutor
# ---------------------------------------------------------------------------


class OperationExecutor:
    """Executes declarative spatial operations via PostGIS SQL."""

    def __init__(self, postgis_conn: Any) -> None:
        self._conn = postgis_conn

    # ------------------------------------------------------------------
    # Public entry points
    # ------------------------------------------------------------------

    def execute_before(
        self,
        operations: list[dict],
        new_data: dict[str, Any],
        geom_wkt: str | None = None,
        srid: int = 4326,
    ) -> dict[str, Any]:
        """Execute BEFORE operations — modify ``new_data`` in place and return it.

        Args:
            operations: List of operation dicts from trigger.conditions.operations.
            new_data:   The NEW row data (modified in place).
            geom_wkt:   WKT geometry of the new feature.
            srid:       SRID of the geometry.

        Returns:
            Modified ``new_data`` dict with computed values.
        """
        for op in _filter_phase(operations, "before"):
            field = op.get("field", "")
            operation = op.get("operation", "")

            # Coalesce: skip if field already has a value.
            if op.get("coalesce") and new_data.get(field) is not None:
                continue
            if not geom_wkt and operation != "custom_expression":
                continue

            try:
                value = self._eval_before(operation, op, geom_wkt, srid, new_data)
                if value is not None:
                    new_data[field] = value
            except Exception as exc:
                log.warning(
                    "before_operation_failed",
                    operation=operation, field=field, error=str(exc),
                )
        return new_data

    def execute_after(
        self,
        operations: list[dict],
        new_data: dict[str, Any],
        old_data: dict[str, Any] | None = None,
        geom_wkt: str | None = None,
        srid: int = 4326,
    ) -> list[dict[str, Any]]:
        """Execute AFTER operations — propagate to distant tables.

        Returns:
            List of update descriptors ``[{"table", "field", "operation"}]``.
        """
        applied: list[dict[str, Any]] = []
        for op in _filter_phase(operations, "after"):
            distant_table = op.get("distant_table", "")
            distant_field = op.get("distant_field", "")
            operation = op.get("operation", "")
            if not distant_table or not distant_field:
                continue
            try:
                sql, params = self._build_after_sql(operation, op, geom_wkt, srid)
                if sql:
                    self._conn.execute(sql, params)
                    applied.append(
                        {
                            "table": distant_table,
                            "field": distant_field,
                            "operation": operation,
                        },
                    )
            except Exception as exc:
                log.warning(
                    "after_operation_failed",
                    operation=operation, table=distant_table, error=str(exc),
                )
        return applied

    # ------------------------------------------------------------------
    # BEFORE operation evaluators (dispatch table)
    # ------------------------------------------------------------------

    def _eval_before(
        self,
        operation: str,
        op: dict,
        geom_wkt: str | None,
        srid: int,
        new_data: dict[str, Any],
    ) -> Any:
        """Look up a BEFORE handler in the dispatch table and evaluate it."""
        handler = _BEFORE_HANDLERS.get(operation)
        if handler is None:
            return None
        return handler(self, op, geom_wkt, srid, new_data)

    # Per-operation evaluators — kept as small bound methods so the dispatch
    # table can reference them by name and they can share self._conn.

    def _b_st_length(self, op, geom_wkt, srid, _new):
        geom, params = _bind_geom(geom_wkt, srid)
        rows = self._conn.execute(f"SELECT ST_Length({geom}::geography) AS val", params)
        return rows[0]["val"] if rows else None

    def _b_st_area(self, op, geom_wkt, srid, _new):
        geom, params = _bind_geom(geom_wkt, srid)
        rows = self._conn.execute(f"SELECT ST_Area({geom}::geography) AS val", params)
        return rows[0]["val"] if rows else None

    def _b_centroid(self, op, geom_wkt, srid, _new):
        geom, params = _bind_geom(geom_wkt, srid)
        rows = self._conn.execute(f"SELECT ST_AsText(ST_Centroid({geom})) AS val", params)
        return rows[0]["val"] if rows else None

    def _b_lookup_id(self, op, geom_wkt, srid, _new, *, predicate: str):
        """Shared body for st_within / st_contains / st_intersects.

        Returns the id of the first reference row matching ``predicate``.
        """
        table = _validate_identifier(op.get("table", "") or "")
        if not table:
            return None
        geom, params = _bind_geom(geom_wkt, srid)
        rows = self._conn.execute(
            f"SELECT id FROM {table} WHERE {predicate}({geom}, geom) LIMIT 1",
            params,
        )
        return rows[0]["id"] if rows else None

    def _b_nearest(self, op, geom_wkt, srid, _new, *, point_wrapper: str | None = None):
        """Shared body for st_nearest / st_dwithin_startpoint / _endpoint."""
        table = _validate_identifier(op.get("table", "") or "")
        if not table:
            return None
        geom, params = _bind_geom(geom_wkt, srid)
        point_expr = f"{point_wrapper}({geom})" if point_wrapper else geom
        rows = self._conn.execute(
            f"SELECT id, ST_Distance({point_expr}::geography, geom::geography) AS dist "
            f"FROM {table} ORDER BY dist LIMIT 1",
            params,
        )
        return rows[0]["id"] if rows else None

    def _b_custom_expression(self, op, _geom_wkt, _srid, _new):
        expr = op.get("custom_expression", "")
        if not expr:
            return None
        _validate_expression(expr)
        rows = self._conn.execute(f"SELECT ({expr}) AS val")
        return rows[0]["val"] if rows else None

    # ------------------------------------------------------------------
    # AFTER operation SQL builders (dispatch table)
    # ------------------------------------------------------------------

    def _build_after_sql(
        self,
        operation: str,
        op: dict,
        geom_wkt: str | None,
        srid: int,
    ) -> tuple[str | None, tuple]:
        """Build SQL for an AFTER operation. Returns ``(sql, params)``."""
        builder = _AFTER_BUILDERS.get(operation)
        if builder is None:
            return None, ()
        return builder(op, geom_wkt, srid)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _filter_phase(operations: list[dict], phase: str) -> list[dict]:
    """Return enabled operations for *phase*, sorted by ``order``."""
    return sorted(
        [op for op in operations if op.get("phase") == phase and op.get("enabled", True)],
        key=lambda op: op.get("order", 999),
    )


def _validated_distant(op: dict) -> tuple[str, str, str, str]:
    """Validate distant_table / distant_field / table / distant_filter.

    Returns a 4-tuple ``(distant_table, distant_field, table, where_clause)``.
    Raises on identifier or expression validation failure.
    """
    distant_table = _validate_identifier(op.get("distant_table", "") or "")
    distant_field = _validate_identifier(op.get("distant_field", "") or "")
    distant_filter = op.get("distant_filter", "")
    if distant_filter:
        _validate_expression(distant_filter)
    table = op.get("table", "") or ""
    if table:
        table = _validate_identifier(table)
    where = f"AND {distant_filter}" if distant_filter else ""
    return distant_table, distant_field, table, where


# ---------------------------------------------------------------------------
# AFTER builders (free functions — no instance state required)
# ---------------------------------------------------------------------------


def _after_count_st_contains(op, geom_wkt, srid):
    geom, params = _bind_geom(geom_wkt, srid)
    if geom is None:
        return None, ()
    dt, df, table, where = _validated_distant(op)
    return (
        f"UPDATE {dt} SET {df} = ("
        f"  SELECT COUNT(*) FROM {table} WHERE ST_Contains({dt}.geom, {table}.geom)"
        f") WHERE ST_Contains({dt}.geom, {geom}) {where}",
        params,
    )


def _after_sum_st_contains(op, geom_wkt, srid):
    geom, params = _bind_geom(geom_wkt, srid)
    if geom is None:
        return None, ()
    dt, df, table, where = _validated_distant(op)
    field = _validate_identifier(op.get("field", "value") or "value")
    return (
        f"UPDATE {dt} SET {df} = ("
        f"  SELECT COALESCE(SUM({table}.{field}), 0) FROM {table} "
        f"  WHERE ST_Contains({dt}.geom, {table}.geom)"
        f") WHERE ST_Contains({dt}.geom, {geom}) {where}",
        params,
    )


def _after_count_st_within(op, geom_wkt, srid):
    geom, params = _bind_geom(geom_wkt, srid)
    if geom is None:
        return None, ()
    dt, df, table, where = _validated_distant(op)
    return (
        f"UPDATE {dt} SET {df} = ("
        f"  SELECT COUNT(*) FROM {table} WHERE ST_Within({table}.geom, {dt}.geom)"
        f") WHERE ST_Within({geom}, {dt}.geom) {where}",
        params,
    )


def _after_sum_st_within(op, geom_wkt, srid):
    geom, params = _bind_geom(geom_wkt, srid)
    if geom is None:
        return None, ()
    dt, df, table, where = _validated_distant(op)
    field = _validate_identifier(op.get("field", "value") or "value")
    return (
        f"UPDATE {dt} SET {df} = ("
        f"  SELECT COALESCE(SUM({table}.{field}), 0) FROM {table} "
        f"  WHERE ST_Within({table}.geom, {dt}.geom)"
        f") WHERE ST_Within({geom}, {dt}.geom) {where}",
        params,
    )


def _after_count_st_intersects(op, geom_wkt, srid):
    geom, params = _bind_geom(geom_wkt, srid)
    if geom is None:
        return None, ()
    dt, df, table, where = _validated_distant(op)
    return (
        f"UPDATE {dt} SET {df} = ("
        f"  SELECT COUNT(*) FROM {table} WHERE ST_Intersects({table}.geom, {dt}.geom)"
        f") WHERE ST_Intersects({dt}.geom, {geom}) {where}",
        params,
    )


def _after_sum_st_intersects(op, geom_wkt, srid):
    geom, params = _bind_geom(geom_wkt, srid)
    if geom is None:
        return None, ()
    dt, df, table, where = _validated_distant(op)
    field = _validate_identifier(op.get("field", "value") or "value")
    return (
        f"UPDATE {dt} SET {df} = ("
        f"  SELECT COALESCE(SUM({table}.{field}), 0) FROM {table} "
        f"  WHERE ST_Intersects({table}.geom, {dt}.geom)"
        f") WHERE ST_Intersects({dt}.geom, {geom}) {where}",
        params,
    )


def _after_string_agg_st_intersects(op, geom_wkt, srid):
    geom, params = _bind_geom(geom_wkt, srid)
    if geom is None:
        return None, ()
    dt, df, table, where = _validated_distant(op)
    field = _validate_identifier(op.get("field", "name") or "name")
    return (
        f"UPDATE {dt} SET {df} = ("
        f"  SELECT STRING_AGG({table}.{field}::TEXT, ', ') FROM {table} "
        f"  WHERE ST_Intersects({table}.geom, {dt}.geom)"
        f") WHERE ST_Intersects({dt}.geom, {geom}) {where}",
        params,
    )


def _after_custom_expression(op, _geom_wkt, _srid):
    expr = op.get("custom_expression", "")
    if not expr:
        return None, ()
    _validate_expression(expr)
    return expr, ()


# ---------------------------------------------------------------------------
# Dispatch tables
# ---------------------------------------------------------------------------


# BEFORE handlers are bound methods of OperationExecutor so they share state.
# The dispatch table stores callables that take ``(self, op, geom_wkt, srid, new_data)``.

_BeforeHandler = Callable[
    [OperationExecutor, dict, str | None, int, dict[str, Any]],
    Any,
]


def _before_st_within(self, op, geom_wkt, srid, new_data):
    return self._b_lookup_id(op, geom_wkt, srid, new_data, predicate="ST_Within")


def _before_st_contains(self, op, geom_wkt, srid, new_data):
    return self._b_lookup_id(op, geom_wkt, srid, new_data, predicate="ST_Contains")


def _before_st_intersects(self, op, geom_wkt, srid, new_data):
    return self._b_lookup_id(op, geom_wkt, srid, new_data, predicate="ST_Intersects")


def _before_st_nearest(self, op, geom_wkt, srid, new_data):
    return self._b_nearest(op, geom_wkt, srid, new_data, point_wrapper=None)


def _before_st_dwithin_startpoint(self, op, geom_wkt, srid, new_data):
    return self._b_nearest(op, geom_wkt, srid, new_data, point_wrapper="ST_StartPoint")


def _before_st_dwithin_endpoint(self, op, geom_wkt, srid, new_data):
    return self._b_nearest(op, geom_wkt, srid, new_data, point_wrapper="ST_EndPoint")


_BEFORE_HANDLERS: dict[str, _BeforeHandler] = {
    "st_length": OperationExecutor._b_st_length,
    "st_area": OperationExecutor._b_st_area,
    "centroid": OperationExecutor._b_centroid,
    "st_within": _before_st_within,
    "st_contains": _before_st_contains,
    "st_intersects": _before_st_intersects,
    "st_nearest": _before_st_nearest,
    "st_dwithin_startpoint": _before_st_dwithin_startpoint,
    "st_dwithin_endpoint": _before_st_dwithin_endpoint,
    "custom_expression": OperationExecutor._b_custom_expression,
}


_AfterBuilder = Callable[[dict, str | None, int], tuple[str | None, tuple]]

_AFTER_BUILDERS: dict[str, _AfterBuilder] = {
    "count_st_contains": _after_count_st_contains,
    "sum_st_contains": _after_sum_st_contains,
    "count_st_within": _after_count_st_within,
    "sum_st_within": _after_sum_st_within,
    "count_st_intersects": _after_count_st_intersects,
    "sum_st_intersects": _after_sum_st_intersects,
    "string_agg_st_intersects": _after_string_agg_st_intersects,
    "custom_expression": _after_custom_expression,
}
