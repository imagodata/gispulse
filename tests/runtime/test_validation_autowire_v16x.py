"""Tests for v1.6.x — ``build_runtime`` auto-wiring of ``validate:`` rules.

Coverage:
- :func:`resolve_validation_table`:
  - Per-rule ``table`` wins over ``default_table`` and auto-detect.
  - ``default_table`` wins over auto-detect.
  - Single-table GPKG → auto-select.
  - Multi-table GPKG without pin → :class:`ValidationTableResolutionError`
    listing the candidates.
  - Empty GPKG → :class:`ValidationTableResolutionError` (no candidate).

- :func:`build_runtime` with ``validate_rules``:
  - Rules wire onto :class:`ValidationRunner`; runner.evaluate() runs
    the compiled SQL against the project GPKG.
  - Cross-source layer registered → ``geom_within`` against external
    GPKG resolves end-to-end.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from gispulse.runtime.config_loader import (
    LayerSourceConfigModel,
    ValidateRuleConfigModel,
)
from gispulse.runtime.headless_runtime import (
    ValidationTableResolutionError,
    build_runtime,
    resolve_validation_table,
)
from gispulse.persistence.gpkg_engine import GeoPackageEngine


# ---------------------------------------------------------------------------
# GPKG fixtures
# ---------------------------------------------------------------------------


def _build_single_table_gpkg(path: Path) -> None:
    eng = GeoPackageEngine(path=path)
    eng.open()
    try:
        conn = eng._get_conn()  # noqa: SLF001 - test setup
        conn.execute(
            'CREATE TABLE "parcels" (id INTEGER PRIMARY KEY AUTOINCREMENT, '
            "name TEXT, area REAL)"
        )
        conn.execute(
            "INSERT INTO gpkg_contents "
            "(table_name, data_type, identifier, last_change, srs_id) "
            "VALUES ('parcels', 'attributes', 'parcels', "
            "strftime('%Y-%m-%dT%H:%M:%SZ','now'), 0)"
        )
        conn.commit()
        eng.enable_change_tracking("parcels", pk_col="id")
    finally:
        eng.close()


def _build_multi_table_gpkg(path: Path) -> None:
    eng = GeoPackageEngine(path=path)
    eng.open()
    try:
        conn = eng._get_conn()  # noqa: SLF001
        for tbl in ("parcels", "buildings"):
            conn.execute(
                f'CREATE TABLE "{tbl}" '
                "(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT)"
            )
            conn.execute(
                "INSERT INTO gpkg_contents "
                "(table_name, data_type, identifier, last_change, srs_id) "
                f"VALUES ('{tbl}', 'attributes', '{tbl}', "
                "strftime('%Y-%m-%dT%H:%M:%SZ','now'), 0)"
            )
        conn.commit()
        eng.enable_change_tracking("parcels", pk_col="id")
        eng.enable_change_tracking("buildings", pk_col="id")
    finally:
        eng.close()


# ---------------------------------------------------------------------------
# resolve_validation_table
# ---------------------------------------------------------------------------


class _Rule:
    """Lightweight stand-in for ``ValidateRuleConfigModel``."""

    def __init__(
        self,
        *,
        id: str = "r1",
        rule: str = "1 == 1",
        table: str | None = None,
    ) -> None:
        self.id = id
        self.rule = rule
        self.table = table
        self.mode = "warn"
        self.tag_field = None
        self.message = None
        self.enabled = True


class TestResolveValidationTable:
    def test_explicit_rule_table_wins(self, tmp_path: Path) -> None:
        gpkg = tmp_path / "single.gpkg"
        _build_single_table_gpkg(gpkg)
        rule = _Rule(table="explicit_table")
        # ``parcels`` is the only user table, but the rule pin overrides.
        assert (
            resolve_validation_table(
                rule, gpkg_path=gpkg, default_table="default_table"
            )
            == "explicit_table"
        )

    def test_default_table_wins_over_autodetect(self, tmp_path: Path) -> None:
        gpkg = tmp_path / "multi.gpkg"
        _build_multi_table_gpkg(gpkg)
        rule = _Rule(table=None)
        assert (
            resolve_validation_table(
                rule, gpkg_path=gpkg, default_table="buildings"
            )
            == "buildings"
        )

    def test_single_table_autodetected(self, tmp_path: Path) -> None:
        gpkg = tmp_path / "single.gpkg"
        _build_single_table_gpkg(gpkg)
        rule = _Rule(table=None)
        assert (
            resolve_validation_table(
                rule, gpkg_path=gpkg, default_table=None
            )
            == "parcels"
        )

    def test_multi_table_without_pin_raises(self, tmp_path: Path) -> None:
        gpkg = tmp_path / "multi.gpkg"
        _build_multi_table_gpkg(gpkg)
        rule = _Rule(id="ambiguous", table=None)
        with pytest.raises(ValidationTableResolutionError) as exc:
            resolve_validation_table(
                rule, gpkg_path=gpkg, default_table=None
            )
        msg = str(exc.value)
        assert "ambiguous" in msg
        assert "parcels" in msg
        assert "buildings" in msg

    def test_empty_gpkg_raises(self, tmp_path: Path) -> None:
        gpkg = tmp_path / "empty.gpkg"
        eng = GeoPackageEngine(path=gpkg)
        eng.open()
        eng.close()  # initialises gpkg_contents but no tables
        rule = _Rule(table=None)
        with pytest.raises(ValidationTableResolutionError) as exc:
            resolve_validation_table(
                rule, gpkg_path=gpkg, default_table=None
            )
        assert "no user tables" in str(exc.value).lower()


# ---------------------------------------------------------------------------
# build_runtime auto-wire
# ---------------------------------------------------------------------------


def _validate_rule(
    *, id: str, rule: str, table: str | None = None
) -> ValidateRuleConfigModel:
    return ValidateRuleConfigModel(
        id=id, rule=rule, mode="warn", table=table
    )


class TestBuildRuntimeValidateWiring:
    def test_no_validate_rules_means_no_runner(self, tmp_path: Path) -> None:
        gpkg = tmp_path / "x.gpkg"
        _build_single_table_gpkg(gpkg)
        rt = build_runtime(gpkg_path=gpkg, triggers=[])
        try:
            assert rt.watcher._validation_runner is None  # noqa: SLF001
        finally:
            rt.close()

    def test_single_table_autowire(self, tmp_path: Path) -> None:
        gpkg = tmp_path / "x.gpkg"
        _build_single_table_gpkg(gpkg)

        rt = build_runtime(
            gpkg_path=gpkg,
            triggers=[],
            validate_rules=[
                _validate_rule(id="non_empty_name", rule="name == name")
            ],
        )
        try:
            runner = rt.watcher._validation_runner  # noqa: SLF001
            assert runner is not None
            assert runner.rule_count == 1
            # The rule's auto-resolved table is ``parcels`` (single layer).
            assert runner._rules[0].table == "parcels"  # noqa: SLF001
        finally:
            rt.close()

    def test_multi_table_requires_pin(self, tmp_path: Path) -> None:
        gpkg = tmp_path / "x.gpkg"
        _build_multi_table_gpkg(gpkg)
        with pytest.raises(ValidationTableResolutionError):
            build_runtime(
                gpkg_path=gpkg,
                triggers=[],
                validate_rules=[
                    _validate_rule(id="ambiguous", rule="name == name")
                ],
            )

    def test_default_table_resolves_multi(self, tmp_path: Path) -> None:
        gpkg = tmp_path / "x.gpkg"
        _build_multi_table_gpkg(gpkg)
        rt = build_runtime(
            gpkg_path=gpkg,
            triggers=[],
            validate_rules=[
                _validate_rule(id="r", rule="name == name"),
            ],
            default_table="buildings",
        )
        try:
            runner = rt.watcher._validation_runner  # noqa: SLF001
            assert runner._rules[0].table == "buildings"  # noqa: SLF001
        finally:
            rt.close()

    def test_per_rule_table_overrides_default(self, tmp_path: Path) -> None:
        gpkg = tmp_path / "x.gpkg"
        _build_multi_table_gpkg(gpkg)
        rt = build_runtime(
            gpkg_path=gpkg,
            triggers=[],
            validate_rules=[
                _validate_rule(id="rA", rule="name == name", table="parcels"),
                _validate_rule(id="rB", rule="name == name", table="buildings"),
            ],
        )
        try:
            runner = rt.watcher._validation_runner  # noqa: SLF001
            tables = {r.table for r in runner._rules}  # noqa: SLF001
            assert tables == {"parcels", "buildings"}
        finally:
            rt.close()


# ---------------------------------------------------------------------------
# Cross-source layer registry wiring
# ---------------------------------------------------------------------------


def _build_communes_lookup_gpkg(path: Path) -> None:
    """Plain SQLite-table GPKG holding a communes lookup."""
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(
            'CREATE TABLE "communes" (code_insee TEXT, region_name TEXT)'
        )
        conn.execute(
            "INSERT INTO communes VALUES ('75056','Île-de-France')"
        )
        conn.commit()
    finally:
        conn.close()


class TestBuildRuntimeLayerSources:
    def test_layer_sources_install_views(self, tmp_path: Path) -> None:
        proj = tmp_path / "proj.gpkg"
        _build_single_table_gpkg(proj)
        ext = tmp_path / "communes.gpkg"
        _build_communes_lookup_gpkg(ext)

        rt = build_runtime(
            gpkg_path=proj,
            triggers=[],
            validate_rules=[
                _validate_rule(id="r", rule="name == name"),
            ],
            layer_sources=[
                LayerSourceConfigModel(name="communes", uri=str(ext)),
            ],
        )
        try:
            runner = rt.watcher._validation_runner  # noqa: SLF001
            assert runner is not None
            # The injected sql_evaluator can resolve the cross-source view.
            rows = runner._sql_evaluator(  # noqa: SLF001
                'SELECT region_name FROM "communes" WHERE code_insee = ?',
                ["75056"],
            )
            assert rows == [("Île-de-France",)]
        finally:
            rt.close()
