"""Format Frontier T2 (#106 v1.6.2) — KML + CSV+WKT + MapInfo TAB.

Slice 1 of EPIC #106. The infrastructure (`FileBlobChangeDetector` +
`DuckDBDiffEngine`) shipped in EPIC #105 (v1.6.1) handles two of the
T2 formats with **zero code change**:

- KML (single file, mtime watched directly)
- CSV+WKT (single file, mtime watched directly; pyogrio writes the
  geometry as a WKT column when ``GEOMETRY=AS_WKT`` is passed)

We also extend ``_COMPANION_EXTENSIONS`` for MapInfo TAB
(``.tab / .dat / .map / .id / .ind``) so the companion-watching
infrastructure resolves the file set correctly. The diff path itself
hangs on this env's DuckDB GDAL build (no MapInfo driver compiled
in), so the read-and-diff tests are skipped — but the companion
resolution test still locks in the multi-file watch contract for the
day we ship MapInfo via a different read path (or a DuckDB build that
includes the driver).

What we still don't ship in T2 today:

- **MapInfo TAB read/diff**: blocked on DuckDB GDAL driver — the
  ``ST_Read('places.tab')`` call hangs or fails on the bundled
  build. Pyogrio reads TAB fine, so a future slice could route
  ``.tab`` through pyogrio for read instead of DuckDB. Tracked as a
  follow-up.
- **DXF**: pyogrio's DXF writer raises ``FieldError`` on custom
  attributes (CAD-format limitation). DuckDB's ``ST_Read`` reads DXF
  fine, so a read-only CDC adapter is feasible — deferred to v1.7+
  as a separate ticket.

These tests live as a regression guard so a future change to the
detector or engine doesn't silently break a format that was working
through the existing infrastructure.
"""
from __future__ import annotations

import os
from pathlib import Path

import geopandas as gpd
import pytest
from shapely.geometry import Point

from core.models import ChangeOperation
from persistence.duckdb_diff_engine import DuckDBDiffEngine
from persistence.file_blob_cdc import (
    FileBlobChangeDetector,
    _resolve_companion_paths,
)


def _bump_mtime(path: Path) -> None:
    stat = os.stat(path)
    os.utime(path, (stat.st_atime, stat.st_mtime + 1.5))


@pytest.fixture
def sample_gdf() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {
            "name": ["A", "B", "C"],
            "category": [1, 2, 3],
            "geometry": [Point(0, 0), Point(1, 1), Point(2, 2)],
        },
        crs="EPSG:4326",
    )


# ---------------------------------------------------------------------------
# KML
# ---------------------------------------------------------------------------


class TestKML:
    def test_first_poll_emits_inserts(
        self, tmp_path: Path, sample_gdf: gpd.GeoDataFrame
    ) -> None:
        path = tmp_path / "places.kml"
        sample_gdf.to_file(str(path), driver="KML")

        det = FileBlobChangeDetector(path)
        try:
            records = det.poll()
        finally:
            det.close()
        assert len(records) == 3
        assert all(r.operation == ChangeOperation.INSERT for r in records)

    def test_through_duckdb_diff_engine(
        self, tmp_path: Path, sample_gdf: gpd.GeoDataFrame
    ) -> None:
        path = tmp_path / "places.kml"
        sample_gdf.to_file(str(path), driver="KML")

        engine = DuckDBDiffEngine(path)
        with engine:
            pending = engine.get_pending_changes()
        assert len(pending) == 3
        assert all(p["operation"] == "INSERT" for p in pending)


# ---------------------------------------------------------------------------
# CSV + WKT
# ---------------------------------------------------------------------------


class TestCSVWKT:
    def test_first_poll_emits_inserts(
        self, tmp_path: Path, sample_gdf: gpd.GeoDataFrame
    ) -> None:
        path = tmp_path / "places.csv"
        # ``GEOMETRY=AS_WKT`` tells OGR to serialize geom as a WKT column
        # rather than two lat/lon columns. DuckDB ``ST_Read`` then
        # decodes it transparently.
        sample_gdf.to_file(str(path), driver="CSV", GEOMETRY="AS_WKT")

        det = FileBlobChangeDetector(path)
        try:
            records = det.poll()
        finally:
            det.close()
        assert len(records) == 3
        assert all(r.operation == ChangeOperation.INSERT for r in records)

    def test_through_duckdb_diff_engine(
        self, tmp_path: Path, sample_gdf: gpd.GeoDataFrame
    ) -> None:
        path = tmp_path / "places.csv"
        sample_gdf.to_file(str(path), driver="CSV", GEOMETRY="AS_WKT")

        engine = DuckDBDiffEngine(path)
        with engine:
            pending = engine.get_pending_changes()
        assert len(pending) == 3


# ---------------------------------------------------------------------------
# MapInfo TAB — multi-file, companion watching
# ---------------------------------------------------------------------------


class TestMapInfoTAB:
    def test_companion_paths_resolved(
        self, tmp_path: Path, sample_gdf: gpd.GeoDataFrame
    ) -> None:
        path = tmp_path / "places.tab"
        sample_gdf.to_file(str(path), driver="MapInfo File")

        # All companion files OGR actually produced should be picked up.
        # MapInfo File typically writes .tab / .dat / .map / .id (no .ind
        # for fresh files); ``_resolve_companion_paths`` returns the
        # ones that exist, in canonical order.
        paths = _resolve_companion_paths(path)
        names = sorted(p.name for p in paths)
        assert "places.tab" in names
        assert "places.dat" in names
        assert "places.map" in names
        # .id is also produced by OGR for MapInfo TAB
        assert "places.id" in names

    def test_first_poll_emits_inserts_via_pyogrio_fallback(
        self, tmp_path: Path, sample_gdf: gpd.GeoDataFrame
    ) -> None:
        # MapInfo TAB is routed through the pyogrio fallback path in
        # ``FileBlobChangeDetector._read_via_pyogrio`` because DuckDB's
        # bundled GDAL wheel does not include the MapInfo driver.
        # Hash semantics match the DuckDB path so events have the same
        # identity (a future DuckDB build that ships the driver
        # produces the same hashes — no event flap).
        path = tmp_path / "places.tab"
        sample_gdf.to_file(str(path), driver="MapInfo File")

        det = FileBlobChangeDetector(path)
        try:
            records = det.poll()
        finally:
            det.close()
        assert len(records) == 3
        assert all(r.operation == ChangeOperation.INSERT for r in records)
        # Property values from the source GDF survived the round-trip.
        names = {r.new_values.get("name") for r in records}
        assert names == {"A", "B", "C"}

    def test_attribute_only_edit_via_pyogrio_fallback(
        self, tmp_path: Path, sample_gdf: gpd.GeoDataFrame
    ) -> None:
        # Killer regression: companion-watching surfaces an attribute-
        # only edit (``.dat`` mtime bump), AND the pyogrio fallback
        # re-reads the new values, AND the diff produces a DELETE+
        # INSERT pair (set semantics, no stable PK).
        path = tmp_path / "places.tab"
        sample_gdf.to_file(str(path), driver="MapInfo File")

        det = FileBlobChangeDetector(path)
        try:
            det.poll()  # baseline

            # MapInfo "edit" means rewriting the whole file set via
            # pyogrio. Pyogrio's ``MapInfo File`` driver refuses to
            # ``mode="w"`` over an existing TAB set ("Unable to create
            # new layers in this single file dataset"), so we delete
            # the companions first and recreate from the in-memory GDF.
            edited = gpd.read_file(str(path))
            edited.loc[edited["name"] == "A", "category"] = 999
            for ext in (".tab", ".dat", ".map", ".id", ".ind"):
                companion = path.with_suffix(ext)
                if companion.exists():
                    companion.unlink()
            edited.to_file(str(path), driver="MapInfo File")
            for ext in (".tab", ".dat", ".map", ".id", ".ind"):
                companion = path.with_suffix(ext)
                if companion.exists():
                    _bump_mtime(companion)

            records = det.poll()
        finally:
            det.close()
        ops = sorted(r.operation.value for r in records)
        assert ops == [ChangeOperation.DELETE.value, ChangeOperation.INSERT.value]

    def test_dat_only_mtime_change_does_not_crash(
        self, tmp_path: Path, sample_gdf: gpd.GeoDataFrame
    ) -> None:
        # The companion-watching layer must not crash the watcher even
        # when the diff path can't actually read the .tab. Until we
        # ship a pyogrio fallback for read-only TAB, ``has_pending_changes``
        # must still report state correctly so the watcher loop can
        # decide whether a poll is worth attempting. We exercise the
        # mtime probe (cheap, no DuckDB call) and confirm it surfaces
        # the .dat companion change.
        path = tmp_path / "places.tab"
        sample_gdf.to_file(str(path), driver="MapInfo File")

        det = FileBlobChangeDetector(path)
        try:
            # Set the baseline mtime without doing the full poll
            # (which would hang on this env). The watcher's actual
            # "first tick" path would face the same hang and is the
            # subject of the follow-up — for now we at least lock in
            # the companion-mtime contract.
            assert det.has_pending_changes() is True

            dat = path.with_suffix(".dat")
            assert dat.exists()
            initial = det._max_companion_mtime()
            assert initial is not None
            _bump_mtime(dat)
            after = det._max_companion_mtime()
            assert after is not None
            assert after > initial
        finally:
            det.close()
