"""Unit tests for the QGIS plugin gispulse-CLI detector (issue v1.4-2).

Subprocesses are mocked so the suite runs in QGIS-less, gispulse-less
sandboxes (CI runners). For an end-to-end probe of the actually-installed
CLI, see `test_detector_real_cli` (auto-skips when gispulse isn't on PATH).
"""

from __future__ import annotations

import shutil
import subprocess
from unittest.mock import patch

import pytest

from qgis_plugin.runtime import detector
from qgis_plugin.runtime.detector import (
    MIN_VERSION,
    DetectorResult,
    clear_cache,
    detect_gispulse,
    install_hint,
)


@pytest.fixture(autouse=True)
def _reset_cache():
    clear_cache()
    yield
    clear_cache()


def _fake_completed(stdout: str = "", returncode: int = 0):
    return subprocess.CompletedProcess(
        args=["gispulse"], returncode=returncode, stdout=stdout, stderr=""
    )


class TestParseVersion:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("gispulse 1.5.1", (1, 5, 1)),
            ("1.3.0", (1, 3, 0)),
            ("GISPulse v2.0.0 (build deadbeef)", (2, 0, 0)),
            ("no version here", None),
            ("", None),
        ],
    )
    def test_parses(self, text: str, expected) -> None:
        assert detector._parse_version(text) == expected


class TestProbePath:
    def test_returns_found_when_which_finds_cli_with_compatible_version(self) -> None:
        with (
            patch.object(shutil, "which", return_value="/usr/local/bin/gispulse"),
            patch.object(subprocess, "run", return_value=_fake_completed("gispulse 1.5.1\n")),
        ):
            r = detect_gispulse()
        assert r.found is True
        assert r.path == "/usr/local/bin/gispulse"
        assert r.version == (1, 5, 1)
        assert r.error is None

    def test_returns_not_found_when_path_empty(self) -> None:
        with (
            patch.object(shutil, "which", return_value=None),
            patch("pathlib.Path.is_file", return_value=False),
            patch.object(subprocess, "run", side_effect=FileNotFoundError("no python")),
        ):
            r = detect_gispulse()
        assert r.found is False
        # `_probe_module` raises FileNotFoundError → that informative error
        # bubbles up instead of the generic fallback.
        err = (r.error or "").lower()
        assert "not found" in err or "failed to invoke" in err


class TestVersionGate:
    def test_rejects_too_old_version(self) -> None:
        too_old = "gispulse 1.2.9"
        with (
            patch.object(shutil, "which", return_value="/usr/local/bin/gispulse"),
            patch.object(subprocess, "run", return_value=_fake_completed(too_old)),
        ):
            r = detect_gispulse()
        assert r.found is False
        assert r.version == (1, 2, 9)
        assert ">=" in (r.error or "")
        assert ".".join(str(p) for p in MIN_VERSION) in (r.error or "")

    def test_accepts_exact_min_version(self) -> None:
        exact = f"gispulse {'.'.join(str(p) for p in MIN_VERSION)}"
        with (
            patch.object(shutil, "which", return_value="/usr/local/bin/gispulse"),
            patch.object(subprocess, "run", return_value=_fake_completed(exact)),
        ):
            r = detect_gispulse()
        assert r.found is True
        assert r.version == MIN_VERSION


class TestSubprocessFailures:
    def test_nonzero_exit_marks_not_found(self) -> None:
        with (
            patch.object(shutil, "which", return_value="/usr/local/bin/gispulse"),
            patch.object(subprocess, "run", return_value=_fake_completed("boom", returncode=1)),
        ):
            r = detect_gispulse()
        assert r.found is False
        assert "exited with 1" in (r.error or "")

    def test_timeout_marks_not_found(self) -> None:
        with (
            patch.object(shutil, "which", return_value="/usr/local/bin/gispulse"),
            patch.object(
                subprocess,
                "run",
                side_effect=subprocess.TimeoutExpired(cmd="gispulse", timeout=10),
            ),
        ):
            r = detect_gispulse()
        assert r.found is False
        assert "timeout" in (r.error or "").lower() or "timed out" in (r.error or "").lower()


class TestCache:
    def test_second_call_is_cached(self) -> None:
        with (
            patch.object(shutil, "which", return_value="/usr/local/bin/gispulse"),
            patch.object(
                subprocess, "run", return_value=_fake_completed("gispulse 1.5.1")
            ) as mock_run,
        ):
            r1 = detect_gispulse()
            r2 = detect_gispulse()
        assert r1 == r2
        # 1 call to verify the version, no second probe
        assert mock_run.call_count == 1

    def test_use_cache_false_re_runs(self) -> None:
        with (
            patch.object(shutil, "which", return_value="/usr/local/bin/gispulse"),
            patch.object(
                subprocess, "run", return_value=_fake_completed("gispulse 1.5.1")
            ) as mock_run,
        ):
            detect_gispulse()
            detect_gispulse(use_cache=False)
        assert mock_run.call_count == 2

    def test_clear_cache_forces_recheck(self) -> None:
        with (
            patch.object(shutil, "which", return_value="/usr/local/bin/gispulse"),
            patch.object(
                subprocess, "run", return_value=_fake_completed("gispulse 1.5.1")
            ) as mock_run,
        ):
            detect_gispulse()
            clear_cache()
            detect_gispulse()
        assert mock_run.call_count == 2


class TestInstallHint:
    @pytest.mark.parametrize("os_name", ["Windows", "windows", "WINDOWS"])
    def test_windows_hint_mentions_osgeo4w(self, os_name: str) -> None:
        assert "OSGeo4W" in install_hint(os_name)
        assert "pip install gispulse" in install_hint(os_name)

    @pytest.mark.parametrize("os_name", ["Darwin", "macos"])
    def test_macos_hint_mentions_brew_or_pipx(self, os_name: str) -> None:
        text = install_hint(os_name)
        assert "brew" in text or "pipx" in text
        assert "gispulse" in text

    def test_linux_hint_mentions_pipx_or_user_install(self) -> None:
        text = install_hint("Linux")
        assert "pipx" in text or "--user" in text
        assert "gispulse" in text


class TestVersionStr:
    def test_version_str_unknown_when_no_version(self) -> None:
        r = DetectorResult(found=False, path=None, version=None, error="x")
        assert r.version_str == "unknown"

    def test_version_str_dotted(self) -> None:
        r = DetectorResult(found=True, path="/x", version=(1, 5, 1), error=None)
        assert r.version_str == "1.5.1"


# ─── End-to-end smoke (auto-skipped on runners without a runnable gispulse) ───


def _gispulse_runs() -> bool:
    exe = shutil.which("gispulse")
    if not exe:
        return False
    try:
        return subprocess.run([exe, "--version"], capture_output=True, timeout=5).returncode == 0
    except Exception:
        return False


@pytest.mark.skipif(not _gispulse_runs(), reason="gispulse CLI not runnable in this env")
def test_detector_real_cli() -> None:
    r = detect_gispulse(use_cache=False)
    assert r.found is True, r.error
    assert r.version is not None
    assert r.version >= MIN_VERSION
