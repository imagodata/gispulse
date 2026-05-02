"""Smoke tests for the `--version` / `-V` global flag (#64)."""

from typer.testing import CliRunner

from gispulse.cli import app

runner = CliRunner()


def test_version_long_flag_prints_gispulse_version():
    res = runner.invoke(app, ["--version"])
    assert res.exit_code == 0, res.output
    first = res.stdout.splitlines()[0]
    assert first.startswith("gispulse "), f"first line was {first!r}"


def test_version_short_flag_alias():
    res = runner.invoke(app, ["-V"])
    assert res.exit_code == 0, res.output
    assert res.stdout.splitlines()[0].startswith("gispulse ")


def test_version_skips_subcommand_dispatch():
    # --version is is_eager; no subcommand should be invoked.
    res = runner.invoke(app, ["--version"])
    assert res.exit_code == 0
    assert "Usage:" not in res.stdout


def test_help_exposes_version_option():
    res = runner.invoke(app, ["--help"])
    assert res.exit_code == 0
    assert "--version" in res.stdout
    assert "-V" in res.stdout
