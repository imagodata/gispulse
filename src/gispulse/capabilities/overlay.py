"""Overlay capabilities — intersection, union, erase with attribute inheritance.

These differ from the existing ``clip`` / ``symmetric_difference`` /
``vector_diff`` capabilities in that they **inherit attributes from both
layers**: each output feature carries the attributes of the contributing
features from the primary AND the reference layer (with suffix collision
handling). This is the standard FME / QGIS / ArcGIS behaviour.
"""

from __future__ import annotations

import geopandas as gpd

from gispulse.capabilities.base import Capability
from gispulse.capabilities.registry import register


_HOW_INTERSECTION = "intersection"
_HOW_UNION = "union"
_HOW_DIFFERENCE = "difference"


def _aligned_ref(gdf: gpd.GeoDataFrame, ref_gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Reproject ref_gdf to gdf.crs if needed; return as-is otherwise."""
    if (
        gdf.crs is not None
        and ref_gdf.crs is not None
        and gdf.crs != ref_gdf.crs
    ):
        return ref_gdf.to_crs(gdf.crs)
    return ref_gdf


@register
class OverlayIntersectionCapability(Capability):
    """Intersection overlay with attribute inheritance from both layers.

    Each output feature is the geometric intersection of one A-feature with
    one B-feature; attributes from both sides are kept (collisions disambiguated
    via ``suffix_left`` / ``suffix_right``).

    Example::

        {"ref_layer": "zones", "suffix_left": "_a", "suffix_right": "_b",
         "keep_geom_type": true}
    """

    name = "overlay_intersection"
    description = (
        "Geometric intersection of two layers with attribute inheritance from "
        "both sides (FME/QGIS-style overlay)."
    )

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        ref_gdf: gpd.GeoDataFrame | None = None,
        keep_geom_type: bool = True,
        suffix_left: str = "_1",
        suffix_right: str = "_2",
        **_,
    ) -> gpd.GeoDataFrame:
        # P2 (beta-test 2026-04-24): align with ``erase`` and the rest of
        # the overlay family — when ``ref_gdf`` is missing the operation
        # degenerates to its identity. ``A ∩ ∅ = ∅`` so we return an empty
        # GeoDataFrame with the primary layer's schema instead of raising.
        # Raising was inconsistent with ``erase`` which passes ``A`` through.
        if ref_gdf is None or ref_gdf.empty:
            return gdf.iloc[0:0].copy()
        if gdf.empty:
            return gdf.copy()
        ref = _aligned_ref(gdf, ref_gdf)
        return gpd.overlay(
            gdf,
            ref,
            how=_HOW_INTERSECTION,
            keep_geom_type=keep_geom_type,
        ).rename(
            columns=lambda c: c if c == gdf.geometry.name else c
        ).pipe(_apply_suffixes, gdf, ref, suffix_left, suffix_right)

    def get_schema(self) -> dict:
        return _overlay_schema()


@register
class OverlayUnionCapability(Capability):
    """Union overlay — keeps the parts from A only, B only, and A∩B.

    Each output feature carries the attributes of whichever layer(s)
    contributed; areas not covered by one side have NaN for that side's
    columns.

    Example::

        {"ref_layer": "zones", "keep_geom_type": true}
    """

    name = "overlay_union"
    description = (
        "Geometric union of two layers — keeps A-only, B-only, and A∩B parts "
        "with attribute inheritance."
    )

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        ref_gdf: gpd.GeoDataFrame | None = None,
        keep_geom_type: bool = True,
        suffix_left: str = "_1",
        suffix_right: str = "_2",
        **_,
    ) -> gpd.GeoDataFrame:
        # P2 (beta-test 2026-04-24): consistency with ``erase``. ``A ∪ ∅ = A``
        # so a missing reference returns the primary layer unchanged instead
        # of raising.
        if ref_gdf is None or ref_gdf.empty:
            return gdf.copy()
        if gdf.empty:
            return ref_gdf.copy()
        ref = _aligned_ref(gdf, ref_gdf)
        return gpd.overlay(
            gdf,
            ref,
            how=_HOW_UNION,
            keep_geom_type=keep_geom_type,
        ).pipe(_apply_suffixes, gdf, ref, suffix_left, suffix_right)

    def get_schema(self) -> dict:
        return _overlay_schema()


@register
class EraseCapability(Capability):
    """Erase — removes the parts of A covered by B; A's attributes are preserved.

    ESRI / FME terminology. Equivalent to ``overlay(how='difference')`` but
    surfaced as a first-class capability since users frequently ask for it.

    Example::

        {"ref_layer": "no_build_zones"}
    """

    name = "erase"
    description = (
        "Removes the parts of the primary layer covered by the reference layer "
        "(geometric difference; primary attributes preserved)."
    )

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        ref_gdf: gpd.GeoDataFrame | None = None,
        keep_geom_type: bool = True,
        **_,
    ) -> gpd.GeoDataFrame:
        if ref_gdf is None or ref_gdf.empty:
            return gdf.copy()
        if gdf.empty:
            return gdf.copy()
        ref = _aligned_ref(gdf, ref_gdf)
        return gpd.overlay(
            gdf,
            ref,
            how=_HOW_DIFFERENCE,
            keep_geom_type=keep_geom_type,
        )

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "ref_layer": {
                    "type": "string",
                    "description": "Layer whose features erase parts of the primary layer.",
                },
                "keep_geom_type": {
                    "type": "boolean",
                    "default": True,
                    "description": "Drop geometry-type fragments unrelated to the primary layer.",
                },
            },
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _apply_suffixes(
    result: gpd.GeoDataFrame,
    left: gpd.GeoDataFrame,
    right: gpd.GeoDataFrame,
    suffix_left: str,
    suffix_right: str,
) -> gpd.GeoDataFrame:
    """Apply suffix renaming for columns colliding between left and right.

    GeoPandas overlay uses ``_1`` / ``_2`` by default; here we expose the
    suffixes as parameters by post-processing the output column names.
    """
    if suffix_left == "_1" and suffix_right == "_2":
        return result
    geom_col = result.geometry.name
    common = (set(left.columns) & set(right.columns)) - {geom_col}
    rename: dict[str, str] = {}
    for col in common:
        if f"{col}_1" in result.columns and suffix_left != "_1":
            rename[f"{col}_1"] = f"{col}{suffix_left}"
        if f"{col}_2" in result.columns and suffix_right != "_2":
            rename[f"{col}_2"] = f"{col}{suffix_right}"
    if rename:
        result = result.rename(columns=rename)
    return result


def _overlay_schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "ref_layer": {
                "type": "string",
                "description": "Reference layer (resolved to ref_gdf by engine).",
            },
            "keep_geom_type": {
                "type": "boolean",
                "default": True,
                "description": (
                    "Drop fragments with a different geometry type than the primary "
                    "layer (e.g. drop point fragments from a polygon overlay)."
                ),
            },
            "suffix_left": {
                "type": "string",
                "default": "_1",
                "description": "Suffix for primary-layer columns on collision.",
            },
            "suffix_right": {
                "type": "string",
                "default": "_2",
                "description": "Suffix for reference-layer columns on collision.",
            },
        },
    }
