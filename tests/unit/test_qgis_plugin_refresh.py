"""Unit tests for the post-run refresh helpers (issue v1.4-5).

Exercises the pure-Python surface only; the Qt-side
`signatures_from_qgs_layer` / `reload_layer_from_gpkg` need a live QGIS
process and are part of the manual review matrix on each install env
(OSGeo4W, Standalone, Homebrew).
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from qgis_plugin.runtime.refresh import (
    BACKUP_TTL_SECONDS,
    ChangeSummary,
    FeatureSignature,
    backup_path,
    compute_change_summary,
    feature_signature,
    format_summary,
    is_backup_within_ttl,
    make_backup,
    signatures_from_features,
)


class TestFeatureSignature:
    def test_same_inputs_produce_same_hash(self) -> None:
        a = feature_signature(1, {"name": "a", "v": 1}, b"\x01\x02")
        b = feature_signature(1, {"name": "a", "v": 1}, b"\x01\x02")
        assert a == b

    def test_attribute_change_changes_signature(self) -> None:
        a = feature_signature(1, {"name": "a"}, b"")
        b = feature_signature(1, {"name": "b"}, b"")
        assert a.attr_hash != b.attr_hash

    def test_geometry_change_changes_signature(self) -> None:
        a = feature_signature(1, {}, b"\x01")
        b = feature_signature(1, {}, b"\x02")
        assert a.geom_hash != b.geom_hash

    def test_attr_order_does_not_matter(self) -> None:
        a = feature_signature(1, {"a": 1, "b": 2}, None)
        b = feature_signature(1, {"b": 2, "a": 1}, None)
        assert a == b

    def test_null_geometry_uses_empty_hash(self) -> None:
        a = feature_signature(1, {}, None)
        assert a.geom_hash == ""

    def test_null_geom_does_not_collide_with_empty_bytes(self) -> None:
        # `b""` and `None` should not produce the same geom_hash
        a = feature_signature(1, {}, None)
        b = feature_signature(1, {}, b"")
        assert a.geom_hash != b.geom_hash


class TestComputeChangeSummary:
    def test_empty_inputs(self) -> None:
        s = compute_change_summary({}, {})
        assert s == ChangeSummary(0, 0, 0, 0)

    def test_pure_addition(self) -> None:
        before = {}
        after = signatures_from_features([(1, {"x": 1}, None), (2, {"x": 2}, None)])
        s = compute_change_summary(before, after)
        assert s.added == 2
        assert s.modified == 0
        assert s.deleted == 0
        assert s.unchanged == 0

    def test_pure_deletion(self) -> None:
        before = signatures_from_features([(1, {"x": 1}, None)])
        after = {}
        s = compute_change_summary(before, after)
        assert s.deleted == 1
        assert s.added == 0
        assert s.modified == 0

    def test_modified_only(self) -> None:
        before = signatures_from_features([(1, {"x": 1}, None)])
        after = signatures_from_features([(1, {"x": 2}, None)])
        s = compute_change_summary(before, after)
        assert s.modified == 1
        assert s.unchanged == 0

    def test_unchanged_only(self) -> None:
        before = signatures_from_features([(1, {"x": 1}, None), (2, {"x": 2}, None)])
        after = before
        s = compute_change_summary(before, after)
        assert s.unchanged == 2
        assert s.modified == 0
        assert s.has_changes is False

    def test_mixed(self) -> None:
        before = signatures_from_features(
            [
                (1, {"x": 1}, None),  # unchanged
                (2, {"x": 2}, None),  # will be modified
                (3, {"x": 3}, None),  # will be deleted
            ]
        )
        after = signatures_from_features(
            [
                (1, {"x": 1}, None),
                (2, {"x": 99}, None),  # modified
                (4, {"x": 4}, None),  # added
            ]
        )
        s = compute_change_summary(before, after)
        assert s.added == 1
        assert s.modified == 1
        assert s.deleted == 1
        assert s.unchanged == 1
        assert s.total_changes == 3
        assert s.has_changes


class TestFormatSummary:
    def test_zero_changes_message(self) -> None:
        text = format_summary(ChangeSummary(0, 0, 0, 100))
        assert "no changes" in text.lower()

    def test_added_only(self) -> None:
        text = format_summary(ChangeSummary(added=12, modified=0, deleted=0, unchanged=5))
        assert "+12 added" in text
        assert "modified" not in text
        assert "deleted" not in text

    def test_full_mix_uses_separator(self) -> None:
        text = format_summary(ChangeSummary(added=12, modified=5, deleted=2, unchanged=10))
        assert "+12 added" in text
        assert "~5 modified" in text
        assert "-2 deleted" in text
        assert " · " in text


class TestBackupPath:
    def test_layout(self, tmp_path: Path) -> None:
        when = datetime(2026, 5, 2, 14, 30, 0)
        p = backup_path(tmp_path, now=when)
        assert p.parent == tmp_path / ".gispulse" / "backups"
        assert p.name == "20260502T143000Z.gpkg"

    def test_does_not_create_dir(self, tmp_path: Path) -> None:
        backup_path(tmp_path, now=datetime(2026, 1, 1))
        assert not (tmp_path / ".gispulse").exists()


class TestMakeBackup:
    def test_copies_source(self, tmp_path: Path) -> None:
        src = tmp_path / "input.gpkg"
        src.write_bytes(b"GPKG-FAKE\x00")
        out = make_backup(src, tmp_path)
        assert out.is_file()
        assert out.read_bytes() == b"GPKG-FAKE\x00"
        assert out.parent == tmp_path / ".gispulse" / "backups"

    def test_creates_parent_dir(self, tmp_path: Path) -> None:
        src = tmp_path / "input.gpkg"
        src.write_bytes(b"x")
        out = make_backup(src, tmp_path)
        assert out.parent.is_dir()


class TestBackupTtl:
    def test_fresh_backup_is_valid(self, tmp_path: Path) -> None:
        b = tmp_path / "fresh.gpkg"
        b.write_bytes(b"x")
        assert is_backup_within_ttl(b) is True

    def test_old_backup_is_invalid(self, tmp_path: Path) -> None:
        b = tmp_path / "old.gpkg"
        b.write_bytes(b"x")
        # Forge an mtime older than TTL
        old = time.time() - BACKUP_TTL_SECONDS - 60
        os.utime(b, (old, old))
        assert is_backup_within_ttl(b) is False

    def test_missing_backup_is_invalid(self, tmp_path: Path) -> None:
        assert is_backup_within_ttl(tmp_path / "absent.gpkg") is False

    def test_now_override(self, tmp_path: Path) -> None:
        b = tmp_path / "fresh.gpkg"
        b.write_bytes(b"x")
        # Pretend "now" is 1h after the file was written
        mtime = datetime.fromtimestamp(b.stat().st_mtime, tz=timezone.utc).replace(tzinfo=None)
        future = mtime + timedelta(hours=1)
        assert is_backup_within_ttl(b, now=future) is False


class TestSignaturesFromFeatures:
    def test_dict_assembly(self) -> None:
        out = signatures_from_features([(1, {"x": 1}, None), (2, {"x": 2}, b"g")])
        assert set(out.keys()) == {1, 2}
        assert all(isinstance(v, FeatureSignature) for v in out.values())

    def test_dedup_on_fid(self) -> None:
        # Test fixture: if the same fid appears twice, last one wins.
        # Real GPKGs guarantee unique fids — this just documents the
        # helper's behaviour in case downstream code relies on it.
        out = signatures_from_features([(1, {"x": 1}, None), (1, {"x": 2}, None)])
        assert len(out) == 1
        assert out[1] == feature_signature(1, {"x": 2}, None)


@pytest.mark.parametrize(
    "summary,expected",
    [
        (ChangeSummary(0, 0, 0, 0), False),
        (ChangeSummary(1, 0, 0, 0), True),
        (ChangeSummary(0, 1, 0, 0), True),
        (ChangeSummary(0, 0, 1, 0), True),
    ],
)
def test_has_changes(summary: ChangeSummary, expected: bool) -> None:
    assert summary.has_changes is expected
