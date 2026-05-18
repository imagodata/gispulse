from __future__ import annotations


import geopandas as gpd
import pandas as pd

from gispulse.capabilities.base import Capability
from gispulse.capabilities.registry import register




@register
class NearestNeighborCapability(Capability):
    """k-nearest-neighbor spatial join from the primary layer to a reference."""

    name = "nearest_neighbor"
    description = (
        "Joins attributes from the k nearest features of a reference layer, "
        "with optional max_distance filter."
    )

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        ref_gdf: gpd.GeoDataFrame | None = None,
        k: int = 1,
        max_distance: float | None = None,
        distance_col: str = "nn_distance",
        columns: list[str] | None = None,
        crs_meters: str = "EPSG:3857",
        **_,
    ) -> gpd.GeoDataFrame:
        """
        Args:
            gdf:           Input GeoDataFrame (left side).
            ref_gdf:       Reference layer (injected via ``ref_layer``).
            k:             Number of neighbors to keep per input feature.
                           For k=1, returns exactly one row per input feature;
                           for k>1, duplicates input rows.
            max_distance:  Optional max distance in units of *crs_meters*.
                           Rows with no neighbor within the threshold are
                           dropped (or kept with NaN distance when k=1).
            distance_col:  Name of the output distance column.
            columns:       Subset of reference columns to join (excluding geometry).
                           None = all.
            crs_meters:    Metric CRS used for accurate distance calculation.

        Returns:
            GeoDataFrame with reference attributes and the distance column.
        """
        if ref_gdf is None:
            raise ValueError(
                "NearestNeighborCapability requires a reference layer "
                "(pass 'ref_layer' or 'ref_gdf')."
            )
        if k < 1:
            raise ValueError("k must be >= 1.")

        if gdf.empty or ref_gdf.empty:
            empty = gdf.copy()
            if distance_col not in empty.columns:
                empty[distance_col] = pd.Series([], dtype=float)
            return empty

        original_crs = gdf.crs
        # Project both sides into a metric CRS so max_distance is meaningful.
        # Common-CRS fallback: when one side has a CRS and the other doesn't,
        # assume they share it. Typical when a multi-layer GPKG is written
        # with per-layer CRS missing on some layers (e.g. BDTOPO Versailles
        # `batiments` ships with crs=None while the other layers carry
        # EPSG:4326). Without this fallback, reprojecting only the side that
        # has a CRS silently mixes units and produces garbage distances
        # (~6.88 M m constant for WGS84 lat/lon vs Lambert93 metres).
        common_crs = gdf.crs or ref_gdf.crs
        if common_crs is None:
            # Neither side has a CRS — fall back to native-unit distance.
            left = gdf.copy()
            right = ref_gdf.copy()
        else:
            left_src = gdf if gdf.crs is not None else gdf.set_crs(common_crs)
            right_src = ref_gdf if ref_gdf.crs is not None else ref_gdf.set_crs(common_crs)
            left = left_src.to_crs(crs_meters)
            right = right_src.to_crs(crs_meters)

        if columns is not None:
            keep_cols = [c for c in columns if c in right.columns]
            if right.geometry.name not in keep_cols:
                keep_cols.append(right.geometry.name)
            right = right[keep_cols]

        # GeoPandas ≥1.0 ships sjoin_nearest; fall back gracefully otherwise.
        sjoin_nearest = getattr(gpd, "sjoin_nearest", None)
        if sjoin_nearest is None:
            # Brute-force fallback — acceptable for small reference layers.
            rows = []
            ref_geoms = list(right.geometry)
            for idx, geom in enumerate(left.geometry):
                if geom is None or geom.is_empty:
                    continue
                distances = [
                    (geom.distance(rg), r_idx)
                    for r_idx, rg in enumerate(ref_geoms)
                ]
                distances.sort(key=lambda t: t[0])
                if max_distance is not None:
                    distances = [d for d in distances if d[0] <= max_distance]
                for dist, r_idx in distances[:k]:
                    base = left.iloc[idx].to_dict()
                    ref_row = right.iloc[r_idx].to_dict()
                    ref_row.pop(right.geometry.name, None)
                    base.update(ref_row)
                    base[distance_col] = dist
                    rows.append(base)
            if not rows:
                empty = left.iloc[0:0].copy()
                empty[distance_col] = pd.Series([], dtype=float)
                return empty.to_crs(original_crs) if original_crs is not None else empty
            result = gpd.GeoDataFrame(rows, geometry=left.geometry.name, crs=left.crs)
        else:
            joined = sjoin_nearest(
                left,
                right,
                how="left",
                distance_col=distance_col,
                max_distance=max_distance,
                exclusive=False,
            )
            if "index_right" in joined.columns:
                joined = joined.drop(columns=["index_right"])
            if k == 1:
                result = joined.reset_index(drop=True)
            else:
                # Rank neighbors per left index and keep top-k
                joined = joined.sort_values(distance_col)
                joined["_nn_rank"] = joined.groupby(level=0).cumcount()
                result = joined[joined["_nn_rank"] < k].drop(columns=["_nn_rank"])
                result = result.reset_index(drop=True)

            if max_distance is not None:
                # Drop rows with no neighbor within the threshold.
                # sjoin_nearest leaves NaN in *distance_col* for orphans.
                mask = result[distance_col].notna() & (result[distance_col] <= max_distance)
                result = result[mask].reset_index(drop=True)

        if original_crs is not None:
            result = result.to_crs(original_crs)
        return result

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "ref_layer": {
                    "type": "string",
                    "description": "Name of the reference layer (nearest neighbors taken from).",
                },
                "k": {
                    "type": "integer",
                    "default": 1,
                    "minimum": 1,
                    "description": "Number of neighbors to keep per input feature.",
                },
                "max_distance": {
                    "type": ["number", "null"],
                    "description": "Optional max distance in units of crs_meters.",
                },
                "distance_col": {
                    "type": "string",
                    "default": "nn_distance",
                    "description": "Name of the output distance column.",
                },
                "columns": {
                    "type": ["array", "null"],
                    "items": {"type": "string"},
                    "description": "Subset of reference columns to join.",
                },
                "crs_meters": {
                    "type": "string",
                    "default": "EPSG:3857",
                    "description": "Metric CRS used for accurate distances.",
                },
            },
            # ``ref_layer`` is pipeline plumbing (stripped by
            # rules.validation._PLUMBING_KEYS before validation) so it cannot
            # appear in ``required`` — the runtime raises a clear ValueError
            # when ``ref_gdf`` is None, which preserves the contract.
        }


# ---------------------------------------------------------------------------
# Advanced geometry constructions
# ---------------------------------------------------------------------------
