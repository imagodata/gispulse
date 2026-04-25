"""
Datasets router for the GISPulse HTTP API.

Endpoints:
    POST /datasets/upload   — upload a spatial file and register a dataset
    POST /datasets/ogc      — register a remote OGC service as a dataset (lazy)
    GET  /datasets          — list all datasets
    GET  /datasets/{id}     — detail for a single dataset
"""

from __future__ import annotations

import ipaddress
import shutil
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
