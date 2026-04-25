"""Density & tessellation capabilities.

Three building blocks used by urban / retail / crime analyses:

- :class:`KDEHeatmapCapability`     — kernel density estimation. Returns a
  point GeoDataFrame with a ``density`` column on a regular grid (or the
  input points with density values sampled at each centroid when
  ``output='points'``).
- :class:`GridCreateCapability`     — generates a regular rectangular
  fishnet over a polygon envelope.
- :class:`HexGridCreateCapability`  — generates a flat-top hexagonal grid
  over a polygon envelope.

All three compute in *crs_meters* and return the result in the same CRS
as the input (or the explicit target CRS if provided).
"""

from __future__ import annotations

import os
from typing import Any

import geopandas as gpd
import numpy as np
import pandas as pd

from capabilities.base import Capability
from capabilities.registry import register


# ---------------------------------------------------------------------------
# KDE heatmap
# ---------------------------------------------------------------------------


_KDE_KERNELS = {"gaussian", "tophat", "epanechnikov"}


def _kde_max_cells() -> int:
    """Hard cap on KDE grid size — prevents `bandwidth=1000, cell_size=1` style
    blow-ups (4M cells, ~1GB RAM, 15s) reported by beta probe 2026-04-24 v3.
    Tunable via ``GISPULSE_KDE_MAX_CELLS`` env var, default 1_000_000.
    """
    try:
        return int(os.environ.get("GISPULSE_KDE_MAX_CELLS", "1000000"))
    except ValueError:
        return 1_000_000


@register
class KDEHeatmapCapability(Capability):
    """Kernel density estimation on a regular grid of points."""

    name = "kde_heatmap"
    description = (
        "Samples a kernel density estimate on a regular grid — returns "
        "points labelled with a 'density' column (intensity per unit area)."
    )

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        bandwidth: float = 100.0,
        cell_size: float = 50.0,
        kernel: str = "gaussian",
        weight_field: str | None = None,
        crs_meters: str = "EPSG:3857",
        **_,
    ) -> gpd.GeoDataFrame:
        """
        Args:
            gdf:          Input point GeoDataFrame (or centroids are used).
            bandwidth:    Kernel bandwidth in *crs_meters* units.
            cell_size:    Output grid step in *crs_meters* units.
            kernel:       'gaussian' / 'tophat' / 'epanechnikov'.
            weight_field: Optional numeric column — sample weights.
            crs_meters:   Metric CRS used for computation and output.

        Returns:
            GeoDataFrame of point geometries on a regular grid, each
            carrying a ``density`` column in /m² units (unweighted counts).
        """
        from sklearn.neighbors import KernelDensity
        from shapely.geometry import Point

        if bandwidth <= 0:
            raise ValueError("bandwidth must be > 0.")
        if cell_size <= 0:
            raise ValueError("cell_size must be > 0.")
        if kernel not in _KDE_KERNELS:
            raise ValueError(
                f"Invalid kernel '{kernel}'. Expected {sorted(_KDE_KERNELS)}."
            )
        if gdf.empty:
            return gpd.GeoDataFrame(
                {"density": pd.Series([], dtype=float), "geometry": []},
                crs=gdf.crs,
            )

        work = gdf.to_crs(crs_meters) if gdf.crs is not None else gdf.copy()
        pts = np.column_stack([
            work.geometry.centroid.x.to_numpy(),
            work.geometry.centroid.y.to_numpy(),
        ])

        sample_weight: np.ndarray | None = None
        if weight_field:
            if weight_field not in work.columns:
                raise ValueError(f"weight_field '{weight_field}' not found.")
            w = pd.to_numeric(work[weight_field], errors="coerce").to_numpy(dtype=float)
            if np.isnan(w).any():
                raise ValueError(f"weight_field '{weight_field}' has NaNs.")
            sample_weight = w

        kde = KernelDensity(bandwidth=bandwidth, kernel=kernel)
        kde.fit(pts, sample_weight=sample_weight)

        minx, miny, maxx, maxy = work.total_bounds
        # Pad by one bandwidth on each side so the edge is represented
        minx -= bandwidth
        miny -= bandwidth
        maxx += bandwidth
        maxy += bandwidth
        xs = np.arange(minx, maxx + cell_size, cell_size)
        ys = np.arange(miny, maxy + cell_size, cell_size)

        max_cells = _kde_max_cells()
        n_cells = len(xs) * len(ys)
        if n_cells > max_cells:
            raise ValueError(
                f"kde_heatmap grid would have {n_cells:,} cells "
                f"(> GISPULSE_KDE_MAX_CELLS={max_cells:,}). "
                f"Increase cell_size or reduce bandwidth — current "
                f"bandwidth={bandwidth} cell_size={cell_size} bounds-area="
                f"{(maxx - minx) * (maxy - miny):.0f}.",
            )

        xx, yy = np.meshgrid(xs, ys)
        grid = np.column_stack([xx.ravel(), yy.ravel()])

        log_density = kde.score_samples(grid)
        density = np.exp(log_density)

        points = [Point(x, y) for x, y in grid]
        out = gpd.GeoDataFrame(
            {"density": density, "geometry": points},
            crs=crs_meters,
        )
        if gdf.crs is not None and str(gdf.crs) != crs_meters:
            out = out.to_crs(gdf.crs)
        return out

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "bandwidth": {
                    "type": "number",
                    "minimum": 0,
                    "default": 100.0,
                    "description": "Kernel bandwidth (crs_meters).",
                },
                "cell_size": {
                    "type": "number",
                    "minimum": 0,
                    "default": 50.0,
                    "description": "Output grid step (crs_meters).",
                },
                "kernel": {
                    "type": "string",
                    "enum": sorted(_KDE_KERNELS),
                    "default": "gaussian",
                },
                "weight_field": {"type": ["string", "null"]},
                "crs_meters": {"type": "string", "default": "EPSG:3857"},
            },
        }


# ---------------------------------------------------------------------------
# Regular grids
# ---------------------------------------------------------------------------


def _bounds_for_grid(
    extent: gpd.GeoDataFrame | None,
    bounds: tuple[float, float, float, float] | None,
    crs_meters: str,
) -> tuple[tuple[float, float, float, float], Any]:
    """Return ((minx, miny, maxx, maxy), out_crs)."""
    if extent is not None and not extent.empty:
        work = extent.to_crs(crs_meters) if extent.crs is not None else extent
        return tuple(work.total_bounds), extent.crs
    if bounds is None:
        raise ValueError(
            "One of 'extent_gdf' (via ref_layer) or 'bounds' must be provided."
        )
    return tuple(bounds), crs_meters


@register
class GridCreateCapability(Capability):
    """Regular rectangular fishnet over an extent."""

    name = "grid_create"
    description = (
        "Creates a regular fishnet (square cells) over the envelope of a "
        "reference layer or an explicit bounds tuple."
    )

    def execute(
        self,
        gdf: gpd.GeoDataFrame | None = None,  # ignored; kept for uniform signature
        ref_gdf: gpd.GeoDataFrame | None = None,
        bounds: tuple[float, float, float, float] | None = None,
        cell_size: float = 100.0,
        crs_meters: str = "EPSG:3857",
        clip_to_extent: bool = True,
        **_,
    ) -> gpd.GeoDataFrame:
        """
        Args:
            gdf:            Ignored; upstream can pipe anything as input.
            ref_gdf:        Reference layer whose envelope drives the grid.
            bounds:         Alternative: explicit (minx, miny, maxx, maxy) in
                            *crs_meters*.
            cell_size:      Cell size in *crs_meters* units.
            crs_meters:     Metric CRS used for computation.
            clip_to_extent: If True and ref_gdf is set, keep only cells that
                            intersect the reference geometry (not just its
                            bbox).
        """
        from shapely.geometry import Polygon

        if cell_size <= 0:
            raise ValueError("cell_size must be > 0.")

        (minx, miny, maxx, maxy), out_crs = _bounds_for_grid(ref_gdf, bounds, crs_meters)

        xs = np.arange(minx, maxx, cell_size)
        ys = np.arange(miny, maxy, cell_size)

        polys: list[Polygon] = []
        rows: list[dict[str, Any]] = []
        for row, y in enumerate(ys):
            for col, x in enumerate(xs):
                polys.append(
                    Polygon([
                        (x, y),
                        (x + cell_size, y),
                        (x + cell_size, y + cell_size),
                        (x, y + cell_size),
                    ])
                )
                rows.append({"row": int(row), "col": int(col)})

        out = gpd.GeoDataFrame(rows, geometry=polys, crs=crs_meters)

        if clip_to_extent and ref_gdf is not None and not ref_gdf.empty:
            ext = ref_gdf.to_crs(crs_meters) if ref_gdf.crs is not None else ref_gdf
            ext_union = ext.geometry.union_all()
            out = out[out.intersects(ext_union)].reset_index(drop=True)

        if out_crs is not None and str(out_crs) != crs_meters:
            out = out.to_crs(out_crs)
        return out

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "ref_layer": {
                    "type": ["string", "null"],
                    "description": "Reference layer name (envelope used).",
                },
                "bounds": {
                    "type": ["array", "null"],
                    "items": {"type": "number"},
                    "minItems": 4,
                    "maxItems": 4,
                    "description": "(minx, miny, maxx, maxy) in crs_meters.",
                },
                "cell_size": {
                    "type": "number",
                    "minimum": 0,
                    "default": 100.0,
                },
                "crs_meters": {"type": "string", "default": "EPSG:3857"},
                "clip_to_extent": {"type": "boolean", "default": True},
            },
            "required": ["cell_size"],
        }


@register
class HexGridCreateCapability(Capability):
    """Flat-top hexagonal grid over an extent."""

    name = "hexgrid_create"
    description = (
        "Creates a flat-top hexagonal grid over the envelope of a reference "
        "layer or an explicit bounds tuple."
    )

    def execute(
        self,
        gdf: gpd.GeoDataFrame | None = None,
        ref_gdf: gpd.GeoDataFrame | None = None,
        bounds: tuple[float, float, float, float] | None = None,
        cell_size: float = 100.0,
        crs_meters: str = "EPSG:3857",
        clip_to_extent: bool = True,
        **_,
    ) -> gpd.GeoDataFrame:
        """
        Args:
            gdf:            Ignored.
            ref_gdf:        Reference layer whose envelope drives the grid.
            bounds:         Alternative explicit envelope in *crs_meters*.
            cell_size:      Hex **edge length** in *crs_meters*.
            crs_meters:     Metric CRS.
            clip_to_extent: If True and ref_gdf is set, keep only cells that
                            intersect the reference geometry.
        """
        from shapely.geometry import Polygon

        if cell_size <= 0:
            raise ValueError("cell_size must be > 0.")

        (minx, miny, maxx, maxy), out_crs = _bounds_for_grid(ref_gdf, bounds, crs_meters)

        # Flat-top hex: width = 2 * size, height = sqrt(3) * size
        # Column step = 1.5 * size, row step = sqrt(3) * size.
        w = 2.0 * cell_size
        h = np.sqrt(3.0) * cell_size
        col_step = 1.5 * cell_size
        row_step = h

        rows_out: list[dict[str, Any]] = []
        polys: list[Polygon] = []
        row = 0
        y = miny
        while y < maxy + row_step:
            col = 0
            x = minx
            if row % 2 == 1:
                x += col_step * 0.5  # offset odd rows
                y_cell = y + row_step * 0.5
            else:
                y_cell = y
            while x < maxx + col_step:
                cx = x + cell_size
                cy = y_cell + h / 2
                hex_coords = [
                    (cx + cell_size * np.cos(a), cy + cell_size * np.sin(a))
                    for a in np.linspace(0, 2 * np.pi, 7)[:-1]
                ]
                polys.append(Polygon(hex_coords))
                rows_out.append({"row": int(row), "col": int(col)})
                col += 1
                x += col_step
            row += 1
            y += row_step

        out = gpd.GeoDataFrame(rows_out, geometry=polys, crs=crs_meters)

        if clip_to_extent and ref_gdf is not None and not ref_gdf.empty:
            ext = ref_gdf.to_crs(crs_meters) if ref_gdf.crs is not None else ref_gdf
            ext_union = ext.geometry.union_all()
            out = out[out.intersects(ext_union)].reset_index(drop=True)

        if out_crs is not None and str(out_crs) != crs_meters:
            out = out.to_crs(out_crs)
        return out

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "ref_layer": {"type": ["string", "null"]},
                "bounds": {
                    "type": ["array", "null"],
                    "items": {"type": "number"},
                    "minItems": 4,
                    "maxItems": 4,
                },
                "cell_size": {
                    "type": "number",
                    "minimum": 0,
                    "default": 100.0,
                    "description": "Hex edge length (crs_meters).",
                },
                "crs_meters": {"type": "string", "default": "EPSG:3857"},
                "clip_to_extent": {"type": "boolean", "default": True},
            },
            "required": ["cell_size"],
        }
