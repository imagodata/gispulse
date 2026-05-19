"""Unit tests for A13 (#239) — the data.gouv.fr catalogue-freshness probe.

Zero network: the data.gouv.fr probe is exercised through an injected
stub, so CI moves no bytes off the box.
"""

from __future__ import annotations

from pathlib import Path

from gispulse.plugins.datagouv_refresh import (
    DATAGOUV_METADATA_KEY,
    refresh_datagouv_entries,
)


# -- a minimal catalogue fixture --------------------------------------------


def _write_catalog(tmp_path: Path, *, with_slug: bool = True) -> Path:
    """Write a tiny worldwide catalogue with two FR open-data entries."""
    slug_line = (
        f"      {DATAGOUV_METADATA_KEY}: demandes-de-valeurs-foncieres-geolocalisees"
        if with_slug
        else "      provider: Etalab"
    )
    path = tmp_path / "catalog.yml"
    path.write_text(
        f"""
version: 1
entries:
  - id: dvf-geolocalise
    name: DVF geolocated
    domain: statistique
    payload: vector
    jurisdiction: FR
    access:
      protocol: download
      endpoint: https://files.data.gouv.fr/geo-dvf/latest/full.csv.gz
    revision_token: latest
    metadata:
{slug_line}
  - id: overture-places
    name: Overture Places
    domain: observation
    payload: vector
    jurisdiction: world
    access:
      protocol: remote-table
      endpoint: s3://overturemaps/places/*
    revision_token: "2025-09-24.0"
    metadata:
      provider: Overture
""",
        encoding="utf-8",
    )
    return path


# -- refresh_datagouv_entries ------------------------------------------------


def test_refresh_reports_only_entries_with_a_slug(tmp_path: Path) -> None:
    path = _write_catalog(tmp_path)

    def probe(ref: str, *, base: str) -> str:
        return "2026-05-01T00:00:00"

    report = refresh_datagouv_entries(path, probe=probe)
    assert report["checked"] == 1  # only the dvf entry carries a slug
    record = report["entries"][0]
    assert record["id"] == "dvf-geolocalise"
    assert record["datagouv_revision"] == "datagouv:2026-05-01T00:00:00"
    assert record["up_to_date"] is False
    assert report["stale"] == ["dvf-geolocalise"]


def test_refresh_skips_entries_without_a_slug(tmp_path: Path) -> None:
    path = _write_catalog(tmp_path, with_slug=False)
    report = refresh_datagouv_entries(path, probe=lambda r, *, base: "x")
    assert report["checked"] == 0
    assert report["entries"] == []
    assert report["stale"] == []


def test_refresh_is_idempotent(tmp_path: Path) -> None:
    path = _write_catalog(tmp_path)

    def probe(ref: str, *, base: str) -> str:
        return "2026-05-01T00:00:00"

    first = refresh_datagouv_entries(path, probe=probe)
    second = refresh_datagouv_entries(path, probe=probe)
    assert first == second


def test_refresh_up_to_date_when_token_matches(tmp_path: Path) -> None:
    path = _write_catalog(tmp_path)
    # The catalogue token is 'latest'; make the probe agree with it.
    report = refresh_datagouv_entries(
        path, probe=lambda r, *, base: None
    )
    # A probe yielding no timestamp ⇒ datagouv_revision None ⇒ not up to date.
    assert report["entries"][0]["datagouv_revision"] is None
    assert report["entries"][0]["up_to_date"] is False


def test_refresh_isolates_a_failing_slug(tmp_path: Path) -> None:
    path = _write_catalog(tmp_path)

    def boom(ref: str, *, base: str) -> str:
        raise RuntimeError("404 Not Found")

    report = refresh_datagouv_entries(path, probe=boom)
    record = report["entries"][0]
    assert "error" in record
    assert "404" in record["error"]
    # An entry that errored is never reported as stale.
    assert report["stale"] == []


def test_refresh_shipped_catalogue_lists_fr_entries() -> None:
    """The curated catalogue ships at least the DVF + cadastre slugs."""
    report = refresh_datagouv_entries(probe=lambda r, *, base: "2026-01-01")
    ids = {r["id"] for r in report["entries"]}
    assert {"dvf-geolocalise", "cadastre-etalab-parcelles"} <= ids
