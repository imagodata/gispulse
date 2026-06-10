"""Clip features against a polygon layer and measure the area of overlap.

Public surface
--------------
* :data:`FULL_OVERLAP_PERCENT`       -- default threshold for full-overlap classification.
* :func:`suggest_metric_crs`         -- pick a UTM CRS from bounds centroid.
* :func:`clip_and_measure_matches`   -- bulk GeoDataFrame variant.
* :func:`measure_spatial_impact`     -- per-feature GeoJSON dict variant.
* :class:`MeasureSpatialImpactCapability` -- registered capability wrapper.

This module is deliberately **generic**: it carries no domain notion
(parcel, permit zone, land use…).  The same intersection + area logic
applies to any polygon feature against any polygon reference layer.

The :func:`measure_spatial_impact` function returns a plain :class:`dict`
with the following keys:

* ``status``            -- ``"measured"`` or ``"not_measured"``.
* ``relation``          -- ``"full_overlap"``, ``"partial_overlap"`` or
  ``"not_measured"``.
* ``overlap_m2``        -- clipped area in m² (float or None).
* ``parcel_percent``    -- percentage of reference polygon covered (float or None).
* ``clipped_geometry``  -- GeoJSON geometry dict of the intersection (or None).
* ``method``            -- constant string recording the algorithm used.

When the branching model ``feat/geo-commons`` lands a shared
``SpatialImpact`` dataclass in ``gispulse.models``, ``measure_spatial_impact``
can be updated to return that model instead of a plain dict — the key
names are intentionally aligned so callers need no migration.
"""

from __future__ import annotations

import json
from typing import Any, Mapping

import geopandas as gpd
import pyproj
from shapely.geometry import mapping, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from gispulse.capabilities.base import Capability
from gispulse.capabilities.registry import register

# ---------------------------------------------------------------------------
# Public constants
# ---------------------------------------------------------------------------

FULL_OVERLAP_PERCENT: float = 98.0
"""Default threshold (%) above which an overlap is classified as *full*.

Pass ``full_overlap_percent=<value>`` to any function or the capability to
override.  The constant exists so call sites can reference it by name
instead of repeating the magic number.
"""

SPATIAL_IMPACT_METHOD: str = "shapely.intersects+intersection+area"
"""Algorithm identifier recorded in every result dict."""


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def suggest_metric_crs(gdf: gpd.GeoDataFrame) -> Any:
    """Pick a suitable metric CRS for area computations from *gdf*'s bounds.

    Strategy: if *gdf* is in a geographic (lat/lon) CRS, derive the UTM zone
    from the centroid of the bounding box.  This gives sub-metre accuracy at
    the scale of a single polygon and avoids a hardcoded EPSG that would be
    wrong on overseas territories.

    For an already-projected *gdf*, returns the current CRS unchanged so
    callers can use this unconditionally.

    Args:
        gdf: Any GeoDataFrame with a CRS set.

    Returns:
        A CRS identifier (string ``"EPSG:<n>"`` or a :class:`pyproj.CRS`
        instance) suitable for :meth:`GeoDataFrame.to_crs`.  Falls back to
        ``"EPSG:2154"`` (Lambert-93) for an empty geographic *gdf*.

    Note:
        A bounding box that straddles the antimeridian (±180°) produces an
        ambiguous raw centroid longitude.  The longitude is normalised to
        ``(-180, 180]`` before zone computation, which yields a defined
        (if potentially imprecise) result rather than an out-of-range EPSG.
        Callers operating on cross-antimeridian data should pre-split their
        geometries or use a pole-centric CRS explicitly.
    """
    if not _is_angular(gdf):
        return gdf.crs
    if gdf.empty:
        return "EPSG:2154"  # Lambert-93, reasonable fallback for metropolitan FR.
    bounds = gdf.total_bounds  # (minx, miny, maxx, maxy)
    lon_raw = (bounds[0] + bounds[2]) / 2
    lat = (bounds[1] + bounds[3]) / 2
    # Normalise to (-180, 180] so antimeridian-straddling bounds and any
    # out-of-range raw longitude produce a valid UTM zone in [1, 60].
    lon = ((lon_raw + 180.0) % 360.0) - 180.0
    zone = max(1, min(60, int((lon + 180) // 6) + 1))
    epsg_base = 32600 if lat >= 0 else 32700
    return f"EPSG:{epsg_base + zone}"


def clip_and_measure_matches(
    source_gdf: gpd.GeoDataFrame,
    parcel_gdf: gpd.GeoDataFrame,
    *,
    full_overlap_percent: float = FULL_OVERLAP_PERCENT,  # noqa: ARG001
) -> gpd.GeoDataFrame:
    """Return rows from *source_gdf* that intersect *parcel_gdf*, clipped.

    Each retained row carries an ``overlap_m2`` column with the area of the
    clipped geometry in metres² (CRS auto-projected to a metric system when
    the input is angular).

    Args:
        source_gdf:          Features to test against the reference polygon(s).
        parcel_gdf:          Reference polygon(s); unioned internally.
        full_overlap_percent: Threshold for classifying overlap as full
                              (passed through for API symmetry; not used
                              directly here since this function only measures
                              area — see :func:`measure_spatial_impact`).

    Returns:
        Copy of intersecting rows from *source_gdf* with ``geometry``
        replaced by the intersection and an ``overlap_m2`` column added.
        Empty GeoDataFrame with no rows when nothing intersects.

    Raises:
        ValueError: when either GeoDataFrame is missing a CRS.  Silent CRS
            mismatches produce wildly wrong area measurements (e.g.
            EPSG:3857 vs EPSG:4326 degrees vs metres); a fast-fail is safer
            than a silently corrupt result.
    """
    # --- CRS guard -----------------------------------------------------------
    if source_gdf.crs is None or parcel_gdf.crs is None:
        raise ValueError(
            "clip_and_measure_matches: both GeoDataFrames must carry a CRS. "
            f"source_gdf.crs={source_gdf.crs!r}, parcel_gdf.crs={parcel_gdf.crs!r}."
        )

    if source_gdf.empty or parcel_gdf.empty:
        result = source_gdf.iloc[0:0].copy()
        result["overlap_m2"] = []
        return result

    # Reproject parcel to source CRS when they differ so that intersects() and
    # intersection() operate in a consistent coordinate space.
    if source_gdf.crs != parcel_gdf.crs:
        parcel_gdf = parcel_gdf.to_crs(source_gdf.crs)

    # --- make_valid ----------------------------------------------------------
    # Apply make_valid before any predicate/intersection so that repairable
    # invalid geometries (bow-ties, self-touching rings…) are handled
    # gracefully instead of raising GEOSException mid-pipeline.
    source_gdf = source_gdf.copy()
    source_gdf["geometry"] = source_gdf.geometry.make_valid()
    parcel_gdf = parcel_gdf.copy()
    parcel_gdf["geometry"] = parcel_gdf.geometry.make_valid()

    parcel_union = unary_union(
        [g for g in parcel_gdf.geometry if g is not None and not g.is_empty]
    )
    if parcel_union.is_empty:
        result = source_gdf.iloc[0:0].copy()
        result["overlap_m2"] = []
        return result

    matches_mask = source_gdf.geometry.apply(
        lambda g: bool(g) and not g.is_empty and g.intersects(parcel_union)
    )
    matches = source_gdf.loc[matches_mask].copy()
    if matches.empty:
        matches["overlap_m2"] = []
        return matches

    matches["geometry"] = matches.geometry.apply(
        lambda g: g.intersection(parcel_union) if g is not None else None
    )

    metric_crs = suggest_metric_crs(matches)
    if metric_crs is not None and matches.crs is not None:
        metric_geom = matches.geometry.to_crs(metric_crs)
    else:
        metric_geom = matches.geometry
    matches["overlap_m2"] = metric_geom.area
    return matches


def measure_spatial_impact(
    feature_geometry: Mapping[str, Any] | None,
    parcel_geometry: Mapping[str, Any] | None,
    *,
    full_overlap_percent: float = FULL_OVERLAP_PERCENT,
) -> dict[str, Any] | None:
    """Measure the spatial overlap of a feature against a reference polygon.

    Accepts GeoJSON geometry dicts (``{"type": "Polygon", "coordinates": […]}``).
    Reprojects to an appropriate metric CRS internally; inputs must be in
    ``EPSG:4326``.

    Args:
        feature_geometry:    GeoJSON geometry of the feature to measure.
        parcel_geometry:     GeoJSON geometry of the reference polygon.
        full_overlap_percent: Threshold (%) above which the relation is
                              classified as ``"full_overlap"`` rather than
                              ``"partial_overlap"``.

    Returns:
        A dict with keys ``status``, ``relation``, ``overlap_m2``,
        ``parcel_percent``, ``clipped_geometry``, ``method``.
        Returns ``None`` when either geometry is absent.

    Notes:
        The key names match the ``SpatialImpact`` Pydantic model in
        ``gispulse-permis``; the dict contract will be aligned with a shared
        dataclass once ``feat/geo-commons`` is merged into core.
    """
    if not feature_geometry or not parcel_geometry:
        return None

    try:
        parcel_gdf = gpd.GeoDataFrame(
            {"id": ["parcel"]},
            geometry=[shape(parcel_geometry)],
            crs="EPSG:4326",
        )
        source_gdf = gpd.GeoDataFrame(
            {"id": ["feature"]},
            geometry=[shape(feature_geometry)],
            crs="EPSG:4326",
        )
        measured = clip_and_measure_matches(source_gdf, parcel_gdf, full_overlap_percent=full_overlap_percent)
        parcel_area_m2 = _area_m2(parcel_gdf)
    except Exception:
        return {
            "status": "not_measured",
            "relation": "not_measured",
            "overlap_m2": None,
            "parcel_percent": None,
            "clipped_geometry": None,
            "method": SPATIAL_IMPACT_METHOD,
        }

    if measured.empty:
        return {
            "status": "measured",
            "relation": "partial_overlap",
            "overlap_m2": 0.0,
            "parcel_percent": 0.0 if parcel_area_m2 else None,
            "clipped_geometry": None,
            "method": SPATIAL_IMPACT_METHOD,
        }

    overlap_m2 = float(measured["overlap_m2"].fillna(0).sum())
    parcel_percent = (overlap_m2 / parcel_area_m2) * 100 if parcel_area_m2 else None
    relation = (
        "full_overlap"
        if parcel_percent is not None and parcel_percent >= full_overlap_percent
        else "partial_overlap"
    )

    return {
        "status": "measured",
        "relation": relation,
        "overlap_m2": overlap_m2,
        "parcel_percent": parcel_percent,
        "clipped_geometry": _geojson_geometry(measured),
        "method": SPATIAL_IMPACT_METHOD,
    }


# ---------------------------------------------------------------------------
# Capability wrapper
# ---------------------------------------------------------------------------


@register
class MeasureSpatialImpactCapability(Capability):
    """Clips each feature in *gdf* against a reference polygon layer and measures overlap.

    For every feature that intersects the reference layer the output carries:

    * ``geometry``    -- clipped intersection geometry;
    * ``overlap_m2``  -- intersection area in metres² (metric CRS auto-detected);
    * ``parcel_percent`` -- percentage of the reference polygon area covered
      (``None`` when the reference area cannot be computed);
    * ``relation``    -- ``"full_overlap"`` when ``parcel_percent >=
      full_overlap_percent``, otherwise ``"partial_overlap"``.

    Features that do not intersect the reference layer are dropped.
    """

    name = "measure_spatial_impact"
    description = (
        "Clips features against a reference polygon layer and measures the "
        "intersection area (overlap_m2, parcel_percent, relation). "
        "CRS is auto-projected to a metric UTM zone for area accuracy."
    )

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        ref_gdf: gpd.GeoDataFrame | None = None,
        full_overlap_percent: float = FULL_OVERLAP_PERCENT,
        **_,
    ) -> gpd.GeoDataFrame:
        """Clip *gdf* against *ref_gdf* and add overlap metrics.

        Args:
            gdf:                  Input feature layer (any polygon/multipolygon).
            ref_gdf:              Reference polygon layer (injected via
                                  ``ref_layer`` pipeline key).
            full_overlap_percent: Threshold (%) for classifying a result row as
                                  ``"full_overlap"`` vs ``"partial_overlap"``.

        Returns:
            Filtered, clipped copy of *gdf* with ``overlap_m2``,
            ``parcel_percent`` and ``relation`` columns added.  Only rows that
            intersect *ref_gdf* are kept.

        Raises:
            ValueError: when *ref_gdf* is None or empty.
        """
        if ref_gdf is None or ref_gdf.empty:
            raise ValueError(
                "measure_spatial_impact requires a non-empty reference polygon "
                "layer (ref_gdf). Pass the reference polygons to clip against."
            )

        result = clip_and_measure_matches(
            gdf, ref_gdf, full_overlap_percent=full_overlap_percent
        )
        if result.empty:
            result["parcel_percent"] = []
            result["relation"] = []
            return result

        parcel_area_m2 = _area_m2(ref_gdf)
        if parcel_area_m2:
            result["parcel_percent"] = result["overlap_m2"].apply(
                lambda m2: (float(m2) / parcel_area_m2) * 100 if m2 is not None else None
            )
        else:
            result["parcel_percent"] = None

        result["relation"] = result["parcel_percent"].apply(
            lambda pct: (
                "full_overlap"
                if pct is not None and pct >= full_overlap_percent
                else "partial_overlap"
            )
        )
        return result

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "ref_layer": {
                    "type": "string",
                    "description": "Reference polygon layer to clip and measure against.",
                },
                "full_overlap_percent": {
                    "type": "number",
                    "default": FULL_OVERLAP_PERCENT,
                    "description": (
                        "Threshold (%) above which the relation is classified as "
                        "'full_overlap'. Default: 98.0."
                    ),
                },
            },
            "required": [],
        }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _is_angular(gdf: gpd.GeoDataFrame) -> bool:
    if gdf.crs is None:
        return False
    try:
        return bool(pyproj.CRS(gdf.crs).is_geographic)
    except Exception:
        return False


def _area_m2(gdf: gpd.GeoDataFrame) -> float | None:
    if gdf.empty:
        return None
    if gdf.crs is None:
        area = float(gdf.geometry.area.sum())
    else:
        metric_crs = suggest_metric_crs(gdf) if _is_angular(gdf) else gdf.crs
        area = float(gdf.to_crs(metric_crs).geometry.area.sum())
    return area if area > 0 else None


def _geojson_geometry(gdf: gpd.GeoDataFrame) -> dict[str, Any] | None:
    if gdf.empty:
        return None
    geometry: BaseGeometry = unary_union(
        [item for item in gdf.geometry if item is not None and not item.is_empty]
    )
    if geometry.is_empty:
        return None
    return json.loads(json.dumps(mapping(geometry)))


__all__ = [
    "FULL_OVERLAP_PERCENT",
    "SPATIAL_IMPACT_METHOD",
    "MeasureSpatialImpactCapability",
    "clip_and_measure_matches",
    "measure_spatial_impact",
    "suggest_metric_crs",
]
