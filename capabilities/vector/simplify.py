from __future__ import annotations

import ast as _ast
import re as _re

import geopandas as gpd
import numpy as np
import pandas as pd

from capabilities.base import Capability
from capabilities.registry import register
from capabilities.strategy import ExecutionContext, ExecutionStrategy, StrategyMode



# ---------------------------------------------------------------------------
# Geometry transformation capabilities
# ---------------------------------------------------------------------------


_SIMPLIFY_ALGORITHMS = {"dp", "vw", "coverage"}


@register
class SimplifyCapability(Capability):
    """Simplifies geometries — Douglas-Peucker, Visvalingam-Whyatt or coverage."""

    name = "simplify"
    description = (
        "Simplifies geometries by reducing vertex count. "
        "Supports Douglas-Peucker (dp), Visvalingam-Whyatt (vw) "
        "and topology-preserving coverage simplification."
    )

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        tolerance: float = 1.0,
        preserve_topology: bool = True,
        algorithm: str = "dp",
        crs_meters: str = "EPSG:3857",
        **_,
    ) -> gpd.GeoDataFrame:
        """
        Args:
            gdf:              Input GeoDataFrame.
            tolerance:        Algorithm-specific tolerance (distance for dp,
                              area for vw) in units of *crs_meters*.
            preserve_topology: Only used by 'dp'. If True, uses the slower
                              topology-preserving Douglas-Peucker.
            algorithm:        'dp' (Douglas-Peucker, default, fastest),
                              'vw' (Visvalingam-Whyatt, area-based — better
                                    visual quality on dense lines),
                              'coverage' (topology-safe for polygon coverages,
                                    shared borders stay aligned).
            crs_meters:       Metric CRS used to interpret tolerance.
        """
        if tolerance <= 0:
            raise ValueError("tolerance must be > 0.")
        if algorithm not in _SIMPLIFY_ALGORITHMS:
            raise ValueError(
                f"Invalid algorithm '{algorithm}'. "
                f"Expected one of {sorted(_SIMPLIFY_ALGORITHMS)}."
            )
        if gdf.empty:
            return gdf.copy()

        original_crs = gdf.crs
        if original_crs is None:
            work = gdf.copy()
        else:
            work = gdf.to_crs(crs_meters)

        work = work.copy()

        if algorithm == "dp":
            work["geometry"] = work.geometry.simplify(
                tolerance, preserve_topology=preserve_topology
            )
        elif algorithm == "vw":
            from shapely import simplify

            work["geometry"] = [
                simplify(g, tolerance=tolerance, preserve_topology=True)
                if g is not None else g
                for g in work.geometry
            ]
        else:  # coverage
            try:
                from shapely import coverage_simplify
            except ImportError as exc:
                raise RuntimeError(
                    "algorithm='coverage' requires shapely >= 2.1."
                ) from exc
            # coverage_simplify expects a collection and preserves shared
            # borders between adjacent polygons of a coverage.
            simplified = coverage_simplify(
                list(work.geometry), tolerance=tolerance, simplify_boundary=True
            )
            work["geometry"] = list(simplified)

        return work.to_crs(original_crs) if original_crs is not None else work

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "tolerance": {
                    "type": "number",
                    "minimum": 0,
                    "description": "Tolerance (distance for dp, area for vw).",
                },
                "preserve_topology": {
                    "type": "boolean",
                    "default": True,
                    "description": "DP only: use topology-preserving variant.",
                },
                "algorithm": {
                    "type": "string",
                    "default": "dp",
                    "enum": sorted(_SIMPLIFY_ALGORITHMS),
                    "description": "dp=Douglas-Peucker, vw=Visvalingam-Whyatt, coverage=topology-safe.",
                },
                "crs_meters": {
                    "type": "string",
                    "default": "EPSG:3857",
                    "description": "Metric CRS used to interpret tolerance.",
                },
            },
            "required": ["tolerance"],
        }
