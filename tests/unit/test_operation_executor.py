"""Tests for rules.operation_executor — BEFORE/AFTER declarative operations.

Operations are declarative spatial SQL computations wired through trigger
configs. They execute against a PostGIS connection. We don't need a real
PostgreSQL here — a fake connection captures the SQL+params and returns
canned rows. This pins:
- Operation dispatch (st_within, st_contains, st_intersects, st_nearest, ...)
- Coalesce behaviour (skip when field already has a value)
- Ordering (operations execute sorted by 'order' key)
- Disabled / phase filtering
- SQL identifier validation (defence against injection via table/field names)
- SQL expression validation for custom_expression
- Silent failure mode (logs warning, doesn't propagate — documented contract)
"""
from __future__ import annotations


import pytest

from gispulse.rules.operation_executor import OperationExecutor


class FakeConn:
    """Minimal PostGIS connection stub — records calls, returns canned rows."""

    def __init__(self, rows_queue: list[list[dict]] | None = None) -> None:
        self.calls: list[tuple[str, tuple]] = []
        self._rows = rows_queue or []

    def execute(self, sql: str, params: tuple = ()) -> list[dict]:
        self.calls.append((sql, params))
        if self._rows:
            return self._rows.pop(0)
        return []


@pytest.fixture
def executor() -> OperationExecutor:
    return OperationExecutor(postgis_conn=FakeConn())


# ---------------------------------------------------------------------------
# BEFORE operations
# ---------------------------------------------------------------------------


class TestExecuteBefore:
    def test_empty_operations_returns_data_unchanged(self):
        exec_ = OperationExecutor(FakeConn())
        data = {"id": 1}
        result = exec_.execute_before([], data, geom_wkt="POINT(0 0)")
        assert result is data
        assert data == {"id": 1}

    def test_st_length_populates_field(self):
        conn = FakeConn(rows_queue=[[{"val": 1234.5}]])
        exec_ = OperationExecutor(conn)
        data = {"id": 1}
        ops = [{"phase": "before", "operation": "st_length", "field": "length_m"}]
        result = exec_.execute_before(ops, data, geom_wkt="LINESTRING(0 0, 1 1)")
        assert result["length_m"] == 1234.5
        assert len(conn.calls) == 1
        # WKT + SRID passed as bind params (injection-safe)
        assert conn.calls[0][1] == ("LINESTRING(0 0, 1 1)", 4326)

    def test_st_area_populates_field(self):
        conn = FakeConn(rows_queue=[[{"val": 4200.0}]])
        exec_ = OperationExecutor(conn)
        data: dict = {}
        ops = [{"phase": "before", "operation": "st_area", "field": "area_m2"}]
        result = exec_.execute_before(ops, data, geom_wkt="POLYGON((0 0, 1 0, 1 1, 0 1, 0 0))")
        assert result["area_m2"] == 4200.0

    def test_centroid_populates_wkt_field(self):
        conn = FakeConn(rows_queue=[[{"val": "POINT(0.5 0.5)"}]])
        exec_ = OperationExecutor(conn)
        ops = [{"phase": "before", "operation": "centroid", "field": "center"}]
        result = exec_.execute_before(ops, {}, geom_wkt="POLYGON((0 0, 1 0, 1 1, 0 1, 0 0))")
        assert result["center"] == "POINT(0.5 0.5)"

    def test_st_within_finds_containing_zone(self):
        conn = FakeConn(rows_queue=[[{"id": "zone-7"}]])
        exec_ = OperationExecutor(conn)
        ops = [{
            "phase": "before",
            "operation": "st_within",
            "field": "zone_id",
            "table": "zones",
        }]
        result = exec_.execute_before(ops, {}, geom_wkt="POINT(2.3 48.8)")
        assert result["zone_id"] == "zone-7"
        assert "zones" in conn.calls[0][0]  # table interpolated
        assert "ST_Within" in conn.calls[0][0]

    def test_st_nearest_orders_by_distance(self):
        conn = FakeConn(rows_queue=[[{"id": "node-42", "dist": 3.2}]])
        exec_ = OperationExecutor(conn)
        ops = [{
            "phase": "before",
            "operation": "st_nearest",
            "field": "nearest_node",
            "table": "nodes",
        }]
        result = exec_.execute_before(ops, {}, geom_wkt="POINT(0 0)")
        assert result["nearest_node"] == "node-42"
        assert "ORDER BY dist" in conn.calls[0][0]

    def test_st_dwithin_startpoint_uses_startpoint(self):
        conn = FakeConn(rows_queue=[[{"id": "n1", "dist": 0}]])
        exec_ = OperationExecutor(conn)
        ops = [{
            "phase": "before",
            "operation": "st_dwithin_startpoint",
            "field": "start_node",
            "table": "nodes",
        }]
        exec_.execute_before(ops, {}, geom_wkt="LINESTRING(0 0, 1 1)")
        assert "ST_StartPoint" in conn.calls[0][0]

    def test_st_dwithin_endpoint_uses_endpoint(self):
        conn = FakeConn(rows_queue=[[{"id": "n2", "dist": 0}]])
        exec_ = OperationExecutor(conn)
        ops = [{
            "phase": "before",
            "operation": "st_dwithin_endpoint",
            "field": "end_node",
            "table": "nodes",
        }]
        exec_.execute_before(ops, {}, geom_wkt="LINESTRING(0 0, 1 1)")
        assert "ST_EndPoint" in conn.calls[0][0]

    def test_skipped_when_no_geometry(self):
        conn = FakeConn(rows_queue=[])
        exec_ = OperationExecutor(conn)
        ops = [{"phase": "before", "operation": "st_length", "field": "length_m"}]
        result = exec_.execute_before(ops, {"id": 1}, geom_wkt=None)
        # No geometry → operation skipped entirely
        assert "length_m" not in result
        assert conn.calls == []

    def test_custom_expression_runs_without_geometry(self):
        conn = FakeConn(rows_queue=[[{"val": 42}]])
        exec_ = OperationExecutor(conn)
        ops = [{
            "phase": "before",
            "operation": "custom_expression",
            "field": "computed",
            "custom_expression": "1 + 1",
        }]
        result = exec_.execute_before(ops, {}, geom_wkt=None)
        assert result["computed"] == 42

    def test_custom_expression_rejects_unsafe_sql(self):
        """validate_expression must block DROP/SELECT/etc."""
        conn = FakeConn()
        exec_ = OperationExecutor(conn)
        ops = [{
            "phase": "before",
            "operation": "custom_expression",
            "field": "x",
            "custom_expression": "DROP TABLE users",
        }]
        # Silent failure contract: logged but not raised
        result = exec_.execute_before(ops, {"x": None}, geom_wkt=None)
        assert "x" not in result or result.get("x") is None
        # Conn was never executed because validation raised first
        assert conn.calls == []


class TestBeforeOperationMeta:
    def test_coalesce_skips_when_field_already_set(self):
        conn = FakeConn()
        exec_ = OperationExecutor(conn)
        ops = [{
            "phase": "before",
            "operation": "st_length",
            "field": "length_m",
            "coalesce": True,
        }]
        result = exec_.execute_before(ops, {"length_m": 999}, geom_wkt="LINESTRING(0 0, 1 1)")
        assert result["length_m"] == 999
        assert conn.calls == []  # no DB roundtrip

    def test_coalesce_runs_when_field_is_none(self):
        conn = FakeConn(rows_queue=[[{"val": 50.0}]])
        exec_ = OperationExecutor(conn)
        ops = [{
            "phase": "before",
            "operation": "st_length",
            "field": "length_m",
            "coalesce": True,
        }]
        result = exec_.execute_before(ops, {"length_m": None}, geom_wkt="LINESTRING(0 0, 1 1)")
        assert result["length_m"] == 50.0

    def test_disabled_operation_is_skipped(self):
        conn = FakeConn()
        exec_ = OperationExecutor(conn)
        ops = [{
            "phase": "before",
            "operation": "st_length",
            "field": "length_m",
            "enabled": False,
        }]
        exec_.execute_before(ops, {}, geom_wkt="LINESTRING(0 0, 1 1)")
        assert conn.calls == []

    def test_after_phase_ops_ignored_by_execute_before(self):
        conn = FakeConn()
        exec_ = OperationExecutor(conn)
        ops = [{"phase": "after", "operation": "st_length", "field": "x"}]
        exec_.execute_before(ops, {}, geom_wkt="LINESTRING(0 0, 1 1)")
        assert conn.calls == []

    def test_operations_execute_in_order_key(self):
        """Lower order runs first — downstream ops see upstream results."""
        conn = FakeConn(rows_queue=[
            [{"val": 100}],  # first: st_area → area_m2
            [{"val": "POINT(0.5 0.5)"}],  # second: centroid → center
        ])
        exec_ = OperationExecutor(conn)
        ops = [
            {"phase": "before", "operation": "centroid", "field": "center", "order": 2},
            {"phase": "before", "operation": "st_area", "field": "area_m2", "order": 1},
        ]
        result = exec_.execute_before(
            ops, {}, geom_wkt="POLYGON((0 0, 1 0, 1 1, 0 1, 0 0))"
        )
        # Both ran
        assert "area_m2" in result and "center" in result
        # First call's SQL should mention ST_Area (order=1 ran first)
        assert "ST_Area" in conn.calls[0][0]
        assert "ST_Centroid" in conn.calls[1][0]


class TestBeforeSilentFailure:
    """Documented contract: operation failures are logged as warnings but
    not raised. Other ops in the batch continue. This is intentional — a
    single bad op must not block the whole write."""

    def test_individual_failure_does_not_block_batch(self, caplog):
        conn = FakeConn(rows_queue=[
            Exception("boom"),  # first call raises (simulated)
            [{"val": 42.0}],    # second call succeeds
        ])

        # Override execute to raise on first call, succeed on second
        original_execute = conn.execute
        call_idx = {"n": 0}

        def flaky(sql, params=()):
            i = call_idx["n"]
            call_idx["n"] += 1
            if i == 0:
                raise RuntimeError("downstream timeout")
            return original_execute(sql, params)

        conn.execute = flaky
        conn._rows = [[{"val": 42.0}]]

        exec_ = OperationExecutor(conn)
        ops = [
            {"phase": "before", "operation": "st_length", "field": "length_m", "order": 1},
            {"phase": "before", "operation": "st_area", "field": "area_m2", "order": 2},
        ]
        result = exec_.execute_before(
            ops, {"id": 1}, geom_wkt="POLYGON((0 0, 1 0, 1 1, 0 1, 0 0))"
        )
        # First op failed → field missing, but second op still populated
        assert "length_m" not in result or result.get("length_m") is None
        assert result.get("area_m2") == 42.0


# ---------------------------------------------------------------------------
# AFTER operations
# ---------------------------------------------------------------------------


class TestExecuteAfter:
    def test_empty_ops_returns_empty_applied_list(self):
        exec_ = OperationExecutor(FakeConn())
        assert exec_.execute_after([], {}, geom_wkt="POINT(0 0)") == []

    def test_missing_distant_table_is_skipped(self):
        conn = FakeConn()
        exec_ = OperationExecutor(conn)
        ops = [{"phase": "after", "operation": "count_st_contains", "distant_field": "cnt"}]
        applied = exec_.execute_after(ops, {}, geom_wkt="POINT(0 0)")
        assert applied == []
        assert conn.calls == []

    def test_count_st_contains_updates_distant(self):
        conn = FakeConn()
        exec_ = OperationExecutor(conn)
        ops = [{
            "phase": "after",
            "operation": "count_st_contains",
            "distant_table": "zones",
            "distant_field": "pois_count",
            "table": "pois",
        }]
        applied = exec_.execute_after(ops, {}, geom_wkt="POINT(2.3 48.8)")
        assert len(applied) == 1
        assert applied[0]["table"] == "zones"
        sql = conn.calls[0][0]
        assert "UPDATE zones" in sql
        assert "SET pois_count" in sql
        assert "ST_Contains" in sql

    def test_sum_st_contains_uses_field_parameter(self):
        conn = FakeConn()
        exec_ = OperationExecutor(conn)
        ops = [{
            "phase": "after",
            "operation": "sum_st_contains",
            "distant_table": "zones",
            "distant_field": "total_pop",
            "table": "buildings",
            "field": "population",
        }]
        exec_.execute_after(ops, {}, geom_wkt="POINT(0 0)")
        sql = conn.calls[0][0]
        assert "SUM(buildings.population)" in sql

    def test_string_agg_st_intersects(self):
        conn = FakeConn()
        exec_ = OperationExecutor(conn)
        ops = [{
            "phase": "after",
            "operation": "string_agg_st_intersects",
            "distant_table": "zones",
            "distant_field": "poi_names",
            "table": "pois",
            "field": "name",
        }]
        exec_.execute_after(ops, {}, geom_wkt="POINT(0 0)")
        sql = conn.calls[0][0]
        assert "STRING_AGG" in sql

    def test_distant_filter_appended_to_where(self):
        conn = FakeConn()
        exec_ = OperationExecutor(conn)
        ops = [{
            "phase": "after",
            "operation": "count_st_contains",
            "distant_table": "zones",
            "distant_field": "cnt",
            "table": "pois",
            "distant_filter": "category=N2000",
        }]
        exec_.execute_after(ops, {}, geom_wkt="POINT(0 0)")
        assert "AND category=N2000" in conn.calls[0][0]

    def test_no_geometry_skips_operation(self):
        conn = FakeConn()
        exec_ = OperationExecutor(conn)
        ops = [{
            "phase": "after",
            "operation": "count_st_contains",
            "distant_table": "zones",
            "distant_field": "cnt",
        }]
        assert exec_.execute_after(ops, {}, geom_wkt=None) == []
        assert conn.calls == []

    def test_before_phase_ignored_by_execute_after(self):
        conn = FakeConn()
        exec_ = OperationExecutor(conn)
        ops = [{
            "phase": "before",
            "operation": "count_st_contains",
            "distant_table": "z",
            "distant_field": "c",
        }]
        exec_.execute_after(ops, {}, geom_wkt="POINT(0 0)")
        assert conn.calls == []


class TestAfterSqlInjectionGuards:
    """All table/field/filter inputs in AFTER ops must pass validate_identifier
    or validate_expression. An attacker controlling these via a malicious
    trigger config must not escape to arbitrary DDL."""

    def test_unsafe_distant_table_is_rejected(self):
        conn = FakeConn()
        exec_ = OperationExecutor(conn)
        ops = [{
            "phase": "after",
            "operation": "count_st_contains",
            "distant_table": "zones; DROP TABLE users",
            "distant_field": "cnt",
        }]
        # Silent failure contract: logged + not raised, zero SQL executed
        applied = exec_.execute_after(ops, {}, geom_wkt="POINT(0 0)")
        assert applied == []
        assert conn.calls == []

    def test_unsafe_distant_filter_is_rejected(self):
        conn = FakeConn()
        exec_ = OperationExecutor(conn)
        ops = [{
            "phase": "after",
            "operation": "count_st_contains",
            "distant_table": "zones",
            "distant_field": "cnt",
            "table": "pois",
            "distant_filter": "1=1; DROP TABLE users",
        }]
        applied = exec_.execute_after(ops, {}, geom_wkt="POINT(0 0)")
        assert applied == []
        assert conn.calls == []

    def test_unsafe_table_in_before_is_rejected(self):
        conn = FakeConn(rows_queue=[[{"id": "x"}]])
        exec_ = OperationExecutor(conn)
        ops = [{
            "phase": "before",
            "operation": "st_within",
            "field": "zone_id",
            "table": "zones'; DROP TABLE--",
        }]
        result = exec_.execute_before(ops, {}, geom_wkt="POINT(0 0)")
        assert "zone_id" not in result
        assert conn.calls == []
