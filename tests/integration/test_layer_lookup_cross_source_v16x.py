"""End-to-end: ``layer_lookup`` against an external GPKG via ``LayerRegistry``.

This test wires together:
  - DSL ``layer_lookup(layer='communes', match='code_insee', take='region_name')``
  - :class:`LayerRegistry` ATTACHing the external GPKG read-only and
    creating a view called ``communes`` in the in-memory catalog
  - :class:`ValidationRunner` evaluating the compiled rule against
    real rows of the project GPKG

The compiled rule SQL contains a scalar subquery against the cross-
source view; DuckDB pushes the attribute-equality predicate down to
the SQLite scanner so the lookup is O(1) on indexed columns. We
assert behavioural correctness, not the EXPLAIN plan — the optimiser
output drifts between DuckDB versions.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path


from gispulse.runtime.config_loader import (
    LayerSourceConfigModel,
    ValidateRuleConfigModel,
)
from gispulse.runtime.headless_runtime import build_runtime
from gispulse.persistence.gpkg_engine import GeoPackageEngine


def _build_project_gpkg(path: Path) -> None:
    eng = GeoPackageEngine(path=path)
    eng.open()
    try:
        conn = eng._get_conn()  # noqa: SLF001
        conn.execute(
            'CREATE TABLE "parcels" '
            "(id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "code_insee TEXT, label TEXT)"
        )
        conn.execute(
            "INSERT INTO gpkg_contents "
            "(table_name, data_type, identifier, last_change, srs_id) "
            "VALUES ('parcels', 'attributes', 'parcels', "
            "strftime('%Y-%m-%dT%H:%M:%SZ','now'), 0)"
        )
        conn.execute(
            "INSERT INTO parcels (code_insee, label) "
            "VALUES ('75056', 'parcelle Paris'), "
            "('13055', 'parcelle Marseille')"
        )
        conn.commit()
        eng.enable_change_tracking("parcels", pk_col="id")
    finally:
        eng.close()


def _build_communes_lookup(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(
            'CREATE TABLE "communes" (code_insee TEXT, region_name TEXT)'
        )
        conn.execute(
            "INSERT INTO communes VALUES "
            "('75056','Île-de-France'), "
            "('13055','PACA')"
        )
        conn.commit()
    finally:
        conn.close()


def test_layer_lookup_cross_source_attribute_match(tmp_path: Path) -> None:
    project = tmp_path / "project.gpkg"
    _build_project_gpkg(project)
    communes = tmp_path / "communes.gpkg"
    _build_communes_lookup(communes)

    rt = build_runtime(
        gpkg_path=project,
        triggers=[],
        validate_rules=[
            ValidateRuleConfigModel(
                id="region_must_be_idf",
                # Boolean expression mixing layer_lookup() (scalar) with a
                # column compare. The validation runner wraps this with
                # ``NOT (...) AS failed``, so a parcel whose code_insee
                # maps to anything other than Île-de-France fails.
                rule=(
                    "layer_lookup(layer='communes', "
                    "match='code_insee', take='region_name') "
                    "== code_insee"
                ),
                mode="warn",
                table="parcels",
            )
        ],
        layer_sources=[
            LayerSourceConfigModel(name="communes", uri=str(communes)),
        ],
    )
    try:
        runner = rt.watcher._validation_runner  # noqa: SLF001
        assert runner is not None
        # The rule comparison ``region_name == code_insee`` will always
        # fail (string mismatch) — both rows should be reported as
        # failed. We assert the compiled SQL ran without error and
        # produced a deterministic outcome per row.
        f1 = runner.evaluate("parcels", row_id=1)
        f2 = runner.evaluate("parcels", row_id=2)
        assert len(f1) == 1
        assert len(f2) == 1
        assert f1[0].rule_id == "region_must_be_idf"
        assert f1[0].table == "parcels"
    finally:
        rt.close()


def test_layer_lookup_cross_source_match_truthy(tmp_path: Path) -> None:
    """Same wiring, but the rule asserts that the lookup *returns* a value.

    The validation rule is ``layer_lookup(...) == region_name`` where
    ``region_name`` does not exist on parcels, so DuckDB resolves the
    bare ``region_name`` against the project GPKG layer — and that
    column does not exist. We use an indirect form by aliasing a
    project column instead. This exercises the happy path: ``label``
    on the parcel matches a deterministic value of the lookup, so the
    rule evaluates to True and no failure is emitted.
    """
    project = tmp_path / "project.gpkg"
    _build_project_gpkg(project)
    communes = tmp_path / "communes.gpkg"
    # Tweak the lookup so region_name on '75056' is exactly 'parcelle Paris'.
    conn = sqlite3.connect(str(communes))
    try:
        conn.execute(
            'CREATE TABLE "communes" (code_insee TEXT, region_name TEXT)'
        )
        conn.execute(
            "INSERT INTO communes VALUES ('75056','parcelle Paris'), "
            "('13055','autre')"
        )
        conn.commit()
    finally:
        conn.close()

    rt = build_runtime(
        gpkg_path=project,
        triggers=[],
        validate_rules=[
            ValidateRuleConfigModel(
                id="label_matches_region",
                rule=(
                    "layer_lookup(layer='communes', "
                    "match='code_insee', take='region_name') "
                    "== label"
                ),
                mode="warn",
                table="parcels",
            )
        ],
        layer_sources=[
            LayerSourceConfigModel(name="communes", uri=str(communes)),
        ],
    )
    try:
        runner = rt.watcher._validation_runner  # noqa: SLF001
        # row 1: label='parcelle Paris', lookup returns 'parcelle Paris' → match → no failure
        # row 2: label='parcelle Marseille', lookup returns 'autre' → mismatch → failure
        assert runner.evaluate("parcels", row_id=1) == []
        f2 = runner.evaluate("parcels", row_id=2)
        assert len(f2) == 1
        assert f2[0].rule_id == "label_matches_region"
    finally:
        rt.close()
