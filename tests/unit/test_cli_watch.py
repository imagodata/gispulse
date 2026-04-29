"""Unit tests for ``gispulse watch`` (v1.3.0 #5).

The full daemon path (signal-driven graceful drain) is exercised by an
integration test under ``tests/integration/cli/test_watch.py``. Here we
cover what fits in-process: command registration, config validation,
build-runtime failure paths.
"""

from __future__ import annotations

import sqlite3
import textwrap
from pathlib import Path

import pytest
from typer.testing import CliRunner

from gispulse.cli import app


@pytest.fixture()
def runner(monkeypatch: pytest.MonkeyPatch) -> CliRunner:
    # Widen the rendered help panel so option names like ``--rules`` /
    # ``--webhook`` aren't dropped from the output when CI runs under a
    # narrow default terminal (GitHub Actions defaults truncate Rich
    # panels mid-option around 80 cols).
    monkeypatch.setenv("COLUMNS", "200")
    return CliRunner()


@pytest.fixture()
def tracked_gpkg(tmp_path: Path) -> Path:
    """GPKG with a tracked ``parcels`` layer (triggers installed)."""
    from persistence.gpkg_engine import GeoPackageEngine

    path = tmp_path / "watch.gpkg"
    engine = GeoPackageEngine(path=path)
    engine.open()
    try:
        conn = engine._get_conn()  # noqa: SLF001
        conn.execute('CREATE TABLE "parcels" (fid INTEGER PRIMARY KEY, name TEXT)')
        conn.commit()
        engine.enable_change_tracking("parcels")
    finally:
        engine.close()
    return path


@pytest.fixture()
def valid_yaml(tmp_path: Path, tracked_gpkg: Path) -> Path:
    cfg = tmp_path / "rules.yaml"
    cfg.write_text(
        textwrap.dedent(
            f"""
            version: 1
            gpkg: {tracked_gpkg}
            triggers:
              - name: webhook_only
                table: parcels
                pk_col: fid
                when: [INSERT]
                actions:
                  - type: webhook
                    url: https://hook.example.com/parcels
            security:
              webhook_allowlist:
                - hook.example.com
            runtime:
              poll_interval_ms: 200
              max_batch: 50
            """,
        ).strip(),
        encoding="utf-8",
    )
    return cfg


def test_watch_help_registers(runner: CliRunner) -> None:
    res = runner.invoke(app, ["watch", "--help"])
    assert res.exit_code == 0
    assert "Watch a GeoPackage" in res.output
    assert "--rules" in res.output
    assert "--webhook" in res.output


def test_watch_missing_rules_exits_2(runner: CliRunner, tracked_gpkg: Path) -> None:
    # Typer treats a missing required option as a usage error (exit 2).
    res = runner.invoke(app, ["watch", str(tracked_gpkg)])
    assert res.exit_code == 2


def test_watch_invalid_yaml_exits_1(
    runner: CliRunner, tracked_gpkg: Path, tmp_path: Path
) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text(": : :\n", encoding="utf-8")
    res = runner.invoke(
        app, ["watch", str(tracked_gpkg), "--rules", str(bad)]
    )
    assert res.exit_code == 1


def test_watch_schema_error_exits_1(
    runner: CliRunner, tracked_gpkg: Path, tmp_path: Path
) -> None:
    # Reference a layer that does not exist in the GPKG.
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        textwrap.dedent(
            f"""
            version: 1
            gpkg: {tracked_gpkg}
            triggers:
              - name: ghost
                table: ghost_layer
                pk_col: fid
                when: [INSERT]
                actions:
                  - type: webhook
                    url: https://hook.example.com/x
            security:
              webhook_allowlist: [hook.example.com]
            runtime:
              poll_interval_ms: 200
              max_batch: 10
            """,
        ).strip(),
        encoding="utf-8",
    )
    res = runner.invoke(app, ["watch", str(tracked_gpkg), "--rules", str(bad)])
    assert res.exit_code == 1


# ---------------------------------------------------------------------------
# --once mode (#11)
# ---------------------------------------------------------------------------


def test_watch_once_empty_changelog_exits_0(
    runner: CliRunner, tracked_gpkg: Path, valid_yaml: Path
) -> None:
    res = runner.invoke(
        app, ["watch", str(tracked_gpkg), "--rules", str(valid_yaml), "--once"]
    )
    assert res.exit_code == 0
    assert "Nothing to drain" in res.output


def test_watch_once_drains_pending_rows(
    runner: CliRunner, tracked_gpkg: Path, valid_yaml: Path
) -> None:
    conn = sqlite3.connect(str(tracked_gpkg))
    conn.execute("INSERT INTO parcels(name) VALUES ('a'),('b'),('c')")
    conn.commit()
    conn.close()

    res = runner.invoke(
        app, ["watch", str(tracked_gpkg), "--rules", str(valid_yaml), "--once"]
    )
    assert res.exit_code == 0
    assert "Processed 3" in res.output

    res2 = runner.invoke(
        app, ["watch", str(tracked_gpkg), "--rules", str(valid_yaml), "--once"]
    )
    assert res2.exit_code == 0
    assert "Nothing to drain" in res2.output


def test_watch_once_exit_zero_if_empty_is_silent(
    runner: CliRunner, tracked_gpkg: Path, valid_yaml: Path
) -> None:
    res = runner.invoke(
        app,
        [
            "watch",
            str(tracked_gpkg),
            "--rules",
            str(valid_yaml),
            "--once",
            "--exit-zero-if-empty",
        ],
    )
    assert res.exit_code == 0
    assert "Nothing to drain" not in res.output


def test_watch_once_batch_limit_caps_drain(
    runner: CliRunner, tracked_gpkg: Path, valid_yaml: Path
) -> None:
    conn = sqlite3.connect(str(tracked_gpkg))
    conn.execute("INSERT INTO parcels(name) VALUES ('1'),('2'),('3'),('4'),('5')")
    conn.commit()
    conn.close()

    res = runner.invoke(
        app,
        [
            "watch", str(tracked_gpkg),
            "--rules", str(valid_yaml),
            "--once", "--batch-limit", "2",
        ],
    )
    assert res.exit_code == 0
    assert "Processed 2" in res.output

    res2 = runner.invoke(
        app,
        [
            "watch", str(tracked_gpkg),
            "--rules", str(valid_yaml),
            "--once", "--batch-limit", "2",
        ],
    )
    assert res2.exit_code == 0
    assert "Processed 2" in res2.output


def test_snapshot_changelog_handles_missing_table() -> None:
    """``_snapshot_changelog`` is best-effort and must never raise."""
    from gispulse.cli_watch import _snapshot_changelog

    class _DummyEngine:
        def _get_conn(self) -> sqlite3.Connection:
            c = sqlite3.connect(":memory:")
            return c

    class _RT:
        engine = _DummyEngine()

    pending, latest = _snapshot_changelog(_RT())
    assert pending == 0
    assert latest == 0
