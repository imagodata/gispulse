"""Flag GRB faces whose parent WBN corridor touches a KNW bridge or tunnel.

Diagnostic only: this module never changes ``functional_class`` (owned by
``classify_grb_faces``) or the bundle-level ``ready_for_costing`` flag. It
adds a ``structure_proximity`` column so a consumer can avoid treating a face
next to a structure as a same-level, drillable surface without a separate
check.

**What ``structure_proximity`` actually measures.** It is corridor-level
*contact*, not per-face distance: every face inside a WBN corridor whose
outer boundary touches a qualifying KNW polygon is flagged identically,
whether that face sits right at the join or far across a wide corridor.
Measured on a 3.5 km sample around Gand centre, flagged corridors can extend
past 300 m from the structure, and a `bridge`-flagged face is not guaranteed
closer to a bridge than a `none` face elsewhere in the same sample — the
label is about which corridor a face belongs to, never a distance ranking.
Nothing in WBN/KNW identifies which face *within* a touched corridor sits at
the actual join versus mid-corridor, and narrowing that down without a
further, explicit evidence source would be exactly the kind of geometric
inference this chantier's classification work has twice had to retract — so
every face in a touched corridor is flagged, deliberately over-inclusive.

Official semantics (Digitaal Vlaanderen, objectenhandboek GRB, `kunstwerk-
knw`): KNW geometry is a polygon, with 17 documented `TYPE` codes. Only two
describe a road structure — 1 = overbrugging (bridge), 12 = tunnelmond
(tunnel entrance). The official page for `overbrugging` states the WBN
corridor "always connects to a Knw of type overbrugging at the expansion
joint or edge of the bridge deck" ("De wegbaan (Wbn) sluit steeds aan op een
kunstwerk (Knw) van het type overbrugging ter hoogte van de uitzettingsvoeg
of de zijrand van het brugdek") — a corridor discontinuity, not a same-level
crossing. Verified on real GRB data (Gand validation bbox, and separately a
3.5 km sample around Gand centre: 91 of 92 bridges touch a WBN corridor at
distance exactly 0, the remaining one at 7.1 m with no corridor within 0.5 m
— no case falls in the 1 mm-to-0.5 m grey zone this module's default
tolerance spans).

Excluded, despite also touching WBN constantly: `TYPE=5` (pijler/pillar).
The handbook's own worked example for `pijler` describes bridge piers
("pijlers van deze brug ... binnen de wegbaan"), so this code is not simply
unrelated street furniture as an earlier version of this docstring claimed —
but its coverage rule also captures pillars supporting *any* civil structure
within 5 m of a WBN, including buildings, so a `pijler` alone does not
specifically prove a bridge or tunnel. It is excluded from the structure
filter to avoid that ambiguity, which means a corridor whose only touching
KNW evidence is a `pijler` (no `TYPE=1`/`12` polygon of its own) stays
`none` here even where the source data suggests a nearby bridge — a known,
one-directional gap (never a false `bridge`/`tunnel`, only a possible missed
one), not yet closed.

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


def _nonempty_id_column(frame, field: str, error_code: str):
    """Validate for NaN/blank on the raw column, before any string coercion.

    ``astype(str)`` turns a real ``NaN``/``None`` into the literal text
    "nan"/"None" on some pandas versions, silently defeating an ``isna()``
    check performed afterwards; checking the raw column first is robust to
    that.
    """
    if frame[field].isna().any():
        raise ValueError(f"{error_code}: unique nonempty IDs required")
    column = frame[field].astype(str)
    if (column.str.strip() == "").any():
        raise ValueError(f"{error_code}: unique nonempty IDs required")
    return column


def reconcile_knw_structures(
    faces: gpd.GeoDataFrame,
    wbn: gpd.GeoDataFrame,
    knw: gpd.GeoDataFrame,
    *,
    boundary_tolerance_m: float,
    polygon_id: str = "polygon_id",
    wbn_id: str = "OIDN",
    knw_id: str = "OIDN",
    knw_type_field: str = "TYPE",
) -> tuple[gpd.GeoDataFrame, dict]:
    """Add ``structure_proximity`` (``bridge``/``tunnel``/``bridge,tunnel``/``none``)
    and ``structure_evidence`` (the touching KNW ``knw_id`` values, e.g.
    ``"21528:bridge"``) to every face, at corridor granularity — see the
    module docstring for exactly what this does and does not prove.

    ``faces`` must carry ``polygon_id`` values that are a subset of ``wbn``'s
    ``wbn_id`` — the same correspondence ``partition_polygons`` establishes
    between its ``polygons`` input and its output faces. ``boundary_tolerance_m``
    is a numerical distance comparison, not a snap or buffer: on validated
    real data a touching bridge/WBN pair measures 0 m apart, so this only
    guards against floating-point noise, not survey imprecision. Only KNW
    records whose ``knw_type_field`` is a structure code (1 or 12) are
    required to have valid geometry and a nonempty ``knw_id``: an unrelated
    KNW record (a pillar, a monument, ...) with a defect must not block
    reconciliation for a bundle that never needed it.
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
        (knw, (knw_type_field,), None),
    ]:
        missing = [field for field in fields if field not in frame]
        if missing:
            raise ValueError(f"GRB_RECONCILE_FIELD_MISSING: {missing}")
        if allowed is not None and any(
            not isinstance(g, allowed) or g.is_empty or not g.is_valid for g in frame.geometry
        ):
            raise ValueError(
                "GRB_RECONCILE_GEOMETRY_INVALID: repair source geometries explicitly before reconciling"
            )
    wbn_id_column = _nonempty_id_column(wbn, wbn_id, "GRB_RECONCILE_ID_INVALID")
    if wbn_id_column.duplicated().any():
        raise ValueError("GRB_RECONCILE_ID_INVALID: unique nonempty WBN IDs required")
    face_parents = faces[polygon_id].astype(str)
    missing_parents = sorted(set(face_parents) - set(wbn_id_column))
    if missing_parents:
        raise ValueError(f"GRB_RECONCILE_PARENT_MISSING: {missing_parents}")

    structure_codes = knw[knw_type_field].map(_code)
    structures = knw[structure_codes.isin(_STRUCTURE_LABELS).to_numpy()]
    # Only the structure-typed subset must carry a usable ID and geometry:
    # an unrelated KNW record (pillar, monument, ...) with a defect must not
    # block reconciliation for a bundle that never needed it (see docstring).
    if knw_id not in structures:
        raise ValueError(f"GRB_RECONCILE_FIELD_MISSING: ['{knw_id}' (on structure-typed KNW rows)]")
    if any(
        not isinstance(g, (Polygon, MultiPolygon)) or g.is_empty or not g.is_valid
        for g in structures.geometry
    ):
        raise ValueError(
            "GRB_RECONCILE_GEOMETRY_INVALID: repair source geometries explicitly before reconciling"
        )
    structure_ids = _nonempty_id_column(structures, knw_id, "GRB_RECONCILE_ID_INVALID")
    structure_types = [_STRUCTURE_LABELS[_code(v)] for v in structures[knw_type_field]]
    tree = STRtree(structures.geometry.to_list())

    # A structure can touch a WBN corridor that carries no face at all in
    # `faces` (e.g. it was outside_acquisition_coverage upstream and never
    # reached partition_polygons). Only count a structure as "matched" for
    # reporting purposes when it touches a corridor that actually has a face
    # here — otherwise `structures_matching_no_corridor` would never fire for
    # exactly the case it exists to surface.
    wbn_ids_with_faces = set(face_parents)
    proximity_by_wbn: dict[str, str] = {}
    evidence_by_wbn: dict[str, str] = {}
    matched_structure_positions: set[int] = set()
    for wbn_id_value, geometry in zip(wbn_id_column, wbn.geometry):
        if len(structure_types) == 0:
            proximity_by_wbn[wbn_id_value] = _NONE
            evidence_by_wbn[wbn_id_value] = ""
            continue
        hits = [
            int(i) for i in tree.query(geometry, predicate="dwithin", distance=boundary_tolerance_m)
        ]
        if wbn_id_value in wbn_ids_with_faces:
            matched_structure_positions.update(hits)
        pairs = sorted({(structure_ids.iloc[i], structure_types[i]) for i in hits})
        proximity_by_wbn[wbn_id_value] = ",".join(sorted({label for _, label in pairs})) or _NONE
        evidence_by_wbn[wbn_id_value] = ",".join(f"{sid}:{label}" for sid, label in pairs)

    result = faces.copy()
    result["structure_proximity"] = face_parents.map(proximity_by_wbn).to_numpy()
    result["structure_evidence"] = face_parents.map(evidence_by_wbn).to_numpy()

    flagged = result.structure_proximity != _NONE
    unmatched_structures = len(structures) - len(matched_structure_positions)
    report = {
        "input_faces": int(len(result)),
        "knw_structures_considered": int(len(structures)),
        "flagged_faces": int(flagged.sum()),
        "flagged_corridors": int(result.loc[flagged, polygon_id].astype(str).nunique()),
        # A structure counted above but absent here touches no WBN corridor
        # carried into `faces` at all — commonly because that corridor was
        # `outside_acquisition_coverage` and never reached partition_polygons.
        # It is a real bridge/tunnel in the bbox this reconciliation could not
        # act on, not evidence that none exists nearby.
        "structures_matching_no_corridor": int(unmatched_structures),
        "proximity_counts": {
            str(label): int(count)
            for label, count in result.structure_proximity.value_counts().to_dict().items()
        },
        "thresholds": {"boundary_tolerance_m": float(boundary_tolerance_m)},
        "inference": False,
    }
    return result, report
