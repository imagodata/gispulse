"""Tests for ``gispulse triggers`` CLI sub-commands.

Strategy
--------
We use Typer's ``CliRunner`` to drive the subapp directly (no
subprocess), then inspect ``result.exit_code`` and ``result.output``.

Three flows covered:
- ``run --once`` against a real GPKG fixture: exit 0, change-log drained.
- ``validate`` on a broken YAML: exit 1.
- ``list`` on an untracked GPKG: exit 0 with empty-table message.
"""

from __future__ import annotations

import sqlite3
import textwrap
from pathlib import Path

import pytest
from typer.testing import CliRunner

from gispulse.cli_triggers import triggers_app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def runner() -> CliRunner:
    """A Typer ``CliRunner``. Newer Click versions dropped the legacy
    ``mix_stderr`` flag; both stdout and stderr land in ``result.output``
    by default, which is fine for our assertions."""
    return CliRunner()


@pytest.fixture()
def fixture_gpkg(tmp_path: Path) -> Path:
    """GPKG with a tracked ``parcels`` table + a single pending change."""
    from gispulse.persistence.gpkg_engine import GeoPackageEngine

    gpkg = tmp_path / "fixture.gpkg"
    engine = GeoPackageEngine(path=gpkg)
    engine.open()
    try:
        conn = engine._get_conn()  # noqa: SLF001
        conn.execute(
            'CREATE TABLE "parcels" '
            '(fid INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT)'
        )
        conn.commit()
        engine.enable_change_tracking("parcels")
    finally:
        engine.close()

    # External writer to fire the trigger and seed _gispulse_change_log.
    ext = sqlite3.connect(str(gpkg))
    try:
        ext.execute('INSERT INTO "parcels"(name) VALUES (?)', ("alpha",))
        ext.commit()
    finally:
        ext.close()

    return gpkg


@pytest.fixture()
def fixture_yaml(fixture_gpkg: Path, tmp_path: Path) -> Path:
    cfg = tmp_path / "triggers.yaml"
    cfg.write_text(
        textwrap.dedent(
            f"""
            version: 1
            gpkg: {fixture_gpkg}
            triggers:
              - name: log_only
                table: parcels
                actions:
                  - type: log_event
            runtime:
              poll_interval_ms: 100
              max_batch: 10
            """,
        ).strip(),
        encoding="utf-8",
    )
    return cfg


# ---------------------------------------------------------------------------
# `gispulse triggers run`
# ---------------------------------------------------------------------------


def test_run_once_succeeds_and_marks_change_processed(
    runner: CliRunner, fixture_yaml: Path, fixture_gpkg: Path
) -> None:
    result = runner.invoke(
        triggers_app,
        ["run", "--config", str(fixture_yaml), "--once"],
    )
    assert result.exit_code == 0, result.output
    assert "OK" in result.stdout

    # The change_log row should now be marked processed (id <= max_id_acked).
    conn = sqlite3.connect(str(fixture_gpkg))
    try:
        unprocessed = conn.execute(
            "SELECT COUNT(*) FROM _gispulse_change_log WHERE processed = 0"
        ).fetchone()[0]
    finally:
        conn.close()
    assert unprocessed == 0, "watcher should have acked every pending row"


def test_run_without_mode_flag_exits_2_with_helpful_message(
    runner: CliRunner, fixture_yaml: Path
) -> None:
    """No mode chosen → exit 2 with usage hint (rather than blocking
    on a daemon the operator did not explicitly opt into)."""
    result = runner.invoke(
        triggers_app,
        ["run", "--config", str(fixture_yaml)],
    )
    assert result.exit_code == 2
    combined = result.output
    assert "--once" in combined or "--watch" in combined


def test_run_with_once_and_watch_is_rejected(
    runner: CliRunner, fixture_yaml: Path
) -> None:
    """``--once`` and ``--watch`` are mutually exclusive."""
    result = runner.invoke(
        triggers_app,
        ["run", "--config", str(fixture_yaml), "--once", "--watch"],
    )
    assert result.exit_code == 2
    assert "mutually exclusive" in result.output


# ---------------------------------------------------------------------------
# `gispulse triggers validate`
# ---------------------------------------------------------------------------


def test_validate_exits_0_on_valid_config(
    runner: CliRunner, fixture_yaml: Path
) -> None:
    result = runner.invoke(
        triggers_app,
        ["validate", "--config", str(fixture_yaml)],
    )
    assert result.exit_code == 0, result.output


def test_validate_exits_1_on_broken_yaml(
    runner: CliRunner, fixture_gpkg: Path, tmp_path: Path
) -> None:
    bad = tmp_path / "broken.yaml"
    bad.write_text(
        f"version: 1\ngpkg: {fixture_gpkg}\ntriggers: [\n - foo: bar\n",
        encoding="utf-8",
    )
    result = runner.invoke(
        triggers_app,
        ["validate", "--config", str(bad)],
    )
    assert result.exit_code == 1
    combined = result.output
    assert "Config error" in combined or "invalid YAML" in combined


def test_validate_exits_1_on_missing_table(
    runner: CliRunner, fixture_gpkg: Path, tmp_path: Path
) -> None:
    bad = tmp_path / "missing_table.yaml"
    bad.write_text(
        textwrap.dedent(
            f"""
            version: 1
            gpkg: {fixture_gpkg}
            triggers:
              - name: typo
                table: parcells
                actions:
                  - type: log_event
            """,
        ).strip(),
        encoding="utf-8",
    )
    result = runner.invoke(
        triggers_app,
        ["validate", "--config", str(bad)],
    )
    assert result.exit_code == 1
    combined = result.output
    assert "parcells" in combined


# ---------------------------------------------------------------------------
# `gispulse triggers list`
# ---------------------------------------------------------------------------


def test_list_reports_tracked_tables(
    runner: CliRunner, fixture_gpkg: Path
) -> None:
    result = runner.invoke(
        triggers_app,
        ["list", "--gpkg", str(fixture_gpkg)],
    )
    assert result.exit_code == 0, result.output
    # Tracked table should appear with its DML ops
    assert "parcels" in result.stdout
    # All three CRUD ops are wired by enable_change_tracking
    for op in ("INSERT", "UPDATE", "DELETE"):
        assert op in result.stdout


def test_list_handles_untracked_gpkg(
    runner: CliRunner, tmp_path: Path
) -> None:
    """An empty GPKG (no GISPulse triggers installed) must exit 0 with
    a clear "not tracked" message rather than crashing."""
    from gispulse.persistence.gpkg_engine import GeoPackageEngine

    raw = tmp_path / "untracked.gpkg"
    eng = GeoPackageEngine(path=raw)
    eng.open()
    try:
        conn = eng._get_conn()  # noqa: SLF001
        conn.execute('CREATE TABLE "parcels"(fid INTEGER PRIMARY KEY)')
        conn.commit()
    finally:
        eng.close()

    result = runner.invoke(
        triggers_app,
        ["list", "--gpkg", str(raw)],
    )
    assert result.exit_code == 0
    assert "No GISPulse triggers" in result.stdout


# ---------------------------------------------------------------------------
# Error path coverage
# ---------------------------------------------------------------------------


def test_run_exits_1_on_config_error(
    runner: CliRunner, fixture_gpkg: Path, tmp_path: Path
) -> None:
    """`gispulse triggers run --once` should exit 1 (not 2) when YAML is
    syntactically broken. Distinct from exit 2 (missing --once flag)."""
    bad = tmp_path / "broken.yaml"
    bad.write_text(
        f"version: 1\ngpkg: {fixture_gpkg}\ntriggers: [\n - foo: bar\n",
        encoding="utf-8",
    )
    result = runner.invoke(
        triggers_app,
        ["run", "--config", str(bad), "--once"],
    )
    assert result.exit_code == 1
    assert "Config error" in result.output or "invalid YAML" in result.output


def test_run_exits_1_on_schema_error_after_load(
    runner: CliRunner, fixture_gpkg: Path, tmp_path: Path
) -> None:
    """If load_config succeeds but validate_against_gpkg fails (e.g. typo
    in trigger.table) the runtime must refuse to start."""
    cfg = tmp_path / "bad_table.yaml"
    cfg.write_text(
        textwrap.dedent(
            f"""
            version: 1
            gpkg: {fixture_gpkg}
            triggers:
              - name: typo
                table: parcells   # typo
                actions:
                  - type: log_event
            """,
        ).strip(),
        encoding="utf-8",
    )
    result = runner.invoke(
        triggers_app,
        ["run", "--config", str(cfg), "--once"],
    )
    assert result.exit_code == 1
    assert "parcells" in result.output


def test_list_exits_1_when_gpkg_is_corrupt(
    runner: CliRunner, tmp_path: Path
) -> None:
    """Hand a path that exists but is not a SQLite file. We bypass
    Typer's file existence check by creating a real file first; sqlite3
    will fail to open it with a clean error."""
    not_sqlite = tmp_path / "not_a_db.gpkg"
    not_sqlite.write_bytes(b"definitely not sqlite")

    result = runner.invoke(
        triggers_app,
        ["list", "--gpkg", str(not_sqlite)],
    )
    # sqlite3.connect() succeeds on any path, then the SELECT fails.
    assert result.exit_code == 1
    assert "Cannot open GPKG" in result.output
