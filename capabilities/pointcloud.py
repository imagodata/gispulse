"""Pointcloud capabilities — LAS/LAZ I/O and 3D point analytics.

Three of the four capabilities in this module operate on a plain ``GeoDataFrame``
of ``Point Z`` features (no LAS dependency); they accept any layer with a Z
dimension and an optional ``classification`` column. Only ``pointcloud_load_las``
requires the optional ``laspy`` extra (``pip install gispulse[pointcloud]``).

ASPRS LAS classification codes (LAS 1.4 spec §3.4):
    0 = created, never classified
    1 = unclassified
    2 = ground
    3 = low vegetation
    4 = medium vegetation
    5 = high vegetation
    6 = building
    7 = low point (noise)
    8 = reserved
    9 = water
    10 = rail
    11 = road surface
    12 = reserved
    13 = wire — guard
    14 = wire — conductor
    15 = transmission tower
    16 = wire — connector
    17 = bridge deck
    18 = high noise
"""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely
from shapely.geometry import Polygon

from capabilities.base import Capability
from capabilities.registry import register


# ---------------------------------------------------------------------------
# ASPRS classification labels (informational)
# ---------------------------------------------------------------------------


ASPRS_CLASSIFICATION_LABELS: dict[int, str] = {
    0: "created_never_classified",
    1: "unclassified",
    2: "ground",
    3: "low_vegetation",
    4: "medium_vegetation",
    5: "high_vegetation",
    6: "building",
    7: "low_noise",
    8: "reserved_8",
    9: "water",
    10: "rail",
    11: "road_surface",
    12: "reserved_12",
    13: "wire_guard",
    14: "wire_conductor",
    15: "transmission_tower",
    16: "wire_connector",
    17: "bridge_deck",
    18: "high_noise",
}


# ---------------------------------------------------------------------------
# pointcloud_load_las — load LAS/LAZ via laspy
# ---------------------------------------------------------------------------


@register
class PointcloudLoadLasCapability(Capability):
    """Loads a LAS / LAZ file into a GeoDataFrame of Point Z features.

    Per-point attributes preserved as columns: ``intensity``,
    ``return_number``, ``number_of_returns``, ``classification``,
    ``scan_angle``, ``user_data``, ``point_source_id``, ``gps_time`` (when
    present in the point format).

    The primary GeoDataFrame input is ignored — this capability is a *source*
    that produces the layer from disk.

    Example::

        {"path": "scan.laz", "crs": "EPSG:2154", "max_points": 1_000_000,
         "classifications": [2, 6]}
    """

    name = "pointcloud_load_las"
    description = "Loads a LAS/LAZ file into a GeoDataFrame of Point Z features."

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        path: str = "",
        crs: str | None = None,
        max_points: int | None = None,
        classifications: list[int] | None = None,
        **_,
    ) -> gpd.GeoDataFrame:
        try:
            import laspy
        except ImportError as exc:
            raise RuntimeError(
                "pointcloud_load_las requires the 'pointcloud' extra. "
                "Install with: pip install 'gispulse[pointcloud]'.",
            ) from exc

        if not path:
            raise ValueError("pointcloud_load_las requires 'path'.")
        las_path = Path(path)
        if not las_path.exists():
            raise FileNotFoundError(f"LAS file not found: {path}")

        las = laspy.read(str(las_path))

        # Build coordinate arrays (laspy applies scale/offset transparently).
        x = np.asarray(las.x, dtype=np.float64)
        y = np.asarray(las.y, dtype=np.float64)
        z = np.asarray(las.z, dtype=np.float64)
        cls = np.asarray(las.classification, dtype=np.int16)

        # Optional class filter applied before geometry construction (cheaper).
        if classifications:
            mask = np.isin(cls, np.asarray(classifications, dtype=np.int16))
            x, y, z, cls = x[mask], y[mask], z[mask], cls[mask]

        if max_points is not None and len(x) > max_points:
            x, y, z, cls = x[:max_points], y[:max_points], z[:max_points], cls[:max_points]

        attrs: dict[str, np.ndarray] = {
            "z": z,
            "classification": cls,
        }
        # Add additional dims when the LAS point format exposes them.
        for dim in ("intensity", "return_number", "number_of_returns",
                    "scan_angle_rank", "scan_angle", "user_data",
                    "point_source_id", "gps_time"):
            if hasattr(las, dim):
                try:
                    arr = np.asarray(getattr(las, dim))
                    if classifications:
                        arr = arr[np.isin(np.asarray(las.classification, dtype=np.int16),
                                          np.asarray(classifications, dtype=np.int16))]
                    if max_points is not None and len(arr) > max_points:
                        arr = arr[:max_points]
                    attrs[dim] = arr
                except Exception:
                    continue

        # Build geometries via shapely.points(x, y, z) — vectorised, fast.
        geoms = shapely.points(x, y, z)
        gdf_out = gpd.GeoDataFrame(attrs, geometry=geoms, crs=crs)
        return gdf_out

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to .las or .laz file."},
                "crs": {
                    "type": ["string", "null"],
                    "description": "CRS to assign (LAS files don't always store one).",
                },
                "max_points": {
                    "type": ["integer", "null"],
                    "minimum": 1,
                    "description": "Cap the number of points loaded (truncates from start).",
                },
                "classifications": {
                    "type": ["array", "null"],
                    "items": {"type": "integer"},
                    "description": "Whitelist of ASPRS class codes (e.g. [2, 6]).",
                },
            },
            "required": ["path"],
        }


# ---------------------------------------------------------------------------
# pointcloud_filter_classification — keep / drop by ASPRS class codes
# ---------------------------------------------------------------------------


@register
class PointcloudFilterClassificationCapability(Capability):
    """Filters point features by ASPRS classification codes.

    Operates on any GeoDataFrame with a ``classification`` integer column.

    Example::

        {"keep": [2, 6]}      # ground + buildings
        {"drop": [7, 18]}     # remove noise points
    """

    name = "pointcloud_filter_classification"
    description = "Filters points by ASPRS classification codes (keep or drop)."

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        keep: list[int] | None = None,
        drop: list[int] | None = None,
        col: str = "classification",
        **_,
    ) -> gpd.GeoDataFrame:
        if keep is None and drop is None:
            raise ValueError(
                "pointcloud_filter_classification requires 'keep' or 'drop'.",
            )
        if keep is not None and drop is not None:
            raise ValueError("Pass either 'keep' or 'drop', not both.")
        if col not in gdf.columns:
            raise KeyError(f"classification column '{col}' not in layer.")
        if gdf.empty:
            return gdf.copy()

        codes = np.asarray(keep if keep is not None else drop, dtype=np.int16)
        mask = gdf[col].isin(codes)
        if drop is not None:
            mask = ~mask
        return gdf[mask].reset_index(drop=True)

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "keep": {
                    "type": ["array", "null"],
                    "items": {"type": "integer"},
                    "description": "ASPRS codes to keep (e.g. [2,6]).",
                },
                "drop": {
                    "type": ["array", "null"],
                    "items": {"type": "integer"},
                    "description": "ASPRS codes to drop (e.g. [7,18]).",
                },
                "col": {
                    "type": "string",
                    "default": "classification",
                    "description": "Column holding the classification code.",
                },
            },
        }


# ---------------------------------------------------------------------------
# pointcloud_zonal_height — per-polygon Z stats (building heights, canopy, ...)
# ---------------------------------------------------------------------------


_ZONAL_STATS = {"min", "max", "mean", "median", "std", "count", "p90", "p95", "p99"}


@register
class PointcloudZonalHeightCapability(Capability):
    """Per-polygon Z statistics from a pointcloud — building height extraction.

    The primary input is the *polygon* layer (e.g. building footprints).
    Points come from ``ref_layer`` and must be a Point Z layer in the same
    CRS (auto-reprojected if not).

    Statistics computed per polygon, prefixed by ``prefix``:
      ``min``, ``max``, ``mean``, ``median``, ``std``, ``count``,
      ``p90``, ``p95``, ``p99``.

    Optional ``ground_col``: when set, height = max(Z) - polygon[ground_col]
    is also added as ``{prefix}height``. Otherwise ``{prefix}height = max - min``
    on the points themselves.

    Example::

        {"ref_layer": "lidar_points", "stats": ["max","p95","count"],
         "prefix": "h_"}
    """

    name = "pointcloud_zonal_height"
    description = (
        "Per-polygon Z statistics from a pointcloud — useful for building "
        "heights, canopy heights, etc."
    )

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        ref_gdf: gpd.GeoDataFrame | None = None,
        stats: list[str] | None = None,
        prefix: str = "z_",
        ground_col: str | None = None,
        **_,
    ) -> gpd.GeoDataFrame:
        if ref_gdf is None or ref_gdf.empty:
            raise ValueError("pointcloud_zonal_height requires a reference layer of points.")
        if gdf.empty:
            return gdf.copy()

        wanted = stats or ["max", "min", "mean", "count"]
        unknown = [s for s in wanted if s not in _ZONAL_STATS]
        if unknown:
            raise ValueError(f"Unknown stat(s) {unknown}. Accepted: {sorted(_ZONAL_STATS)}.")

        # Align CRS for the spatial join.
        if (
            gdf.crs is not None
            and ref_gdf.crs is not None
            and gdf.crs != ref_gdf.crs
        ):
            ref_gdf = ref_gdf.to_crs(gdf.crs)

        # Spatial join: each point gets the index of the polygon containing it.
        polys = gdf.copy()
        polys["_poly_idx"] = polys.index
        joined = gpd.sjoin(
            ref_gdf[[ref_gdf.geometry.name]].assign(
                _z=ref_gdf.geometry.z,
            ),
            polys[["_poly_idx", polys.geometry.name]],
            how="inner",
            predicate="within",
        )

        if joined.empty:
            # No points fell inside any polygon — return polygons with NaN stats.
            result = gdf.copy()
            for s in wanted:
                result[f"{prefix}{s}"] = np.nan
            result[f"{prefix}height"] = np.nan
            return result

        grouped = joined.groupby("_poly_idx")["_z"]
        out_cols: dict[str, pd.Series] = {}
        for s in wanted:
            if s == "min":
                out_cols[s] = grouped.min()
            elif s == "max":
                out_cols[s] = grouped.max()
            elif s == "mean":
                out_cols[s] = grouped.mean()
            elif s == "median":
                out_cols[s] = grouped.median()
            elif s == "std":
                out_cols[s] = grouped.std(ddof=0)
            elif s == "count":
                out_cols[s] = grouped.count()
            elif s == "p90":
                out_cols[s] = grouped.quantile(0.90)
            elif s == "p95":
                out_cols[s] = grouped.quantile(0.95)
            elif s == "p99":
                out_cols[s] = grouped.quantile(0.99)

        stats_df = pd.DataFrame(out_cols).rename(columns=lambda c: f"{prefix}{c}")
        result = gdf.join(stats_df, how="left")

        # Compute height: prefer ground subtraction, fallback to (max - min).
        max_col = f"{prefix}max"
        min_col = f"{prefix}min"
        if ground_col and ground_col in result.columns and max_col in result.columns:
            result[f"{prefix}height"] = result[max_col] - result[ground_col]
        elif max_col in result.columns and min_col in result.columns:
            result[f"{prefix}height"] = result[max_col] - result[min_col]
        return result

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "ref_layer": {
                    "type": "string",
                    "description": "Pointcloud layer alias (resolved to ref_gdf).",
                },
                "stats": {
                    "type": ["array", "null"],
                    "items": {"type": "string", "enum": sorted(_ZONAL_STATS)},
                    "default": ["max", "min", "mean", "count"],
                },
                "prefix": {
                    "type": "string",
                    "default": "z_",
                    "description": "Column-name prefix for output statistics.",
                },
                "ground_col": {
                    "type": ["string", "null"],
                    "description": "Polygon column with ground elevation; height = max - ground.",
                },
            },
        }


# ---------------------------------------------------------------------------
# pointcloud_grid_summary — bin points into a regular polygon grid with Z stats
# ---------------------------------------------------------------------------


@register
class PointcloudGridSummaryCapability(Capability):
    """Bins point Z values into a regular square grid, returns one polygon per cell.

    Each cell carries the requested Z statistics (default: mean, count). Empty
    cells are dropped unless ``drop_empty=False``.

    Example::

        {"cell_size": 1.0, "stats": ["mean", "max", "count"], "drop_empty": true}
    """

    name = "pointcloud_grid_summary"
    description = (
        "Bins pointcloud Z values into a regular grid; returns a polygon layer "
        "with per-cell Z statistics."
    )

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        cell_size: float = 1.0,
        stats: list[str] | None = None,
        drop_empty: bool = True,
        **_,
    ) -> gpd.GeoDataFrame:
        if cell_size <= 0:
            raise ValueError("cell_size must be > 0.")
        if gdf.empty:
            return gpd.GeoDataFrame(
                geometry=[], crs=gdf.crs,
                columns=["row", "col"] + [f"z_{s}" for s in (stats or ["mean", "count"])],
            )

        wanted = stats or ["mean", "count"]
        unknown = [s for s in wanted if s not in _ZONAL_STATS]
        if unknown:
            raise ValueError(f"Unknown stat(s) {unknown}. Accepted: {sorted(_ZONAL_STATS)}.")

        if not gdf.geometry.has_z.all():
            raise ValueError(
                "pointcloud_grid_summary requires 3D points (z coord). "
                "Got at least one 2D geometry — load with z or use add_z first.",
            )

        x = gdf.geometry.x.to_numpy()
        y = gdf.geometry.y.to_numpy()
        z = gdf.geometry.z.to_numpy()

        # Compute cell indices anchored on (xmin, ymin).
        xmin, ymin = float(x.min()), float(y.min())
        col_idx = ((x - xmin) // cell_size).astype(np.int64)
        row_idx = ((y - ymin) // cell_size).astype(np.int64)

        df = pd.DataFrame({"row": row_idx, "col": col_idx, "z": z})
        grouped = df.groupby(["row", "col"])["z"]

        out_cols: dict[str, pd.Series] = {}
        for s in wanted:
            if s == "min":
                out_cols[f"z_{s}"] = grouped.min()
            elif s == "max":
                out_cols[f"z_{s}"] = grouped.max()
            elif s == "mean":
                out_cols[f"z_{s}"] = grouped.mean()
            elif s == "median":
                out_cols[f"z_{s}"] = grouped.median()
            elif s == "std":
                out_cols[f"z_{s}"] = grouped.std(ddof=0)
            elif s == "count":
                out_cols[f"z_{s}"] = grouped.count()
            elif s == "p90":
                out_cols[f"z_{s}"] = grouped.quantile(0.90)
            elif s == "p95":
                out_cols[f"z_{s}"] = grouped.quantile(0.95)
            elif s == "p99":
                out_cols[f"z_{s}"] = grouped.quantile(0.99)

        stats_df = pd.DataFrame(out_cols).reset_index()

        if drop_empty:
            # Empty cells are not present in the groupby — already filtered.
            pass

        # Build cell polygons.
        def _cell(row, col):
            x0 = xmin + col * cell_size
            y0 = ymin + row * cell_size
            return Polygon([
                (x0, y0),
                (x0 + cell_size, y0),
                (x0 + cell_size, y0 + cell_size),
                (x0, y0 + cell_size),
            ])

        geoms = [_cell(r, c) for r, c in zip(stats_df["row"], stats_df["col"])]
        return gpd.GeoDataFrame(stats_df, geometry=geoms, crs=gdf.crs)

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "cell_size": {
                    "type": "number",
                    "exclusiveMinimum": 0.0,
                    "description": "Cell side length in CRS units.",
                },
                "stats": {
                    "type": ["array", "null"],
                    "items": {"type": "string", "enum": sorted(_ZONAL_STATS)},
                    "default": ["mean", "count"],
                },
                "drop_empty": {
                    "type": "boolean",
                    "default": True,
                    "description": "Skip grid cells with no points (default).",
                },
            },
            "required": ["cell_size"],
        }
