"""Smoke tests for the `--version` / `-V` global flag (#64)."""

import re

from typer.testing import CliRunner

from gispulse.cli import app

runner = CliRunner()

# Rich (used by Typer) injects ANSI escape sequences that fragment substrings
# like `--version` in CI where colour is on. Strip them before assertions.
_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _strip_ansi(s: str) -> str:
    return _ANSI.sub("", s)


def test_version_long_flag_prints_gispulse_version():
    res = runner.invoke(app, ["--version"])
    assert res.exit_code == 0, res.output
    first = _strip_ansi(res.stdout).splitlines()[0]
    assert first.startswith("gispulse "), f"first line was {first!r}"


def test_version_short_flag_alias():
    res = runner.invoke(app, ["-V"])
    assert res.exit_code == 0, res.output
    assert _strip_ansi(res.stdout).splitlines()[0].startswith("gispulse ")


def test_version_skips_subcommand_dispatch():
    # --version is is_eager; no subcommand should be invoked.
    res = runner.invoke(app, ["--version"])
    assert res.exit_code == 0
    assert "Usage:" not in _strip_ansi(res.stdout)


def test_help_exposes_version_option():
    res = runner.invoke(app, ["--help"])
    assert res.exit_code == 0
    plain = _strip_ansi(res.stdout)
    assert "--version" in plain
    assert "-V" in plain
