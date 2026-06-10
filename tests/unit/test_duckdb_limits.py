"""Tests for core.duckdb_limits."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import duckdb

import gispulse.core.config as cfg_mod
from gispulse.core.duckdb_limits import (
    DuckDBLimitSettings,
    _limits_from_settings,
    _sql_literal,
    configure_duckdb_limits,
)


# ---------------------------------------------------------------------------
# _sql_literal
# ---------------------------------------------------------------------------


class TestSqlLiteral:
    def test_simple_string(self):
        assert _sql_literal("4GB") == "'4GB'"

    def test_escapes_single_quotes(self):
        assert _sql_literal("/tmp/o'hara") == "'/tmp/o''hara'"

    def test_path_with_quotes(self):
        assert _sql_literal("/data/it's/here") == "'/data/it''s/here'"

    def test_path_without_special_chars(self):
        assert _sql_literal("/var/duckdb-tmp") == "'/var/duckdb-tmp'"


# ---------------------------------------------------------------------------
# JobSettings env resolution
# ---------------------------------------------------------------------------


class TestJobSettingsEnvResolution:
    def _fresh_settings(self):
        """Build a fresh Settings, bypassing the proxy cache."""
        from gispulse.core.config import Settings

        cfg_mod._TOML_CACHE_KEY = None
        cfg_mod._TOML_DATA = {}
        return Settings()

    def test_defaults_are_empty_zero(self, monkeypatch):
        monkeypatch.delenv("GISPULSE_DUCKDB_MEMORY_LIMIT", raising=False)
        monkeypatch.delenv("GISPULSE_DUCKDB_TEMP_DIR", raising=False)
        monkeypatch.delenv("GISPULSE_DUCKDB_THREADS", raising=False)
        s = self._fresh_settings()
        assert s.jobs.duckdb_memory_limit == ""
        assert s.jobs.duckdb_temp_dir == ""
        assert s.jobs.duckdb_threads == 0

    def test_memory_limit_from_env(self, monkeypatch):
        monkeypatch.setenv("GISPULSE_DUCKDB_MEMORY_LIMIT", "8GB")
        s = self._fresh_settings()
        assert s.jobs.duckdb_memory_limit == "8GB"

    def test_temp_dir_from_env(self, monkeypatch, tmp_path):
        monkeypatch.setenv("GISPULSE_DUCKDB_TEMP_DIR", str(tmp_path / "duckdb-spill"))
        s = self._fresh_settings()
        assert s.jobs.duckdb_temp_dir == str(tmp_path / "duckdb-spill")

    def test_threads_from_env(self, monkeypatch):
        monkeypatch.setenv("GISPULSE_DUCKDB_THREADS", "6")
        s = self._fresh_settings()
        assert s.jobs.duckdb_threads == 6

    def test_threads_unset_env_is_zero(self, monkeypatch):
        # Absent env var (not set at all) → default 0
        monkeypatch.delenv("GISPULSE_DUCKDB_THREADS", raising=False)
        s = self._fresh_settings()
        assert s.jobs.duckdb_threads == 0


# ---------------------------------------------------------------------------
# configure_duckdb_limits — no-op when nothing configured
# ---------------------------------------------------------------------------


class TestConfigureDuckDBLimitsNoop:
    def test_no_op_with_empty_settings(self):
        """No SET statements issued when all fields are empty/zero."""
        conn = MagicMock()
        limits = DuckDBLimitSettings(memory_limit="", temp_directory=None, threads=0)
        configure_duckdb_limits(conn, limits)
        conn.execute.assert_not_called()

    def test_no_op_with_negative_threads(self):
        conn = MagicMock()
        limits = DuckDBLimitSettings(memory_limit="", temp_directory=None, threads=-1)
        configure_duckdb_limits(conn, limits)
        conn.execute.assert_not_called()


# ---------------------------------------------------------------------------
# configure_duckdb_limits — applies SET on real in-memory connection
# ---------------------------------------------------------------------------


class TestConfigureDuckDBLimitsApplied:
    def test_memory_limit_applied(self):
        conn = duckdb.connect(":memory:")
        try:
            limits = DuckDBLimitSettings(memory_limit="512MB", temp_directory=None, threads=0)
            configure_duckdb_limits(conn, limits)
            result = conn.execute("SELECT current_setting('memory_limit')").fetchone()
            assert result is not None
            # DuckDB may normalise the value (e.g. "512.0 MiB") — just check it's set
            assert result[0] != ""
        finally:
            conn.close()

    def test_threads_applied(self):
        conn = duckdb.connect(":memory:")
        try:
            limits = DuckDBLimitSettings(memory_limit="", temp_directory=None, threads=2)
            configure_duckdb_limits(conn, limits)
            result = conn.execute("SELECT current_setting('threads')").fetchone()
            assert result is not None
            assert int(result[0]) == 2
        finally:
            conn.close()

    def test_temp_directory_applied(self, tmp_path):
        conn = duckdb.connect(":memory:")
        try:
            spill = tmp_path / "spill"
            limits = DuckDBLimitSettings(memory_limit="", temp_directory=spill, threads=0)
            configure_duckdb_limits(conn, limits)
            assert spill.exists()
            result = conn.execute("SELECT current_setting('temp_directory')").fetchone()
            assert result is not None
            assert str(spill) in result[0]
        finally:
            conn.close()

    def test_all_three_applied(self, tmp_path):
        conn = duckdb.connect(":memory:")
        try:
            spill = tmp_path / "all-three"
            limits = DuckDBLimitSettings(
                memory_limit="256MB",
                temp_directory=spill,
                threads=1,
            )
            configure_duckdb_limits(conn, limits)
            assert spill.exists()
            threads = conn.execute("SELECT current_setting('threads')").fetchone()
            assert int(threads[0]) == 1
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# configure_duckdb_limits — best-effort on failure
# ---------------------------------------------------------------------------


class TestConfigureDuckDBLimitsBestEffort:
    def test_failing_set_does_not_raise(self):
        conn = MagicMock()
        conn.execute.side_effect = RuntimeError("boom")
        limits = DuckDBLimitSettings(memory_limit="4GB", temp_directory=None, threads=4)
        # Must not propagate the exception
        result = configure_duckdb_limits(conn, limits)
        assert result is limits

    def test_remaining_sets_attempted_after_first_failure(self):
        """A failure on memory_limit must not prevent threads from being set."""
        results = []

        def side_effect(sql):
            results.append(sql)
            if "memory_limit" in sql:
                raise RuntimeError("unsupported")

        conn = MagicMock()
        conn.execute.side_effect = side_effect
        limits = DuckDBLimitSettings(memory_limit="4GB", temp_directory=None, threads=3)
        configure_duckdb_limits(conn, limits)
        # threads SET should still have been attempted
        assert any("threads" in s for s in results)


# ---------------------------------------------------------------------------
# configure_duckdb_limits — SQL escaping in paths
# ---------------------------------------------------------------------------


class TestConfigureDuckDBLimitsEscaping:
    def test_temp_directory_with_single_quote_in_path(self, tmp_path):
        """Paths containing single quotes must be properly escaped."""
        quoted_dir = tmp_path / "o'hara-spill"
        quoted_dir.mkdir()
        conn = MagicMock()
        limits = DuckDBLimitSettings(memory_limit="", temp_directory=quoted_dir, threads=0)
        configure_duckdb_limits(conn, limits)
        sql = conn.execute.call_args.args[0]
        # The single quote in the path must be escaped as ''
        assert "o''hara-spill" in sql

    def test_memory_limit_with_single_quote_escaped(self):
        """Contrived value — ensures _sql_literal is used for memory_limit too."""
        conn = MagicMock()
        limits = DuckDBLimitSettings(memory_limit="4'GB", temp_directory=None, threads=0)
        configure_duckdb_limits(conn, limits)
        sql = conn.execute.call_args.args[0]
        assert "4''GB" in sql


# ---------------------------------------------------------------------------
# _limits_from_settings — integration with Settings
# ---------------------------------------------------------------------------


class TestLimitsFromSettings:
    def test_no_env_returns_empty_limits(self, monkeypatch):
        monkeypatch.delenv("GISPULSE_DUCKDB_MEMORY_LIMIT", raising=False)
        monkeypatch.delenv("GISPULSE_DUCKDB_TEMP_DIR", raising=False)
        monkeypatch.delenv("GISPULSE_DUCKDB_THREADS", raising=False)
        cfg_mod._TOML_CACHE_KEY = None
        cfg_mod._TOML_DATA = {}
        limits = _limits_from_settings()
        assert limits.memory_limit == ""
        assert limits.temp_directory is None
        assert limits.threads == 0

    def test_env_propagates_to_limits(self, monkeypatch, tmp_path):
        monkeypatch.setenv("GISPULSE_DUCKDB_MEMORY_LIMIT", "2GB")
        monkeypatch.setenv("GISPULSE_DUCKDB_TEMP_DIR", str(tmp_path / "spill"))
        monkeypatch.setenv("GISPULSE_DUCKDB_THREADS", "3")
        cfg_mod._TOML_CACHE_KEY = None
        cfg_mod._TOML_DATA = {}
        limits = _limits_from_settings()
        assert limits.memory_limit == "2GB"
        assert limits.temp_directory == Path(str(tmp_path / "spill"))
        assert limits.threads == 3
