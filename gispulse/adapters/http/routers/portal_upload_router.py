"""
Portal upload router — dataset upload and URL import.

Supports both local filesystem and S3/MinIO storage backends via
the :class:`~persistence.storage.DatasetStorage` abstraction.
"""

from __future__ import annotations

import ipaddress
import shutil
import tempfile
import uuid
from pathlib import Path
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, field_validator

from gispulse.adapters.http.layer_utils import get_layer_styles, get_full_style_defs, load_layers
from gispulse.adapters.http.rate_limit import limiter
from gispulse.adapters.http.routers._upload_utils import (
    find_duplicate_by_hash as _find_duplicate,
    sha256_file as _sha256,
)
from core.logging import get_logger
from persistence.io import dataset_from_file, detect_format
from persistence.storage import DatasetStorage

log = get_logger(__name__)

router = APIRouter()

# ---------------------------------------------------------------------------
# SSRF protection (#241)
# ---------------------------------------------------------------------------

_PRIVATE_RANGES = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),  # link-local
    ipaddress.ip_network("127.0.0.0/8"),     # loopback IPv4
    ipaddress.ip_network("::1/128"),          # loopback IPv6
    ipaddress.ip_network("fc00::/7"),         # unique local IPv6
]

_BLOCKED_HOSTNAMES = {"localhost"}


def _is_safe_url(url: str) -> bool:
    """Return True if the URL is safe to fetch (not a private/internal address).

    Resolves DNS before checking to prevent DNS rebinding attacks.

    Rejects:
    - Non-http/https schemes
    - localhost, 127.x.x.x, ::1
    - 169.254.x.x (link-local)
    - 10.x.x.x, 172.16-31.x.x, 192.168.x.x
    - Internal hostnames (.local, .internal, .corp)
    """
    import socket

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

    # Block internal TLDs that may resolve to private addresses
    lower_host = host.lower()
    if any(lower_host.endswith(suffix) for suffix in (".local", ".internal", ".corp", ".intra", ".lan")):
        return False

    # Check IP literals directly
    try:
        addr = ipaddress.ip_address(host)
        for network in _PRIVATE_RANGES:
            if addr in network:
                return False
        return True
    except ValueError:
        pass  # Not an IP literal — it's a hostname

    # Resolve DNS and check all resolved addresses against private ranges
    # This prevents DNS rebinding attacks where a hostname resolves to a private IP
    try:
        addr_infos = socket.getaddrinfo(host, parsed.port or 443, proto=socket.IPPROTO_TCP)
        for _family, _type, _proto, _canonname, sockaddr in addr_infos:
            ip_str = sockaddr[0]
            try:
                addr = ipaddress.ip_address(ip_str)
                for network in _PRIVATE_RANGES:
                    if addr in network:
                        return False
            except ValueError:
                return False
    except socket.gaierror:
        # DNS resolution failed — URL is likely invalid but not an SSRF risk.
        # Allow it through; the actual HTTP fetch will fail with a connection error.
        pass

    return True


def _get_storage(request: Request) -> DatasetStorage:
    """Get the storage backend from app state."""
    return request.app.state.storage


def _storage_key(dataset_id: str, filename: str) -> str:
    """Build a storage key for a dataset file."""
    return f"{dataset_id}/{filename}"


@router.post("/datasets/upload")
@limiter.limit("20/minute")
async def upload_dataset(
    request: Request,
    file: UploadFile = File(...),
    force: bool = False,
) -> JSONResponse:
    """Upload a spatial file and register it."""
    data_dir: Path = request.app.state.data_dir
    dataset_repo = request.app.state.dataset_repo
    layer_cache: dict = request.app.state.layer_cache
    storage = _get_storage(request)

    if not file.filename:
        raise HTTPException(400, "No filename provided")

    # Sanitize filename to prevent path traversal attacks
    safe_filename = Path(file.filename).name
    if not safe_filename:
        raise HTTPException(400, "Invalid filename.")

    dataset_id = str(uuid.uuid4())

    # Write to a temp file for format detection and metadata extraction
    # (these operations require local filesystem access)
    tmp_dir = Path(tempfile.mkdtemp(prefix="gispulse_upload_"))
    tmp_dest = tmp_dir / safe_filename
    try:
        with open(tmp_dest, "wb") as f:
            shutil.copyfileobj(file.file, f)

        driver = detect_format(str(tmp_dest))
        if driver is None:
            raise HTTPException(400, f"Unsupported format: {safe_filename}")

        file_hash = _sha256(tmp_dest)
        if not force:
            existing = _find_duplicate(dataset_repo, file_hash)
            if existing is not None:
                raise HTTPException(
                    409,
                    detail={
                        "conflict": True,
                        "existing_id": str(existing.id),
                        "existing_name": existing.name,
                        "hash": file_hash,
                    },
                )

        # Upload to storage backend
        storage_key = _storage_key(dataset_id, safe_filename)
        with open(tmp_dest, "rb") as fh:
            await storage.upload(storage_key, fh)

        # For local storage, resolve the actual path for source_path.
        # For S3, source_path stores the storage key prefixed with "s3://".
        local_path = await storage.get_local_path(storage_key)
        if local_path is not None:
            source_path = str(local_path)
            inspect_path = str(local_path)
        else:
            source_path = f"s3://{storage_key}"
            # Use the temp file for inspection (still on disk)
            inspect_path = str(tmp_dest)

        try:
            dataset = dataset_from_file(inspect_path)
            dataset.source_path = source_path
            if dataset.metadata is None:
                dataset.metadata = {}
            dataset.metadata["file_hash"] = file_hash
            dataset.metadata["storage_key"] = storage_key
            dataset_repo.save(dataset)
        except Exception as e:
            await storage.delete(storage_key)
            log.error("dataset_index_failed", file=safe_filename, error=str(e))
            raise HTTPException(400, f"Failed to read file: {e}")

        try:
            layers, layer_gdfs = load_layers(inspect_path, Path(safe_filename).stem)
            layer_cache[str(dataset.id)] = layer_gdfs
        except Exception as e:
            log.warning("layer_cache_failed", dataset_id=str(dataset.id), error=str(e))
            layers = []

        styles = get_layer_styles(inspect_path)
        style_defs = get_full_style_defs(inspect_path)
        log.info("dataset_uploaded", id=str(dataset.id), name=dataset.name, layers=len(layers))

        file_size = tmp_dest.stat().st_size if tmp_dest.exists() else 0
        return JSONResponse(
            content={
                "id": str(dataset.id),
                "name": dataset.name,
                "source_path": source_path,
                "format": dataset.format or driver,
                "crs": dataset.crs,
                "file_size": file_size,
                "layers": layers,
                "styles": styles,
                "style_defs": style_defs,
                "created_at": dataset.created_at.isoformat(),
            },
            status_code=201,
        )
    finally:
        # Clean up temp directory (local storage already has the file)
        shutil.rmtree(tmp_dir, ignore_errors=True)


class ImportFromUrlBody(BaseModel):
    url: str
    name: str | None = None

    @field_validator("url")
    @classmethod
    def url_must_be_safe(cls, v: str) -> str:
        if not _is_safe_url(v):
            raise ValueError(
                "URL is not allowed. Only http/https to public hosts are accepted."
            )
        return v


@router.post("/datasets/import-url")
@limiter.limit("10/minute")
async def import_from_url(
    request: Request,
    body: ImportFromUrlBody,
) -> JSONResponse:
    """Download a remote spatial file and register it as a dataset."""
    data_dir: Path = request.app.state.data_dir
    dataset_repo = request.app.state.dataset_repo
    layer_cache: dict = request.app.state.layer_cache
    storage = _get_storage(request)

    url_path = body.url.split("?")[0].split("#")[0]
    filename = url_path.rsplit("/", 1)[-1] or "download.geojson"
    if not any(filename.endswith(ext) for ext in (".gpkg", ".geojson", ".json", ".shp", ".fgb", ".csv", ".parquet", ".gml", ".kml")):
        filename = f"{filename}.geojson"

    dataset_id = str(uuid.uuid4())

    # Download to temp file first
    tmp_dir = Path(tempfile.mkdtemp(prefix="gispulse_import_"))
    tmp_dest = tmp_dir / filename
    try:
        try:
            _MAX_DOWNLOAD_SIZE = 100 * 1024 * 1024  # 100 MB
            async with httpx.AsyncClient(follow_redirects=True, timeout=120) as client:
                async with client.stream("GET", body.url) as resp:
                    resp.raise_for_status()
                    # Check Content-Length header if available
                    content_length = resp.headers.get("content-length")
                    if content_length and int(content_length) > _MAX_DOWNLOAD_SIZE:
                        raise HTTPException(413, f"Remote file too large ({content_length} bytes, max {_MAX_DOWNLOAD_SIZE})")
                    downloaded = 0
                    with open(tmp_dest, "wb") as f:
                        async for chunk in resp.aiter_bytes(chunk_size=65536):
                            downloaded += len(chunk)
                            if downloaded > _MAX_DOWNLOAD_SIZE:
                                raise HTTPException(413, f"Download exceeds {_MAX_DOWNLOAD_SIZE} bytes limit")
                            f.write(chunk)
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(400, f"Download failed: {e}")

        driver = detect_format(str(tmp_dest))
        if driver is None:
            raise HTTPException(400, f"Unsupported format after download: {filename}")

        # Upload to storage backend
        storage_key = _storage_key(dataset_id, filename)
        with open(tmp_dest, "rb") as fh:
            await storage.upload(storage_key, fh)

        local_path = await storage.get_local_path(storage_key)
        if local_path is not None:
            source_path = str(local_path)
            inspect_path = str(local_path)
        else:
            source_path = f"s3://{storage_key}"
            inspect_path = str(tmp_dest)

        try:
            dataset = dataset_from_file(inspect_path)
            if body.name:
                dataset.name = body.name
            dataset.source_path = source_path
            if dataset.metadata is None:
                dataset.metadata = {}
            dataset.metadata["storage_key"] = storage_key
            dataset_repo.save(dataset)
        except Exception as e:
            await storage.delete(storage_key)
            raise HTTPException(400, f"Failed to read downloaded file: {e}")

        try:
            layers, layer_gdfs = load_layers(inspect_path, dataset.name)
            layer_cache[str(dataset.id)] = layer_gdfs
        except Exception as e:
            log.warning("import_url_cache_failed", dataset_id=str(dataset.id), error=str(e))
            layers = []

        styles = get_layer_styles(inspect_path)
        style_defs = get_full_style_defs(inspect_path)
        log.info("dataset_imported_url", id=str(dataset.id), name=dataset.name, url=body.url)

        file_size = tmp_dest.stat().st_size if tmp_dest.exists() else 0
        return JSONResponse(
            content={
                "id": str(dataset.id),
                "name": dataset.name,
                "source_path": source_path,
                "format": dataset.format or driver,
                "crs": dataset.crs,
                "file_size": file_size,
                "layers": layers,
                "styles": styles,
                "style_defs": style_defs,
                "created_at": dataset.created_at.isoformat(),
            },
            status_code=201,
        )
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
