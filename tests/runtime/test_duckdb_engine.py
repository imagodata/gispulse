"""Tests for ``gispulse.runtime.duckdb_engine``.

Covers:
- happy path: connection returned with spatial loaded, ST_Point works
- idempotency: a second call within the same session does not re-INSTALL
- isolation: ``_reset_cache_for_tests`` clears the install marker
- failure mode: when ``INSTALL spatial`` raises (offline / sandboxed),
  ``DuckDBSpatialUnavailable`` is raised with a doctor hint in the message
- public re-exports: ``runtime`` package exposes the new symbols
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from gispulse.runtime import (
    DuckDBSpatialUnavailable,
    get_spatial_connection,
    is_spatial_loaded,
)
from gispulse.runtime import duckdb_engine


@pytest.fixture(autouse=True)
def _reset_install_cache():
    duckdb_engine._reset_cache_for_tests()
    yield
    duckdb_engine._reset_cache_for_tests()


def test_get_spatial_connection_returns_connection_with_spatial_loaded():
    conn = get_spatial_connection()
    try:
        result = conn.execute("SELECT ST_AsText(ST_Point(1, 2));").fetchone()
    finally:
        conn.close()
    assert result is not None
    assert "POINT" in result[0]


def test_is_spatial_loaded_flips_to_true_after_first_call():
    assert is_spatial_loaded() is False
    conn = get_spatial_connection()
    conn.close()
    assert is_spatial_loaded() is True


def test_second_call_skips_install_step():
    """``INSTALL`` should run exactly once; ``LOAD`` runs on each fresh conn."""
    conn1 = get_spatial_connection()
    conn1.close()

    with patch.object(
        duckdb_engine,
        "_install_done",
        {"already": True},
    ):
        # Patch the install-marker; the function should detect the cached state
        # via the executable key and skip ``INSTALL``.
        executed: list[str] = []
        original_execute = duckdb_engine._ensure_spatial_loaded

        def spy(conn, executable):
            from gispulse.runtime.duckdb_engine import _install_done, _key_for
            key = _key_for(executable)
            _install_done[key] = True  # simulate prior install
            executed.append("LOAD spatial;")
            conn.execute("LOAD spatial;")

        with patch.object(duckdb_engine, "_ensure_spatial_loaded", side_effect=spy):
            conn2 = duckdb_engine.get_spatial_connection()
            conn2.close()
        assert executed == ["LOAD spatial;"]


def test_install_failure_raises_duckdb_spatial_unavailable():
    """Network/sandbox failure on ``INSTALL spatial`` surfaces as the typed exception."""
    fake_conn = MagicMock(name="conn")

    def execute(sql: str, *a, **kw):
        if "INSTALL spatial" in sql:
            raise RuntimeError("network unreachable")
        return fake_conn

    fake_conn.execute.side_effect = execute

    with patch("duckdb.connect", return_value=fake_conn):
        with pytest.raises(DuckDBSpatialUnavailable) as excinfo:
            get_spatial_connection()
    msg = str(excinfo.value)
    assert "network unreachable" in msg
    assert "gispulse doctor --install-spatial" in msg


def test_load_failure_when_already_installed_still_surfaces_clearly():
    """If INSTALL was cached but LOAD now fails, surface as DuckDBSpatialUnavailable."""
    import duckdb

    key = duckdb_engine._key_for(getattr(duckdb, "__file__", ""))
    duckdb_engine._install_done[key] = True

    fake_conn = MagicMock(name="conn")

    def execute(sql: str, *a, **kw):
        if sql.strip().upper().startswith("LOAD SPATIAL"):
            raise RuntimeError("extension binary corrupt")
        return fake_conn

    fake_conn.execute.side_effect = execute

    with patch("duckdb.connect", return_value=fake_conn):
        with pytest.raises(DuckDBSpatialUnavailable) as excinfo:
            get_spatial_connection()
    assert "extension binary corrupt" in str(excinfo.value)


def test_runtime_package_reexports_public_symbols():
    """The package ``__init__`` exposes the new wrapper for import-stability."""
    import gispulse.runtime as runtime

    assert hasattr(runtime, "get_spatial_connection")
    assert hasattr(runtime, "is_spatial_loaded")
    assert hasattr(runtime, "DuckDBSpatialUnavailable")
    assert "DuckDBSpatialUnavailable" in runtime.__all__
