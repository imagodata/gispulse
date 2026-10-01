"""Conservative classification of reconstructed GRB faces.

The source geometry does not itself identify which side of a WGO boundary is
the carriageway. This helper therefore only accepts an explicit upstream
functional label and refuses to infer one from WBN/WGO geometry.
"""

from __future__ import annotations

import geopandas as gpd


_ALLOWED = {"carriageway_paved", "carriageway_unpaved", "sidewalk", "unmapped"}
# Every explicit class other than ``unmapped`` was decided by a documented
# source code, so it is per-face classified. This per-face flag is a
# self-check only: the GRB bundle-level ``ready_for_costing`` stays false, and
# this column is never published (see gispulse-src-grb's prepare step).
_CLASSIFIED = {"carriageway_paved", "carriageway_unpaved", "sidewalk"}


def validate_functional_faces(
    faces: gpd.GeoDataFrame,
    *,
    class_field: str = "functional_class",
    allowed: set[str] | None = None,
) -> tuple[gpd.GeoDataFrame, dict]:
    """Validate an explicit class column; never derive classes from geometry."""
    if class_field not in faces:
        raise ValueError("GRB_FUNCTION_CLASS_MISSING: upstream functional classification required")
    target = set(allowed or _ALLOWED)
    values = faces[class_field].fillna("unmapped").astype(str)
    unknown = sorted(set(values) - target)
    if unknown:
        raise ValueError(f"GRB_FUNCTION_CLASS_UNKNOWN: {unknown}")
    result = faces.copy()
    result["functional_class"] = values
    result["classification_source"] = "upstream_explicit"
    result["ready_for_costing"] = values.isin(_CLASSIFIED)
    report = {
        "input_faces": len(result),
        "classified_faces": int(result.ready_for_costing.sum()),
        "unmapped_faces": int((~result.ready_for_costing).sum()),
        "unknown_values": unknown,
        "inference": False,
    }
    return result, report
