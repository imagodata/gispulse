"""Typed parameter dictionaries for GISPulse capabilities.

Provides :class:`~typing.TypedDict` definitions mirroring the JSON Schema
of each built-in capability, enabling IDE autocompletion and static type
checking when building :class:`~core.pipeline.StepSpec` instances.

Usage::

    from gispulse.core.capability_params import BufferParams, FilterParams

    step = StepSpec(
        id="buf",
        capability="buffer",
        params=BufferParams(distance=50),
    )

The JSON Schema in each capability's ``get_schema()`` remains the
canonical validation source at runtime. These TypedDicts are *mirrors*
for developer ergonomics, not replacements.
"""

from __future__ import annotations

from typing import Any, TypedDict

from typing_extensions import NotRequired


# ---------------------------------------------------------------------------
# filter
# ---------------------------------------------------------------------------


class FilterParams(TypedDict, total=False):
    """Parameters for the ``filter`` capability.

    Filters features using attribute expressions and/or spatial predicates.
    """

    expression: str
    """Pandas query expression (e.g. ``"area > 1000"``)."""

    spatial_predicate: str | None
    """Spatial relationship: intersects, contains, within, crosses,
    touches, overlaps, disjoint, equals, dwithin."""

    ref_wkt: str | None
    """WKT reference geometry for spatial filtering."""

    ref_geojson: dict[str, Any] | None
    """GeoJSON reference geometry for spatial filtering."""

    ref_layer: str | None
    """Reference layer name for cross-layer spatial filtering."""

    buffer_distance: float | None
    """Buffer distance in meters for spatial predicate."""


# ---------------------------------------------------------------------------
# buffer
# ---------------------------------------------------------------------------


class BufferParams(TypedDict):
    """Parameters for the ``buffer`` capability.

    Creates a fixed-distance buffer around each geometry.
    """

    distance: float
    """Buffer distance (in CRS units or meters if reprojected)."""

    crs_meters: NotRequired[str]
    """Metric CRS for distance calculation (default: EPSG:3857)."""


# ---------------------------------------------------------------------------
# spatial_join
# ---------------------------------------------------------------------------


class SpatialJoinParams(TypedDict):
    """Parameters for the ``spatial_join`` capability.

    Joins attributes from a reference layer based on spatial relationship.
    """

    ref_layer: str
    """Name of the reference layer to join from."""

    how: NotRequired[str]
    """Join type: ``'inner'``, ``'left'``, or ``'right'`` (default: inner)."""

    predicate: NotRequired[str]
    """Spatial predicate: ``'intersects'``, ``'within'``, ``'contains'``
    (default: intersects)."""

    columns: NotRequired[list[str] | None]
    """Columns to keep from the reference layer. None = keep all."""


# ---------------------------------------------------------------------------
# dissolve
# ---------------------------------------------------------------------------


class DissolveParams(TypedDict, total=False):
    """Parameters for the ``dissolve`` capability.

    Dissolves features, optionally grouped by an attribute column.
    """

    by: str | None
    """Column to group by. None = dissolve all into one feature."""


# ---------------------------------------------------------------------------
# centroid
# ---------------------------------------------------------------------------


class CentroidParams(TypedDict, total=False):
    """Parameters for the ``centroid`` capability.

    Replaces each feature's geometry with its centroid. No parameters.
    """

    pass


# ---------------------------------------------------------------------------
# clip
# ---------------------------------------------------------------------------


class ClipParams(TypedDict):
    """Parameters for the ``clip`` capability.

    Clips a layer to the boundaries of a reference layer.
    """

    ref_layer: str
    """Reference layer whose geometry defines the clip boundary."""


# ---------------------------------------------------------------------------
# area_length
# ---------------------------------------------------------------------------


class AreaLengthParams(TypedDict, total=False):
    """Parameters for the ``area_length`` capability.

    Computes area (m^2) and/or length (m) and adds them as columns.
    """

    crs_meters: str
    """Metric CRS for computation (default: EPSG:3857)."""

    area_col: str
    """Column name for area (default: area_m2)."""

    length_col: str
    """Column name for length (default: length_m)."""

    compute_area: bool
    """Compute area (default: True)."""

    compute_length: bool
    """Compute length (default: True)."""


# ---------------------------------------------------------------------------
# calculate
# ---------------------------------------------------------------------------


class CalculateParams(TypedDict):
    """Parameters for the ``calculate`` capability.

    Computes new columns from arithmetic or string expressions.
    """

    expressions: dict[str, str]
    """Mapping of ``column_name -> expression`` to compute."""


# ---------------------------------------------------------------------------
# intersects
# ---------------------------------------------------------------------------


class IntersectsParams(TypedDict, total=False):
    """Parameters for the ``intersects`` capability.

    Filters features that spatially intersect a reference geometry or layer.
    """

    wkt: str
    """WKT geometry to test intersection against."""

    ref_layer: str
    """Reference layer name to test intersection against."""


# ---------------------------------------------------------------------------
# reproject
# ---------------------------------------------------------------------------


class ReprojectParams(TypedDict):
    """Parameters for the ``reproject`` capability.

    Reprojects a layer to a target CRS.
    """

    target_crs: str
    """Target CRS (e.g. ``'EPSG:4326'``, ``'EPSG:2154'``)."""


# ---------------------------------------------------------------------------
# Union type for all known capability params
# ---------------------------------------------------------------------------


CapabilityParams = (
    FilterParams | BufferParams | SpatialJoinParams | DissolveParams |
    CentroidParams | ClipParams | AreaLengthParams | CalculateParams |
    IntersectsParams | ReprojectParams
)
"""Union of all typed capability parameter dicts."""


# Map capability name -> TypedDict class (for runtime lookup)
PARAMS_TYPE_MAP: dict[str, type] = {
    "filter": FilterParams,
    "buffer": BufferParams,
    "spatial_join": SpatialJoinParams,
    "dissolve": DissolveParams,
    "centroid": CentroidParams,
    "clip": ClipParams,
    "area_length": AreaLengthParams,
    "calculate": CalculateParams,
    "intersects": IntersectsParams,
    "reproject": ReprojectParams,
}
