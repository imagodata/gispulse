"""Tests for the PostGIS dialect-drift scanner (issue #146)."""

from __future__ import annotations

import textwrap

import pytest

from gispulse.runtime.config_loader import parse_config_text
from gispulse.runtime.dialect_scanner import (
    scan_expression,
    scan_for_dialect_drift,
)


# ---------------------------------------------------------------------------
# scan_expression — the five blocklisted patterns
# ---------------------------------------------------------------------------


def test_portable_sql_is_not_flagged() -> None:
    """ST_Buffer with an explicit distance is portable DuckDB-spatial."""
    assert scan_expression("UPDATE t SET g = ST_Buffer(geom, 100)", "x") == []


def test_st_transform_2arg_flagged() -> None:
    findings = scan_expression("SELECT ST_Transform(geom, 4326)", "x")
    assert len(findings) == 1
    assert "ST_Transform" in findings[0].construct


def test_geography_cast_flagged() -> None:
    findings = scan_expression("SELECT geography(geom)", "x")
    assert any("geography" in f.construct for f in findings)


def test_intersects_shorthand_flagged() -> None:
    findings = scan_expression("WHERE INTERSECTS(a, b)", "x")
    assert any("INTERSECTS" in f.construct for f in findings)


def test_st_intersects_is_portable() -> None:
    """The portable ST_Intersects must not trip the INTERSECTS rule."""
    assert scan_expression("WHERE ST_Intersects(a, b)", "x") == []


def test_bbox_operator_flagged() -> None:
    findings = scan_expression("WHERE a.geom && b.geom", "x")
    assert any("&&" in f.construct for f in findings)


def test_type_cast_flagged() -> None:
    geom = scan_expression("SELECT g::geometry", "x")
    geog = scan_expression("SELECT g :: geography", "x")
    assert geom and geog


def test_multiple_constructs_all_reported() -> None:
    findings = scan_expression(
        "SELECT ST_Transform(geom, 2154), g::geometry", "x"
    )
    assert len(findings) == 2


# ---------------------------------------------------------------------------
# scan_for_dialect_drift — over a parsed config
# ---------------------------------------------------------------------------


def _config(engine_line: str = "", expression: str = "ST_Transform(geom, 4326)"):
    text = textwrap.dedent(
        f"""
        version: 1
        gpkg: ./unused.gpkg
        {engine_line}
        triggers:
          - name: enrich
            table: parcels
            actions:
              - type: run_sql
                expression: "{expression}"
        """
    ).strip()
    return parse_config_text(text, resolve_gpkg=False)


def test_run_sql_drift_is_found() -> None:
    findings = scan_for_dialect_drift(_config())
    assert len(findings) == 1
    assert "run_sql action" in findings[0].location


def test_portable_run_sql_clean() -> None:
    assert scan_for_dialect_drift(_config(expression="ST_Buffer(geom, 50)")) == []


def test_engine_postgis_silences_the_scan() -> None:
    """Pinning engine: postgis makes the constructs legitimate (#146)."""
    cfg = _config(engine_line="engine: postgis")
    assert scan_for_dialect_drift(cfg) == []


def test_finding_message_is_actionable() -> None:
    finding = scan_for_dialect_drift(_config())[0]
    msg = finding.message()
    assert "PostGIS-only" in msg and finding.hint in msg


def test_loader_emits_dialect_drift_warning(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """parse_config_text logs a dialect_drift event for a drifting config."""
    _config()  # parse_config_text runs _warn_dialect_drift
    out = capsys.readouterr()
    assert "dialect_drift" in (out.out + out.err)


def test_loader_silent_for_portable_config(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """No dialect_drift noise for a portable config."""
    _config(expression="ST_Buffer(geom, 50)")
    out = capsys.readouterr()
    assert "dialect_drift" not in (out.out + out.err)
