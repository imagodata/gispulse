"""File-blob change detection — mtime watch + DuckDB snapshot diff.

This module implements the format-frontier CDC mechanism described in
``architecture_duckdb_universal_2026_05_04`` and EPIC #105 (Format Frontier
T1). It is the building block used by :class:`DuckDBDiffEngine` to bring
DML detection to file formats that have no native trigger surface
(GeoJSON, FlatGeobuf, Shapefile, KML, CSV+WKT, MapInfo TAB, DXF, …).

Algorithm
---------

1. **Watch ``mtime``.** ``os.stat(path).st_mtime`` is the cheap probe;
   below an mtime delta we skip the read entirely.
2. **Read full state via DuckDB.** ``ST_Read('path')`` decodes the file
   to a relation of ``(geom, *attrs)`` regardless of format. This is
   the authoritative pivot — the engine never depends on
   format-specific Python decoders for diff.
3. **Hash each row.** ``md5(ST_AsWKB(geom) || json(props))`` produces
   a stable identifier for the (geom, attrs) tuple. Reordered files
   with the same content produce the same hashes (set semantics).
4. **Diff against last snapshot.** The snapshot is persisted as a
   DuckDB sidecar file next to the watched blob — same format that
   we use to compute the diff, so the join is trivial.
5. **Emit ``ChangeRecord`` events.** Set diff means we can detect
   INSERT (hash new) and DELETE (hash gone) reliably; UPDATE is
   undetectable without a stable PK in the file format. We emit
   INSERT/DELETE only and document the limitation loyally — see
   ``docs-site/guide/formats.md``.

Limitations (intentional for v1.6.1 slice 2)
--------------------------------------------

- No UPDATE detection. A user editing a feature in QGIS produces
  one DELETE (old hash) + one INSERT (new hash). The trigger evaluator
  must treat this as an edit pair, not as two unrelated events.
- Polling (no inotify). v1.7+ may add ``watchdog`` / ``inotify`` for
  sub-second detection. For now the watcher loop chooses the poll
  interval.
- Single-layer files only. A GPKG with multiple layers would need
  per-layer hashing — but multi-layer files belong to the trigger
  engine path, not this CDC path.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.models import ChangeOperation, ChangeRecord

logger = logging.getLogger(__name__)


# Suffix appended to the watched blob to host the sidecar snapshot.
# Kept short and ``.duckdb`` so QGIS / OGR ignore it.
SNAPSHOT_SUFFIX = ".gispulse-snapshot.duckdb"


# Map of "primary" file extension → companion extensions that share an
# atomic edit unit. Editing one without the other(s) breaks the format
# (Shapefile attribute edit only touches ``.dbf``; geometry edit
# touches ``.shp`` and ``.shx``). The detector watches ``max(mtime)``
# across all existing companions so attribute-only edits surface.
#
# Single-file formats (``.geojson``, ``.fgb``, ``.kml``, ``.csv``,
# ``.tab``, ``.dxf``) do not appear here — the default branch in
# :func:`_resolve_companion_paths` returns just the primary file.
_COMPANION_EXTENSIONS: dict[str, tuple[str, ...]] = {
    ".shp": (".shp", ".dbf", ".shx", ".prj", ".cpg"),
    # MapInfo TAB ships as a 4-file set: the descriptor (``.tab``), the
    # attribute table (``.dat``), the geometry index (``.map``), and the
    # row index (``.id``). Some MapInfo writers also produce ``.ind`` for
    # secondary indices — included defensively so it's watched if
    # present. (#106 v1.6.2)
    ".tab": (".tab", ".dat", ".map", ".id", ".ind"),
}


def _resolve_companion_paths(primary: Path) -> list[Path]:
    """Return the list of companion files we must mtime-watch.

    Always includes ``primary`` itself. For Shapefile this returns the
    five-file set ``(.shp, .dbf, .shx, .prj, .cpg)`` filtered to those
    that actually exist on disk. The order is deterministic
    (``primary`` first, then companions in canonical order) so debug
    logs are stable.
    """
    suffix = primary.suffix.lower()
    extensions = _COMPANION_EXTENSIONS.get(suffix)
    if extensions is None:
        return [primary]
    base = primary.with_suffix("")
    paths: list[Path] = []
    for ext in extensions:
        candidate = base.with_suffix(ext)
        # Always include the primary even if it happens to be missing
        # so callers can still distinguish "file gone" from "no
        # companion present".
        if candidate == primary or candidate.exists():
            paths.append(candidate)
    return paths


@dataclass
class FileBlobSnapshot:
    """In-memory representation of one snapshot row.

    Persisted to a DuckDB sidecar; reloaded on every poll to compute
    the diff against the freshly-read file.
    """

    row_hash: str
    geom_wkt: str | None
    properties: dict[str, Any] = field(default_factory=dict)


class FileBlobChangeDetector:
    """mtime + DuckDB snapshot diff CDC for a single file blob.

    Concrete formats supported via DuckDB ``ST_Read``: ``.geojson``,
    ``.fgb``, ``.shp``, ``.kml``, ``.csv``, ``.tab``, ``.dxf``. The
    detector is engine-agnostic — callers (e.g. :class:`DuckDBDiffEngine`)
    instantiate it per dataset and feed its :meth:`poll` output into
    the trigger evaluator.

    The detector is **single-threaded**: callers must serialize
    :meth:`poll` calls. The watcher loop already provides this
    invariant.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        snapshot_path: str | Path | None = None,
        table_name: str | None = None,
    ) -> None:
        self._path = Path(path).resolve()
        self._snapshot_path = (
            Path(snapshot_path).resolve()
            if snapshot_path is not None
            else self._path.with_name(self._path.name + SNAPSHOT_SUFFIX)
        )
        # Table name reported on emitted ChangeRecords. Defaults to the
        # blob filename without extension — matches QGIS / OGR
        # conventions for single-layer files.
        self._table_name = table_name or self._path.stem
        self._last_mtime: float | None = None
        self._duckdb: Any = None  # lazy

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def _get_duckdb(self) -> Any:
        if self._duckdb is None:
            import duckdb

            self._duckdb = duckdb.connect(":memory:")
            try:
                self._duckdb.install_extension("spatial")
                self._duckdb.load_extension("spatial")
            except Exception as exc:  # pragma: no cover — defensive
                logger.warning("duckdb_spatial_unavailable: %s", exc)
                raise
        return self._duckdb

    def close(self) -> None:
        if self._duckdb is not None:
            try:
                self._duckdb.close()
            except Exception:
                pass
            self._duckdb = None

    # ------------------------------------------------------------------
    # Polling
    # ------------------------------------------------------------------

    @property
    def path(self) -> Path:
        return self._path

    @property
    def snapshot_path(self) -> Path:
        return self._snapshot_path

    @property
    def table_name(self) -> str:
        return self._table_name

    def _max_companion_mtime(self) -> float | None:
        """Return ``max(mtime)`` across all existing companion files,
        or ``None`` when none exist (file deleted / never created).

        For single-file formats this is just ``os.stat(primary).st_mtime``.
        For Shapefile it spans ``.shp / .dbf / .shx / .prj / .cpg`` so an
        attribute-only edit (which only touches ``.dbf``) is not missed.
        """
        mtimes: list[float] = []
        for path in _resolve_companion_paths(self._path):
            try:
                mtimes.append(os.stat(path).st_mtime)
            except FileNotFoundError:
                continue
        return max(mtimes) if mtimes else None

    def has_pending_changes(self) -> bool:
        """Cheap mtime probe — True iff a poll would do work."""
        current = self._max_companion_mtime()
        if current is None:
            return False
        return self._last_mtime is None or current > self._last_mtime

    def poll(self) -> list[ChangeRecord]:
        """Detect changes since the last poll and return ChangeRecord list.

        Returns ``[]`` when:
        - the file does not exist (yet),
        - mtime is unchanged since the last successful poll,
        - the file content is unchanged (mtime touched without write,
          e.g. ``touch``).

        First poll on a fresh detector treats every row as INSERT
        (the snapshot was empty).
        """
        current_mtime = self._max_companion_mtime()
        if current_mtime is None:
            return []
        if self._last_mtime is not None and current_mtime <= self._last_mtime:
            return []
        if not self._path.exists():
            # A companion still exists but the primary is gone — the
            # file format is broken; log defensively and treat as
            # missing rather than crashing the watcher.
            logger.warning(
                "file_blob_primary_missing: companions present but %s not — skipping poll",
                self._path,
            )
            return []

        new_snapshot = self._read_current_snapshot()
        old_snapshot = self._load_persisted_snapshot()
        records = self._diff(old_snapshot, new_snapshot)

        self._persist_snapshot(new_snapshot)
        self._last_mtime = current_mtime
        return records

    # ------------------------------------------------------------------
    # Internals — DuckDB read + snapshot persistence
    # ------------------------------------------------------------------

    # Synthetic columns added by OGR / DuckDB ``ST_Read`` that should not
    # contribute to the row hash. ``OGC_FID`` is the row index OGR
    # assigns on read; it shifts when a feature is inserted in the
    # middle of a GeoJSON, which would cause every later feature to
    # surface as DELETE+INSERT and pollute the diff.
    _SYNTHETIC_PROP_COLS: frozenset[str] = frozenset({"OGC_FID", "geom"})

    def _read_current_snapshot(self) -> dict[str, FileBlobSnapshot]:
        """Read every row of the file blob via DuckDB ``ST_Read`` and
        produce a ``{row_hash: FileBlobSnapshot}`` map.

        The hash is ``md5(ST_AsWKB(geom) || json_object(props))`` — see
        module docstring for rationale. ``ST_AsText`` is used for the
        in-memory ``geom_wkt`` so the ChangeRecord stays JSON-friendly
        without a binary blob.
        """
        conn = self._get_duckdb()

        # Introspect column names; ``ST_Read`` exposes geometry as
        # ``geom`` plus the file's attributes. Exclude OGR's synthetic
        # ``OGC_FID`` and ``geom`` from the property projection so the
        # hash commutes with row reordering.
        desc = conn.execute(
            "SELECT * FROM ST_Read(?) LIMIT 0", [str(self._path)]
        ).description
        prop_cols = [
            c[0] for c in (desc or []) if c[0] not in self._SYNTHETIC_PROP_COLS
        ]
        if prop_cols:
            prop_args = ", ".join(f"'{c}', \"{c}\"" for c in prop_cols)
            props_select = f"json_object({prop_args})"
        else:
            props_select = "json_object()"

        rows = conn.execute(
            f"""
            SELECT
                ST_AsWKB(geom)  AS geom_wkb,
                ST_AsText(geom) AS geom_wkt,
                {props_select}  AS props_json
            FROM ST_Read(?)
            """,
            [str(self._path)],
        ).fetchall()

        snapshot: dict[str, FileBlobSnapshot] = {}
        for geom_wkb, geom_wkt, props_json in rows:
            geom_blob = bytes(geom_wkb) if geom_wkb is not None else b""
            props_text = props_json if isinstance(props_json, str) else (
                json.dumps(props_json, sort_keys=True, default=str)
            )
            digest = hashlib.md5(geom_blob + props_text.encode("utf-8")).hexdigest()
            try:
                props = json.loads(props_text) if props_text else {}
            except json.JSONDecodeError:
                props = {}
            snapshot[digest] = FileBlobSnapshot(
                row_hash=digest,
                geom_wkt=geom_wkt,
                properties=props,
            )
        return snapshot

    def _load_persisted_snapshot(self) -> dict[str, FileBlobSnapshot]:
        """Load the previously-persisted snapshot from the sidecar.

        Returns ``{}`` on first poll (no sidecar yet) or when the
        sidecar cannot be opened (corrupted DuckDB file — defensive).
        """
        if not self._snapshot_path.exists():
            return {}
        import duckdb

        try:
            with duckdb.connect(str(self._snapshot_path), read_only=True) as conn:
                rows = conn.execute(
                    "SELECT row_hash, geom_wkt, properties FROM snapshot"
                ).fetchall()
        except duckdb.Error:
            logger.warning(
                "file_blob_snapshot_corrupted: %s — treating as empty",
                self._snapshot_path,
            )
            return {}

        snapshot: dict[str, FileBlobSnapshot] = {}
        for row_hash, geom_wkt, props_json in rows:
            try:
                props = json.loads(props_json) if props_json else {}
            except json.JSONDecodeError:
                props = {}
            snapshot[row_hash] = FileBlobSnapshot(
                row_hash=row_hash, geom_wkt=geom_wkt, properties=props
            )
        return snapshot

    def _persist_snapshot(self, snapshot: dict[str, FileBlobSnapshot]) -> None:
        """Replace the sidecar with the new snapshot.

        Idempotent — recreates the table from scratch. The sidecar
        path is opened in read-write mode, the snapshot table is
        recreated and populated row-by-row.
        """
        import duckdb

        # We always recreate the file to avoid leaving a stale
        # ``snapshot`` table around if the schema ever evolves.
        if self._snapshot_path.exists():
            try:
                self._snapshot_path.unlink()
            except OSError as exc:  # pragma: no cover — fs error
                logger.warning("file_blob_snapshot_unlink_failed: %s", exc)
                return

        with duckdb.connect(str(self._snapshot_path)) as conn:
            conn.execute(
                "CREATE TABLE snapshot (row_hash TEXT PRIMARY KEY, "
                "geom_wkt TEXT, properties TEXT)"
            )
            if snapshot:
                conn.executemany(
                    "INSERT INTO snapshot (row_hash, geom_wkt, properties) "
                    "VALUES (?, ?, ?)",
                    [
                        (
                            s.row_hash,
                            s.geom_wkt,
                            json.dumps(s.properties, sort_keys=True, default=str),
                        )
                        for s in snapshot.values()
                    ],
                )

    def _diff(
        self,
        old: dict[str, FileBlobSnapshot],
        new: dict[str, FileBlobSnapshot],
    ) -> list[ChangeRecord]:
        """Produce ``ChangeRecord`` events from a set diff.

        Set semantics — UPDATE is undetectable without a stable PK in
        the source file. A QGIS edit produces one DELETE + one
        INSERT; the trigger evaluator owns the policy of how to react
        to such pairs (typically: the user binds the same trigger to
        both ``when: [INSERT, DELETE]`` so it fires on either side).
        """
        records: list[ChangeRecord] = []

        for h in new.keys() - old.keys():
            row = new[h]
            records.append(
                ChangeRecord(
                    table_name=self._table_name,
                    feature_id=h,
                    operation=ChangeOperation.INSERT,
                    new_values=dict(row.properties),
                    new_geom_wkt=row.geom_wkt,
                )
            )

        for h in old.keys() - new.keys():
            row = old[h]
            records.append(
                ChangeRecord(
                    table_name=self._table_name,
                    feature_id=h,
                    operation=ChangeOperation.DELETE,
                    old_values=dict(row.properties),
                    old_geom_wkt=row.geom_wkt,
                )
            )

        return records
