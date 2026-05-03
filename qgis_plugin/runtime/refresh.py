"""Layer-refresh helpers for the post-run flow (issue v1.4-5).

Splits cleanly into two zones:

* **pure** — `FeatureSignature`, `feature_signature`, `compute_change_summary`,
  `format_summary`, `backup_path`. No QGIS, no I/O. Unit-tested in CI.
* **Qt-side** — `signatures_from_qgs_layer`, `signatures_from_gpkg`,
  `reload_layer_from_gpkg`. Lazily imported by the dock widget.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _utc_from_timestamp(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, tz=timezone.utc).replace(tzinfo=None)


@dataclass(frozen=True)
class FeatureSignature:
    """Stable fingerprint for one feature.

    `attr_hash` and `geom_hash` are SHA-256 hex digests; `geom_hash` is
    the empty string for null geometries so the diff doesn't mistake a
    null↔null pair for a change.
    """

    fid: int
    attr_hash: str
    geom_hash: str


@dataclass(frozen=True)
class ChangeSummary:
    added: int
    modified: int
    deleted: int
    unchanged: int

    @property
    def total_changes(self) -> int:
        return self.added + self.modified + self.deleted

    @property
    def has_changes(self) -> bool:
        return self.total_changes > 0


def feature_signature(
    fid: int, attributes: Mapping[str, Any], geom_wkb: bytes | None
) -> FeatureSignature:
    """Hash the attribute dict + geometry WKB into a `FeatureSignature`.

    Attribute values are normalised through `json.dumps(sort_keys=True,
    default=str)` so any ordering / Decimal / datetime quirks survive a
    round-trip without spuriously triggering "modified".
    """
    blob = json.dumps(dict(attributes), sort_keys=True, default=str).encode("utf-8")
    attr_hash = hashlib.sha256(blob).hexdigest()
    # `b""` must hash differently from `None` — an empty geometry isn't
    # the same as a null geometry as far as the diff is concerned.
    geom_hash = hashlib.sha256(geom_wkb).hexdigest() if geom_wkb is not None else ""
    return FeatureSignature(fid=fid, attr_hash=attr_hash, geom_hash=geom_hash)


def compute_change_summary(
    before: Mapping[int, FeatureSignature],
    after: Mapping[int, FeatureSignature],
) -> ChangeSummary:
    """Diff two `{fid: FeatureSignature}` snapshots.

    `fid` is the join key — gispulse must preserve it across runs (the
    GPKG provider does this by default).
    """
    before_fids = set(before)
    after_fids = set(after)
    added = len(after_fids - before_fids)
    deleted = len(before_fids - after_fids)
    common = before_fids & after_fids
    modified = sum(1 for fid in common if before[fid] != after[fid])
    unchanged = len(common) - modified
    return ChangeSummary(added=added, modified=modified, deleted=deleted, unchanged=unchanged)


def format_summary(summary: ChangeSummary) -> str:
    """One-line user-facing summary, matching the issue's mock-up.

    Returns a neutral message when nothing changed so the banner stays
    truthful instead of showing a string of zeros.
    """
    if not summary.has_changes:
        return "No changes detected."
    parts: list[str] = []
    if summary.added:
        parts.append(f"+{summary.added} added")
    if summary.modified:
        parts.append(f"~{summary.modified} modified")
    if summary.deleted:
        parts.append(f"-{summary.deleted} deleted")
    return " · ".join(parts)


# Backups are kept long enough for the user to undo a run; the dock
# widget only exposes a "Restore" button while this window is open.
BACKUP_TTL_SECONDS = 5 * 60


def backup_path(project_dir: str | Path, *, now: datetime | None = None) -> Path:
    """`<project_dir>/.gispulse/backups/<UTC-timestamp>.gpkg`.

    Symmetric with `log_file_path` — directory is *not* created here.
    """
    stamp = (now or _utcnow()).strftime("%Y%m%dT%H%M%SZ")
    return Path(project_dir) / ".gispulse" / "backups" / f"{stamp}.gpkg"


def make_backup(src_gpkg: str | Path, project_dir: str | Path) -> Path:
    """Copy the temp GPKG into `.gispulse/backups/` so the user can
    Restore. Caller is responsible for displaying the resulting path
    (e.g. wired to a 5-minute Restore button)."""
    dst = backup_path(project_dir)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src_gpkg, dst)
    return dst


def is_backup_within_ttl(backup: Path, *, now: datetime | None = None) -> bool:
    """The Restore button is only useful within `BACKUP_TTL_SECONDS`.
    Returns False if the backup is older or missing."""
    if not backup.is_file():
        return False
    mtime = _utc_from_timestamp(backup.stat().st_mtime)
    age = (now or _utcnow()) - mtime
    return age.total_seconds() <= BACKUP_TTL_SECONDS


# ─── Qt-side helpers (kept inert until called) ─────────────────────


def signatures_from_qgs_layer(layer) -> dict[int, FeatureSignature]:
    """Walk a `QgsVectorLayer` and produce `{fid: FeatureSignature}`."""
    sigs: dict[int, FeatureSignature] = {}
    field_names = [f.name() for f in layer.fields()]
    for feat in layer.getFeatures():
        attrs = {name: feat.attribute(name) for name in field_names}
        geom = feat.geometry()
        wkb = bytes(geom.asWkb()) if geom and not geom.isEmpty() else None
        sigs[feat.id()] = feature_signature(feat.id(), attrs, wkb)
    return sigs


def signatures_from_gpkg(gpkg_path: str | Path, layer_name: str) -> dict[int, FeatureSignature]:
    """Open a GPKG layer with `QgsVectorLayer` and snapshot it.

    Lives behind a function call (not a top-level import) so the whole
    `refresh` module stays pure-Python importable in unit tests.
    """
    from qgis.core import QgsVectorLayer

    uri = f"{gpkg_path}|layername={layer_name}"
    layer = QgsVectorLayer(uri, layer_name, "ogr")
    if not layer.isValid():
        raise RuntimeError(f"could not open GPKG layer {layer_name!r} in {gpkg_path}")
    return signatures_from_qgs_layer(layer)


def reload_layer_from_gpkg(layer, gpkg_path: str | Path, layer_name: str) -> None:
    """Repoint the live QGIS layer at the (now-updated) temp GPKG and
    refresh the canvas. Caller is responsible for guarding `layer.isEditable()`
    before calling this."""
    uri = f"{gpkg_path}|layername={layer_name}"
    layer.setDataSource(uri, layer.name(), "ogr")
    layer.dataProvider().reloadData()
    layer.triggerRepaint()


def restore_from_backup(layer, backup: str | Path, layer_name: str) -> None:
    """Repoint the layer at the pre-run backup. Symmetric with
    `reload_layer_from_gpkg` so the dock-widget Restore button is a
    one-liner."""
    reload_layer_from_gpkg(layer, backup, layer_name)


def signatures_from_features(
    features: Iterable[tuple[int, Mapping[str, Any], bytes | None]],
) -> dict[int, FeatureSignature]:
    """Test-helper convenience: build snapshots without QGIS by feeding
    `(fid, attrs, geom_wkb)` triples directly.

    Kept in the production module (not the test module) so downstream
    plugins can reuse it for their own snapshots.
    """
    return {fid: feature_signature(fid, attrs, geom) for fid, attrs, geom in features}
