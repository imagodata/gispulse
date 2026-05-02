"""Unit tests for the runner's log helpers (issue v1.4-4).

The QProcess wrapper itself can't be exercised without Qt; what's
testable in pure Python — level detection, HTML escaping, log path
generation, argv composition — lives here.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from qgis_plugin.runtime.log_format import (
    LEVEL_COLOR,
    LogLevel,
    format_log_html,
    log_file_path,
    parse_log_level,
)
from qgis_plugin.runtime.runner import build_command, shell_repr


class TestParseLogLevel:
    @pytest.mark.parametrize(
        "line,expected",
        [
            ("[info] starting trigger", LogLevel.INFO),
            ("[INFO] starting trigger", LogLevel.INFO),
            ("plain progress", LogLevel.INFO),
            ("[warn] cache miss", LogLevel.WARN),
            ("[WARNING] config defaulted", LogLevel.WARN),
            ("warning: deprecated flag", LogLevel.WARN),
            ("[error] connection refused", LogLevel.ERROR),
            ("[ERR] failed to load", LogLevel.ERROR),
            ("[critical] db down", LogLevel.ERROR),
            ("[fatal] cannot continue", LogLevel.ERROR),
            ("Error: cannot open dataset", LogLevel.ERROR),
            ("Traceback (most recent call last):", LogLevel.ERROR),
        ],
    )
    def test_dispatches(self, line: str, expected: LogLevel) -> None:
        assert parse_log_level(line) is expected

    def test_error_outranks_warn_when_both_present(self) -> None:
        assert parse_log_level("[error] also a warn condition") is LogLevel.ERROR

    def test_unknown_line_defaults_to_info(self) -> None:
        assert parse_log_level("just some output") is LogLevel.INFO


class TestFormatLogHtml:
    def test_uses_level_color(self) -> None:
        html = format_log_html("hello", LogLevel.INFO)
        assert LEVEL_COLOR[LogLevel.INFO] in html
        assert "hello" in html

    def test_escapes_html_special_chars(self) -> None:
        html = format_log_html("<script>alert(1)</script>", LogLevel.WARN)
        # No raw `<script>` survives in the output Qt would render.
        assert "<script>" not in html
        assert "&lt;script&gt;" in html
        assert "&lt;/script&gt;" in html

    def test_escapes_ampersand(self) -> None:
        html = format_log_html("a & b", LogLevel.INFO)
        assert "a &amp; b" in html

    def test_strips_trailing_newline(self) -> None:
        html = format_log_html("trailing\n", LogLevel.INFO)
        assert html.endswith("</span>")
        assert "trailing\n" not in html
        assert ">trailing<" in html


class TestLogFilePath:
    def test_path_layout(self, tmp_path: Path) -> None:
        when = datetime(2026, 5, 2, 14, 30, 0)
        p = log_file_path(tmp_path, now=when)
        assert p.parent == tmp_path / ".gispulse" / "runs"
        assert p.name == "20260502T143000Z.log"

    def test_does_not_create_dir(self, tmp_path: Path) -> None:
        # Function is pure; runner is responsible for mkdir.
        p = log_file_path(tmp_path, now=datetime(2026, 1, 1))
        assert not p.parent.exists()


class TestBuildCommand:
    def test_argv_shape(self) -> None:
        argv = build_command(
            exe="/usr/local/bin/gispulse",
            rules_path="/tmp/rules.yml",
            dataset_path="/tmp/data.gpkg",
        )
        assert argv == [
            "/usr/local/bin/gispulse",
            "triggers",
            "run",
            "--rules",
            "/tmp/rules.yml",
            "--dataset",
            "/tmp/data.gpkg",
        ]

    def test_shell_repr_quotes_paths_with_spaces(self) -> None:
        argv = build_command(
            exe="gispulse",
            rules_path="/tmp/with space/rules.yml",
            dataset_path="/tmp/data.gpkg",
        )
        rendered = shell_repr(argv)
        assert "'/tmp/with space/rules.yml'" in rendered
