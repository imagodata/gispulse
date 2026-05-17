"""Tests for v1.6.x config_loader extensions.

Coverage:
- ``ValidateRuleConfigModel.table`` (per-rule pin) parses cleanly.
- ``GISPulseConfig.default_table`` (top-level fallback) parses cleanly.
- ``GISPulseConfig.layers`` (cross-source declarations #122) parse and
  reject duplicate layer names.
- ``LayerSourceConfigModel`` validates ``schema:`` aliasing (Pydantic v2
  reserved name handling).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gispulse.runtime.config_loader import (
    LayerSourceConfigModel,
    ValidateRuleConfigModel,
    load_config,
)


# ---------------------------------------------------------------------------
# ValidateRuleConfigModel.table
# ---------------------------------------------------------------------------


class TestValidateRuleTable:
    def test_default_is_none(self) -> None:
        m = ValidateRuleConfigModel(id="r", rule="1 == 1")
        assert m.table is None

    def test_pin_per_rule(self) -> None:
        m = ValidateRuleConfigModel(id="r", rule="1 == 1", table="parcels")
        assert m.table == "parcels"

    def test_extra_keys_still_forbidden(self) -> None:
        with pytest.raises(Exception):
            ValidateRuleConfigModel(
                id="r", rule="1 == 1", bogus="x"
            )


# ---------------------------------------------------------------------------
# LayerSourceConfigModel
# ---------------------------------------------------------------------------


class TestLayerSourceModel:
    def test_minimal(self) -> None:
        m = LayerSourceConfigModel(name="communes", uri="./c.gpkg")
        assert m.name == "communes"
        assert m.uri == "./c.gpkg"
        assert m.table is None
        assert m.schema_ == "public"

    def test_explicit_table_and_schema(self) -> None:
        m = LayerSourceConfigModel(
            name="parcels",
            uri="postgresql://u@h/db",
            table="ref_parcels",
            schema="cadastre",
        )
        assert m.table == "ref_parcels"
        assert m.schema_ == "cadastre"


# ---------------------------------------------------------------------------
# GISPulseConfig top-level extensions
# ---------------------------------------------------------------------------


def _yaml_with_extensions(gpkg_path: Path, extra: str = "") -> str:
    """Compose a minimal triggers.yaml. ``extra`` is concatenated at column 0."""
    base = (
        f"version: 1\n"
        f"gpkg: {gpkg_path}\n"
        f"triggers: []\n"
    )
    return base + extra


@pytest.fixture()
def fixture_gpkg(tmp_path: Path) -> Path:
    from gispulse.persistence.gpkg_engine import GeoPackageEngine

    gpkg = tmp_path / "f.gpkg"
    eng = GeoPackageEngine(path=gpkg)
    eng.open()
    try:
        conn = eng._get_conn()  # noqa: SLF001
        conn.execute(
            'CREATE TABLE "parcels" (fid INTEGER PRIMARY KEY)'
        )
        conn.commit()
        eng.enable_change_tracking("parcels")
    finally:
        eng.close()
    return gpkg


class TestGISPulseConfigExtensions:
    def test_default_table_field(self, fixture_gpkg: Path, tmp_path: Path) -> None:
        cfg_path = tmp_path / "triggers.yaml"
        cfg_path.write_text(
            _yaml_with_extensions(fixture_gpkg, "default_table: parcels")
        )
        cfg = load_config(cfg_path)
        assert cfg.default_table == "parcels"

    def test_layers_block(self, fixture_gpkg: Path, tmp_path: Path) -> None:
        ext = tmp_path / "communes.gpkg"
        ext.touch()
        cfg_path = tmp_path / "triggers.yaml"
        cfg_path.write_text(
            _yaml_with_extensions(
                fixture_gpkg,
                f"""layers:
  - name: communes
    uri: {ext}
  - name: zonage
    uri: ./zonage.parquet
""",
            )
        )
        cfg = load_config(cfg_path)
        assert len(cfg.layers) == 2
        assert {layer.name for layer in cfg.layers} == {"communes", "zonage"}

    def test_layers_duplicate_name_rejected(self, fixture_gpkg: Path, tmp_path: Path) -> None:
        cfg_path = tmp_path / "triggers.yaml"
        cfg_path.write_text(
            _yaml_with_extensions(
                fixture_gpkg,
                """layers:
  - name: communes
    uri: ./a.gpkg
  - name: communes
    uri: ./b.gpkg
""",
            )
        )
        with pytest.raises(Exception) as exc:
            load_config(cfg_path)
        assert "communes" in str(exc.value).lower() or "duplicate" in str(exc.value).lower()

    def test_validate_block_with_per_rule_table(
        self, fixture_gpkg: Path, tmp_path: Path
    ) -> None:
        cfg_path = tmp_path / "triggers.yaml"
        cfg_path.write_text(
            _yaml_with_extensions(
                fixture_gpkg,
                """validate:
  - id: surface_min
    rule: "1 == 1"
    table: parcels
    mode: warn
""",
            )
        )
        cfg = load_config(cfg_path)
        assert len(cfg.validate_rules) == 1
        assert cfg.validate_rules[0].table == "parcels"
