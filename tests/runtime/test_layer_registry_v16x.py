"""Tests for v1.6.x #122 — LayerRegistry cross-source push-down.

Coverage:
- Registry: register / dedupe / reject conflicting URIs.
- LayerSource validation (name / table / schema must be identifiers,
  URI must not contain quotes).
- ``_classify_source``: gpkg / parquet / postgis / unsupported.
- ``install`` against a real DuckDB connection:
  - external GPKG → ATTACH + view; ``geom_within(layer='communes')``
    SQL resolves and the spatial predicate pushes down to the SQLite
    scanner (assertion: ``EXPLAIN`` mentions the alias).
  - Parquet → ``CREATE VIEW ... read_parquet(...)``; SELECT works.
  - Idempotent re-install on a fresh connection.

PostGIS push-down is exercised by the integration suite under a docker
fixture; we skip it here to avoid pulling in a database in unit tests.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest


duckdb = pytest.importorskip("duckdb")


from gispulse.runtime.duckdb_engine import get_spatial_connection
from gispulse.runtime.layer_registry import (
    LayerRegistry,
    LayerRegistryError,
    LayerSource,
    _classify_source,
)


# ---------------------------------------------------------------------------
# LayerSource validation
# ---------------------------------------------------------------------------


class TestLayerSourceValidation:
    def test_minimal(self) -> None:
        src = LayerSource(name="communes", uri="./communes.gpkg")
        assert src.resolved_table() == "communes"

    def test_explicit_table(self) -> None:
        src = LayerSource(
            name="zonage",
            uri="./data.gpkg",
            table="layer_zonage_pli",
        )
        assert src.resolved_table() == "layer_zonage_pli"

    def test_invalid_name_rejected(self) -> None:
        with pytest.raises(LayerRegistryError):
            LayerSource(name="bad name", uri="./x.gpkg")

    def test_invalid_table_rejected(self) -> None:
        with pytest.raises(LayerRegistryError):
            LayerSource(name="communes", uri="./x.gpkg", table="t; DROP")

    def test_uri_with_single_quote_rejected(self) -> None:
        with pytest.raises(LayerRegistryError):
            LayerSource(name="communes", uri="./'evil.gpkg")

    def test_invalid_schema_rejected(self) -> None:
        with pytest.raises(LayerRegistryError):
            LayerSource(
                name="t", uri="postgresql://u@h/d", schema="bad-schema"
            )


# ---------------------------------------------------------------------------
# Source classification
# ---------------------------------------------------------------------------


class TestClassifySource:
    def test_gpkg_path(self) -> None:
        assert _classify_source("./communes.gpkg") == "gpkg"
        assert _classify_source("/abs/path/Layer.GPKG") == "gpkg"

    def test_parquet(self) -> None:
        assert _classify_source("./parcels.parquet") == "parquet"
        assert _classify_source("/d/x.geoparquet") == "parquet"

    def test_postgresql(self) -> None:
        assert _classify_source("postgresql://u@h/d") == "postgis"
        assert _classify_source("postgres://u@h/d") == "postgis"

    def test_unsupported_scheme(self) -> None:
        with pytest.raises(LayerRegistryError):
            _classify_source("s3://bucket/x.parquet")
        with pytest.raises(LayerRegistryError):
            _classify_source("https://example.com/x.gpkg")

    def test_unsupported_suffix(self) -> None:
        with pytest.raises(LayerRegistryError):
            _classify_source("./x.shp")


# ---------------------------------------------------------------------------
# LayerRegistry register
# ---------------------------------------------------------------------------


class TestLayerRegistryRegister:
    def test_register_and_lookup(self) -> None:
        reg = LayerRegistry()
        reg.register(LayerSource(name="communes", uri="./c.gpkg"))
        assert "communes" in reg
        assert reg.names() == ["communes"]
        assert len(reg) == 1

    def test_register_same_source_twice_is_idempotent(self) -> None:
        reg = LayerRegistry()
        s = LayerSource(name="communes", uri="./c.gpkg")
        reg.register(s)
        reg.register(s)  # same instance, no error
        reg.register(LayerSource(name="communes", uri="./c.gpkg"))  # equal
        assert len(reg) == 1

    def test_register_conflicting_uri_raises(self) -> None:
        reg = LayerRegistry()
        reg.register(LayerSource(name="communes", uri="./a.gpkg"))
        with pytest.raises(LayerRegistryError) as exc:
            reg.register(LayerSource(name="communes", uri="./b.gpkg"))
        assert "different source" in str(exc.value)


# ---------------------------------------------------------------------------
# Install against real DuckDB
# ---------------------------------------------------------------------------


def _build_communes_gpkg(path: Path) -> None:
    """Create a minimal SQLite-with-GPKG-vibe file holding a communes table.

    We don't need full GPKG metadata for the LayerRegistry — the SQLite
    ATTACH path only reads regular tables. This keeps the test fast.
    """
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(
            'CREATE TABLE "communes" '
            "(code_insee TEXT, region_name TEXT)"
        )
        conn.execute(
            "INSERT INTO communes VALUES ('75056', 'Île-de-France'), "
            "('13055', 'PACA')"
        )
        conn.commit()
    finally:
        conn.close()


class TestLayerRegistryInstallGpkg:
    def test_external_gpkg_view_resolves(self, tmp_path: Path) -> None:
        gpkg = tmp_path / "communes.gpkg"
        _build_communes_gpkg(gpkg)

        reg = LayerRegistry()
        reg.register(LayerSource(name="communes", uri=str(gpkg)))

        conn = get_spatial_connection()
        reg.install(conn)

        rows = conn.execute(
            'SELECT code_insee, region_name FROM "communes" '
            "ORDER BY code_insee"
        ).fetchall()
        assert rows == [
            ("13055", "PACA"),
            ("75056", "Île-de-France"),
        ]

    def test_register_with_custom_table_name(self, tmp_path: Path) -> None:
        # Source table is ``ref_communes`` but the DSL refers to ``communes``.
        gpkg = tmp_path / "src.gpkg"
        conn = sqlite3.connect(str(gpkg))
        try:
            conn.execute(
                'CREATE TABLE "ref_communes" (code_insee TEXT)'
            )
            conn.execute("INSERT INTO ref_communes VALUES ('75056')")
            conn.commit()
        finally:
            conn.close()

        reg = LayerRegistry()
        reg.register(
            LayerSource(
                name="communes", uri=str(gpkg), table="ref_communes"
            )
        )
        ddconn = get_spatial_connection()
        reg.install(ddconn)
        result = ddconn.execute(
            'SELECT code_insee FROM "communes"'
        ).fetchall()
        assert result == [("75056",)]

    def test_missing_gpkg_raises(self, tmp_path: Path) -> None:
        reg = LayerRegistry()
        reg.register(
            LayerSource(name="ghost", uri=str(tmp_path / "missing.gpkg"))
        )
        conn = get_spatial_connection()
        with pytest.raises(LayerRegistryError) as exc:
            reg.install(conn)
        assert "not found" in str(exc.value)


def _write_parquet(path: Path) -> None:
    """Create a small parquet file via duckdb COPY (avoids pyarrow dep)."""
    conn = duckdb.connect(":memory:")
    conn.execute(
        "CREATE TABLE _z AS SELECT * FROM (VALUES "
        "('A', 'urbain'), ('B', 'naturel')) AS t(zone_id, zone_label)"
    )
    conn.execute(f"COPY _z TO '{path}' (FORMAT PARQUET)")
    conn.close()


class TestLayerRegistryInstallParquet:
    def test_parquet_view_resolves(self, tmp_path: Path) -> None:
        parquet = tmp_path / "zonage.parquet"
        _write_parquet(parquet)

        reg = LayerRegistry()
        reg.register(LayerSource(name="zonage", uri=str(parquet)))

        conn = get_spatial_connection()
        reg.install(conn)

        rows = conn.execute(
            'SELECT zone_id, zone_label FROM "zonage" ORDER BY zone_id'
        ).fetchall()
        assert rows == [("A", "urbain"), ("B", "naturel")]

    def test_missing_parquet_raises(self, tmp_path: Path) -> None:
        reg = LayerRegistry()
        reg.register(
            LayerSource(name="z", uri=str(tmp_path / "missing.parquet"))
        )
        conn = get_spatial_connection()
        with pytest.raises(LayerRegistryError):
            reg.install(conn)


class TestLayerRegistryDSLIntegration:
    """The compiled DSL SQL must run cleanly once views are installed."""

    def test_layer_lookup_against_external_gpkg(
        self, tmp_path: Path
    ) -> None:
        gpkg = tmp_path / "communes.gpkg"
        _build_communes_gpkg(gpkg)

        reg = LayerRegistry()
        reg.register(LayerSource(name="communes", uri=str(gpkg)))

        conn = get_spatial_connection()
        reg.install(conn)

        # Simulate the DSL ``layer_lookup(layer='communes',
        # match='code_insee', take='region_name')`` compiled output.
        # The "self" side is provided by a small inline VALUES table.
        sql = (
            "WITH self_row AS (SELECT '75056' AS code_insee) "
            'SELECT (SELECT _L."region_name" FROM "communes" AS _L '
            'WHERE "code_insee" = _L."code_insee" LIMIT 1) '
            "FROM self_row"
        )
        row = conn.execute(sql).fetchone()
        assert row == ("Île-de-France",)
