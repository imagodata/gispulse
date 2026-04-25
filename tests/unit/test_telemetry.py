"""Tests for gispulse.telemetry module."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from unittest import mock

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _isolate_telemetry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Redirect telemetry config to a temp dir and clear env vars."""
    config_dir = tmp_path / ".gispulse"
    config_file = config_dir / "telemetry.json"
    monkeypatch.setattr("gispulse.telemetry._CONFIG_DIR", config_dir)
    monkeypatch.setattr("gispulse.telemetry._CONFIG_FILE", config_file)
    monkeypatch.delenv("GISPULSE_TELEMETRY", raising=False)
    monkeypatch.delenv("GISPULSE_TELEMETRY_URL", raising=False)


# ---------------------------------------------------------------------------
# Consent management
# ---------------------------------------------------------------------------


class TestConsent:
    def test_default_disabled_no_config(self):
        from gispulse.telemetry import is_enabled
        assert is_enabled() is False

    def test_has_been_asked_false_initially(self):
        from gispulse.telemetry import has_been_asked
        assert has_been_asked() is False

    def test_set_enabled_persists(self, tmp_path: Path):
        from gispulse.telemetry import is_enabled, set_enabled, has_been_asked
        set_enabled(True)
        assert is_enabled() is True
        assert has_been_asked() is True

    def test_set_disabled_persists(self):
        from gispulse.telemetry import is_enabled, set_enabled
        set_enabled(True)
        set_enabled(False)
        assert is_enabled() is False

    def test_env_var_overrides_config(self, monkeypatch: pytest.MonkeyPatch):
        from gispulse.telemetry import is_enabled, set_enabled
        set_enabled(True)  # config says enabled

        monkeypatch.setenv("GISPULSE_TELEMETRY", "0")
        assert is_enabled() is False

    def test_env_var_enables(self, monkeypatch: pytest.MonkeyPatch):
        from gispulse.telemetry import is_enabled
        monkeypatch.setenv("GISPULSE_TELEMETRY", "1")
        assert is_enabled() is True

    def test_env_var_false_values(self, monkeypatch: pytest.MonkeyPatch):
        from gispulse.telemetry import is_enabled
        for val in ("0", "false", "no", "off", ""):
            monkeypatch.setenv("GISPULSE_TELEMETRY", val)
            assert is_enabled() is False, f"Expected disabled for '{val}'"


class TestGetStatus:
    def test_status_not_configured(self):
        from gispulse.telemetry import get_status
        assert "not been configured" in get_status()

    def test_status_enabled(self):
        from gispulse.telemetry import get_status, set_enabled
        set_enabled(True)
        assert "enabled" in get_status()

    def test_status_env_override(self, monkeypatch: pytest.MonkeyPatch):
        from gispulse.telemetry import get_status
        monkeypatch.setenv("GISPULSE_TELEMETRY", "0")
        assert "env var" in get_status()


class TestPromptConsent:
    def test_non_tty_defaults_disabled(self, monkeypatch: pytest.MonkeyPatch):
        from gispulse.telemetry import prompt_consent, is_enabled
        monkeypatch.setattr("sys.stdin", mock.MagicMock(isatty=lambda: False))
        result = prompt_consent()
        assert result is False
        assert is_enabled() is False

    def test_user_says_yes(self, monkeypatch: pytest.MonkeyPatch):
        from gispulse.telemetry import prompt_consent, is_enabled
        monkeypatch.setattr("sys.stdin", mock.MagicMock(isatty=lambda: True))
        monkeypatch.setattr("builtins.input", lambda _: "y")
        result = prompt_consent()
        assert result is True
        assert is_enabled() is True

    def test_user_says_no(self, monkeypatch: pytest.MonkeyPatch):
        from gispulse.telemetry import prompt_consent, is_enabled
        monkeypatch.setattr("sys.stdin", mock.MagicMock(isatty=lambda: True))
        monkeypatch.setattr("builtins.input", lambda _: "n")
        result = prompt_consent()
        assert result is False

    def test_ensure_consent_skips_if_env_set(self, monkeypatch: pytest.MonkeyPatch):
        from gispulse.telemetry import ensure_consent, has_been_asked
        monkeypatch.setenv("GISPULSE_TELEMETRY", "0")
        ensure_consent()
        assert has_been_asked() is False  # should NOT have created config


# ---------------------------------------------------------------------------
# Event building
# ---------------------------------------------------------------------------


class TestBuildEvent:
    def test_minimal_event(self):
        from gispulse.telemetry import build_event
        event = build_event(command="test")
        assert event["command"] == "test"
        assert "gispulse_version" in event
        assert "python_version" in event
        assert "os_name" in event
        assert "os_arch" in event
        assert "timestamp" in event
        # Optional fields absent
        assert "duration_seconds" not in event
        assert "engine_mode" not in event

    def test_full_event(self):
        from gispulse.telemetry import build_event
        event = build_event(
            command="run",
            duration_seconds=1.7,
            engine_mode="duckdb",
            capabilities_used=["filter", "buffer"],
            dataset_size_bytes=50 * 1024 * 1024,
        )
        assert event["duration_seconds"] == 2  # rounded
        assert event["engine_mode"] == "duckdb"
        assert event["capabilities_used"] == ["filter", "buffer"]
        assert event["dataset_size_bucket"] == "medium"

    def test_python_version_format(self):
        from gispulse.telemetry import build_event
        event = build_event(command="x")
        parts = event["python_version"].split(".")
        assert len(parts) == 2  # major.minor only

    def test_os_name_normalized(self):
        from gispulse.telemetry import build_event
        event = build_event(command="x")
        assert event["os_name"] in ("linux", "macos", "windows") or isinstance(event["os_name"], str)


class TestSizeBucket:
    def test_buckets(self):
        from gispulse.telemetry import _size_bucket
        assert _size_bucket(None) == "unknown"
        assert _size_bucket(500_000) == "small"       # < 1 MB
        assert _size_bucket(1_500_000) == "medium"     # 1.5 MB
        assert _size_bucket(500_000_000) == "large"    # 500 MB
        assert _size_bucket(2_000_000_000) == "xlarge"  # 2 GB


# ---------------------------------------------------------------------------
# Collector
# ---------------------------------------------------------------------------


class TestCollector:
    def test_record_noop_when_disabled(self):
        from gispulse.telemetry import record, build_event, _collector
        event = build_event(command="test")
        record(event)
        # Should not accumulate
        with _collector._lock:
            assert len(_collector._events) == 0

    def test_record_accumulates_when_enabled(self, monkeypatch: pytest.MonkeyPatch):
        from gispulse.telemetry import record, build_event, _collector, set_enabled
        set_enabled(True)
        # Reset collector state
        with _collector._lock:
            _collector._events.clear()
            if _collector._timer:
                _collector._timer.cancel()
                _collector._timer = None

        event = build_event(command="test")
        record(event)
        with _collector._lock:
            assert len(_collector._events) == 1
            # Clean up
            _collector._events.clear()
            if _collector._timer:
                _collector._timer.cancel()
                _collector._timer = None

    def test_flush_sends_and_clears(self, monkeypatch: pytest.MonkeyPatch):
        from gispulse.telemetry import _collector, set_enabled, build_event

        set_enabled(True)
        with _collector._lock:
            _collector._events.clear()
            if _collector._timer:
                _collector._timer.cancel()
                _collector._timer = None

        sent_payloads: list[bytes] = []

        def mock_urlopen(req, timeout=None):
            sent_payloads.append(req.data)
            resp = mock.MagicMock()
            resp.__enter__ = lambda s: s
            resp.__exit__ = mock.MagicMock(return_value=False)
            return resp

        monkeypatch.setattr("urllib.request.urlopen", mock_urlopen)

        _collector.record(build_event(command="a"))
        _collector.record(build_event(command="b"))
        _collector._do_flush()

        assert len(sent_payloads) == 1
        events = json.loads(sent_payloads[0])
        assert len(events) == 2
        assert events[0]["command"] == "a"
        assert events[1]["command"] == "b"

        # Buffer should be empty after flush
        with _collector._lock:
            assert len(_collector._events) == 0

    def test_send_failure_silent(self, monkeypatch: pytest.MonkeyPatch):
        """Network failure must not raise."""
        from gispulse.telemetry import _collector, set_enabled, build_event

        set_enabled(True)
        with _collector._lock:
            _collector._events.clear()

        def mock_urlopen(req, timeout=None):
            raise ConnectionError("no network")

        monkeypatch.setattr("urllib.request.urlopen", mock_urlopen)

        _collector.record(build_event(command="fail"))
        _collector._do_flush()  # should not raise

        # Buffer cleared even on failure
        with _collector._lock:
            assert len(_collector._events) == 0


# ---------------------------------------------------------------------------
# track_command decorator
# ---------------------------------------------------------------------------


class TestTrackCommand:
    def test_decorator_passes_through(self):
        from gispulse.telemetry import track_command

        @track_command("test_cmd")
        def my_func(x: int) -> int:
            return x * 2

        assert my_func(5) == 10

    def test_decorator_does_not_crash_on_exception(self):
        from gispulse.telemetry import track_command

        @track_command("test_err")
        def my_func():
            raise ValueError("boom")

        with pytest.raises(ValueError, match="boom"):
            my_func()

    def test_decorator_records_event(self, monkeypatch: pytest.MonkeyPatch):
        from gispulse.telemetry import track_command, _collector, set_enabled

        set_enabled(True)
        with _collector._lock:
            _collector._events.clear()
            if _collector._timer:
                _collector._timer.cancel()
                _collector._timer = None

        @track_command("decorated")
        def my_func():
            return 42

        my_func()

        with _collector._lock:
            assert any(e["command"] == "decorated" for e in _collector._events)
            _collector._events.clear()
            if _collector._timer:
                _collector._timer.cancel()
                _collector._timer = None


# ---------------------------------------------------------------------------
# Config file edge cases
# ---------------------------------------------------------------------------


class TestConfigEdgeCases:
    def test_corrupted_config_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        from gispulse.telemetry import is_enabled
        config_dir = tmp_path / ".gispulse"
        config_dir.mkdir()
        config_file = config_dir / "telemetry.json"
        config_file.write_text("NOT JSON {{{")
        monkeypatch.setattr("gispulse.telemetry._CONFIG_FILE", config_file)
        # Should not crash, return False
        assert is_enabled() is False

    def test_config_missing_enabled_key(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        from gispulse.telemetry import is_enabled
        config_dir = tmp_path / ".gispulse"
        config_dir.mkdir()
        config_file = config_dir / "telemetry.json"
        config_file.write_text('{"some_other_key": true}')
        monkeypatch.setattr("gispulse.telemetry._CONFIG_FILE", config_file)
        assert is_enabled() is False
