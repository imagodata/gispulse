"""Tests for Phase 3 SpatialEngine ABC, DuckDB adapter, engine factory, and EventHub."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import uuid4

import geopandas as gpd
import pytest
from shapely.geometry import Point

from core.models import Project, Trigger, TriggerEvent, TriggerType
from persistence.engine import SpatialEngine
from persistence.engine_factory import create_spatial_engine
from persistence.tier import TierError, check_tier, enforce_engine_tier, get_current_tier


# ------------------------------------------------------------------
# SpatialEngine contract: DuckDBSession
# ------------------------------------------------------------------


class TestDuckDBSpatialEngine:
    """Verify DuckDBSession satisfies the SpatialEngine interface."""

    def test_is_spatial_engine(self):
        from persistence.duckdb_engine import DuckDBSession

        engine = DuckDBSession()
        assert isinstance(engine, SpatialEngine)

    def test_backend_name(self):
        from persistence.duckdb_engine import DuckDBSession

        engine = DuckDBSession()
        assert engine.backend_name == "duckdb"
        assert engine.is_persistent is False

    def test_open_close_lifecycle(self):
        from persistence.duckdb_engine import DuckDBSession

        engine = DuckDBSession()
        engine.open()
        assert engine.conn is not None
        engine.close()

    def test_context_manager(self):
        from persistence.duckdb_engine import DuckDBSession

        with DuckDBSession() as engine:
            assert engine.backend_name == "duckdb"

    def test_register_and_sql_to_gdf(self):
        from persistence.duckdb_engine import DuckDBSession
        import pandas as pd

        # DuckDB cannot register GeoDataFrames with geometry columns directly,
        # so we test with a plain DataFrame (matching real-world usage pattern).
        df = pd.DataFrame({"name": ["a", "b"], "x": [0.0, 1.0]})
        with DuckDBSession() as engine:
            engine.conn.register("test_tbl", df)
            result = engine.sql_to_gdf("SELECT * FROM test_tbl")
            assert len(result) == 2

    def test_execute_sql(self):
        from persistence.duckdb_engine import DuckDBSession

        with DuckDBSession() as engine:
            rows = engine.execute_sql("SELECT 1 AS n")
            assert rows == [{"n": 1}]

    def test_load_write_roundtrip(self, tmp_path: Path):
        from persistence.duckdb_engine import DuckDBSession

        gdf = gpd.GeoDataFrame(
            {"val": [10, 20]},
            geometry=[Point(0, 0), Point(1, 1)],
            crs="EPSG:4326",
        )
        out = tmp_path / "test.gpkg"
        with DuckDBSession() as engine:
            ref = engine.write_layer(gdf, str(out), layer="pts")
            assert Path(ref).exists()

            layers = engine.list_layers(str(out))
            assert "pts" in layers

            loaded = engine.load_layer(str(out), layer="pts")
            assert len(loaded) == 2


# ------------------------------------------------------------------
# Engine factory
# ------------------------------------------------------------------


class TestEngineFactory:
    def test_default_gpkg(self):
        engine = create_spatial_engine()
        assert engine.backend_name == "gpkg"

    def test_explicit_duckdb(self):
        engine = create_spatial_engine("duckdb")
        assert engine.backend_name == "duckdb"

    def test_postgis_without_dsn_raises(self):
        from persistence.tier import make_test_license_key
        env = {"GISPULSE_DSN": "", "GISPULSE_TIER": "pro", "GISPULSE_LICENCE_SKIP_VERIFY": "true", "GISPULSE_LICENSE_KEY": make_test_license_key("pro")}
        with patch.dict(os.environ, env, clear=False):
            with pytest.raises(ValueError, match="DSN"):
                create_spatial_engine("postgis")

    def test_unknown_backend_raises(self):
        with pytest.raises(ValueError, match="Unknown engine"):
            create_spatial_engine("mysql")

    def test_env_var_override(self):
        with patch.dict(os.environ, {"GISPULSE_ENGINE": "duckdb"}, clear=False):
            engine = create_spatial_engine()
            assert engine.backend_name == "duckdb"


# ------------------------------------------------------------------
# Tier gating
# ------------------------------------------------------------------


class TestTierGating:
    """Tests for Community vs Pro tier enforcement."""

    def test_community_allows_duckdb(self):
        with patch.dict(os.environ, {"GISPULSE_TIER": "community"}, clear=False):
            engine = create_spatial_engine("duckdb")
            assert engine.backend_name == "duckdb"

    def test_community_blocks_postgis(self):
        env = {"GISPULSE_TIER": "community", "GISPULSE_DSN": "postgresql://x@localhost/db"}
        with patch.dict(os.environ, env, clear=False):
            with pytest.raises(TierError, match="Pro"):
                create_spatial_engine("postgis")

    def test_community_blocks_hybrid(self):
        env = {"GISPULSE_TIER": "community", "GISPULSE_DSN": "postgresql://x@localhost/db"}
        with patch.dict(os.environ, env, clear=False):
            with pytest.raises(TierError, match="Pro"):
                create_spatial_engine("hybrid")

    def test_pro_allows_postgis(self):
        env = {
            "GISPULSE_TIER": "pro",
            "GISPULSE_LICENCE_SKIP_VERIFY": "true",
            "GISPULSE_LICENSE_KEY": "eyJvcmciOiAidGVzdCIsICJ0aWVyIjogInBybyIsICJleHAiOiAiMjAzMC0wMS0wMVQwMDowMDowMFoifQ.AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
            "GISPULSE_DSN": "postgresql://x@localhost/db",
        }
        with patch.dict(os.environ, env, clear=False):
            # PostGIS instantiation will fail without a real DB, but tier check passes
            # so we expect a connection error, not a TierError.
            try:
                create_spatial_engine("postgis")
            except TierError:
                pytest.fail("TierError raised for pro tier — should be allowed")
            except Exception:
                pass  # connection errors are expected without a real DB

    def test_pro_without_license_key_raises(self):
        env = {"GISPULSE_TIER": "pro", "GISPULSE_LICENSE_KEY": ""}
        with patch.dict(os.environ, env, clear=False):
            with pytest.raises(TierError, match="license key"):
                create_spatial_engine("postgis")

    def test_default_tier_is_community(self):
        env_remove = {
            k: "" for k in ("GISPULSE_TIER",) if k in os.environ
        }
        with patch.dict(os.environ, env_remove, clear=False):
            os.environ.pop("GISPULSE_TIER", None)
            assert get_current_tier() == "community"

    def test_unknown_tier_falls_back_to_community(self):
        with patch.dict(os.environ, {"GISPULSE_TIER": "ultimate"}, clear=False):
            assert get_current_tier() == "community"

    def test_check_tier_unknown_required(self):
        with pytest.raises(ValueError, match="Unknown tier"):
            check_tier("platinum")

    def test_enforce_engine_tier_duckdb_always_passes(self):
        with patch.dict(os.environ, {"GISPULSE_TIER": "community"}, clear=False):
            enforce_engine_tier("duckdb")  # should not raise

    def test_enterprise_allows_everything(self):
        from persistence.tier import make_test_license_key
        env = {"GISPULSE_TIER": "enterprise", "GISPULSE_LICENCE_SKIP_VERIFY": "true", "GISPULSE_LICENSE_KEY": make_test_license_key("enterprise")}
        with patch.dict(os.environ, env, clear=False):
            enforce_engine_tier("duckdb")
            enforce_engine_tier("postgis")
            enforce_engine_tier("hybrid")


# ------------------------------------------------------------------
# Project model
# ------------------------------------------------------------------


class TestProjectModel:
    def test_defaults(self):
        p = Project(name="test")
        assert p.name == "test"
        assert p.engine_backend == "duckdb"
        assert p.schema_name == "public"
        assert p.datasets == []
        assert p.rules == []
        assert p.triggers == []

    def test_postgis_project(self):
        p = Project(
            name="prod",
            engine_backend="postgis",
            dsn="postgresql://user:pass@localhost/db",
            schema_name="gis_project_1",
        )
        assert p.engine_backend == "postgis"
        assert p.dsn is not None


# ------------------------------------------------------------------
# EventHub
# ------------------------------------------------------------------


class TestEventHub:
    def test_broadcast_no_subscribers(self):
        from gispulse.adapters.http.event_hub import EventHub

        hub = EventHub()
        hub.broadcast("test")  # should not raise

    def test_subscribe_receive(self):
        from gispulse.adapters.http.event_hub import EventHub

        hub = EventHub()
        q = hub.subscribe()
        hub.broadcast("layer_updated", {"table": "public.test"})
        assert q.qsize() == 1
        msg = json.loads(q.get_nowait())
        assert msg["type"] == "layer_updated"
        assert msg["data"]["table"] == "public.test"

    def test_unsubscribe(self):
        from gispulse.adapters.http.event_hub import EventHub

        hub = EventHub()
        q = hub.subscribe()
        assert hub.subscriber_count == 1
        hub.unsubscribe(q)
        assert hub.subscriber_count == 0


# ------------------------------------------------------------------
# TriggerManager (unit — mocked engine)
# ------------------------------------------------------------------


class TestTriggerManager:
    def test_dispatch_calls_runner(self):
        from gispulse.adapters.esb.trigger_manager import TriggerManager

        mock_engine = MagicMock()
        runner_calls = []

        def mock_runner(rule_id, table, row_id):
            runner_calls.append((rule_id, table, row_id))

        mgr = TriggerManager(engine=mock_engine, rule_runner=mock_runner)

        trigger = Trigger(
            name="test_trigger",
            trigger_type=TriggerType.DML,
            event=TriggerEvent.DATA_CHANGED,
            rule_id=uuid4(),
            conditions={"table": "parcelles", "schema": "public"},
        )
        mgr._installed["public.parcelles"] = trigger

        payload = json.dumps({
            "trigger_id": str(trigger.id),
            "table": "public.parcelles",
            "operation": "INSERT",
            "row_id": "abc-123",
        })
        mgr.dispatch(payload)

        assert len(runner_calls) == 1
        assert runner_calls[0][0] == trigger.rule_id

    def test_dispatch_no_match(self):
        from gispulse.adapters.esb.trigger_manager import TriggerManager

        mgr = TriggerManager(engine=MagicMock())
        payload = json.dumps({"table": "unknown", "operation": "INSERT"})
        mgr.dispatch(payload)  # should not raise

    def test_install_all(self):
        from gispulse.adapters.esb.trigger_manager import TriggerManager

        mock_engine = MagicMock()
        mgr = TriggerManager(engine=mock_engine)

        triggers = [
            Trigger(
                name="t1",
                trigger_type=TriggerType.DML,
                enabled=True,
                conditions={"table": "t1", "schema": "public"},
            ),
            Trigger(
                name="t2",
                trigger_type=TriggerType.DML,
                enabled=False,
                conditions={"table": "t2", "schema": "public"},
            ),
            Trigger(
                name="t3",
                trigger_type=TriggerType.SCHEDULE,
                enabled=True,
                conditions={},
            ),
        ]
        count = mgr.install_all(triggers)
        assert count == 1  # only t1 is DML + enabled
