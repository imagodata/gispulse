"""Flag GRB faces whose parent WBN corridor touches a KNW bridge or tunnel.

Diagnostic only: this module never changes ``functional_class`` (owned by
``classify_grb_faces``) or the bundle-level ``ready_for_costing`` flag. It
adds a ``structure_proximity`` column so a consumer can avoid treating a face
next to a structure as a same-level, drillable surface without a separate
check — the reconciliation this chantier's README has documented as
required but, until now, unimplemented.

Official semantics (Digitaal Vlaanderen, objectenhandboek GRB, `kunstwerk-
knw`): KNW geometry is a polygon. Only two of its 15 documented `TYPE` codes
describe a road structure — 1 = overbrugging (bridge), 12 = tunnelmond
(tunnel entrance); the other 13 (hydraulic structures, monuments, pylons,
pillars, chimneys, silos, wind turbines, breakwaters, palisades, ...) cover
unrelated infrastructure the same layer carries and are excluded. The
official worked example states the WBN corridor "is interrupted at a
bridge; the bridge is measured at ground level; the waterway underneath
passes without interruption" — a corridor discontinuity, not a same-level
crossing. On real GRB data (Gand validation bbox) every bridge polygon
touches its adjacent WBN corridor at distance exactly 0: the corridor's
outer boundary meets the bridge polygon's boundary where the at-grade road
ends and the structure begins.

Because nothing in WBN/KNW identifies which face *within* a touched corridor
sits at that join versus mid-corridor, every face in a touched corridor is
flagged, not just the ones nearest the structure. Narrowing that down without
a further, explicit evidence source would be exactly the kind of geometric
inference this chantier's classification work has twice had to retract.

https://www.vlaanderen.be/digitaal-vlaanderen/onze-diensten-en-platformen/basiskaart-vlaanderen-grb/objectenhandboek-basiskaart-vlaanderen-grb/kunstwerk-knw
"""

from __future__ import annotations

import math

import geopandas as gpd
from shapely import STRtree
from shapely.geometry import MultiPolygon, Polygon

_BRIDGE, _TUNNEL = 1, 12
_STRUCTURE_LABELS = {_BRIDGE: "bridge", _TUNNEL: "tunnel"}
_NONE = "none"


def _code(value) -> int | None:
    """Normalise a raw GRB integer code; anything unusable stays unknown."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return int(number) if math.isfinite(number) and number == int(number) else None


def reconcile_knw_structures(
    faces: gpd.GeoDataFrame,
    wbn: gpd.GeoDataFrame,
    knw: gpd.GeoDataFrame,
    *,
    boundary_tolerance_m: float,
    polygon_id: str = "polygon_id",
    wbn_id: str = "OIDN",
    knw_type_field: str = "TYPE",
) -> tuple[gpd.GeoDataFrame, dict]:
    """Add ``structure_proximity`` (``bridge``/``tunnel``/``bridge,tunnel``/``none``).

    ``faces`` must carry ``polygon_id`` values that are a subset of ``wbn``'s
    ``wbn_id`` — the same correspondence ``partition_polygons`` establishes
    between its ``polygons`` input and its output faces. ``boundary_tolerance_m``
    is a numerical distance comparison, not a snap or buffer: on validated
    real data a touching bridge/WBN pair measures 0 m apart, so this only
    guards against floating-point noise, not survey imprecision.
    """
    if (
        faces.crs is None
        or wbn.crs != faces.crs
        or knw.crs != faces.crs
        or not faces.crs.is_projected
        or any(a.unit_conversion_factor != 1 for a in faces.crs.axis_info[:2])
    ):
        raise ValueError("GRB_RECONCILE_CRS_INVALID: identical projected metre CRS required")
    if not math.isfinite(boundary_tolerance_m) or boundary_tolerance_m < 0:
        raise ValueError("GRB_RECONCILE_TOLERANCE_INVALID: nonnegative finite tolerance required")
    for frame, fields, allowed in [
        (faces, (polygon_id,), (Polygon, MultiPolygon)),
        (wbn, (wbn_id,), (Polygon, MultiPolygon)),
        (knw, (knw_type_field,), (Polygon, MultiPolygon)),
    ]:
        missing = [field for field in fields if field not in frame]
        if missing:
            raise ValueError(f"GRB_RECONCILE_FIELD_MISSING: {missing}")
        if any(not isinstance(g, allowed) or g.is_empty or not g.is_valid for g in frame.geometry):
            raise ValueError(
                "GRB_RECONCILE_GEOMETRY_INVALID: repair source geometries explicitly before reconciling"
            )
    wbn_id_column = wbn[wbn_id].astype(str)
    if (
        wbn_id_column.isna().any()
        or (wbn_id_column.str.strip() == "").any()
        or wbn_id_column.duplicated().any()
    ):
        raise ValueError("GRB_RECONCILE_ID_INVALID: unique nonempty WBN IDs required")
    face_parents = faces[polygon_id].astype(str)
    missing_parents = sorted(set(face_parents) - set(wbn_id_column))
    if missing_parents:
        raise ValueError(f"GRB_RECONCILE_PARENT_MISSING: {missing_parents}")

    structure_codes = knw[knw_type_field].map(_code)
    structures = knw[structure_codes.isin(_STRUCTURE_LABELS).to_numpy()]
    structure_types = [_STRUCTURE_LABELS[_code(v)] for v in structures[knw_type_field]]
    tree = STRtree(structures.geometry.to_list())

    proximity_by_wbn: dict[str, str] = {}
    for wbn_id_value, geometry in zip(wbn_id_column, wbn.geometry):
        if len(structure_types) == 0:
            proximity_by_wbn[wbn_id_value] = _NONE
            continue
        hits = tree.query(geometry, predicate="dwithin", distance=boundary_tolerance_m)
        labels = sorted({structure_types[int(i)] for i in hits})
        proximity_by_wbn[wbn_id_value] = ",".join(labels) if labels else _NONE

    result = faces.copy()
    result["structure_proximity"] = face_parents.map(proximity_by_wbn).to_numpy()

    flagged = result.structure_proximity != _NONE
    report = {
        "input_faces": int(len(result)),
        "knw_structures_considered": int(len(structures)),
        "flagged_faces": int(flagged.sum()),
        "flagged_corridors": int(result.loc[flagged, polygon_id].astype(str).nunique()),
        "proximity_counts": {
            str(label): int(count)
            for label, count in result.structure_proximity.value_counts().to_dict().items()
        },
    }
    return result, report
