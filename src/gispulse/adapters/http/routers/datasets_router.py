"""
Datasets router for the GISPulse HTTP API.

NOTE: Uploads are NOT auto-tracked. Call POST /datasets/{id}/enable_tracking
explicitly to start receiving DML events on /ws/events. Lot 2 v1's silent
"best-effort auto-enable" was removed in Lot 2 v2 — it called
``engine.enable_change_tracking(layer)`` on the *project* GPKG, which
almost never contained the uploaded layer, so tracking was a silent no-op
(Beta shadow-zone ``test_app_state_holds_only_one_change_log_watcher``).

Endpoints:
    POST /datasets/upload                       — upload a spatial file and register a dataset
    POST /datasets/ogc                          — register a remote OGC service as a dataset (lazy)
    GET  /datasets                              — list all datasets
    GET  /datasets/{id}                         — detail for a single dataset
    POST /datasets/{id}/enable_tracking         — start change-log watcher for the dataset (GPKG only)
    POST /datasets/{id}/disable_tracking        — stop watcher and drop triggers
    GET  /datasets/{id}/tracking_status         — report current tracking state
"""

from __future__ import annotations

import ipaddress
import logging
import shutil
import sqlite3
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response, UploadFile

from gispulse.adapters.http.dependencies import get_data_dir, get_dataset_repo, get_storage
from gispulse.adapters.http.rate_limit import limiter
from gispulse.adapters.http.schemas import DatasetResponse, OGCDatasetCreate
from gispulse.core.config import settings as _cfg
from gispulse.core.models import Dataset, OGCSourceConfig
from gispulse.persistence.io import dataset_from_file, supported_extensions
from gispulse.persistence.repository import Repository
from gispulse.persistence.storage import DatasetStorage

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/datasets", tags=["datasets"])

# ---------------------------------------------------------------------------
# SSRF protection (#241)
# ---------------------------------------------------------------------------

_PRIVATE_RANGES = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
]

_BLOCKED_HOSTNAMES = {"localhost"}


def _is_safe_url(url: str) -> bool:
    """Return True if the URL is safe (not pointing to a private/internal address)."""
    try:
        parsed = urlparse(url)
    except Exception:
        return False

    if parsed.scheme not in ("http", "https"):
        return False

    host = parsed.hostname or ""
    if not host:
        return False

    if host.lower() in _BLOCKED_HOSTNAMES:
        return False

    try:
        addr = ipaddress.ip_address(host)
        for network in _PRIVATE_RANGES:
            if addr in network:
                return False
    except ValueError:
        pass

    return True


def _dataset_to_response(ds: Dataset) -> DatasetResponse:
    # Hide server filesystem paths in read-only / public-demo deployments.
    source_path = None if _cfg.api.read_only else ds.source_path
    return DatasetResponse(
        id=ds.id,
        name=ds.name,
        source_path=source_path,
        data_category=ds.data_category,
        crs=ds.crs,
        format=ds.format,
        metadata=ds.metadata,
        created_at=ds.created_at,
    )


@router.post("/upload", response_model=DatasetResponse, status_code=201)
@limiter.limit("20/minute")
async def upload_dataset(
    request: Request,
    file: UploadFile,
    repo: Repository = Depends(get_dataset_repo),
    data_dir: Path = Depends(get_data_dir),
    storage: DatasetStorage = Depends(get_storage),
) -> DatasetResponse:
    """Upload a spatial file and register it as a dataset.

    The file is saved via the storage backend (local or S3) and inspected
    to extract layer metadata, CRS, and feature counts.

    Supported formats: GPKG, GeoJSON, Shapefile, FlatGeobuf, GML, KML,
    GeoParquet, SpatiaLite, CSV, and more.
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="Filename is required.")

    # Sanitize filename to prevent path traversal attacks
    safe_filename = Path(file.filename).name
    if not safe_filename:
        raise HTTPException(status_code=400, detail="Invalid filename.")

    ext = Path(safe_filename).suffix.lower()
    if ext not in supported_extensions():
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported format '{ext}'. Supported: {sorted(supported_extensions())}",
        )

    from uuid import uuid4

    dataset_id = uuid4()
    storage_key = f"{dataset_id}/{safe_filename}"

    # Enforce upload size limit (default 500 MB, max 5000 MB)
    from gispulse.core.config import settings as _cfg
    _max_mb = _cfg.api.max_upload_mb
    _MAX_UPLOAD_SIZE = _max_mb * 1024 * 1024

    # Write to temp file for format detection, then persist via storage
    tmp_dir = Path(tempfile.mkdtemp(prefix="gispulse_upload_"))
    tmp_dest = tmp_dir / safe_filename
    try:
        total_written = 0
        with open(tmp_dest, "wb") as f:
            while chunk := await file.read(1024 * 1024):  # 1 MB chunks
                total_written += len(chunk)
                if total_written > _MAX_UPLOAD_SIZE:
                    f.close()
                    shutil.rmtree(tmp_dir, ignore_errors=True)
                    raise HTTPException(
                        status_code=413,
                        detail=f"File too large. Maximum upload size: {_MAX_UPLOAD_SIZE // (1024*1024)} MB.",
                    )
                f.write(chunk)

        # Upload to storage backend
        with open(tmp_dest, "rb") as fh:
            await storage.upload(storage_key, fh)

        # Determine source_path based on backend
        local_path = await storage.get_local_path(storage_key)
        if local_path is not None:
            source_path = str(local_path)
            inspect_path = str(local_path)
        else:
            source_path = f"s3://{storage_key}"
            inspect_path = str(tmp_dest)

        try:
            dataset = dataset_from_file(inspect_path)
            dataset.id = dataset_id
            dataset.source_path = source_path
            if dataset.metadata is None:
                dataset.metadata = {}
            dataset.metadata["storage_key"] = storage_key
            repo.save(dataset)
        except Exception as exc:
            await storage.delete(storage_key)
            raise HTTPException(
                status_code=422,
                detail=f"Failed to process file: {exc}",
            )

        # ------------------------------------------------------------------
        # Lot 2 v2 — change tracking is NO LONGER auto-enabled on upload.
        # The previous implementation called engine.enable_change_tracking
        # on the *project* GPKG (which doesn't contain the uploaded layer)
        # so live-sync was a silent no-op. Clients must now POST to
        # /datasets/{id}/enable_tracking to opt in.
        # ------------------------------------------------------------------
        return _dataset_to_response(dataset)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@router.post("/ogc", response_model=DatasetResponse, status_code=201)
@limiter.limit("30/minute")
def register_ogc_dataset(
    request: Request,
    body: OGCDatasetCreate,
    repo: Repository = Depends(get_dataset_repo),
) -> DatasetResponse:
    """Register a remote OGC service (WFS / OGC API Features) as a dataset.

    This is a *lazy* registration: no data is downloaded.  The OGC source
    configuration is stored on the dataset and can be fetched later via
    ``adapters.ogc.loader.load_ogc_dataset()``.
    """
    if body.source_type not in ("wfs", "ogc_api_features"):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid source_type '{body.source_type}'. "
            "Must be 'wfs' or 'ogc_api_features'.",
        )

    if not _is_safe_url(body.url):
        raise HTTPException(
            status_code=400,
            detail="URL is not allowed. Only http/https to public hosts are accepted.",
        )

    ogc_cfg = OGCSourceConfig(
        source_type=body.source_type,
        url=body.url,
        layer_name=body.layer_name,
        version=body.version,
        crs=body.crs,
        auth=body.auth,
        max_features=body.max_features,
    )

    dataset = Dataset(
        name=body.name,
        crs=body.crs,
        format=body.source_type,
        ogc_source=ogc_cfg,
        metadata={"ogc_url": body.url, "ogc_layer": body.layer_name},
    )
    repo.save(dataset)
    return _dataset_to_response(dataset)


@router.get("")
def list_datasets(
    limit: int = 50,
    offset: int = 0,
    repo: Repository = Depends(get_dataset_repo),
) -> dict:
    """Return paginated datasets."""
    all_items = repo.list_all()
    total = len(all_items)
    page = all_items[offset : offset + limit]
    return {
        "items": [_dataset_to_response(ds) for ds in page],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/{dataset_id}", response_model=DatasetResponse)
def get_dataset(
    dataset_id: UUID,
    repo: Repository = Depends(get_dataset_repo),
) -> DatasetResponse:
    """Return a single dataset by UUID.

    Raises:
        404: If the dataset does not exist.
    """
    ds = repo.get(dataset_id)
    if ds is None:
        raise HTTPException(
            status_code=404,
            detail=f"Dataset '{dataset_id}' not found.",
        )
    return _dataset_to_response(ds)  # type: ignore[arg-type]


@router.delete("/{dataset_id}", status_code=204)
def delete_dataset(
    dataset_id: UUID,
    request: Request,
    repo: Repository = Depends(get_dataset_repo),
) -> Response:
    """Delete a dataset and the files backing it.

    Closes the public-API gap exposed by the 2026-04-16 VPS audit
    (#437): the portal endpoint ``/api/portal/datasets/{id}`` already
    supported DELETE, but the canonical public ``/datasets/{id}`` only
    declared GET — clients hitting it received a misleading 405.

    Cascade policy (v1.2): no cascade. Rules / scenarios / triggers that
    reference the deleted dataset's UUID become orphaned references —
    they are not auto-deleted because that would couple the API to the
    rules subsystem and surprise the caller. Callers needing
    transactional cleanup should DELETE the rules first and the dataset
    last. A future cascade-mode flag may be added if the orphan-ref
    pattern bites users in practice.

    Raises:
        404: If the dataset does not exist.
    """
    from gispulse.adapters.http.dataset_ops import delete_dataset as _delete

    layer_cache = getattr(request.app.state, "layer_cache", None)
    try:
        _delete(
            dataset_id=dataset_id,
            repo=repo,
            layer_cache=layer_cache if isinstance(layer_cache, dict) else None,
        )
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=f"Dataset '{dataset_id}' not found.",
        )
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# Change-tracking lifecycle endpoints (Lot 2 v2 — Q1)
# ---------------------------------------------------------------------------


def _resolve_dataset_local_path(ds: Dataset) -> Path:
    """Return the absolute local path to the dataset's source file.

    Format-agnostic: validates only that the source is local
    (not ``s3://``) and exists on disk. The caller decides whether
    the format is appropriate for the operation it wants to perform
    — see :func:`_resolve_gpkg_path` for the GPKG-only variant kept
    for the SQL DDL teardown path in ``disable_tracking``.

    Raises:
        HTTPException(400): If the dataset has no source / is remote-only.
        HTTPException(500): If the path is missing on disk.
    """
    src = ds.source_path or ""
    if not src or src.startswith("s3://"):
        raise HTTPException(
            status_code=400,
            detail={
                "error": {
                    "code": "tracking_remote_not_supported",
                    "message": (
                        "Cannot enable tracking on a remote-only dataset. "
                        "Tracking requires direct local access."
                    ),
                }
            },
        )
    p = Path(src)
    if not p.exists():
        raise HTTPException(
            status_code=500,
            detail=f"Dataset source file not found on disk: {src}",
        )
    return p


def _resolve_gpkg_path(ds: Dataset) -> Path:
    """Return the absolute path to the dataset's GPKG file.

    Format-restricted variant of :func:`_resolve_dataset_local_path`
    used by the SQL DDL teardown path in ``disable_tracking`` (which
    still requires a SQLite handle).

    Raises:
        HTTPException(400): If the dataset is not a local GPKG.
        HTTPException(500): If the path is missing/unreadable.
    """
    if (ds.format or "").lower() != "gpkg":
        raise HTTPException(
            status_code=400,
            detail={
                "error": {
                    "code": "tracking_unsupported_format",
                    "format": ds.format,
                    "message": (
                        "This operation requires a local GPKG. PostGIS "
                        "uses pg_notify (Pro tier) and non-SQLite formats "
                        "use the duckdb_diff engine."
                    ),
                }
            },
        )
    return _resolve_dataset_local_path(ds)


def _resolve_engine_kind_for_tracking(ds: Dataset, path: Path) -> str:
    """Pick the engine kind for a tracking-enable operation.

    Routes by file URI suffix via
    :func:`gispulse.runtime.engine_inference.infer_engine`. PostGIS
    URIs are rejected here because the HTTP enable_tracking path
    relies on SQLite triggers or file-blob CDC — pg_notify
    integration is a Pro/v1.7+ feature surfaced via a different
    endpoint.

    Returns one of ``"gpkg"``, ``"spatialite"``, ``"duckdb_diff"``.
    Raises HTTPException(400) for unsupported routes.
    """
    from gispulse.runtime.engine_inference import infer_engine

    fmt = (ds.format or "").lower()
    # Trust the dataset.format hint for GPKG (the upload path stamps
    # this from pyogrio inspection — more reliable than URI suffix on
    # demos where files may be renamed).
    if fmt == "gpkg":
        return "gpkg"
    inferred = infer_engine(str(path))
    if inferred in ("gpkg", "spatialite", "duckdb_diff"):
        return inferred
    raise HTTPException(
        status_code=400,
        detail={
            "error": {
                "code": "tracking_unsupported_format",
                "format": ds.format,
                "path_suffix": path.suffix,
                "message": (
                    "Change tracking via this endpoint requires a "
                    "GPKG, SpatiaLite, or a file-blob format readable "
                    "by DuckDB-spatial (GeoJSON, FlatGeobuf, "
                    "Shapefile, KML, CSV+WKT, MapInfo TAB)."
                ),
            }
        },
    )


def _layers_in_gpkg(path: Path) -> list[str]:
    """Return user-visible spatial layer names from a GPKG.

    Excludes ``_gispulse_*`` internal tables and the OGC ``gpkg_*`` system
    tables. Falls back to a SQLite scan when ``pyogrio.list_layers`` is
    unavailable for some reason (legacy GDAL).
    """
    try:
        import pyogrio

        info = pyogrio.list_layers(str(path))
        # pyogrio.list_layers returns ndarray (name, geom_type) — first col
        # is layer name.
        names = [str(row[0]) for row in info]
    except Exception:
        from gispulse.persistence.gpkg_connection import connect_gpkg

        names = []
        with connect_gpkg(path) as conn:
            try:
                rows = conn.execute(
                    "SELECT table_name FROM gpkg_contents WHERE data_type='features'"
                ).fetchall()
                names = [str(r[0]) for r in rows]
            except sqlite3.Error:
                names = []
    return [
        n
        for n in names
        if not n.startswith("_gispulse_") and not n.startswith("gpkg_")
    ]


@router.post("/{dataset_id}/enable_tracking")
def enable_tracking(
    dataset_id: UUID,
    request: Request,
    repo: Repository = Depends(get_dataset_repo),
) -> dict:
    """Enable change-tracking on every layer of a GPKG dataset.

    Steps:
        1. Tier gate (``local_triggers``).
        2. Resolve the GPKG file from the dataset's ``source_path``.
        3. ``CREATE TRIGGER IF NOT EXISTS`` for INSERT/UPDATE/DELETE on
           every layer (idempotent — re-calling is a no-op).
        4. Register the dataset with the :class:`WatcherRegistry` so
           ``dml.changed`` events start landing on ``/ws/events``.

    Returns ``{"dataset_id", "tracking_enabled": True, "layers_tracked": [...]}``.

    Raises:
        400: ``tracking_unsupported_format`` if dataset is not GPKG.
        400: ``invalid_layer_name`` if a layer name fails the identifier
             check (quotes, semicolons, spaces — see SQLi guard).
        402: Tier doesn't grant ``local_triggers``.
        404: Dataset not found.
    """
    ds = repo.get(dataset_id)
    if ds is None:
        raise HTTPException(status_code=404, detail=f"Dataset '{dataset_id}' not found.")

    # Tier gating — propagate the original 402 contract from Lot 1.
    from gispulse.persistence.tier import TierError, enforce_feature

    try:
        enforce_feature("local_triggers")
    except TierError as exc:
        raise HTTPException(status_code=402, detail=str(exc))

    dataset_path = _resolve_dataset_local_path(ds)
    engine_kind = _resolve_engine_kind_for_tracking(ds, dataset_path)

    # Idempotency short-circuit: if the registry already holds a watcher
    # for this dataset, opening another engine on the same file (and a
    # pyogrio handle for layer listing) collides with the watcher's
    # connection — see ``test_app_state_holds_only_one_change_log_watcher``
    # and the WAL "disk I/O error" symptom on SQLite-family engines.
    # Return the cached layer snapshot from the registry instead.
    registry = getattr(request.app.state, "watcher_registry", None)
    if registry is not None and registry.is_registered(str(dataset_id)):
        return {
            "dataset_id": str(dataset_id),
            "tracking_enabled": True,
            "layers_tracked": registry.get_layers(str(dataset_id)),
        }

    tracked: list[str] = []
    invalid: list[str] = []

    if engine_kind in ("gpkg", "spatialite"):
        # SQLite-family engines: list spatial layers and install the
        # AFTER INSERT/UPDATE/DELETE triggers idempotently. The
        # ``_layers_in_gpkg`` helper also reads SpatiaLite catalog
        # tables (``geometry_columns``) when the GPKG-specific
        # ``gpkg_contents`` is absent.
        layers = _layers_in_gpkg(dataset_path)
        if not layers:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": {
                        "code": "tracking_no_layers",
                        "message": (
                            "File has no spatial layers to track. "
                            "Add a layer via QGIS / pyogrio before enabling "
                            "tracking."
                        ),
                    }
                },
            )

        # Open a short-lived engine to install the triggers. The
        # WatcherRegistry will open its own engine on register() — we
        # cannot share this one without leaking it on the hot path.
        if engine_kind == "spatialite":
            from gispulse.persistence.spatialite_engine import SpatiaLiteEngine

            install_engine = SpatiaLiteEngine(dataset_path)
        else:
            from gispulse.persistence.gpkg_engine import GeoPackageEngine

            install_engine = GeoPackageEngine(dataset_path)
        install_engine.open()
        try:
            for layer in layers:
                try:
                    install_engine.enable_change_tracking(layer)
                    tracked.append(layer)
                except ValueError as exc:
                    # SQLi guard rejected an exotic identifier — surface to caller.
                    invalid.append(layer)
                    logger.warning(
                        "enable_tracking_invalid_layer dataset_id=%s layer=%s err=%s",
                        dataset_id,
                        layer,
                        exc,
                    )
                except Exception as exc:
                    logger.warning(
                        "enable_tracking_install_failed dataset_id=%s layer=%s err=%s",
                        dataset_id,
                        layer,
                        exc,
                    )
        finally:
            install_engine.close()
    else:
        # ``duckdb_diff`` — file-blob CDC has no triggers to install.
        # The detector creates its sidecar snapshot on first poll. The
        # "tracked layer" name is the file stem (single-layer-per-file
        # contract — multi-layer files belong to the SQLite path).
        tracked = [dataset_path.stem]

    if invalid:
        raise HTTPException(
            status_code=400,
            detail={
                "error": {
                    "code": "invalid_layer_name",
                    "invalid_layers": invalid,
                    "message": (
                        "One or more layers have names that are unsafe for "
                        "trigger DDL (quotes, semicolons, spaces, dots). "
                        "Rename them to plain identifiers before enabling "
                        "tracking."
                    ),
                }
            },
        )

    if not tracked:
        raise HTTPException(
            status_code=500,
            detail="Failed to install change-tracking on any layer.",
        )

    # Hook up to the watcher registry so events flow.
    if registry is None:
        raise HTTPException(
            status_code=503,
            detail="Watcher registry not initialised — check app lifespan.",
        )

    trigger_repo = getattr(request.app.state, "trigger_repo", None)

    def _active_triggers():
        if trigger_repo is None:
            return []
        try:
            items = trigger_repo.list_all()
        except Exception:
            return []
        return [t for t in items if getattr(t, "enabled", True)]

    registry.register(
        str(dataset_id),
        dataset_path,
        engine_kind=engine_kind,
        triggers_provider=_active_triggers,
        layers=tracked,
    )

    return {
        "dataset_id": str(dataset_id),
        "tracking_enabled": True,
        "layers_tracked": tracked,
    }


@router.post("/{dataset_id}/disable_tracking")
def disable_tracking(
    dataset_id: UUID,
    request: Request,
    repo: Repository = Depends(get_dataset_repo),
) -> dict:
    """Stop the watcher and drop the SQLite triggers on every layer.

    Idempotent: calling on a non-tracked dataset returns
    ``{"tracking_enabled": False, "layers_tracked": []}`` without error.
    """
    ds = repo.get(dataset_id)
    if ds is None:
        raise HTTPException(status_code=404, detail=f"Dataset '{dataset_id}' not found.")

    registry = getattr(request.app.state, "watcher_registry", None)
    if registry is None:
        raise HTTPException(
            status_code=503,
            detail="Watcher registry not initialised — check app lifespan.",
        )

    if not registry.is_registered(str(dataset_id)):
        return {
            "dataset_id": str(dataset_id),
            "tracking_enabled": False,
            "layers_tracked": [],
        }

    # Drop triggers via a fresh engine — the registry's engine will be
    # closed when we unregister, and we want the DDL to land before that.
    try:
        gpkg_path = _resolve_gpkg_path(ds)
    except HTTPException:
        # If resolve fails (file moved, format flipped) we still want to
        # tear down the watcher to avoid leaking a thread.
        registry.unregister(str(dataset_id))
        return {
            "dataset_id": str(dataset_id),
            "tracking_enabled": False,
            "layers_tracked": [],
        }

    layers = _layers_in_gpkg(gpkg_path)
    from gispulse.persistence.gpkg_engine import GeoPackageEngine

    drop_engine = GeoPackageEngine(gpkg_path)
    drop_engine.open()
    try:
        for layer in layers:
            try:
                drop_engine.disable_change_tracking(layer)
            except Exception as exc:
                logger.warning(
                    "disable_tracking_drop_failed dataset_id=%s layer=%s err=%s",
                    dataset_id,
                    layer,
                    exc,
                )
    finally:
        drop_engine.close()

    registry.unregister(str(dataset_id))

    return {
        "dataset_id": str(dataset_id),
        "tracking_enabled": False,
        "layers_tracked": [],
    }


@router.get("/{dataset_id}/tracking_status")
def tracking_status(
    dataset_id: UUID,
    request: Request,
    repo: Repository = Depends(get_dataset_repo),
) -> dict:
    """Return the current tracking state for a dataset.

    Layer list is the GPKG's user layers when tracking is enabled, empty
    otherwise. The watcher's running flag is reported via ``enabled``.
    """
    ds = repo.get(dataset_id)
    if ds is None:
        raise HTTPException(status_code=404, detail=f"Dataset '{dataset_id}' not found.")

    registry = getattr(request.app.state, "watcher_registry", None)
    enabled = bool(registry and registry.is_registered(str(dataset_id)))

    layers: list[str] = []
    if enabled:
        try:
            gpkg_path = _resolve_gpkg_path(ds)
            layers = _layers_in_gpkg(gpkg_path)
        except HTTPException:
            layers = []

    return {
        "dataset_id": str(dataset_id),
        "enabled": enabled,
        "layers_tracked": layers,
    }


# ---------------------------------------------------------------------------
# Issue #93 — change-log inspect endpoints (CLI ↔ Portal parity P0-2)
# ---------------------------------------------------------------------------
#
# Three endpoints back the portal's "Change-log" tab for a dataset.
# All three open a fresh ``sqlite3`` connection on the GPKG, run their
# read against the v3 schema, then close. Read-only paths use Row
# factory so we can return ``dict(r)`` cleanly. The doctor path
# delegates to ``persistence.changelog_doctor.run_doctor`` and may
# call ``install_change_tracking`` when ``auto_fix=true`` — this is
# the only branch that mutates the GPKG.


def _open_dataset_gpkg(ds: Dataset) -> sqlite3.Connection:
    """Open a SQLite connection on the dataset's GPKG with row factory.

    Mirrors the CLI's ``_open_gpkg`` (cli_track) so the HTTP path
    keeps the same lifecycle (WAL + busy_timeout). Caller owns the
    connection.
    """
    from gispulse.persistence.gpkg_connection import connect_gpkg

    path = _resolve_gpkg_path(ds)
    return connect_gpkg(path, row_factory=sqlite3.Row)


@router.get("/{dataset_id}/changelog")
def get_changelog(
    dataset_id: UUID,
    layer: str | None = None,
    op: str | None = None,
    since_id: int = 0,
    limit: int = 50,
    repo: Repository = Depends(get_dataset_repo),
) -> dict[str, Any]:
    """Paginated tail of pending ``_gispulse_change_log`` rows.

    Closes #93 part 1. Mirrors ``gispulse track tail`` from the CLI —
    same SQL contract via :mod:`persistence.changelog_reader`. Use
    ``since_id`` to page forward without offset drift.

    Query params:
        layer:    Optional ``table_name`` filter.
        op:       Optional INSERT/UPDATE/DELETE filter (case-insensitive).
        since_id: Cursor — return rows with ``id > since_id``. ``0`` = first page.
        limit:    1 ≤ limit ≤ 500. Default 50.

    Returns: ``{dataset_id, items, next_since_id, has_more}``.
    """
    from gispulse.persistence.changelog_reader import (
        ChangelogReaderError,
        list_pending_changes,
        next_since_id,
    )

    ds = repo.get(dataset_id)
    if ds is None:
        raise HTTPException(
            status_code=404, detail=f"Dataset '{dataset_id}' not found."
        )

    conn = _open_dataset_gpkg(ds)
    try:
        try:
            items = list_pending_changes(
                conn,
                layer=layer,
                op=op,
                since_id=since_id,
                limit=limit,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except ChangelogReaderError as exc:
            raise HTTPException(
                status_code=404,
                detail={
                    "error": {
                        "code": "changelog_missing",
                        "message": str(exc),
                    }
                },
            )
    finally:
        conn.close()

    cursor = next_since_id(items, fallback=since_id)
    return {
        "dataset_id": str(dataset_id),
        "items": items,
        "next_since_id": cursor,
        "has_more": len(items) == limit,
    }


@router.get("/{dataset_id}/changelog/stats")
def get_changelog_stats(
    dataset_id: UUID,
    repo: Repository = Depends(get_dataset_repo),
) -> dict[str, Any]:
    """Per-layer aggregates of the change-log.

    Closes #93 part 2. Mirrors ``gispulse track list``'s aggregate
    output. Returns total pending / processed plus a per-layer
    breakdown ordered by layer name.
    """
    from gispulse.persistence.changelog_reader import (
        ChangelogReaderError,
        changelog_stats,
    )

    ds = repo.get(dataset_id)
    if ds is None:
        raise HTTPException(
            status_code=404, detail=f"Dataset '{dataset_id}' not found."
        )

    conn = _open_dataset_gpkg(ds)
    try:
        try:
            stats = changelog_stats(conn)
        except ChangelogReaderError as exc:
            raise HTTPException(
                status_code=404,
                detail={
                    "error": {
                        "code": "changelog_missing",
                        "message": str(exc),
                    }
                },
            )
    finally:
        conn.close()

    return {"dataset_id": str(dataset_id), **stats}


@router.post("/{dataset_id}/changelog/doctor")
def post_changelog_doctor(
    dataset_id: UUID,
    auto_fix: bool = False,
    repo: Repository = Depends(get_dataset_repo),
) -> dict[str, Any]:
    """Run the full health-check sweep on a tracked dataset.

    Closes #93 part 3. Mirrors ``gispulse track doctor`` from the CLI —
    same checks via :mod:`persistence.changelog_doctor`. When
    ``auto_fix=true`` (editor / Pro), partially-installed layers get
    their full trigger set re-installed in place.

    Returns:
        ``{dataset_id, ok, status, errors, repaired, checks,
        health_score}``.
    """
    from gispulse.persistence.changelog_doctor import health_score, run_doctor

    ds = repo.get(dataset_id)
    if ds is None:
        raise HTTPException(
            status_code=404, detail=f"Dataset '{dataset_id}' not found."
        )

    conn = _open_dataset_gpkg(ds)
    try:
        result = run_doctor(conn, auto_fix=auto_fix)
    finally:
        conn.close()

    result["dataset_id"] = str(dataset_id)
    result["health_score"] = health_score(result.get("checks", []))
    return result
