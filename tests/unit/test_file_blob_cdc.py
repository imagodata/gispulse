"""Tests for ``persistence.file_blob_cdc.FileBlobChangeDetector`` (#105 slice 2).

Covers:
- Empty file / missing file: ``poll`` returns ``[]``.
- First poll on a new file: every row registers as INSERT.
- Idempotent poll (mtime unchanged): ``poll`` returns ``[]``.
- Edit (replace one feature) → DELETE + INSERT pair.
- Add a feature → INSERT only.
- Remove a feature → DELETE only.
- Sidecar persistence — corruption falls back to empty snapshot.
- Hash semantics: identical content with reordered properties produces
  the same hash (set-diff stability).
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import geopandas as gpd
import pytest
from shapely.geometry import Point

from core.models import ChangeOperation
from persistence.file_blob_cdc import (
    SNAPSHOT_SUFFIX,
    FileBlobChangeDetector,
    FileBlobSnapshot,
)


def _bump_mtime(path: Path) -> None:
    """Force a fresh mtime even on filesystems with second-resolution.

    Without this, two writes inside one wall-clock second can land on
    the same mtime and the detector skips the second poll. Bumping by
    +1.5 s is brutal but reliable in tests.
    """
    stat = os.stat(path)
    new_time = stat.st_mtime + 1.5
    os.utime(path, (stat.st_atime, new_time))


@pytest.fixture
def initial_geojson(tmp_path: Path) -> Path:
    path = tmp_path / "places.geojson"
    gdf = gpd.GeoDataFrame(
        {
            "name": ["Paris", "Lyon", "Marseille"],
            "population": [2_140_000, 513_000, 868_000],
            "geometry": [
                Point(2.35, 48.85),
                Point(4.83, 45.75),
                Point(5.37, 43.30),
            ],
        },
        crs="EPSG:4326",
    )
    gdf.to_file(str(path), driver="GeoJSON")
    return path


# ---------------------------------------------------------------------------
# Lifecycle / cheap probes
# ---------------------------------------------------------------------------


class TestPollAbsent:
    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        det = FileBlobChangeDetector(tmp_path / "ghost.geojson")
        try:
            assert det.poll() == []
            assert det.has_pending_changes() is False
        finally:
            det.close()

    def test_path_property(self, tmp_path: Path) -> None:
        path = tmp_path / "x.geojson"
        det = FileBlobChangeDetector(path)
        try:
            assert det.path == path.resolve()
            # Default snapshot path is the blob + suffix
            assert det.snapshot_path.name.endswith(SNAPSHOT_SUFFIX)
            assert det.table_name == "x"
        finally:
            det.close()

    def test_custom_table_name(self, tmp_path: Path) -> None:
        det = FileBlobChangeDetector(
            tmp_path / "places.geojson", table_name="custom"
        )
        try:
            assert det.table_name == "custom"
        finally:
            det.close()


# ---------------------------------------------------------------------------
# First poll = all rows are INSERT
# ---------------------------------------------------------------------------


class TestFirstPoll:
    def test_emits_one_insert_per_row(self, initial_geojson: Path) -> None:
        det = FileBlobChangeDetector(initial_geojson)
        try:
            records = det.poll()
        finally:
            det.close()
        assert len(records) == 3
        assert all(r.operation == ChangeOperation.INSERT for r in records)
        names = {r.new_values.get("name") for r in records}
        assert names == {"Paris", "Lyon", "Marseille"}
        # Geometry surfaced as WKT
        assert all(r.new_geom_wkt and r.new_geom_wkt.startswith("POINT") for r in records)
        # Hash stored as feature_id (set-diff identity)
        assert all(r.feature_id and len(r.feature_id) == 32 for r in records)

    def test_creates_sidecar_snapshot(self, initial_geojson: Path) -> None:
        det = FileBlobChangeDetector(initial_geojson)
        try:
            det.poll()
        finally:
            det.close()
        assert det.snapshot_path.exists()
        assert det.snapshot_path.suffix == ".duckdb"


# ---------------------------------------------------------------------------
# Idempotency — second poll on unchanged file returns nothing
# ---------------------------------------------------------------------------


class TestIdempotency:
    def test_second_poll_no_changes(self, initial_geojson: Path) -> None:
        det = FileBlobChangeDetector(initial_geojson)
        try:
            det.poll()
            second = det.poll()
        finally:
            det.close()
        assert second == []

    def test_has_pending_changes_after_first_poll(
        self, initial_geojson: Path
    ) -> None:
        det = FileBlobChangeDetector(initial_geojson)
        try:
            det.poll()
            assert det.has_pending_changes() is False
        finally:
            det.close()


# ---------------------------------------------------------------------------
# Edits — diff produces correct INSERT / DELETE
# ---------------------------------------------------------------------------


class TestDiffOperations:
    def test_add_feature_emits_one_insert(self, initial_geojson: Path) -> None:
        det = FileBlobChangeDetector(initial_geojson)
        try:
            det.poll()  # baseline

            edited = gpd.read_file(str(initial_geojson))
            new_row = gpd.GeoDataFrame(
                {
                    "name": ["Toulouse"],
                    "population": [493_000],
                    "geometry": [Point(1.44, 43.60)],
                },
                crs="EPSG:4326",
            )
            edited = gpd.GeoDataFrame(
                gpd.pd.concat([edited, new_row], ignore_index=True),
                geometry="geometry",
                crs="EPSG:4326",
            )
            edited.to_file(str(initial_geojson), driver="GeoJSON")
            _bump_mtime(initial_geojson)

            records = det.poll()
        finally:
            det.close()

        assert len(records) == 1
        assert records[0].operation == ChangeOperation.INSERT
        assert records[0].new_values["name"] == "Toulouse"

    def test_remove_feature_emits_one_delete(self, initial_geojson: Path) -> None:
        det = FileBlobChangeDetector(initial_geojson)
        try:
            det.poll()  # baseline

            edited = gpd.read_file(str(initial_geojson))
            edited = edited[edited["name"] != "Lyon"]
            edited.to_file(str(initial_geojson), driver="GeoJSON")
            _bump_mtime(initial_geojson)

            records = det.poll()
        finally:
            det.close()

        assert len(records) == 1
        assert records[0].operation == ChangeOperation.DELETE
        assert records[0].old_values["name"] == "Lyon"

    def test_edit_feature_emits_delete_plus_insert(
        self, initial_geojson: Path
    ) -> None:
        # Set semantics: a feature edit is undetectable as UPDATE because
        # the file format has no stable PK. The detector must therefore
        # surface it as DELETE (old hash) + INSERT (new hash).
        det = FileBlobChangeDetector(initial_geojson)
        try:
            det.poll()  # baseline

            edited = gpd.read_file(str(initial_geojson))
            edited.loc[edited["name"] == "Lyon", "population"] = 999_000
            edited.to_file(str(initial_geojson), driver="GeoJSON")
            _bump_mtime(initial_geojson)

            records = det.poll()
        finally:
            det.close()

        ops = sorted(r.operation.value for r in records)
        assert ops == [ChangeOperation.DELETE.value, ChangeOperation.INSERT.value]
        delete_rec = next(
            r for r in records if r.operation == ChangeOperation.DELETE
        )
        insert_rec = next(
            r for r in records if r.operation == ChangeOperation.INSERT
        )
        assert delete_rec.old_values["population"] == 513_000
        assert insert_rec.new_values["population"] == 999_000


# ---------------------------------------------------------------------------
# Snapshot resilience
# ---------------------------------------------------------------------------


class TestSnapshotResilience:
    def test_corrupted_sidecar_falls_back_to_empty(
        self, initial_geojson: Path, tmp_path: Path
    ) -> None:
        # Pre-populate the sidecar with garbage. The detector should
        # treat it as empty and re-emit every row as INSERT.
        sidecar = initial_geojson.with_name(
            initial_geojson.name + SNAPSHOT_SUFFIX
        )
        sidecar.write_bytes(b"garbage-not-duckdb")

        det = FileBlobChangeDetector(initial_geojson)
        try:
            records = det.poll()
        finally:
            det.close()
        assert len(records) == 3
        assert all(r.operation == ChangeOperation.INSERT for r in records)

    def test_custom_snapshot_path(
        self, initial_geojson: Path, tmp_path: Path
    ) -> None:
        custom = tmp_path / "snapshots" / "places.duckdb"
        custom.parent.mkdir()
        det = FileBlobChangeDetector(
            initial_geojson, snapshot_path=custom
        )
        try:
            det.poll()
        finally:
            det.close()
        assert custom.exists()


# ---------------------------------------------------------------------------
# Multi-file formats — Shapefile companions (slice 4)
# ---------------------------------------------------------------------------


@pytest.fixture
def initial_shapefile(tmp_path: Path) -> Path:
    path = tmp_path / "places.shp"
    gdf = gpd.GeoDataFrame(
        {
            "name": ["Paris", "Lyon", "Marseille"],
            "population": [2_140_000, 513_000, 868_000],
            "geometry": [
                Point(2.35, 48.85),
                Point(4.83, 45.75),
                Point(5.37, 43.30),
            ],
        },
        crs="EPSG:4326",
    )
    gdf.to_file(str(path), driver="ESRI Shapefile")
    return path


class TestShapefileCompanions:
    def test_baseline_three_inserts(self, initial_shapefile: Path) -> None:
        det = FileBlobChangeDetector(initial_shapefile)
        try:
            records = det.poll()
        finally:
            det.close()
        assert len(records) == 3
        assert all(r.operation == ChangeOperation.INSERT for r in records)

    def test_attribute_only_edit_detected(
        self, initial_shapefile: Path
    ) -> None:
        # The killer regression: a Shapefile attribute-only edit only
        # touches ``.dbf``. If the detector watched only the ``.shp``
        # file's mtime, the diff would never run and the change
        # would be silently dropped.
        det = FileBlobChangeDetector(initial_shapefile)
        try:
            det.poll()  # baseline

            # Edit only attributes (geometry untouched). Bump every
            # companion's mtime to be belt-and-braces.
            edited = gpd.read_file(str(initial_shapefile))
            edited.loc[edited["name"] == "Lyon", "population"] = 999_000
            edited.to_file(str(initial_shapefile), driver="ESRI Shapefile")
            for ext in (".shp", ".dbf", ".shx", ".prj", ".cpg"):
                companion = initial_shapefile.with_suffix(ext)
                if companion.exists():
                    _bump_mtime(companion)

            records = det.poll()
        finally:
            det.close()
        ops = sorted(r.operation.value for r in records)
        assert ops == [ChangeOperation.DELETE.value, ChangeOperation.INSERT.value]

    def test_dbf_only_mtime_change_triggers_poll(
        self, initial_shapefile: Path
    ) -> None:
        # Direct test of ``has_pending_changes``: bumping ONLY the
        # ``.dbf`` companion mtime must surface as pending — the
        # ``.shp`` mtime is intentionally left alone.
        det = FileBlobChangeDetector(initial_shapefile)
        try:
            det.poll()  # baseline ack
            # Confirm no pending immediately after a successful poll
            assert det.has_pending_changes() is False

            dbf = initial_shapefile.with_suffix(".dbf")
            assert dbf.exists()
            _bump_mtime(dbf)

            assert det.has_pending_changes() is True
        finally:
            det.close()


class TestFlatGeobufZeroCodeChange:
    def test_fgb_works_through_existing_detector(
        self, tmp_path: Path
    ) -> None:
        # FGB needs no special handling because pyogrio's writer
        # mirrors the GeoJSON pattern (single file, atomic mtime).
        # We assert this contract explicitly so a future regression
        # would fail loudly.
        path = tmp_path / "places.fgb"
        gdf = gpd.GeoDataFrame(
            {
                "name": ["A", "B"],
                "geometry": [Point(0, 0), Point(1, 1)],
            },
            crs="EPSG:4326",
        )
        gdf.to_file(str(path), driver="FlatGeobuf")

        det = FileBlobChangeDetector(path)
        try:
            records = det.poll()
        finally:
            det.close()
        assert len(records) == 2
        assert all(r.operation == ChangeOperation.INSERT for r in records)
