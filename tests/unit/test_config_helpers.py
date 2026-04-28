"""Tests for core.config TOML / env resolution helpers + Settings proxy.

Focuses on the pure helpers (_load_toml, _find_toml, _find_profile_toml,
_deep_merge, _resolve) and on the Settings factory / proxy behaviour that
tests downstream (monkeypatch on env → settings picks up changes).
"""
from __future__ import annotations

from pathlib import Path


from core.config import (
    Settings,
    _deep_merge,
    _find_profile_toml,
    _find_toml,
    _load_toml,
    _resolve,
    get_settings,
    settings,
)
import core.config as cfg_mod


# ---------------------------------------------------------------------------
# _load_toml
# ---------------------------------------------------------------------------


class TestLoadToml:
    def test_missing_file_returns_empty(self, tmp_path):
        assert _load_toml(tmp_path / "nope.toml") == {}

    def test_valid_toml(self, tmp_path):
        p = tmp_path / "cfg.toml"
        p.write_text('[engine]\nbackend = "duckdb"\n', encoding="utf-8")
        data = _load_toml(p)
        assert data == {"engine": {"backend": "duckdb"}}

    def test_malformed_toml_returns_empty(self, tmp_path):
        p = tmp_path / "bad.toml"
        p.write_text("not = toml = syntax", encoding="utf-8")
        # Graceful degradation contract — malformed TOML → empty dict, not raise
        assert _load_toml(p) == {}

    def test_nested_sections(self, tmp_path):
        p = tmp_path / "c.toml"
        p.write_text(
            '[engine]\nbackend = "postgis"\n[api]\nenv = "production"\n',
            encoding="utf-8",
        )
        data = _load_toml(p)
        assert data["engine"]["backend"] == "postgis"
        assert data["api"]["env"] == "production"


# ---------------------------------------------------------------------------
# _find_toml
# ---------------------------------------------------------------------------


class TestFindToml:
    def test_explicit_env_var_wins(self, tmp_path, monkeypatch):
        target = tmp_path / "custom.toml"
        monkeypatch.setenv("GISPULSE_CONFIG", str(target))
        monkeypatch.chdir(tmp_path)
        assert _find_toml() == target

    def test_cwd_file_used_when_no_env(self, tmp_path, monkeypatch):
        monkeypatch.delenv("GISPULSE_CONFIG", raising=False)
        cwd_toml = tmp_path / "gispulse.toml"
        cwd_toml.write_text("", encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        assert _find_toml() == cwd_toml

    def test_falls_back_to_home(self, tmp_path, monkeypatch):
        monkeypatch.delenv("GISPULSE_CONFIG", raising=False)
        # Empty cwd, no gispulse.toml
        monkeypatch.chdir(tmp_path)
        result = _find_toml()
        # Falls back to ~/.gispulse/gispulse.toml — path only, may not exist
        assert result.name == "gispulse.toml"
        assert ".gispulse" in str(result)


# ---------------------------------------------------------------------------
# _find_profile_toml
# ---------------------------------------------------------------------------


class TestFindProfileToml:
    def test_no_profile_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.delenv("GISPULSE_PROFILE", raising=False)
        assert _find_profile_toml(tmp_path / "gispulse.toml") is None

    def test_profile_file_next_to_base(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GISPULSE_PROFILE", "prod")
        base = tmp_path / "gispulse.toml"
        profile = tmp_path / "gispulse.prod.toml"
        profile.write_text("", encoding="utf-8")
        assert _find_profile_toml(base) == profile

    def test_profile_file_in_cwd(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GISPULSE_PROFILE", "dev")
        monkeypatch.chdir(tmp_path)
        profile = tmp_path / "gispulse.dev.toml"
        profile.write_text("", encoding="utf-8")
        # Base path in a different location — profile should still be found in cwd
        base = Path("/unreachable/gispulse.toml")
        assert _find_profile_toml(base) == profile

    def test_missing_profile_file_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GISPULSE_PROFILE", "nonexistent")
        monkeypatch.chdir(tmp_path)
        assert _find_profile_toml(tmp_path / "gispulse.toml") is None

    def test_non_standard_base_name_also_checks_gispulse_suffix(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("GISPULSE_PROFILE", "prod")
        base = tmp_path / "custom.toml"
        fallback = tmp_path / "gispulse.prod.toml"
        fallback.write_text("", encoding="utf-8")
        assert _find_profile_toml(base) == fallback


# ---------------------------------------------------------------------------
# _deep_merge
# ---------------------------------------------------------------------------


class TestDeepMerge:
    def test_disjoint_keys(self):
        assert _deep_merge({"a": 1}, {"b": 2}) == {"a": 1, "b": 2}

    def test_overlay_overrides_scalar(self):
        assert _deep_merge({"a": 1}, {"a": 2}) == {"a": 2}

    def test_nested_dict_merged(self):
        assert _deep_merge(
            {"db": {"host": "localhost", "port": 5432}},
            {"db": {"port": 6543, "ssl": True}},
        ) == {"db": {"host": "localhost", "port": 6543, "ssl": True}}

    def test_overlay_non_dict_replaces_dict(self):
        """When overlay has a scalar where base has a dict, overlay wins
        (scalar replaces the whole dict)."""
        assert _deep_merge({"x": {"a": 1}}, {"x": "plain"}) == {"x": "plain"}

    def test_empty_overlay_returns_base_copy(self):
        base = {"a": 1, "b": {"c": 2}}
        result = _deep_merge(base, {})
        assert result == base
        # Result is a new dict, not the same reference
        assert result is not base

    def test_deeply_nested(self):
        assert _deep_merge(
            {"a": {"b": {"c": {"d": 1}}}},
            {"a": {"b": {"c": {"e": 2}}}},
        ) == {"a": {"b": {"c": {"d": 1, "e": 2}}}}


# ---------------------------------------------------------------------------
# _resolve (env > toml > default precedence)
# ---------------------------------------------------------------------------


class TestResolve:
    def test_default_when_no_env_no_toml(self, monkeypatch):
        # Clear cache + any env var that could match
        monkeypatch.delenv("GISPULSE_TEST_VAR", raising=False)
        cfg_mod._TOML_CACHE_KEY = None
        cfg_mod._TOML_DATA = {}
        result = _resolve("GISPULSE_TEST_VAR", "nope", "nope", default="fallback")
        assert result == "fallback"

    def test_env_var_wins_over_default(self, monkeypatch):
        monkeypatch.setenv("GISPULSE_TEST_VAR", "from_env")
        cfg_mod._TOML_CACHE_KEY = None
        cfg_mod._TOML_DATA = {}
        result = _resolve("GISPULSE_TEST_VAR", "nope", "nope", default="fallback")
        assert result == "from_env"

    def test_toml_wins_over_default_when_no_env(self, monkeypatch):
        monkeypatch.delenv("GISPULSE_TEST_VAR", raising=False)
        monkeypatch.setattr(
            cfg_mod,
            "_get_toml_data",
            lambda: {"mysec": {"mykey": "from_toml"}},
        )
        result = _resolve(
            "GISPULSE_TEST_VAR", "mysec", "mykey", default="fallback"
        )
        assert result == "from_toml"

    def test_env_wins_over_toml(self, monkeypatch):
        monkeypatch.setenv("GISPULSE_TEST_VAR", "env_wins")
        monkeypatch.setattr(
            cfg_mod,
            "_get_toml_data",
            lambda: {"s": {"k": "toml_val"}},
        )
        result = _resolve(
            "GISPULSE_TEST_VAR", "s", "k", default="x"
        )
        assert result == "env_wins"


# ---------------------------------------------------------------------------
# Settings root + factory + proxy
# ---------------------------------------------------------------------------


class TestGetSettings:
    def test_returns_fresh_instance(self):
        a = get_settings()
        b = get_settings()
        # get_settings builds a new instance each call
        assert a is not b
        assert isinstance(a, Settings)

    def test_sub_settings_are_accessible(self):
        s = get_settings()
        assert hasattr(s, "engine")
        assert hasattr(s, "database")
        assert hasattr(s, "storage")
        assert hasattr(s, "api")
        assert hasattr(s, "redis")
        assert hasattr(s, "logging")
        assert hasattr(s, "session")

    def test_default_engine_backend(self, monkeypatch):
        # Ensure env vars that could change the backend are cleared
        monkeypatch.delenv("GISPULSE_ENGINE", raising=False)
        cfg_mod._TOML_CACHE_KEY = None
        cfg_mod._TOML_DATA = {}
        s = get_settings()
        # Default is gpkg per EngineSettings annotation
        assert s.engine.backend in ("gpkg", "duckdb", "postgis", "hybrid")

    def test_env_var_picked_up_at_get_settings_time(self, monkeypatch):
        """Changing GISPULSE_ENGINE must be reflected in the next call."""
        monkeypatch.setenv("GISPULSE_ENGINE", "duckdb")
        cfg_mod._TOML_CACHE_KEY = None
        cfg_mod._TOML_DATA = {}
        s = get_settings()
        assert s.engine.backend == "duckdb"


class TestSettingsProxy:
    def test_proxy_delegates_attribute_access(self):
        # The module-level `settings` is a _SettingsProxy — accessing
        # .engine must transparently delegate to get_settings()
        assert hasattr(settings, "engine")
        assert hasattr(settings, "api")

    def test_proxy_repr_returns_underlying(self):
        r = repr(settings)
        assert isinstance(r, str)
        assert "Settings" in r or "engine" in r

    def test_proxy_picks_up_env_changes(self, monkeypatch):
        """The whole point of the proxy: monkeypatch.setenv takes effect
        without needing to re-import or rebuild a singleton."""
        monkeypatch.setenv("GISPULSE_TIER", "pro")
        cfg_mod._TOML_CACHE_KEY = None
        cfg_mod._TOML_DATA = {}
        # Accessing settings.engine.tier rebuilds Settings with the new env
        assert settings.engine.tier == "pro"
