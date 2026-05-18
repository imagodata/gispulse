"""
Examples router for the GISPulse "Try it" mini-backend (v1.5.x).

Exposes a tiny, read-only catalogue of bundled GPKG datasets so the
public portal (`demo.gispulse.dev` / GitHub Pages) can showcase the
runtime without ever touching user data.

Endpoints:

* ``GET /examples``                                — registry list
* ``GET /examples/{id}``                           — dataset metadata
* ``GET /examples/{id}/preview``                   — TileJSON 3.0 doc
* ``GET /examples/{id}/tiles/{z}/{x}/{y}.mvt``     — MVT vector tile
* ``POST /examples/{id}/triggers/dryrun``          — sandbox trigger
                                                     evaluation, no
                                                     side effects
* ``GET /examples/health``                         — registry liveness

Datasets are static, bundled in ``examples/datasets/*.gpkg`` and never
mutated by the API. The dry-run endpoint accepts trigger configurations
and synthetic DML records, evaluates them in-memory via
:class:`rules.trigger_evaluator.TriggerEvaluator`, and returns the list
of fired triggers + the actions a real :class:`ActionDispatcher` would
have dispatched. Outbound side effects (webhooks, SQL execution) are
**always** bypassed — see :class:`DryRunDispatcher`.

Hard limits (DoS protection on a public endpoint):

* dry-run timeout: 5 s per request
* max simulated DML records: 1000
* max triggers per request: 50
* MVT tile cache: capped at ~50 MB on disk (LRU)
"""

from __future__ import annotations

import gzip
import io
import logging
import math
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/examples", tags=["examples"])

# ---------------------------------------------------------------------------
# Static registry
# ---------------------------------------------------------------------------

# router file: ``gispulse/adapters/http/routers/examples_router.py`` —
# ``parents[5]`` walks up routers/http/adapters/gispulse/src/<repo>.
_REPO_ROOT = Path(__file__).resolve().parents[5]
_DEFAULT_DATASETS_DIR = _REPO_ROOT / "examples" / "datasets"


@dataclass(frozen=True)
class ExampleDataset:
    """A single bundled dataset exposed through the examples router."""

    id: str
    title: str
    description: str
    file: str  # relative path under ``examples/datasets/``
    layer: str  # primary layer name (used for previews + dryrun)
    scenario: str  # short tag: "parcels", "isochrone", "audit", "agriculture", ...

    def absolute_path(self, base_dir: Path) -> Path:
        return (base_dir / self.file).resolve()


# Curated registry. Keep small (<10 MB per file) — these are bundled in the
# repo and shipped with the wheel for the read-only public demo.
_REGISTRY: dict[str, ExampleDataset] = {
    ds.id: ds
    for ds in (
        ExampleDataset(
            id="muret-parcels",
            title="Muret — Cadastral parcels",
            description=(
                "17 000+ cadastral parcels covering the city of Muret (Haute-"
                "Garonne, France). Polygons in EPSG:4326 with owner, surface, "
                "and zoning attributes — handy for testing DML triggers on a "
                "realistic urban dataset."
            ),
            file="muret_parcels.gpkg",
            layer="parcels",
            scenario="parcels",
        ),
        ExampleDataset(
            id="muret-flood-zones",
            title="Muret — Flood risk zones",
            description=(
                "Three flood-risk polygons over the Muret area (low/medium/"
                "high). Pair with the parcels dataset to demo spatial-"
                "constraint triggers (parcel touches flood zone)."
            ),
            file="muret_flood_zones.gpkg",
            layer="flood_zones",
            scenario="audit",
        ),
        ExampleDataset(
            id="toulouse-isochrones",
            title="Toulouse — Pedestrian isochrones",
            description=(
                "Three concentric pedestrian-travel-time rings (5/10/15 min) "
                "around central Toulouse. Synthetic but topologically valid "
                "polygons in EPSG:4326."
            ),
            file="isochrones.gpkg",
            layer="isochrones",
            scenario="isochrone",
        ),
        ExampleDataset(
            id="bordeaux-rpg",
            title="Bordeaux — Agricultural parcels (RPG)",
            description=(
                "500 agricultural parcels (Registre Parcellaire Graphique) "
                "around Bordeaux (Gironde). MultiPolygon geometries with "
                "crop-type and surface attributes."
            ),
            file="rpg_bordeaux.gpkg",
            layer="parcelles_agricoles",
            scenario="agriculture",
        ),
    )
}


def get_registry() -> dict[str, ExampleDataset]:
    """Return the static registry. Exposed for tests / overrides."""
    return _REGISTRY


def _datasets_dir(request: Request) -> Path:
    """Return the directory that holds the bundled GPKG fixtures.

    Falls back to the repo-relative default. Tests can override via
    ``app.state.examples_datasets_dir``.
    """
    override = getattr(request.app.state, "examples_datasets_dir", None)
    if override is not None:
        return Path(override).resolve()
    return _DEFAULT_DATASETS_DIR


def _resolve_or_404(dataset_id: str, base_dir: Path) -> tuple[ExampleDataset, Path]:
    ds = _REGISTRY.get(dataset_id)
    if ds is None:
        raise HTTPException(status_code=404, detail=f"Example '{dataset_id}' not found.")
    path = ds.absolute_path(base_dir)
    base_resolved = base_dir.resolve()
    # Defence in depth — make sure id-controlled paths can't escape the
    # registry directory even if the registry is ever mutated at runtime.
    if not path.is_relative_to(base_resolved) or not path.is_file():
        raise HTTPException(
            status_code=404,
            detail=f"Example '{dataset_id}' is registered but the file is missing.",
        )
    return ds, path


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class ExampleSummary(BaseModel):
    """Summary item returned by ``GET /examples``."""

    id: str
    title: str
    description: str
    scenario: str
    layer_count: int
    feature_count: int
    size_bytes: int


class ExampleLayer(BaseModel):
    name: str
    geometry_type: str | None = None
    feature_count: int
    crs: str | None = None
    fields: list[str] = Field(default_factory=list)


class ExampleDetail(ExampleSummary):
    """Detailed metadata for a single example."""

    layers: list[ExampleLayer]
    bounds: tuple[float, float, float, float] | None = None
    primary_layer: str


class TileJSONResponse(BaseModel):
    tilejson: str = "3.0.0"
    name: str
    description: str
    tiles: list[str]
    minzoom: int = 0
    maxzoom: int = 22
    bounds: list[float]
    center: list[float]
    scheme: str = "xyz"
    format: str = "pbf"
    vector_layers: list[dict[str, Any]]
    attribution: str = "GISPulse examples — bundled fixture"


class HealthResponse(BaseModel):
    status: str
    dataset_count: int
    missing: list[str] = Field(default_factory=list)


# Trigger dry-run -----------------------------------------------------------

_MAX_DRYRUN_RECORDS = 1000
_MAX_DRYRUN_TRIGGERS = 50
_DRYRUN_TIMEOUT_S = 5.0


class SimulatedDML(BaseModel):
    """A single DML record fed to the trigger evaluator."""

    table: str
    operation: str = Field(default="INSERT", pattern=r"^(INSERT|UPDATE|DELETE)$")
    feature_id: str | None = None
    new_values: dict[str, Any] = Field(default_factory=dict)
    old_values: dict[str, Any] = Field(default_factory=dict)
    new_geom_wkt: str | None = None
    old_geom_wkt: str | None = None
    session_id: str | None = None

    @field_validator("operation", mode="before")
    @classmethod
    def _upper_op(cls, v: Any) -> Any:
        if isinstance(v, str):
            return v.upper()
        return v


class TriggerActionConfig(BaseModel):
    action_type: str
    config: dict[str, Any] = Field(default_factory=dict)


class TriggerSpec(BaseModel):
    """Subset of :class:`core.models.Trigger` exposed to the public API.

    Only the fields the in-memory evaluator actually reads are accepted.
    Identifiers are optional; missing ones get a synthetic UUID.
    """

    id: str | None = None
    name: str = ""
    trigger_type: str = "dml"
    enabled: bool = True
    conditions: dict[str, Any] = Field(default_factory=dict)
    actions: list[TriggerActionConfig] = Field(default_factory=list)


class DryRunRequest(BaseModel):
    triggers: list[TriggerSpec] = Field(default_factory=list)
    simulated_dml: list[SimulatedDML] = Field(default_factory=list)


class DryRunFiredEvent(BaseModel):
    trigger_id: str
    trigger_name: str
    matched: bool
    table: str
    operation: str
    feature_id: str | None
    eval_time_ms: float
    cascade_depth: int


class DryRunSimulatedAction(BaseModel):
    trigger_id: str
    trigger_name: str
    action_type: str
    config: dict[str, Any]
    table: str
    feature_id: str | None


class DryRunResponse(BaseModel):
    events: list[DryRunFiredEvent]
    actions: list[DryRunSimulatedAction]
    duration_ms: float
    truncated: bool = False
    notes: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Layer / dataset inspection (pyogrio is mandatory in the gispulse runtime)
# ---------------------------------------------------------------------------


def _read_layer_info(path: Path) -> list[ExampleLayer]:
    import pyogrio  # local import — keeps router import light at startup

    layers_meta: list[ExampleLayer] = []
    raw_layers = pyogrio.list_layers(path)
    # ``pyogrio.list_layers`` returns a 2-D ndarray of shape (n, 2) where
    # column 0 is the layer name and column 1 the geometry type.
    # Iterating yields per-row arrays, so we explicitly index into [0] —
    # passing the row directly to ``read_info(layer=...)`` would crash with
    # "Layer '['name' 'GeomType']' could not be opened".
    for row in raw_layers:
        try:
            layer_name = str(row[0])
        except (IndexError, TypeError):
            layer_name = str(row)
        try:
            info = pyogrio.read_info(path, layer=layer_name)
        except Exception:
            logger.exception("examples_read_info_failed", extra={"path": str(path), "layer": layer_name})
            continue
        fields_raw = info.get("fields")
        if fields_raw is None:
            fields = []
        else:
            try:
                fields = list(fields_raw)
            except TypeError:
                fields = []
        layers_meta.append(
            ExampleLayer(
                name=str(layer_name),
                geometry_type=str(info.get("geometry_type")) if info.get("geometry_type") else None,
                feature_count=int(info.get("features") or 0),
                crs=str(info.get("crs")) if info.get("crs") else None,
                fields=[str(f) for f in fields],
            )
        )
    return layers_meta


def _read_layer_bounds(path: Path, layer: str) -> tuple[float, float, float, float] | None:
    """Read the layer's WGS-84 bounding box. Returns None if unavailable."""
    try:
        import geopandas as gpd
        import pyogrio

        info = pyogrio.read_info(path, layer=layer)
        bounds = info.get("total_bounds")
        if bounds is not None and len(bounds) == 4:
            return tuple(float(v) for v in bounds)  # type: ignore[return-value]
        # Fall back to reading the layer (small fixture, this is OK)
        gdf = gpd.read_file(path, layer=layer)
        if gdf.empty:
            return None
        if gdf.crs is not None and gdf.crs.to_epsg() != 4326:
            gdf = gdf.to_crs(epsg=4326)
        minx, miny, maxx, maxy = gdf.total_bounds
        return (float(minx), float(miny), float(maxx), float(maxy))
    except Exception:
        logger.exception("examples_bounds_failed", extra={"path": str(path), "layer": layer})
        return None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/health", response_model=HealthResponse)
def examples_health(request: Request) -> HealthResponse:
    """Liveness probe + registry sanity check."""
    base = _datasets_dir(request)
    missing: list[str] = []
    for ds in _REGISTRY.values():
        if not ds.absolute_path(base).is_file():
            missing.append(ds.id)
    return HealthResponse(
        status="ok" if not missing else "degraded",
        dataset_count=len(_REGISTRY) - len(missing),
        missing=missing,
    )


@router.get("", response_model=list[ExampleSummary])
def list_examples(request: Request) -> list[ExampleSummary]:
    base = _datasets_dir(request)
    out: list[ExampleSummary] = []
    for ds in _REGISTRY.values():
        path = ds.absolute_path(base)
        if not path.is_file():
            # Skip silently — health endpoint surfaces the issue.
            continue
        try:
            layers = _read_layer_info(path)
        except Exception:
            logger.exception("examples_layer_info_failed", extra={"id": ds.id})
            continue
        feature_count = sum(layer.feature_count for layer in layers)
        out.append(
            ExampleSummary(
                id=ds.id,
                title=ds.title,
                description=ds.description,
                scenario=ds.scenario,
                layer_count=len(layers),
                feature_count=feature_count,
                size_bytes=path.stat().st_size,
            )
        )
    return out


@router.get("/{dataset_id}", response_model=ExampleDetail)
def get_example(dataset_id: str, request: Request) -> ExampleDetail:
    base = _datasets_dir(request)
    ds, path = _resolve_or_404(dataset_id, base)
    layers = _read_layer_info(path)
    feature_count = sum(layer.feature_count for layer in layers)
    bounds = _read_layer_bounds(path, ds.layer)
    return ExampleDetail(
        id=ds.id,
        title=ds.title,
        description=ds.description,
        scenario=ds.scenario,
        layer_count=len(layers),
        feature_count=feature_count,
        size_bytes=path.stat().st_size,
        layers=layers,
        primary_layer=ds.layer,
        bounds=bounds,
    )


# ---------------------------------------------------------------------------
# TileJSON / MVT
# ---------------------------------------------------------------------------


_MVT_CONTENT_TYPE = "application/vnd.mapbox-vector-tile"
_TILE_CACHE_LOCK = threading.Lock()
_TILE_CACHE: dict[tuple[str, int, int, int], bytes] = {}
_TILE_CACHE_BYTES = 0
_TILE_CACHE_MAX_BYTES = 50 * 1024 * 1024  # 50 MB


def _public_base_url(request: Request) -> str:
    proto = request.headers.get("x-forwarded-proto")
    host = request.headers.get("x-forwarded-host")
    if proto and host:
        return f"{proto}://{host.split(',')[0].strip()}"
    return str(request.base_url).rstrip("/")


def _tile_bounds(z: int, x: int, y: int) -> tuple[float, float, float, float]:
    """Return WGS-84 (minx, miny, maxx, maxy) for a Slippy-Map tile."""
    n = 2.0 ** z
    lon_min = x / n * 360.0 - 180.0
    lon_max = (x + 1) / n * 360.0 - 180.0
    lat_max = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))
    lat_min = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (y + 1) / n))))
    return (lon_min, lat_min, lon_max, lat_max)


def _cache_get(key: tuple[str, int, int, int]) -> bytes | None:
    with _TILE_CACHE_LOCK:
        return _TILE_CACHE.get(key)


def _cache_put(key: tuple[str, int, int, int], data: bytes) -> None:
    global _TILE_CACHE_BYTES
    with _TILE_CACHE_LOCK:
        size = len(data)
        # Evict oldest entries until we fit. dict preserves insertion order.
        while _TILE_CACHE_BYTES + size > _TILE_CACHE_MAX_BYTES and _TILE_CACHE:
            oldest_key = next(iter(_TILE_CACHE))
            evicted = _TILE_CACHE.pop(oldest_key)
            _TILE_CACHE_BYTES -= len(evicted)
        _TILE_CACHE[key] = data
        _TILE_CACHE_BYTES += size


def _reset_tile_cache() -> None:
    """Test helper — clears the MVT cache."""
    global _TILE_CACHE_BYTES
    with _TILE_CACHE_LOCK:
        _TILE_CACHE.clear()
        _TILE_CACHE_BYTES = 0


def _encode_mvt(
    path: Path,
    layer: str,
    z: int,
    x: int,
    y: int,
) -> bytes:
    """Encode an MVT tile from a GPKG layer.

    Uses :mod:`mapbox_vector_tile` if available (proper protobuf); falls
    back to a gzipped GeoJSON ``FeatureCollection`` so the endpoint stays
    functional out of the box. The TileJSON ``format`` field reflects the
    actual encoding via the response ``Content-Type``.
    """
    import geopandas as gpd
    from shapely.geometry import box as shapely_box

    minx, miny, maxx, maxy = _tile_bounds(z, x, y)
    tile_bbox = shapely_box(minx, miny, maxx, maxy)

    # Read once with a bbox filter — pyogrio pushes this down to GDAL.
    try:
        gdf = gpd.read_file(path, layer=layer, bbox=(minx, miny, maxx, maxy))
    except Exception:
        logger.exception("examples_mvt_read_failed", extra={"path": str(path), "layer": layer})
        return b""

    if gdf.empty:
        return b""

    # Re-project to 4326 if we got something else (defence in depth — the
    # bundled fixtures are all in 4326).
    if gdf.crs is not None and gdf.crs.to_epsg() != 4326:
        gdf = gdf.to_crs(epsg=4326)

    # Optional MVT proper -----------------------------------------------------
    try:
        import mapbox_vector_tile  # type: ignore[import-not-found]

        # Use Web-Mercator coordinates (EPSG:3857) clipped to the tile bbox,
        # because mapbox_vector_tile expects local tile coords by default.
        proj = gdf.to_crs(epsg=3857)
        m_minx = x / (2 ** z) * 40075016.6856 - 20037508.3428
        m_maxx = (x + 1) / (2 ** z) * 40075016.6856 - 20037508.3428
        m_maxy = 20037508.3428 - y / (2 ** z) * 40075016.6856
        m_miny = 20037508.3428 - (y + 1) / (2 ** z) * 40075016.6856
        features = []
        for _, row in proj.iterrows():
            geom = row.geometry
            if geom is None or geom.is_empty:
                continue
            try:
                clipped = geom.intersection(
                    shapely_box(m_minx, m_miny, m_maxx, m_maxy)
                )
            except Exception:
                continue
            if clipped.is_empty:
                continue
            props = {
                k: v
                for k, v in row.items()
                if k != "geometry" and v is not None
            }
            features.append({"geometry": clipped, "properties": props})
        if not features:
            return b""
        encoded = mapbox_vector_tile.encode(
            [
                {
                    "name": layer,
                    "features": features,
                }
            ],
            quantize_bounds=(m_minx, m_miny, m_maxx, m_maxy),
        )
        return _gzip_bytes(encoded)
    except ImportError:
        # Fallback: gzipped GeoJSON. Not strictly MVT but the demo portal
        # can branch on the response Content-Type.
        clipped = gdf.copy()
        clipped["geometry"] = clipped.geometry.intersection(tile_bbox)
        clipped = clipped[~clipped.geometry.is_empty]
        if clipped.empty:
            return b""
        return _gzip_bytes(clipped.to_json().encode("utf-8"))


def _gzip_bytes(payload: bytes) -> bytes:
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", compresslevel=6) as gz:
        gz.write(payload)
    return buf.getvalue()


@router.get("/{dataset_id}/preview", response_model=TileJSONResponse)
def example_preview(dataset_id: str, request: Request) -> TileJSONResponse:
    base = _datasets_dir(request)
    ds, path = _resolve_or_404(dataset_id, base)
    bounds = _read_layer_bounds(path, ds.layer) or (-180.0, -85.0511, 180.0, 85.0511)
    center_lon = (bounds[0] + bounds[2]) / 2.0
    center_lat = (bounds[1] + bounds[3]) / 2.0
    layers_meta = _read_layer_info(path)

    base_url = _public_base_url(request)
    tile_url = f"{base_url}/examples/{dataset_id}/tiles/{{z}}/{{x}}/{{y}}.mvt"
    return TileJSONResponse(
        name=ds.title,
        description=ds.description,
        tiles=[tile_url],
        bounds=list(bounds),
        center=[center_lon, center_lat, 12],
        vector_layers=[
            {
                "id": layer.name,
                "fields": {f: "" for f in layer.fields},
                "geometry_type": layer.geometry_type,
            }
            for layer in layers_meta
        ],
    )


@router.get("/{dataset_id}/tiles/{z}/{x}/{y}.mvt")
def example_tile(
    dataset_id: str,
    z: int,
    x: int,
    y: int,
    request: Request,
) -> Response:
    if z < 0 or z > 24:
        raise HTTPException(status_code=400, detail=f"Invalid zoom level: {z}")
    max_tile = 2 ** z - 1
    if x < 0 or x > max_tile or y < 0 or y > max_tile:
        raise HTTPException(
            status_code=400,
            detail=f"Tile x={x} y={y} out of range for z={z}",
        )

    base = _datasets_dir(request)
    ds, path = _resolve_or_404(dataset_id, base)

    cache_key = (dataset_id, z, x, y)
    cached = _cache_get(cache_key)
    if cached is not None:
        if not cached:
            return Response(status_code=204)
        return Response(
            content=cached,
            media_type=_MVT_CONTENT_TYPE,
            headers={
                "Cache-Control": "public, max-age=3600",
                "Content-Encoding": "gzip",
            },
        )

    encoded = _encode_mvt(path, ds.layer, z, x, y)
    _cache_put(cache_key, encoded)
    if not encoded:
        return Response(status_code=204, headers={"Cache-Control": "public, max-age=300"})
    return Response(
        content=encoded,
        media_type=_MVT_CONTENT_TYPE,
        headers={
            "Cache-Control": "public, max-age=3600",
            "Content-Encoding": "gzip",
        },
    )


# ---------------------------------------------------------------------------
# Trigger dry-run
# ---------------------------------------------------------------------------


@dataclass
class DryRunDispatcher:
    """Records actions instead of dispatching them.

    Mirrors the public surface of
    :class:`gispulse.adapters.esb.action_dispatcher.ActionDispatcher` —
    enough so the dry-run path stays a drop-in replacement when we
    eventually wire it through a TriggerEvaluator + ActionDispatcher
    pipeline. For now the evaluator returns ``FiredTrigger`` and the
    router maps ``actions_dispatched`` back through this dispatcher.
    """

    captured: list[DryRunSimulatedAction] = field(default_factory=list)

    def record(
        self,
        *,
        trigger: TriggerSpec,
        action: TriggerActionConfig,
        record_table: str,
        feature_id: str | None,
    ) -> None:
        self.captured.append(
            DryRunSimulatedAction(
                trigger_id=trigger.id or "",
                trigger_name=trigger.name,
                action_type=action.action_type,
                config=dict(action.config),
                table=record_table,
                feature_id=feature_id,
            )
        )


def _to_change_record(payload: SimulatedDML):
    """Convert a public ``SimulatedDML`` into a domain ``ChangeRecord``."""
    from gispulse.core.enums import ChangeOperation
    from gispulse.core.models import ChangeRecord

    return ChangeRecord(
        session_id=payload.session_id or "dryrun",
        table_name=payload.table,
        feature_id=payload.feature_id,
        operation=ChangeOperation(payload.operation),
        old_values=dict(payload.old_values),
        new_values=dict(payload.new_values),
        old_geom_wkt=payload.old_geom_wkt,
        new_geom_wkt=payload.new_geom_wkt,
    )


def _to_trigger(spec: TriggerSpec):
    """Convert a public ``TriggerSpec`` into a domain ``Trigger``."""
    from gispulse.core.enums import TriggerType
    from gispulse.core.graph import ActionDef, ActionType
    from gispulse.core.models import Trigger

    try:
        ttype = TriggerType(spec.trigger_type.lower())
    except ValueError:
        # Public callers can pick any short name we ship; default to DML
        # so they can experiment without learning every enum value.
        ttype = TriggerType.DML

    actions: list[ActionDef] = []
    for cfg in spec.actions:
        try:
            atype = ActionType(cfg.action_type.lower())
        except ValueError:
            # Skip unknown action types silently — this is a sandbox.
            continue
        actions.append(ActionDef(action_type=atype, config=dict(cfg.config)))

    try:
        tid = UUID(spec.id) if spec.id else uuid4()
    except (TypeError, ValueError):
        tid = uuid4()

    return Trigger(
        id=tid,
        name=spec.name or "dryrun-trigger",
        trigger_type=ttype,
        conditions=dict(spec.conditions),
        actions=actions,
        enabled=spec.enabled,
    )


@router.post("/{dataset_id}/triggers/dryrun", response_model=DryRunResponse)
def dryrun_triggers(
    dataset_id: str,
    payload: DryRunRequest,
    request: Request,
) -> DryRunResponse:
    """Evaluate ``payload.triggers`` against ``payload.simulated_dml``.

    Pure in-memory evaluation — no database writes, no webhooks, no
    layer mutations. Datasets are touched read-only (the registry path
    is validated to exist; the evaluator does not open the GPKG, it
    works against the synthetic DML records the caller supplies).
    """
    base = _datasets_dir(request)
    ds, _path = _resolve_or_404(dataset_id, base)

    notes: list[str] = []
    truncated = False
    triggers = payload.triggers
    records = payload.simulated_dml

    if len(triggers) > _MAX_DRYRUN_TRIGGERS:
        triggers = triggers[:_MAX_DRYRUN_TRIGGERS]
        truncated = True
        notes.append(
            f"Trigger list truncated to {_MAX_DRYRUN_TRIGGERS} (was {len(payload.triggers)})."
        )
    if len(records) > _MAX_DRYRUN_RECORDS:
        records = records[:_MAX_DRYRUN_RECORDS]
        truncated = True
        notes.append(
            f"DML list truncated to {_MAX_DRYRUN_RECORDS} (was {len(payload.simulated_dml)})."
        )

    # Defaults: feed at least one INSERT on the primary layer when the
    # caller forgot to provide DML — easier "explore-by-clicking" UX.
    if not records:
        records = [SimulatedDML(table=ds.layer, operation="INSERT", feature_id="dryrun-1")]
        notes.append(
            f"No DML provided — defaulted to one INSERT on '{ds.layer}'."
        )

    from gispulse.rules.trigger_evaluator import TriggerEvaluator

    evaluator = TriggerEvaluator()
    domain_triggers = [_to_trigger(t) for t in triggers]
    # Maintain three parallel mappings keyed by domain trigger id:
    #   - the original public spec (for action capture)
    #   - the filtered list of recognised action specs (unknown
    #     ``action_type`` values are silently dropped during conversion,
    #     matching the production ``ActionDispatcher`` contract)
    spec_by_id = {str(dt.id): spec for dt, spec in zip(domain_triggers, triggers, strict=False)}
    valid_actions_by_id: dict[str, list[TriggerActionConfig]] = {}
    from gispulse.core.graph import ActionType as _ActionType

    for dt, spec in zip(domain_triggers, triggers, strict=False):
        valid: list[TriggerActionConfig] = []
        for cfg in spec.actions:
            try:
                _ActionType(cfg.action_type.lower())
            except ValueError:
                continue
            valid.append(cfg)
        valid_actions_by_id[str(dt.id)] = valid

    domain_records = [_to_change_record(r) for r in records]

    dispatcher = DryRunDispatcher()
    events: list[DryRunFiredEvent] = []

    deadline = time.perf_counter() + _DRYRUN_TIMEOUT_S
    t0 = time.perf_counter()

    for record in domain_records:
        if time.perf_counter() > deadline:
            notes.append(
                f"Evaluation aborted — exceeded {_DRYRUN_TIMEOUT_S}s timeout."
            )
            truncated = True
            break
        try:
            fired = evaluator.evaluate(record, domain_triggers)
        except Exception as exc:
            logger.warning("examples_dryrun_eval_failed", extra={"error": str(exc)})
            notes.append(f"Evaluator raised {type(exc).__name__}: {exc}")
            continue
        for ft in fired:
            spec = spec_by_id.get(str(ft.trigger_id))
            events.append(
                DryRunFiredEvent(
                    trigger_id=str(ft.trigger_id),
                    trigger_name=spec.name if spec else "",
                    matched=ft.matched,
                    table=record.table_name,
                    operation=record.operation.value,
                    feature_id=record.feature_id,
                    eval_time_ms=ft.eval_time_ms,
                    cascade_depth=ft.cascade_depth,
                )
            )
            if ft.matched and spec is not None:
                for action_cfg in valid_actions_by_id.get(str(ft.trigger_id), []):
                    dispatcher.record(
                        trigger=spec,
                        action=action_cfg,
                        record_table=record.table_name,
                        feature_id=record.feature_id,
                    )

    duration_ms = (time.perf_counter() - t0) * 1000.0
    return DryRunResponse(
        events=events,
        actions=dispatcher.captured,
        duration_ms=round(duration_ms, 3),
        truncated=truncated,
        notes=notes,
    )


# Public symbols ------------------------------------------------------------


__all__ = [
    "router",
    "get_registry",
    "ExampleDataset",
    "DryRunDispatcher",
    "_reset_tile_cache",
]
