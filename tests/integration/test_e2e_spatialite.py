"""
E2E integration tests — cycle complet GISPulse sans PostGIS.

Cycle couvert :
    GPKG (input)
        → SpatiaLiteSession.load_gpkg()
        → RuleEngine.apply_all()            [filter / buffer]
        → session.conn (insert SQLite)
        → _process_pending_changes()        [TriggerEvaluator]
        → commit_to_gpkg()
    GPKG (output enrichi)

Aucun PostGIS requis — SpatiaLite en mémoire uniquement.
"""
from __future__ import annotations

import geopandas as gpd
import pytest
from shapely.geometry import box

from gispulse.core.models import Trigger, TriggerEvent, TriggerType
from gispulse.persistence.spatialite_session import SpatiaLiteSession
from gispulse.persistence.session_provisioner import SessionProvisioner
from gispulse.rules.engine import RuleEngine
from gispulse.core.models import Rule


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def parcels_gpkg(tmp_path) -> str:
    """GPKG source — 4 parcelles avec attribut 'area'."""
    gdf = gpd.GeoDataFrame(
        {
            "name": ["A", "B", "C", "D"],
            "area": [50.0, 150.0, 200.0, 10.0],
        },
        geometry=[
            box(0, 0, 1, 1),
            box(1, 0, 2, 1),
            box(2, 0, 3, 1),
            box(3, 0, 4, 1),
        ],
        crs="EPSG:4326",
    )
    path = str(tmp_path / "parcels.gpkg")
    gdf.to_file(path, layer="parcels", driver="GPKG")
    return path


@pytest.fixture
def session_mem() -> SpatiaLiteSession:
    """Session SpatiaLite en mémoire, ouverte."""
    s = SpatiaLiteSession(db_path=":memory:")
    s.open()
    yield s
    s.close()


@pytest.fixture
def insert_trigger() -> Trigger:
    """Trigger qui matche les INSERT sur la table 'parcels'."""
    return Trigger(
        name="on_parcel_insert",
        event=TriggerEvent.DATA_CHANGED,
        trigger_type=TriggerType.DML,
        conditions={"table": "parcels", "operation": "INSERT"},
        enabled=True,
    )


@pytest.fixture
def update_trigger() -> Trigger:
    """Trigger qui matche les UPDATE sur la table 'parcels'."""
    return Trigger(
        name="on_parcel_update",
        event=TriggerEvent.DATA_CHANGED,
        trigger_type=TriggerType.DML,
        conditions={"table": "parcels", "operation": "UPDATE"},
        enabled=True,
    )


# ---------------------------------------------------------------------------
# 1. Cycle basique : load_gpkg → commit_to_gpkg (sans règle ni trigger)
# ---------------------------------------------------------------------------


class TestBasicGpkgRoundtrip:
    def test_load_then_commit(self, parcels_gpkg, session_mem, tmp_path):
        """GPKG → SpatiaLite → GPKG : données préservées."""
        session_mem.load_gpkg(parcels_gpkg, layer="parcels")

        out_path = str(tmp_path / "out.gpkg")
        session_mem.commit_to_gpkg(out_path, layer="parcels")

        gdf = gpd.read_file(out_path)
        assert len(gdf) == 4
        assert set(gdf["name"]) == {"A", "B", "C", "D"}

    def test_load_preserves_attributes(self, parcels_gpkg, session_mem, tmp_path):
        """Les attributs numériques sont conservés après le roundtrip."""
        session_mem.load_gpkg(parcels_gpkg, layer="parcels")
        out_path = str(tmp_path / "out.gpkg")
        session_mem.commit_to_gpkg(out_path, layer="parcels")

        gdf = gpd.read_file(out_path)
        row_b = gdf[gdf["name"] == "B"].iloc[0]
        assert float(row_b["area"]) == pytest.approx(150.0)

    def test_load_preserves_geometry(self, parcels_gpkg, session_mem, tmp_path):
        """Les géométries WKT sont reconstruites correctement."""
        session_mem.load_gpkg(parcels_gpkg, layer="parcels")
        out_path = str(tmp_path / "out.gpkg")
        session_mem.commit_to_gpkg(out_path, layer="parcels")

        gdf = gpd.read_file(out_path)
        assert all(g is not None for g in gdf.geometry)
        assert all(g.geom_type == "Polygon" for g in gdf.geometry)

    def test_table_registered(self, parcels_gpkg, session_mem):
        """load_gpkg enregistre la table dans _tables."""
        session_mem.load_gpkg(parcels_gpkg, layer="parcels")
        assert "parcels" in session_mem._tables

    def test_change_log_created(self, parcels_gpkg, session_mem):
        """_change_log est créée après open()."""
        tables = {
            row[0]
            for row in session_mem.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "_change_log" in tables


# ---------------------------------------------------------------------------
# 2. Cycle avec RuleEngine : filter → commit
# ---------------------------------------------------------------------------


class TestRuleEngineCycle:
    def test_filter_rule_via_engine(self, parcels_gpkg, tmp_path):
        """RuleEngine filter + roundtrip GPKG → seules les grandes parcelles."""
        gdf = gpd.read_file(parcels_gpkg, layer="parcels")

        rule = Rule(
            name="large_only",
            capability="filter",
            config={"expression": "area > 100"},
        )
        engine = RuleEngine()
        filtered = engine.apply(rule, gdf)

        # Écrire le résultat filtré dans une session SpatiaLite
        s = SpatiaLiteSession(db_path=":memory:")
        s.open()
        try:
            # Injecter le GeoDataFrame filtré dans SQLite

            df = filtered.copy()
            df["geometry"] = filtered.geometry.apply(lambda g: g.wkt if g else None)
            df.to_sql("parcels", s.conn, if_exists="replace", index=False)
            s.conn.commit()
            s._tables.append("parcels")

            out_path = str(tmp_path / "filtered.gpkg")
            s.commit_to_gpkg(out_path, layer="parcels")
        finally:
            s.close()

        result = gpd.read_file(out_path)
        assert len(result) == 2  # B (150) et C (200)
        assert set(result["name"]) == {"B", "C"}

    def test_buffer_rule_via_engine(self, parcels_gpkg, tmp_path):
        """RuleEngine buffer → géométries agrandies."""
        gdf = gpd.read_file(parcels_gpkg, layer="parcels")

        rule = Rule(
            name="buf_01",
            capability="buffer",
            config={"distance": 0.1},
        )
        engine = RuleEngine()
        buffered = engine.apply(rule, gdf)

        # Les géométries bufférisées ont une aire > originale
        for orig, buf in zip(gdf.geometry, buffered.geometry):
            assert buf.area > orig.area


# ---------------------------------------------------------------------------
# 3. Cycle avec Triggers : changements SQLite → FiredTrigger
# ---------------------------------------------------------------------------


class TestTriggerCycle:
    def test_insert_fires_trigger(
        self, parcels_gpkg, session_mem, insert_trigger, tmp_path
    ):
        """Un INSERT dans la session déclenche le trigger configuré."""
        session_mem.load_gpkg(parcels_gpkg, layer="parcels")
        session_mem._triggers = [insert_trigger]

        # Simuler un INSERT (nouvel enregistrement)
        session_mem.conn.execute(
            "INSERT INTO parcels (name, area, geometry) VALUES (?, ?, ?)",
            ("E", 300.0, "POLYGON((4 0, 5 0, 5 1, 4 1, 4 0))"),
        )
        session_mem.conn.commit()

        fired = session_mem._process_pending_changes("test_session")
        assert len(fired) >= 1
        matched = [f for f in fired if f.matched]
        assert len(matched) == 1
        assert matched[0].result_summary["trigger_name"] == "on_parcel_insert"

    def test_update_fires_trigger(
        self, parcels_gpkg, session_mem, update_trigger, tmp_path
    ):
        """Un UPDATE dans la session déclenche le trigger UPDATE."""
        session_mem.load_gpkg(parcels_gpkg, layer="parcels")
        session_mem._triggers = [update_trigger]

        session_mem.conn.execute(
            "UPDATE parcels SET area = 999 WHERE name = 'A'"
        )
        session_mem.conn.commit()

        fired = session_mem._process_pending_changes("test_session")
        matched = [f for f in fired if f.matched]
        assert len(matched) >= 1
        assert matched[0].result_summary["trigger_name"] == "on_parcel_update"

    def test_insert_trigger_not_match_update(
        self, parcels_gpkg, session_mem, insert_trigger
    ):
        """Un trigger configuré pour INSERT ne match pas un UPDATE."""
        session_mem.load_gpkg(parcels_gpkg, layer="parcels")
        session_mem._triggers = [insert_trigger]

        session_mem.conn.execute("UPDATE parcels SET area = 1 WHERE name = 'A'")
        session_mem.conn.commit()

        fired = session_mem._process_pending_changes("test_session")
        matched = [f for f in fired if f.matched]
        assert len(matched) == 0

    def test_change_log_marked_processed(
        self, parcels_gpkg, session_mem, insert_trigger
    ):
        """Les entrées _change_log sont marquées processed=1 après évaluation."""
        session_mem.load_gpkg(parcels_gpkg, layer="parcels")
        session_mem._triggers = [insert_trigger]

        session_mem.conn.execute(
            "INSERT INTO parcels (name, area, geometry) VALUES (?, ?, ?)",
            ("F", 50.0, "POINT(0 0)"),
        )
        session_mem.conn.commit()

        session_mem._process_pending_changes("s")

        unprocessed = session_mem.conn.execute(
            "SELECT COUNT(*) FROM _change_log WHERE processed = 0"
        ).fetchone()[0]
        assert unprocessed == 0

    def test_fired_triggers_accumulated(
        self, parcels_gpkg, session_mem, insert_trigger
    ):
        """Les FiredTrigger sont accumulés dans session.fired_triggers."""
        from gispulse.rules.trigger_evaluator import TriggerEvaluator

        session_mem.load_gpkg(parcels_gpkg, layer="parcels")
        session_mem._triggers = [insert_trigger]
        session_mem._evaluator = TriggerEvaluator()

        session_mem.conn.execute(
            "INSERT INTO parcels (name, area, geometry) VALUES (?, ?, ?)",
            ("G", 100.0, "POINT(1 1)"),
        )
        session_mem.conn.commit()
        session_mem._process_pending_changes("s")

        assert len(session_mem.fired_triggers) >= 1

    def test_clear_fired(self, parcels_gpkg, session_mem, insert_trigger):
        """clear_fired() vide le registre des FiredTrigger."""
        from gispulse.rules.trigger_evaluator import TriggerEvaluator

        session_mem.load_gpkg(parcels_gpkg, layer="parcels")
        session_mem._triggers = [insert_trigger]
        session_mem._evaluator = TriggerEvaluator()

        session_mem.conn.execute(
            "INSERT INTO parcels (name, area, geometry) VALUES (?, ?, ?)",
            ("H", 100.0, "POINT(1 1)"),
        )
        session_mem.conn.commit()
        session_mem._process_pending_changes("s")

        session_mem.clear_fired()
        assert session_mem.fired_triggers == []


# ---------------------------------------------------------------------------
# 4. Cycle complet E2E : GPKG → règle → trigger → export
# ---------------------------------------------------------------------------


class TestFullE2ECycle:
    def test_full_pipeline(self, parcels_gpkg, tmp_path):
        """
        Cycle E2E complet sans PostGIS :

        1. Charger GPKG dans SpatiaLite
        2. Lire + filtrer via RuleEngine (area > 100)
        3. Écrire le résultat dans la session
        4. Insérer une nouvelle parcelle → déclencher un trigger
        5. Exporter vers GPKG enrichi
        6. Vérifier contenu GPKG + FiredTrigger
        """
        # -- Setup ----------------------------------------------------------
        trigger = Trigger(
            name="new_large_parcel",
            event=TriggerEvent.DATA_CHANGED,
            trigger_type=TriggerType.DML,
            conditions={"table": "result", "operation": "INSERT"},
            enabled=True,
        )

        s = SpatiaLiteSession(db_path=":memory:")
        s.open()

        try:
            # 1. Charger GPKG
            s.load_gpkg(parcels_gpkg, layer="parcels")

            # 2. Lire + filtrer
            gdf = gpd.read_file(parcels_gpkg, layer="parcels")
            rule = Rule(
                name="filter_large",
                capability="filter",
                config={"expression": "area > 100"},
            )
            engine = RuleEngine()
            filtered = engine.apply(rule, gdf)
            assert len(filtered) == 2

            # 3. Écrire résultat filtré dans une nouvelle table "result"

            df = filtered.copy()
            df["geometry"] = filtered.geometry.apply(lambda g: g.wkt if g else None)
            df.to_sql("result", s.conn, if_exists="replace", index=False)
            s.conn.commit()
            s._tables.append("result")

            # 4. Insérer une nouvelle parcelle dans "result"
            #    (le SQLite trigger la capture dans _change_log)
            from gispulse.persistence.spatialite_session import _build_sqlite_triggers

            for sql in _build_sqlite_triggers("result"):
                s.conn.execute(sql)
            s.conn.commit()

            from gispulse.rules.trigger_evaluator import TriggerEvaluator

            s._triggers = [trigger]
            s._evaluator = TriggerEvaluator()
            s.conn.execute(
                "INSERT INTO result (name, area, geometry) VALUES (?, ?, ?)",
                ("X_new", 500.0, "POLYGON((10 10, 11 10, 11 11, 10 11, 10 10))"),
            )
            s.conn.commit()

            fired = s._process_pending_changes("e2e_session")

            # 5. Export GPKG enrichi
            out_path = str(tmp_path / "enriched.gpkg")
            s.commit_to_gpkg(out_path, layer="result")

        finally:
            s.close()

        # 6. Assertions -------------------------------------------------------
        # Contenu GPKG
        result_gdf = gpd.read_file(out_path)
        assert len(result_gdf) == 3  # B, C + X_new
        assert "X_new" in set(result_gdf["name"])

        # FiredTrigger
        matched = [f for f in fired if f.matched]
        assert len(matched) == 1
        assert matched[0].result_summary["trigger_name"] == "new_large_parcel"
        assert matched[0].eval_time_ms >= 0

    def test_e2e_no_postgis_required(self, parcels_gpkg):
        """Confirme que le cycle E2E fonctionne sans variable GISPULSE_BASE_DSN."""
        import os

        # Aucune variable PostGIS configurée
        dsn = os.environ.get("GISPULSE_BASE_DSN", "")
        provisioner = SessionProvisioner(base_dsn=dsn)
        session_model = provisioner.create_session(
            source_client="test", backend="auto"
        )

        # Sans PostGIS, le backend doit être SpatiaLite
        from gispulse.core.models import SessionBackend

        if not dsn:
            assert session_model.backend == SessionBackend.SPATIALITE

    def test_e2e_multiple_triggers(self, parcels_gpkg, tmp_path):
        """Plusieurs triggers évalués en parallèle sur le même changement."""
        t_insert = Trigger(
            name="on_insert",
            event=TriggerEvent.DATA_CHANGED,
            trigger_type=TriggerType.DML,
            conditions={"table": "parcels", "operation": "INSERT"},
            enabled=True,
        )
        t_any = Trigger(
            name="on_any",
            event=TriggerEvent.DATA_CHANGED,
            trigger_type=TriggerType.DML,
            conditions={},  # pas de filtre → match tout
            enabled=True,
        )
        t_disabled = Trigger(
            name="disabled",
            event=TriggerEvent.DATA_CHANGED,
            trigger_type=TriggerType.DML,
            conditions={"table": "parcels", "operation": "INSERT"},
            enabled=False,
        )

        s = SpatiaLiteSession(db_path=":memory:")
        s.open()
        try:
            s.load_gpkg(parcels_gpkg, layer="parcels")
            s._triggers = [t_insert, t_any, t_disabled]

            s.conn.execute(
                "INSERT INTO parcels (name, area, geometry) VALUES (?, ?, ?)",
                ("Z", 42.0, "POINT(0 0)"),
            )
            s.conn.commit()
            fired = s._process_pending_changes("multi")
        finally:
            s.close()

        matched = [f for f in fired if f.matched]
        trigger_names = {f.result_summary["trigger_name"] for f in matched}

        # on_insert et on_any matchent, disabled ne matche pas
        assert "on_insert" in trigger_names
        assert "on_any" in trigger_names
        assert "disabled" not in trigger_names


# ---------------------------------------------------------------------------
# 5. SessionProvisioner — sélection backend auto
# ---------------------------------------------------------------------------


class TestSessionProvisionerBackend:
    def test_auto_selects_spatialite_no_dsn(self):
        """backend='auto' sans base_dsn → SpatiaLite."""
        from gispulse.core.models import SessionBackend

        p = SessionProvisioner(base_dsn="")
        s = p.create_session(backend="auto")
        assert s.backend == SessionBackend.SPATIALITE

    def test_explicit_spatialite(self):
        """backend='spatialite' forcé."""
        from gispulse.core.models import SessionBackend

        p = SessionProvisioner(base_dsn="postgresql://x/y")
        s = p.create_session(backend="spatialite")
        assert s.backend == SessionBackend.SPATIALITE

    def test_explicit_postgis(self):
        """backend='postgis' forcé."""
        from gispulse.core.models import SessionBackend

        p = SessionProvisioner(base_dsn="postgresql://host/db")
        s = p.create_session(backend="postgis")
        assert s.backend == SessionBackend.POSTGIS

    def test_auto_selects_postgis_with_dsn(self):
        """backend='auto' avec base_dsn → PostGIS."""
        from gispulse.core.models import SessionBackend

        p = SessionProvisioner(base_dsn="postgresql://host/db")
        s = p.create_session(backend="auto")
        assert s.backend == SessionBackend.POSTGIS
