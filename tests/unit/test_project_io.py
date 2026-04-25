"""Tests for persistence.project_io — YAML/JSON ↔ GPKG project round-trip.

project_io is how users export/import their declarative config (rules,
triggers, ref_layers, relations, scenarios) for version control. Bugs
silently drop entries or corrupt YAML — hard to notice until import
time.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from persistence.gpkg_schema import bootstrap_gpkg_project
from persistence.project_io import (
    _strip_defaults,
    _to_dict,
    export_project,
    import_project,
)


@pytest.fixture
def empty_gpkg(tmp_path) -> Path:
    """Bootstrap a fresh empty GPKG project."""
    path = tmp_path / "project.gpkg"
    conn = sqlite3.connect(str(path))
    try:
        bootstrap_gpkg_project(conn)
    finally:
        conn.close()
    return path


@pytest.fixture
def populated_gpkg(tmp_path) -> Path:
    """GPKG with a rule + trigger pre-seeded via direct INSERT."""
    path = tmp_path / "populated.gpkg"
    conn = sqlite3.connect(str(path))
    try:
        bootstrap_gpkg_project(conn)
        # Rule
        conn.execute(
            "INSERT INTO _gispulse_rules (id, name, description, capability, config, enabled) "
            "VALUES ('rule-1', 'buffer_50m', 'Buffer rule', 'buffer', ?, 1)",
            (json.dumps({"distance": 50}),),
        )
        # Trigger
        conn.execute(
            "INSERT INTO _gispulse_triggers "
            "(id, name, trigger_type, conditions, predicates, actions, enabled) "
            "VALUES ('trig-1', 'on_insert', 'dml', ?, '[]', '[]', 1)",
            (json.dumps({"table": "parcels", "operation": "INSERT"}),),
        )
        conn.commit()
    finally:
        conn.close()
    return path


# ---------------------------------------------------------------------------
# _to_dict helper
# ---------------------------------------------------------------------------


class TestToDict:
    def test_serialises_uuid_as_string(self):
        from uuid import uuid4
        from core.models import Rule

        rule = Rule(id=uuid4(), name="r")
        d = _to_dict(rule)
        assert isinstance(d["id"], str)

    def test_serialises_datetime_as_iso(self):
        from datetime import datetime, timezone
        from dataclasses import dataclass, field

        @dataclass
        class WithDate:
            when: datetime = field(default_factory=lambda: datetime(2026, 4, 17, tzinfo=timezone.utc))

        d = _to_dict(WithDate())
        assert "2026-04-17" in d["when"]


# ---------------------------------------------------------------------------
# _strip_defaults
# ---------------------------------------------------------------------------


class TestStripDefaults:
    def test_current_impl_is_effectively_a_noop(self):
        """Pin a known quirk: _strip_defaults has a bug in its condition
        (``f.default is not f.default`` is always False), so no defaults are
        ever stripped. Pin this so a future fix is a deliberate change."""
        from dataclasses import dataclass

        @dataclass
        class Obj:
            a: int = 0
            b: str = ""

        data = {"a": 0, "b": "custom"}
        result = _strip_defaults(data, Obj)
        # Current (buggy) behaviour: returns everything unchanged
        assert result == data


# ---------------------------------------------------------------------------
# export_project (JSON)
# ---------------------------------------------------------------------------


class TestExportJson:
    def test_empty_gpkg_exports_minimal_project(self, empty_gpkg, tmp_path):
        out = tmp_path / "out.json"
        export_project(empty_gpkg, out, format="json")
        assert out.exists()
        data = json.loads(out.read_text(encoding="utf-8"))
        assert data["version"]
        assert data["source"] == "project.gpkg"
        # Empty tables should not emit keys
        assert "rules" not in data or data["rules"] == []

    def test_populated_project_exports_rules(self, populated_gpkg, tmp_path):
        out = tmp_path / "out.json"
        export_project(populated_gpkg, out, format="json")
        data = json.loads(out.read_text(encoding="utf-8"))
        assert len(data["rules"]) == 1
        rule = data["rules"][0]
        assert rule["name"] == "buffer_50m"
        assert rule["capability"] == "buffer"
        # JSON columns must be parsed back to dicts
        assert isinstance(rule["config"], dict)
        assert rule["config"]["distance"] == 50

    def test_populated_project_exports_triggers(self, populated_gpkg, tmp_path):
        out = tmp_path / "out.json"
        export_project(populated_gpkg, out, format="json")
        data = json.loads(out.read_text(encoding="utf-8"))
        assert len(data["triggers"]) == 1
        trig = data["triggers"][0]
        assert trig["name"] == "on_insert"
        assert isinstance(trig["conditions"], dict)

    def test_include_ids_false_strips_id(self, populated_gpkg, tmp_path):
        out = tmp_path / "out.json"
        export_project(populated_gpkg, out, format="json", include_ids=False)
        data = json.loads(out.read_text(encoding="utf-8"))
        for rule in data.get("rules", []):
            assert "id" not in rule

    def test_creates_parent_dir(self, empty_gpkg, tmp_path):
        out = tmp_path / "nested" / "deep" / "out.json"
        export_project(empty_gpkg, out, format="json")
        assert out.exists()

    def test_format_auto_detects_json_from_extension(
        self, populated_gpkg, tmp_path
    ):
        out = tmp_path / "auto.json"
        export_project(populated_gpkg, out, format="auto")
        # File should be valid JSON (not YAML)
        data = json.loads(out.read_text(encoding="utf-8"))
        assert "version" in data

    def test_format_auto_default_to_json_for_unknown_ext(
        self, populated_gpkg, tmp_path
    ):
        out = tmp_path / "unknown.txt"
        export_project(populated_gpkg, out, format="auto")
        # Falls through to JSON (default branch)
        data = json.loads(out.read_text(encoding="utf-8"))
        assert "version" in data


# ---------------------------------------------------------------------------
# export_project (YAML) — optional, skip if pyyaml missing
# ---------------------------------------------------------------------------


yaml = pytest.importorskip("yaml", reason="pyyaml not installed")


class TestExportYaml:
    def test_yaml_export(self, populated_gpkg, tmp_path):
        out = tmp_path / "out.yaml"
        export_project(populated_gpkg, out, format="yaml")
        data = yaml.safe_load(out.read_text(encoding="utf-8"))
        assert len(data["rules"]) == 1
        assert data["rules"][0]["name"] == "buffer_50m"

    def test_format_auto_detects_yaml_from_extension(
        self, populated_gpkg, tmp_path
    ):
        out = tmp_path / "auto.yml"
        export_project(populated_gpkg, out, format="auto")
        data = yaml.safe_load(out.read_text(encoding="utf-8"))
        assert "version" in data


# ---------------------------------------------------------------------------
# import_project
# ---------------------------------------------------------------------------


class TestImportJson:
    def test_import_into_fresh_gpkg(self, tmp_path):
        # Write a config file
        config = {
            "version": 1,
            "rules": [
                {
                    "id": "r1",
                    "name": "filter_high",
                    "description": "",
                    "capability": "filter",
                    "config": {"expression": "value > 10"},
                    "enabled": True,
                }
            ],
        }
        config_path = tmp_path / "config.json"
        config_path.write_text(json.dumps(config), encoding="utf-8")

        gpkg = tmp_path / "new_project.gpkg"
        stats = import_project(config_path, gpkg, merge=True)
        assert stats.get("rules") == 1

        # Verify the rule landed in the GPKG
        conn = sqlite3.connect(str(gpkg))
        try:
            row = conn.execute(
                "SELECT name, capability FROM _gispulse_rules WHERE id='r1'"
            ).fetchone()
        finally:
            conn.close()
        assert row[0] == "filter_high"
        assert row[1] == "filter"

    def test_import_non_dict_raises(self, tmp_path):
        config_path = tmp_path / "bad.json"
        config_path.write_text(json.dumps(["not a dict"]), encoding="utf-8")
        gpkg = tmp_path / "out.gpkg"
        with pytest.raises(ValueError, match="JSON/YAML object"):
            import_project(config_path, gpkg)

    def test_import_preserves_nested_dicts(self, tmp_path):
        """config/conditions fields are round-tripped as JSON strings in DB
        but the exported YAML/JSON representation must be a dict."""
        config = {
            "version": 1,
            "rules": [
                {
                    "id": "rx",
                    "name": "complex",
                    "capability": "buffer",
                    "config": {"distance": 50, "crs": "EPSG:3857"},
                    "enabled": True,
                }
            ],
        }
        config_path = tmp_path / "c.json"
        config_path.write_text(json.dumps(config), encoding="utf-8")
        gpkg = tmp_path / "out.gpkg"
        import_project(config_path, gpkg)

        # Round-trip: export and compare
        exported = tmp_path / "exported.json"
        export_project(gpkg, exported, format="json")
        data = json.loads(exported.read_text(encoding="utf-8"))
        rule = next(r for r in data["rules"] if r["id"] == "rx")
        assert rule["config"] == {"distance": 50, "crs": "EPSG:3857"}

    def test_import_merge_upserts_existing(self, tmp_path):
        gpkg = tmp_path / "project.gpkg"
        # First import
        config1 = {
            "version": 1,
            "rules": [
                {"id": "r1", "name": "v1", "capability": "buffer", "enabled": True}
            ],
        }
        path1 = tmp_path / "c1.json"
        path1.write_text(json.dumps(config1), encoding="utf-8")
        import_project(path1, gpkg, merge=True)

        # Second import with same id but different name
        config2 = {
            "version": 1,
            "rules": [
                {"id": "r1", "name": "v2_updated", "capability": "buffer", "enabled": True}
            ],
        }
        path2 = tmp_path / "c2.json"
        path2.write_text(json.dumps(config2), encoding="utf-8")
        import_project(path2, gpkg, merge=True)

        # Should still have exactly one row with the updated name
        conn = sqlite3.connect(str(gpkg))
        try:
            rows = conn.execute(
                "SELECT name FROM _gispulse_rules WHERE id='r1'"
            ).fetchall()
        finally:
            conn.close()
        assert len(rows) == 1
        assert rows[0][0] == "v2_updated"

    def test_empty_table_key_is_skipped(self, tmp_path):
        """Config with empty lists shouldn't create spurious inserts."""
        config = {
            "version": 1,
            "rules": [],
            "triggers": [],
        }
        path = tmp_path / "empty.json"
        path.write_text(json.dumps(config), encoding="utf-8")
        gpkg = tmp_path / "out.gpkg"
        stats = import_project(path, gpkg)
        assert stats == {}


class TestImportYaml:
    def test_yaml_import(self, tmp_path):
        config_path = tmp_path / "c.yaml"
        config_path.write_text(
            "version: 1\n"
            "rules:\n"
            "  - id: y1\n"
            "    name: yaml_rule\n"
            "    capability: buffer\n"
            "    enabled: true\n",
            encoding="utf-8",
        )
        gpkg = tmp_path / "out.gpkg"
        stats = import_project(config_path, gpkg)
        assert stats.get("rules") == 1


# ---------------------------------------------------------------------------
# Round-trip (export → import → export) parity
# ---------------------------------------------------------------------------


class TestRoundTrip:
    def test_export_import_export_parity(self, populated_gpkg, tmp_path):
        """Exporting a project, importing into a fresh GPKG, and re-exporting
        must produce equivalent rule metadata."""
        first_out = tmp_path / "first.json"
        export_project(populated_gpkg, first_out, format="json")

        new_gpkg = tmp_path / "new.gpkg"
        import_project(first_out, new_gpkg, merge=False)

        second_out = tmp_path / "second.json"
        export_project(new_gpkg, second_out, format="json")

        first_data = json.loads(first_out.read_text(encoding="utf-8"))
        second_data = json.loads(second_out.read_text(encoding="utf-8"))

        # Rule names/configs preserved
        assert {r["name"] for r in first_data["rules"]} == {
            r["name"] for r in second_data["rules"]
        }
