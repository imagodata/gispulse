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
from urllib.parse import urlparse
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile

from gispulse.adapters.http.dependencies import get_data_dir, get_dataset_repo, get_storage
from gispulse.adapters.http.rate_limit import limiter
from gispulse.adapters.http.schemas import DatasetResponse, OGCDatasetCreate
from core.config import settings as _cfg
from core.models import Dataset, OGCSourceConfig
from persistence.io import dataset_from_file, supported_extensions
from persistence.repository import Repository
from persistence.storage import DatasetStorage

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
    from core.config import settings as _cfg
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


# ---------------------------------------------------------------------------
# Change-tracking lifecycle endpoints (Lot 2 v2 — Q1)
# ---------------------------------------------------------------------------


def _resolve_gpkg_path(ds: Dataset) -> Path:
    """Return the absolute path to the dataset's GPKG file.

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
                        "Change tracking is only supported for local GPKG "
                        "datasets. PostGIS uses pg_notify (Pro tier) and "
                        "DuckDB tracking ships in Lot 3."
                    ),
                }
            },
        )
    src = ds.source_path or ""
    if not src or src.startswith("s3://"):
        raise HTTPException(
            status_code=400,
            detail={
                "error": {
                    "code": "tracking_remote_not_supported",
                    "message": (
                        "Cannot enable tracking on a remote-only GPKG. "
                        "Tracking requires direct SQLite access."
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
        names = []
        with sqlite3.connect(str(path)) as conn:
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
    from persistence.tier import TierError, enforce_feature

    try:
        enforce_feature("local_triggers")
    except TierError as exc:
        raise HTTPException(status_code=402, detail=str(exc))

    gpkg_path = _resolve_gpkg_path(ds)

    # Idempotency short-circuit: if the registry already holds a watcher
    # for this dataset, opening another engine on the same GPKG (and a
    # pyogrio handle for layer listing) collides with the watcher's
    # SQLite connection in WAL mode and surfaces as "disk I/O error".
    # Return the cached layer snapshot from the registry instead — no
    # filesystem touch, no second handle.
    registry = getattr(request.app.state, "watcher_registry", None)
    if registry is not None and registry.is_registered(str(dataset_id)):
        return {
            "dataset_id": str(dataset_id),
            "tracking_enabled": True,
            "layers_tracked": registry.get_layers(str(dataset_id)),
        }

    layers = _layers_in_gpkg(gpkg_path)
    if not layers:
        raise HTTPException(
            status_code=400,
            detail={
                "error": {
                    "code": "tracking_no_layers",
                    "message": "GPKG file has no spatial layers to track.",
                }
            },
        )

    # Open a short-lived engine to install the triggers idempotently.
    # The WatcherRegistry will open its own engine on register() — we
    # cannot share this one without leaking it on the hot path.
    from persistence.gpkg_engine import GeoPackageEngine

    tracked: list[str] = []
    invalid: list[str] = []
    install_engine = GeoPackageEngine(gpkg_path)
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
        gpkg_path,
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
    from persistence.gpkg_engine import GeoPackageEngine

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
