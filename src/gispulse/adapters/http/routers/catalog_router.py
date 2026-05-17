"""Catalog REST API — projections, basemaps, flux, open data, import."""

from __future__ import annotations

import logging
import shutil
import tempfile
from dataclasses import asdict
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query, Request

from gispulse.catalog.models import CatalogDomain, FluxEntry, FluxProtocol, OpenDataEntry
from gispulse.catalog import registry as catalog_registry
from gispulse.adapters.http.schemas import CatalogImportRequest

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/catalog", tags=["catalog"])


def _serialize(entries):
    return [asdict(e) for e in entries]


@router.get("/providers")
def list_providers():
    """List all registered catalog providers."""
    return catalog_registry.list_providers()


@router.get("/projections")
def list_projections(
    search: str | None = None,
    tags: str | None = Query(None, description="Comma-separated tags"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    tag_list = [t.strip() for t in tags.split(",")] if tags else None
    return _serialize(
        catalog_registry.search(
            domain=CatalogDomain.PROJECTION,
            search=search,
            tags=tag_list,
            limit=limit,
            offset=offset,
        )
    )


@router.get("/basemaps")
def list_basemaps(
    search: str | None = None,
    tags: str | None = Query(None, description="Comma-separated tags"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    tag_list = [t.strip() for t in tags.split(",")] if tags else None
    return _serialize(
        catalog_registry.search(
            domain=CatalogDomain.BASEMAP,
            search=search,
            tags=tag_list,
            limit=limit,
            offset=offset,
        )
    )


@router.get("/flux")
def list_flux(
    search: str | None = None,
    protocol: str | None = Query(None, description="wms, wfs, wmts, tms, xyz"),
    provider: str | None = None,
    tags: str | None = Query(None, description="Comma-separated tags"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    tag_list = [t.strip() for t in tags.split(",")] if tags else None
    entries = catalog_registry.search(
        domain=CatalogDomain.FLUX,
        search=search,
        tags=tag_list,
        provider=provider,
        limit=limit,
        offset=offset,
    )
    if protocol:
        entries = [e for e in entries if getattr(e, "protocol", None) == protocol]
    return _serialize(entries)


@router.get("/opendata")
def list_opendata(
    search: str | None = None,
    provider: str | None = None,
    tags: str | None = Query(None, description="Comma-separated tags"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    tag_list = [t.strip() for t in tags.split(",")] if tags else None
    return _serialize(
        catalog_registry.search(
            domain=CatalogDomain.OPENDATA,
            search=search,
            tags=tag_list,
            provider=provider,
            limit=limit,
            offset=offset,
        )
    )


@router.get("/search")
def search_all(
    q: str = Query(..., min_length=1),
    domain: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """Cross-domain full-text search."""
    cat_domain = CatalogDomain(domain) if domain else None
    return _serialize(
        catalog_registry.search(
            domain=cat_domain,
            search=q,
            limit=limit,
            offset=offset,
        )
    )


@router.get("/entry/{entry_id:path}")
def get_entry(entry_id: str):
    """Get a single catalog entry by full ID."""
    entry = catalog_registry.get_entry(entry_id)
    if not entry:
        raise HTTPException(404, f"Entry '{entry_id}' not found")
    return asdict(entry)


# ---------------------------------------------------------------------------
# Catalog import — fetch remote data and materialise as local dataset
# ---------------------------------------------------------------------------


@router.post("/import", status_code=201)
async def import_catalog_entry(body: CatalogImportRequest, request: Request):
    """Import a catalog entry as a local dataset.

    Supports:
    - **flux (WFS / OGC Features)**: fetches vector features with optional bbox
    - **opendata with download_url**: downloads the file
    - **flux (WMS/WMTS/TMS/XYZ)**: returns metadata for adding as external layer (no download)
    """
    entry = catalog_registry.get_entry(body.entry_id)
    if not entry:
        raise HTTPException(404, f"Catalog entry '{body.entry_id}' not found")

    data_dir: Path = request.app.state.data_dir
    dataset_repo = request.app.state.dataset_repo
    layer_cache: dict = request.app.state.layer_cache

    bbox = tuple(body.bbox) if body.bbox else None

    # --- Flux entries (WFS / OGC API Features) ---
    if isinstance(entry, FluxEntry) and entry.protocol in (
        FluxProtocol.WFS,
        FluxProtocol.OGC_FEATURES,
    ):
        return await _import_wfs_flux(
            entry, bbox, body.crs, body.max_features, body.name,
            data_dir, dataset_repo, layer_cache,
        )

    # --- Flux entries (raster services — no download, return layer info) ---
    if isinstance(entry, FluxEntry):
        return {
            "type": "external_layer",
            "entry_id": entry.id,
            "name": body.name or entry.name,
            "protocol": entry.protocol.value,
            "service_url": entry.service_url,
            "layer_name": entry.layer_name,
            "message": "Raster flux added as external layer (no local download).",
        }

    # --- OpenData entries with download_url ---
    if isinstance(entry, OpenDataEntry) and entry.download_url:
        return await _import_opendata_download(
            entry, body.name, data_dir, dataset_repo, layer_cache,
        )

    # --- OpenData entries with linked WFS (no download_url but wfs hint) ---
    if isinstance(entry, OpenDataEntry) and not entry.download_url:
        wfs_flux_id = entry.metadata.get("wfs_flux_id")
        wfs_layer = entry.metadata.get("wfs_layer")
        wfs_url = entry.metadata.get("wfs_url")

        if wfs_flux_id:
            # Redirect to the linked WFS flux entry
            flux_entry = catalog_registry.get_entry(wfs_flux_id)
            if flux_entry and isinstance(flux_entry, FluxEntry):
                return await _import_wfs_flux(
                    flux_entry, bbox, body.crs, body.max_features,
                    body.name or entry.name,
                    data_dir, dataset_repo, layer_cache,
                )
        elif wfs_layer and wfs_url:
            # Build a synthetic FluxEntry for direct WFS query
            synthetic = FluxEntry(
                id=entry.id,
                domain=CatalogDomain.FLUX,
                provider=entry.provider,
                name=entry.name,
                description=entry.description,
                tags=entry.tags,
                service_url=f"{wfs_url}?SERVICE=WFS&VERSION=2.0.0",
                protocol=FluxProtocol.WFS,
                layer_name=wfs_layer,
                attribution=f"© {entry.provider.upper()}",
                default_crs="EPSG:4326",
            )
            return await _import_wfs_flux(
                synthetic, bbox, body.crs, body.max_features,
                body.name or entry.name,
                data_dir, dataset_repo, layer_cache,
            )

        raise HTTPException(
            400,
            f"Entry '{body.entry_id}' has no download URL and no WFS link. "
            "Try using a bbox with the WFS flux entry directly.",
        )

    raise HTTPException(400, f"Cannot import entry of type {type(entry).__name__}")


async def _import_wfs_flux(
    entry: FluxEntry,
    bbox: tuple | None,
    crs: str,
    max_features: int | None,
    name: str | None,
    data_dir: Path,
    dataset_repo,
    layer_cache: dict,
) -> dict:
    """Fetch WFS/OGC Features and save as GPKG."""

    from gispulse.adapters.ogc.wfs_client import fetch_ogc_api_features, fetch_wfs
    from gispulse.core.models import Dataset, OGCSourceConfig
    from gispulse.adapters.http.layer_utils import build_layer_meta, load_layers

    ogc_cfg = OGCSourceConfig(
        source_type="wfs" if entry.protocol == FluxProtocol.WFS else "ogc_api_features",
        url=entry.service_url.split("?")[0],
        layer_name=entry.layer_name,
        crs=crs,
        max_features=max_features,
    )

    log.info("catalog_import_wfs: %s bbox=%s", entry.layer_name, bbox)

    try:
        if entry.protocol == FluxProtocol.WFS:
            gdf = fetch_wfs(ogc_cfg, bbox=bbox)
        else:
            gdf = fetch_ogc_api_features(ogc_cfg, bbox=bbox)
    except Exception as exc:
        raise HTTPException(502, f"Failed to fetch from OGC service: {exc}")

    if gdf.empty:
        raise HTTPException(
            404, "No features found for the given parameters (bbox may be empty)."
        )

    # Reproject if needed
    if crs and crs != "EPSG:4326" and gdf.crs:
        try:
            gdf = gdf.to_crs(crs)
        except Exception:
            pass

    # Save to GPKG
    dataset_id = uuid4()
    ds_name = name or f"{entry.name} ({len(gdf)} features)"
    safe_layer = entry.layer_name.replace(":", "_").replace("/", "_")
    dest_dir = data_dir / str(dataset_id)
    dest_dir.mkdir(parents=True, exist_ok=True)
    gpkg_path = dest_dir / f"{safe_layer}.gpkg"

    gdf.to_file(str(gpkg_path), driver="GPKG", layer=safe_layer)

    dataset = Dataset(
        id=dataset_id,
        name=ds_name,
        source_path=str(gpkg_path),
        crs=crs,
        format="GPKG",
        ogc_source=ogc_cfg,
        metadata={
            "catalog_entry": entry.id,
            "bbox": list(bbox) if bbox else None,
            "feature_count": len(gdf),
            "provider": entry.provider,
        },
    )
    dataset_repo.save(dataset)

    # Build layer metadata for frontend
    try:
        layers = load_layers(str(gpkg_path))
        layer_metas = [build_layer_meta(str(gpkg_path), ln) for ln in layers]
        layer_cache[str(dataset_id)] = layer_metas
    except Exception as e:
        log.warning("catalog_import_cache_failed: %s", e)
        layer_metas = []

    log.info(
        "catalog_import_complete: %s → %s (%d features)",
        entry.id, gpkg_path, len(gdf),
    )

    return {
        "id": str(dataset_id),
        "name": ds_name,
        "source_path": str(gpkg_path),
        "format": "GPKG",
        "crs": crs,
        "feature_count": len(gdf),
        "layers": layer_metas,
        "created_at": dataset.created_at.isoformat(),
        "catalog_entry": entry.id,
        "bbox": list(bbox) if bbox else None,
    }


async def _import_opendata_download(
    entry: OpenDataEntry,
    name: str | None,
    data_dir: Path,
    dataset_repo,
    layer_cache: dict,
) -> dict:
    """Download an opendata file and register as dataset."""
    import httpx

    from gispulse.persistence.io import dataset_from_file
    from gispulse.adapters.http.layer_utils import build_layer_meta, get_layer_styles, load_layers

    url = entry.download_url
    if not url:
        raise HTTPException(400, "No download URL available for this entry.")

    url_path = url.split("?")[0].split("#")[0]
    filename = url_path.rsplit("/", 1)[-1] or "download.gpkg"

    dataset_id = uuid4()
    dest_dir = data_dir / str(dataset_id)
    dest_dir.mkdir(parents=True, exist_ok=True)

    tmp_dir = Path(tempfile.mkdtemp(prefix="gispulse_catalog_"))
    tmp_dest = tmp_dir / filename

    try:
        max_size = 200 * 1024 * 1024  # 200 MB
        async with httpx.AsyncClient(follow_redirects=True, timeout=180) as client:
            async with client.stream("GET", url) as resp:
                resp.raise_for_status()
                cl = resp.headers.get("content-length")
                if cl and int(cl) > max_size:
                    raise HTTPException(413, f"File too large ({cl} bytes)")
                downloaded = 0
                with open(tmp_dest, "wb") as f:
                    async for chunk in resp.aiter_bytes(65536):
                        downloaded += len(chunk)
                        if downloaded > max_size:
                            raise HTTPException(413, "File too large (exceeded 200 MB)")
                        f.write(chunk)

        # Copy to data dir
        final_path = dest_dir / filename
        shutil.copy2(str(tmp_dest), str(final_path))

        # Parse dataset
        try:
            dataset = dataset_from_file(str(final_path))
            dataset.id = dataset_id
            dataset.name = name or entry.name
            dataset.source_path = str(final_path)
            dataset.metadata = {
                "catalog_entry": entry.id,
                "download_url": url,
                "provider": entry.provider,
            }
            dataset_repo.save(dataset)
        except Exception as exc:
            raise HTTPException(422, f"Failed to process downloaded file: {exc}")

        # Build layer metadata
        try:
            layers_list = load_layers(str(final_path))
            layer_metas = [build_layer_meta(str(final_path), ln) for ln in layers_list]
            layer_cache[str(dataset_id)] = layer_metas
        except Exception:
            layer_metas = []

        styles = get_layer_styles(str(final_path))
        file_size = final_path.stat().st_size

        return {
            "id": str(dataset_id),
            "name": dataset.name,
            "source_path": str(final_path),
            "format": dataset.format,
            "crs": dataset.crs,
            "file_size": file_size,
            "layers": layer_metas,
            "styles": styles,
            "created_at": dataset.created_at.isoformat(),
            "catalog_entry": entry.id,
        }
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
