"""
Unit tests for the GISPulse CLI — command parsing, init, formats, capabilities, doctor.
"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from gispulse.cli import app

runner = CliRunner()


class TestCliInit:
    def test_init_creates_project_structure(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["init", str(tmp_path)])
        assert result.exit_code == 0
        assert (tmp_path / "rules").is_dir()
        assert (tmp_path / "data").is_dir()
        assert (tmp_path / "output").is_dir()

    def test_init_creates_template_rules(self, tmp_path: Path) -> None:
        runner.invoke(app, ["init", str(tmp_path)])
        rules_file = tmp_path / "rules" / "rules.json"
        assert rules_file.exists()
        rules = json.loads(rules_file.read_text())
        assert isinstance(rules, list)
        assert len(rules) > 0

    def test_init_with_name(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["init", str(tmp_path), "--name", "my_project"])
        assert result.exit_code == 0

    def test_init_idempotent(self, tmp_path: Path) -> None:
        runner.invoke(app, ["init", str(tmp_path)])
        result = runner.invoke(app, ["init", str(tmp_path)])
        assert result.exit_code == 0


class TestCliFormats:
    def test_formats_command(self) -> None:
        result = runner.invoke(app, ["formats"])
        assert result.exit_code == 0
        assert "gpkg" in result.stdout.lower() or "GPKG" in result.stdout


class TestCliCapabilities:
    def test_capabilities_command(self) -> None:
        result = runner.invoke(app, ["capabilities"])
        assert result.exit_code == 0
        assert "buffer" in result.stdout.lower() or "filter" in result.stdout.lower()


class TestCliDoctor:
    def test_doctor_command(self) -> None:
        result = runner.invoke(app, ["doctor"])
        assert result.exit_code == 0
        assert "python" in result.stdout.lower() or "geopandas" in result.stdout.lower()

    def test_doctor_install_spatial(self) -> None:
        result = runner.invoke(app, ["doctor", "--install-spatial"])
        assert result.exit_code == 0, result.stdout
        assert "DuckDB Spatial" in result.stdout
        assert "EPSG:4326" in result.stdout
        assert "EPSG:2154" in result.stdout

    def test_doctor_install_spatial_json(self) -> None:
        result = runner.invoke(app, ["doctor", "--install-spatial", "--json"])
        assert result.exit_code == 0, result.stdout
        # Skip the leading update-notifier banner if present.
        json_line = result.stdout.strip().splitlines()[-1]
        payload = json.loads(json_line)
        assert payload["install"] == "ok"
        assert {c["epsg"] for c in payload["epsg"]} >= {4326, 3857, 2154, 27572}
        assert all(isinstance(c["ok"], bool) for c in payload["epsg"])


class TestCliHelp:
    def test_help(self) -> None:
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "gispulse" in result.stdout.lower()

    def test_run_help(self) -> None:
        result = runner.invoke(app, ["run", "--help"])
        assert result.exit_code == 0

    def test_validate_help(self) -> None:
        result = runner.invoke(app, ["validate", "--help"])
        assert result.exit_code == 0
