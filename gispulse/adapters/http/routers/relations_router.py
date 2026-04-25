"""
Relations router for the GISPulse HTTP API.

Endpoints:
    POST   /relations                       -- create a relation
    GET    /relations                       -- list all relations (filterable)
    GET    /relations/{id}                  -- detail for a single relation
    PUT    /relations/{id}                  -- update a relation
    DELETE /relations/{id}                  -- delete a relation
    POST   /relations/{id}/confirm          -- confirm a detected relation
    POST   /relations/{id}/attach-trigger   -- attach a trigger to a relation
    POST   /relations/{id}/detach-trigger   -- detach trigger from a relation
    POST   /relations/{id}/add-computation  -- add a computed field
    DELETE /relations/{id}/computed/{name}  -- remove a computed field
    GET    /relations/{id}/preview-sql      -- preview generated SQL
    POST   /relations/detect               -- run auto-detection on loaded datasets
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from gispulse.adapters.http.dependencies import get_relation_repo, get_trigger_repo, get_viewer_state
from gispulse.adapters.http.schemas import (
    AddComputationRequest,
    AttachTriggerRequest,
    ComputedFieldIn,
    PreviewSQLResponse,
    RelationCreate,
    RelationResponse,
    RelationUpdate,
)
from core.models import (
    ComputedFieldDef,
    ComputationRefreshMode,
    TableRelation,
)
from persistence.repository import Repository

from core.sql_safety import validate_expression as _validate_expression
from core.sql_safety import validate_identifier as _validate_identifier

router = APIRouter(prefix="/relations", tags=["relations"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _relation_to_response(rel: TableRelation) -> RelationResponse:
    computed = []
    for cf in rel.computed_fields:
        if isinstance(cf, dict):
            computed.append(ComputedFieldIn(**cf))
        else:
            computed.append(ComputedFieldIn(
                name=cf.name,
                expression=cf.expression,
                target_field=cf.target_field,
                agg_function=cf.agg_function,
                source_field=cf.source_field,
                refresh_mode=cf.refresh_mode.value if hasattr(cf.refresh_mode, "value") else cf.refresh_mode,
                cron=cf.cron,
            ))
    return RelationResponse(
        id=rel.id,
        source_layer_id=rel.source_layer_id,
        target_layer_id=rel.target_layer_id,
        source_layer_name=rel.source_layer_name,
        target_layer_name=rel.target_layer_name,
        relation_type=rel.relation_type,
        source_field=rel.source_field,
        target_field=rel.target_field,
        spatial_op=rel.spatial_op,
        spatial_config=rel.spatial_config,
        confidence=rel.confidence,
        confirmed=rel.confirmed,
        auto_detected=rel.auto_detected,
        label=rel.label,
        trigger_id=rel.trigger_id,
        computed_fields=computed,
        created_at=rel.created_at,
        updated_at=rel.updated_at,
    )


def _generate_sql(rel: TableRelation) -> list[str]:
    """Generate preview SQL statements for computed fields.

    All identifiers are validated to prevent SQL injection.
    This generates preview-only SQL — not executed directly.
    """
    statements: list[str] = []
    for cf in rel.computed_fields:
        if isinstance(cf, dict):
            cf = ComputedFieldDef(**cf)

        target = _validate_identifier(rel.target_layer_name or "target_table")
        source = _validate_identifier(rel.source_layer_name or "source_table")
        field_name = _validate_identifier(cf.target_field or cf.name)
        expr = cf.expression

        # Validate expression if present
        if expr:
            _validate_expression(expr)

        # Build spatial WHERE clause
        where = ""
        if rel.spatial_op and rel.relation_type == "spatial":
            _validate_identifier(rel.spatial_op)
            st_func = f"ST_{rel.spatial_op.capitalize()}"
            where = f"WHERE {st_func}(s.geom, t.geom)"
            buffer_m = rel.spatial_config.get("buffer_m")
            distance = rel.spatial_config.get("distance")
            if buffer_m is not None:
                buffer_m = float(buffer_m)  # Ensure numeric
                where = f"WHERE {st_func}(ST_Buffer(s.geom, {buffer_m}), t.geom)"
            if distance is not None:
                distance = float(distance)  # Ensure numeric
                where = f"WHERE ST_DWithin(s.geom, t.geom, {distance})"
        elif rel.relation_type == "fk" and rel.source_field and rel.target_field:
            _validate_identifier(rel.source_field)
            _validate_identifier(rel.target_field)
            where = f"WHERE s.{rel.source_field} = t.{rel.target_field}"
        elif rel.relation_type == "attribute" and rel.source_field:
            _validate_identifier(rel.source_field)
            where = f"WHERE s.{rel.source_field} = t.{rel.source_field}"

        # Build the full SQL
        if cf.agg_function:
            _validate_identifier(cf.agg_function)
            agg_arg = f"s.{_validate_identifier(cf.source_field)}" if cf.source_field else "*"
            sub = f"SELECT {cf.agg_function}({agg_arg}) FROM {source} s {where}"
            sql = f"UPDATE {target} t SET {field_name} = ({sub});"
        else:
            sql = f"UPDATE {target} t SET {field_name} = ({expr}) FROM {source} s {where};"

        statements.append(sql)
    return statements


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


@router.post("", response_model=RelationResponse, status_code=201)
def create_relation(
    body: RelationCreate,
    repo: Repository = Depends(get_relation_repo),
) -> RelationResponse:
    rel = TableRelation(
        source_layer_id=body.source_layer_id,
        target_layer_id=body.target_layer_id,
        source_layer_name=body.source_layer_name,
        target_layer_name=body.target_layer_name,
        relation_type=body.relation_type,
        source_field=body.source_field,
        target_field=body.target_field,
        spatial_op=body.spatial_op,
        spatial_config=body.spatial_config,
        confidence=body.confidence,
        confirmed=body.confirmed,
        label=body.label,
    )
    repo.save(rel)
    return _relation_to_response(rel)


@router.get("", response_model=list[RelationResponse])
def list_relations(
    repo: Repository = Depends(get_relation_repo),
    layer_id: UUID | None = Query(None, description="Filter by source or target layer ID."),
    relation_type: str | None = Query(None, description="Filter by relation type."),
    has_trigger: bool | None = Query(None, description="Filter: has attached trigger."),
    confirmed: bool | None = Query(None, description="Filter: confirmed relations only."),
) -> list[RelationResponse]:
    all_rels = repo.list_all()
    result = []
    for rel in all_rels:
        if layer_id and rel.source_layer_id != layer_id and rel.target_layer_id != layer_id:
            continue
        if relation_type and rel.relation_type != relation_type:
            continue
        if has_trigger is True and rel.trigger_id is None:
            continue
        if has_trigger is False and rel.trigger_id is not None:
            continue
        if confirmed is not None and rel.confirmed != confirmed:
            continue
        result.append(_relation_to_response(rel))
    return result


@router.get("/{relation_id}", response_model=RelationResponse)
def get_relation(
    relation_id: UUID,
    repo: Repository = Depends(get_relation_repo),
) -> RelationResponse:
    rel = repo.get(relation_id)
    if rel is None:
        raise HTTPException(status_code=404, detail="Relation not found.")
    return _relation_to_response(rel)


@router.put("/{relation_id}", response_model=RelationResponse)
def update_relation(
    relation_id: UUID,
    body: RelationUpdate,
    repo: Repository = Depends(get_relation_repo),
) -> RelationResponse:
    rel = repo.get(relation_id)
    if rel is None:
        raise HTTPException(status_code=404, detail="Relation not found.")

    updates = body.model_dump(exclude_unset=True)
    for key, value in updates.items():
        if value is not None:
            setattr(rel, key, value)
    rel.updated_at = datetime.now(timezone.utc)
    repo.save(rel)
    return _relation_to_response(rel)


@router.delete("/{relation_id}", status_code=204)
def delete_relation(
    relation_id: UUID,
    repo: Repository = Depends(get_relation_repo),
) -> None:
    if not repo.delete(relation_id):
        raise HTTPException(status_code=404, detail="Relation not found.")


# ---------------------------------------------------------------------------
# Confirm
# ---------------------------------------------------------------------------


@router.post("/{relation_id}/confirm", response_model=RelationResponse)
def confirm_relation(
    relation_id: UUID,
    repo: Repository = Depends(get_relation_repo),
) -> RelationResponse:
    rel = repo.get(relation_id)
    if rel is None:
        raise HTTPException(status_code=404, detail="Relation not found.")
    rel.confirmed = True
    rel.updated_at = datetime.now(timezone.utc)
    repo.save(rel)
    return _relation_to_response(rel)


# ---------------------------------------------------------------------------
# Attach / Detach trigger
# ---------------------------------------------------------------------------


@router.post("/{relation_id}/attach-trigger", response_model=RelationResponse)
def attach_trigger(
    relation_id: UUID,
    body: AttachTriggerRequest,
    repo: Repository = Depends(get_relation_repo),
    trigger_repo: Repository = Depends(get_trigger_repo),
) -> RelationResponse:
    rel = repo.get(relation_id)
    if rel is None:
        raise HTTPException(status_code=404, detail="Relation not found.")

    trigger = trigger_repo.get(body.trigger_id)
    if trigger is None:
        raise HTTPException(status_code=404, detail="Trigger not found.")

    rel.trigger_id = body.trigger_id
    rel.updated_at = datetime.now(timezone.utc)
    repo.save(rel)
    return _relation_to_response(rel)


@router.post("/{relation_id}/detach-trigger", response_model=RelationResponse)
def detach_trigger(
    relation_id: UUID,
    repo: Repository = Depends(get_relation_repo),
) -> RelationResponse:
    rel = repo.get(relation_id)
    if rel is None:
        raise HTTPException(status_code=404, detail="Relation not found.")
    rel.trigger_id = None
    rel.updated_at = datetime.now(timezone.utc)
    repo.save(rel)
    return _relation_to_response(rel)


# ---------------------------------------------------------------------------
# Computed fields
# ---------------------------------------------------------------------------


@router.post("/{relation_id}/add-computation", response_model=RelationResponse)
def add_computation(
    relation_id: UUID,
    body: AddComputationRequest,
    repo: Repository = Depends(get_relation_repo),
) -> RelationResponse:
    rel = repo.get(relation_id)
    if rel is None:
        raise HTTPException(status_code=404, detail="Relation not found.")

    # Ensure computed_fields is a proper list of ComputedFieldDef
    if not isinstance(rel.computed_fields, list):
        rel.computed_fields = []

    # Check for duplicate field name
    existing_names = set()
    for cf in rel.computed_fields:
        n = cf.get("name") if isinstance(cf, dict) else cf.name
        existing_names.add(n)
    if body.name in existing_names:
        raise HTTPException(status_code=409, detail=f"Computed field '{body.name}' already exists.")

    cf = ComputedFieldDef(
        name=body.name,
        expression=body.expression,
        target_field=body.target_field or body.name,
        agg_function=body.agg_function,
        source_field=body.source_field,
        refresh_mode=ComputationRefreshMode(body.refresh_mode),
        cron=body.cron,
    )
    rel.computed_fields.append(cf)
    rel.updated_at = datetime.now(timezone.utc)
    repo.save(rel)
    return _relation_to_response(rel)


@router.delete("/{relation_id}/computed/{field_name}", response_model=RelationResponse)
def remove_computation(
    relation_id: UUID,
    field_name: str,
    repo: Repository = Depends(get_relation_repo),
) -> RelationResponse:
    rel = repo.get(relation_id)
    if rel is None:
        raise HTTPException(status_code=404, detail="Relation not found.")

    original_len = len(rel.computed_fields)
    rel.computed_fields = [
        cf for cf in rel.computed_fields
        if (cf.get("name") if isinstance(cf, dict) else cf.name) != field_name
    ]
    if len(rel.computed_fields) == original_len:
        raise HTTPException(status_code=404, detail=f"Computed field '{field_name}' not found.")

    rel.updated_at = datetime.now(timezone.utc)
    repo.save(rel)
    return _relation_to_response(rel)


# ---------------------------------------------------------------------------
# SQL preview
# ---------------------------------------------------------------------------


@router.get("/{relation_id}/preview-sql", response_model=PreviewSQLResponse)
def preview_sql(
    relation_id: UUID,
    repo: Repository = Depends(get_relation_repo),
) -> PreviewSQLResponse:
    rel = repo.get(relation_id)
    if rel is None:
        raise HTTPException(status_code=404, detail="Relation not found.")
    return PreviewSQLResponse(
        relation_id=rel.id,
        sql_statements=_generate_sql(rel),
    )


# ---------------------------------------------------------------------------
# Auto-detection
# ---------------------------------------------------------------------------


@router.post("/detect", response_model=list[RelationResponse])
def detect_relations(
    repo: Repository = Depends(get_relation_repo),
    viewer_state: dict = Depends(get_viewer_state),
) -> list[RelationResponse]:
    """Run auto-detection on all loaded datasets.

    Uses the SpatialRelationDetector to find relationships between layers
    and persists them as unconfirmed TableRelation records.
    """
    try:
        from capabilities.relation_detector import SpatialRelationDetector
    except ImportError:
        raise HTTPException(
            status_code=501,
            detail="SpatialRelationDetector not available (missing geopandas).",
        )

    layer_cache = viewer_state.get("layer_cache", {})
    if not layer_cache:
        return []

    # Build GeoDataFrame dict from layer_cache
    import geopandas as gpd

    layers: dict[str, gpd.GeoDataFrame] = {}
    for name, meta in layer_cache.items():
        gdf = meta.get("gdf")
        if gdf is not None and isinstance(gdf, gpd.GeoDataFrame) and len(gdf) > 0:
            layers[name] = gdf

    if len(layers) < 2:
        return []

    detector = SpatialRelationDetector(sample_size=500)
    detected = detector.analyze_all(layers)

    results: list[RelationResponse] = []
    for det in detected:
        if det.confidence < 0.2:
            continue

        # Map relation_type from detector to our types
        rel_type = "spatial"
        spatial_op = det.relation_type
        if det.relation_type == "attribute":
            rel_type = "attribute"
            spatial_op = None

        rel = TableRelation(
            source_layer_name=det.layer_a,
            target_layer_name=det.layer_b,
            relation_type=rel_type,
            spatial_op=spatial_op,
            confidence=det.confidence,
            confirmed=False,
            auto_detected=True,
            label=det.suggested_name or f"{det.layer_a} → {det.layer_b}",
        )
        repo.save(rel)
        results.append(_relation_to_response(rel))

    return results
