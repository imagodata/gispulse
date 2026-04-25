"""Detects spatial and attribute relationships between GeoDataFrame layers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import geopandas as gpd
import numpy as np


def _suggest_metric_crs_safe(gdf: gpd.GeoDataFrame) -> str:
    """Best-effort metric CRS suggestion; falls back to EPSG:3857 on errors."""
    try:
        from core.crs import suggest_metric_crs
        return suggest_metric_crs(gdf)
    except Exception:
        return "EPSG:3857"


@dataclass
class DetectedRelation:
    """A detected relationship between two layers."""

    layer_a: str
    layer_b: str
    relation_type: str  # "contains", "overlaps", "touches", "proximity", "attribute"
    confidence: float   # 0.0 - 1.0
    sample_stats: dict[str, Any] = field(default_factory=dict)
    suggested_name: str = ""
    suggested_rule: dict[str, Any] | None = None


class SpatialRelationDetector:
    """Detects spatial and attribute relationships between layers by sampling."""

    def __init__(self, sample_size: int = 1000) -> None:
        self.sample_size = sample_size

    def analyze(
        self,
        layer_a: gpd.GeoDataFrame,
        layer_b: gpd.GeoDataFrame,
        name_a: str = "layer_a",
        name_b: str = "layer_b",
    ) -> list[DetectedRelation]:
        """Analyze relationships between two layers.

        Returns candidates sorted by descending confidence.
        """
        results: list[DetectedRelation] = []

        # Sample if needed
        a = self._sample(layer_a)
        b = self._sample(layer_b)

        # 1. Attribute analysis: find common field names (excluding geometry, id, fid)
        results.extend(self._detect_attribute_relations(a, b, name_a, name_b))

        # 2. Spatial analysis (only if both have geometry)
        if not a.geometry.is_empty.all() and not b.geometry.is_empty.all():
            results.extend(self._detect_contains(a, b, name_a, name_b))
            results.extend(self._detect_overlaps(a, b, name_a, name_b))
            results.extend(self._detect_touches(a, b, name_a, name_b))
            results.extend(self._detect_proximity(a, b, name_a, name_b))

        return sorted(results, key=lambda r: r.confidence, reverse=True)

    def analyze_all(
        self,
        layers: dict[str, gpd.GeoDataFrame],
    ) -> list[DetectedRelation]:
        """Analyze all pairs of layers."""
        results: list[DetectedRelation] = []
        names = list(layers.keys())
        for i in range(len(names)):
            for j in range(i + 1, len(names)):
                results.extend(
                    self.analyze(layers[names[i]], layers[names[j]], names[i], names[j])
                )
        return results

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _sample(self, gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
        if len(gdf) <= self.sample_size:
            return gdf
        return gdf.sample(n=self.sample_size, random_state=42)

    def _detect_attribute_relations(
        self,
        a: gpd.GeoDataFrame,
        b: gpd.GeoDataFrame,
        name_a: str,
        name_b: str,
    ) -> list[DetectedRelation]:
        """Find common attribute fields with meaningful value overlap."""
        skip = {"geometry", "id", "fid", "index", "ogc_fid", "gid"}
        cols_a = {c.lower() for c in a.columns} - skip
        cols_b = {c.lower() for c in b.columns} - skip
        common = cols_a & cols_b

        if not common:
            return []

        relations: list[DetectedRelation] = []
        for col in common:
            col_a = next(c for c in a.columns if c.lower() == col)
            col_b = next(c for c in b.columns if c.lower() == col)

            vals_a = set(a[col_a].dropna().unique())
            vals_b = set(b[col_b].dropna().unique())

            if not vals_a or not vals_b:
                continue

            overlap = vals_a & vals_b
            denominator = min(len(vals_a), len(vals_b))
            overlap_ratio = len(overlap) / denominator if denominator > 0 else 0.0

            if overlap_ratio > 0.1:  # at least 10 % value overlap
                confidence = min(0.5 + overlap_ratio * 0.4, 0.95)
                relations.append(
                    DetectedRelation(
                        layer_a=name_a,
                        layer_b=name_b,
                        relation_type="attribute",
                        confidence=round(confidence, 2),
                        sample_stats={
                            "field": col,
                            "overlap_ratio": round(overlap_ratio, 2),
                            "common_values": len(overlap),
                        },
                        suggested_name=f"join_on_{col}",
                        suggested_rule={
                            "capability": "spatial_join",
                            "config": {"join_field": col},
                        },
                    )
                )
        return relations

    def _detect_contains(
        self,
        a: gpd.GeoDataFrame,
        b: gpd.GeoDataFrame,
        name_a: str,
        name_b: str,
    ) -> list[DetectedRelation]:
        """Detect containment (polygon layer wrapping point/line layer)."""
        results: list[DetectedRelation] = []

        a_geom_types = set(a.geometry.geom_type.unique())
        b_geom_types = set(b.geometry.geom_type.unique())
        polygon_types = {"Polygon", "MultiPolygon"}

        # a contains b
        if a_geom_types & polygon_types and not (b_geom_types & polygon_types):
            try:
                joined = gpd.sjoin(b, a, predicate="within", how="inner")
                match_pct = len(joined) / len(b) if len(b) > 0 else 0.0
                if match_pct > 0.3:
                    results.append(
                        DetectedRelation(
                            layer_a=name_a,
                            layer_b=name_b,
                            relation_type="contains",
                            confidence=round(min(match_pct, 0.99), 2),
                            sample_stats={
                                "match_pct": round(match_pct, 2),
                                "matched": len(joined),
                                "total": len(b),
                            },
                            suggested_name=f"{name_b}_within_{name_a}",
                            suggested_rule={
                                "capability": "spatial_join",
                                "config": {"predicate": "within"},
                            },
                        )
                    )
            except Exception:
                pass

        # b contains a
        if b_geom_types & polygon_types and not (a_geom_types & polygon_types):
            try:
                joined = gpd.sjoin(a, b, predicate="within", how="inner")
                match_pct = len(joined) / len(a) if len(a) > 0 else 0.0
                if match_pct > 0.3:
                    results.append(
                        DetectedRelation(
                            layer_a=name_b,
                            layer_b=name_a,
                            relation_type="contains",
                            confidence=round(min(match_pct, 0.99), 2),
                            sample_stats={
                                "match_pct": round(match_pct, 2),
                                "matched": len(joined),
                                "total": len(a),
                            },
                            suggested_name=f"{name_a}_within_{name_b}",
                            suggested_rule={
                                "capability": "spatial_join",
                                "config": {"predicate": "within"},
                            },
                        )
                    )
            except Exception:
                pass

        return results

    def _detect_overlaps(
        self,
        a: gpd.GeoDataFrame,
        b: gpd.GeoDataFrame,
        name_a: str,
        name_b: str,
    ) -> list[DetectedRelation]:
        """Detect polygon-polygon overlaps."""
        a_types = set(a.geometry.geom_type.unique())
        b_types = set(b.geometry.geom_type.unique())
        polygon_types = {"Polygon", "MultiPolygon"}

        if not (a_types & polygon_types) or not (b_types & polygon_types):
            return []

        try:
            joined = gpd.sjoin(a, b, predicate="intersects", how="inner")
            match_pct = len(joined) / len(a) if len(a) > 0 else 0.0
            if match_pct > 0.2:
                return [
                    DetectedRelation(
                        layer_a=name_a,
                        layer_b=name_b,
                        relation_type="overlaps",
                        confidence=round(min(0.4 + match_pct * 0.5, 0.95), 2),
                        sample_stats={
                            "match_pct": round(match_pct, 2),
                            "intersections": len(joined),
                        },
                        suggested_name=f"{name_a}_overlaps_{name_b}",
                        suggested_rule={"capability": "intersect", "config": {}},
                    )
                ]
        except Exception:
            pass
        return []

    def _detect_touches(
        self,
        a: gpd.GeoDataFrame,
        b: gpd.GeoDataFrame,
        name_a: str,
        name_b: str,
    ) -> list[DetectedRelation]:
        """Detect line-point or line-line topology (touches at endpoints)."""
        a_types = set(a.geometry.geom_type.unique())
        b_types = set(b.geometry.geom_type.unique())
        line_types = {"LineString", "MultiLineString"}
        point_types = {"Point", "MultiPoint"}

        has_touch_pair = (a_types & line_types and b_types & point_types) or (
            b_types & line_types and a_types & point_types
        )
        if not has_touch_pair:
            return []

        try:
            joined = gpd.sjoin(a, b, predicate="touches", how="inner")
            total = max(len(a), len(b))
            match_pct = len(joined) / total if total > 0 else 0.0
            if match_pct > 0.1:
                return [
                    DetectedRelation(
                        layer_a=name_a,
                        layer_b=name_b,
                        relation_type="touches",
                        confidence=round(min(0.3 + match_pct * 0.6, 0.95), 2),
                        sample_stats={
                            "match_pct": round(match_pct, 2),
                            "touch_count": len(joined),
                        },
                        suggested_name=f"{name_a}_touches_{name_b}",
                        suggested_rule={
                            "capability": "spatial_join",
                            "config": {"predicate": "touches"},
                        },
                    )
                ]
        except Exception:
            pass
        return []

    def _detect_proximity(
        self,
        a: gpd.GeoDataFrame,
        b: gpd.GeoDataFrame,
        name_a: str,
        name_b: str,
    ) -> list[DetectedRelation]:
        """Detect consistent proximity pattern via nearest-neighbour analysis."""
        try:
            from shapely.ops import nearest_points

            # Use projected CRS for distance calculations when possible; fall back
            # to the native CRS (distance values will still be consistent for the
            # coefficient-of-variation heuristic even if the units vary).
            a_proj = a.to_crs(a.estimate_utm_crs()) if a.crs and a.crs.is_geographic else a
            b_proj = b.to_crs(a_proj.crs) if b.crs else b

            a_centroids = a_proj.geometry.centroid
            b_union = b_proj.geometry.union_all()

            sample = a_centroids.head(min(100, len(a_centroids)))
            distances: list[float] = []
            for geom in sample:
                nearest = nearest_points(geom, b_union)[1]
                distances.append(geom.distance(nearest))

            if not distances:
                return []

            avg_dist = float(np.mean(distances))
            std_dist = float(np.std(distances))

            # Flag consistently close neighbours (low coefficient of variation)
            if avg_dist > 0 and std_dist / avg_dist < 0.5:
                confidence = max(0.3, min(0.8 - avg_dist / 10_000, 0.7))
                return [
                    DetectedRelation(
                        layer_a=name_a,
                        layer_b=name_b,
                        relation_type="proximity",
                        confidence=round(confidence, 2),
                        sample_stats={
                            "avg_distance": round(avg_dist, 1),
                            "std_distance": round(std_dist, 1),
                            "sample_size": len(distances),
                        },
                        suggested_name=f"{name_a}_near_{name_b}",
                        suggested_rule={
                            "capability": "buffer",
                            "config": {
                                "distance": round(avg_dist * 1.5, 0),
                                "crs_meters": _suggest_metric_crs_safe(a),
                            },
                        },
                    )
                ]
        except Exception:
            pass
        return []
