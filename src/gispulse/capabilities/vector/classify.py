from __future__ import annotations


import geopandas as gpd
import pandas as pd

from gispulse.capabilities.base import Capability
from gispulse.capabilities.registry import register



@register
class ClassifyByRingCapability(Capability):
    """Classifies each feature by the smallest containing ring from a stack
    of reference ring layers.

    Shortcut for the concentric-accessibility pattern (e.g. isochrones →
    bands). Given N ring layers sharing a numeric ``ring_field`` (default
    ``cost_budget``), each feature gets:

    - ``value_col`` (default ``ring_value``): smallest ring value from any
      intersecting ring, or ``outside_value`` (default ``99999``) when the
      feature falls outside every ring.
    - ``class_col`` (default ``ring_class``): 1-indexed class, inner ring = 1,
      ``outside`` = N + 1.
    - ``color_col`` (default ``ring_color``): hex color from ``palette``
      (named palette or list of N + 1 hex codes).

    Replaces the 4-step chain ``merge_layers`` → ``spatial_aggregate`` (min)
    → ``calculate`` (fill NaN) → ``classify`` (manual breaks) with a single
    step whose output palette index aligns naturally with ring order.

    Example::

        {"capability": "classify_by_ring",
         "params": {"ref_layers": ["iso_500", "iso_750", "iso_1000", "iso_1500"],
                    "palette": ["#1a9850", "#fee08b", "#fdae61",
                                "#f46d43", "#a50026"]}}
    """

    name = "classify_by_ring"
    description = (
        "Classifies each feature by the smallest containing ring from a list "
        "of ref_layers. Emits value + class + color columns in one step."
    )

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        ref_gdfs: list[gpd.GeoDataFrame] | None = None,
        ring_field: str = "cost_budget",
        outside_value: float = 99999.0,
        class_col: str = "ring_class",
        color_col: str = "ring_color",
        value_col: str = "ring_value",
        palette: str | list[str] | None = None,
        use_centroid: bool = False,
        ring_simplify_tolerance: float = 0.0,
        **_,
    ) -> gpd.GeoDataFrame:
        if not ref_gdfs:
            raise ValueError(
                "classify_by_ring requires 'ref_layers' (list of ring layers)"
            )

        target_crs = gdf.crs
        rings: list[gpd.GeoDataFrame] = []
        for extra in ref_gdfs:
            if extra is None or len(extra) == 0:
                continue
            if ring_field not in extra.columns:
                raise ValueError(
                    f"classify_by_ring: ref layer missing '{ring_field}' field"
                )
            if target_crs is not None and extra.crs is not None and extra.crs != target_crs:
                extra = extra.to_crs(target_crs)
            rings.append(extra[[ring_field, extra.geometry.name]])

        if not rings:
            raise ValueError("classify_by_ring: no non-empty ref layers")

        merged = pd.concat(rings, ignore_index=True)
        merged_rings = gpd.GeoDataFrame(
            merged, geometry=rings[0].geometry.name, crs=target_crs
        )

        # Cheap perf knob: ring polygons coming out of `isochrone` with
        # `cost_budgets` are unions of thousands of buffered road edges and
        # carry boundaries with 10 000+ vertices each. Simplifying before sjoin
        # cuts the per-pair intersects cost without changing class assignment
        # at the metric tolerance (typically 5-20 m for urban networks).
        if ring_simplify_tolerance > 0:
            merged_rings = merged_rings.copy()
            merged_rings.geometry = merged_rings.geometry.simplify(
                ring_simplify_tolerance, preserve_topology=True
            )

        # `use_centroid` swaps the polygon-vs-polygon `intersects` for a
        # point-in-polygon `within` query on building centroids — orders of
        # magnitude faster when input geometries are small footprints (e.g.
        # buildings) and rings are huge multi-vertex polygons (isochrones,
        # buffers). Trade: a building straddling a ring boundary classifies by
        # the ring its centroid lands in, not the strict smallest-containing.
        if use_centroid:
            import warnings

            geom_col = gdf.geometry.name
            left = gdf[[geom_col]].copy()
            with warnings.catch_warnings():
                # The geographic-CRS centroid warning is benign here: we use
                # the centroid only as a positional probe for sjoin, never for
                # distance or area math. Suppress it so the (intentional) opt-in
                # doesn't drown user logs.
                warnings.filterwarnings(
                    "ignore",
                    message="Geometry is in a geographic CRS",
                    category=UserWarning,
                )
                left.geometry = left.geometry.centroid
            predicate = "within"
        else:
            left = gdf[[gdf.geometry.name]]
            predicate = "intersects"

        # sjoin preserves the left (gdf) index on matched rows; groupby on
        # that index gives the smallest ring value per input feature.
        joined = gpd.sjoin(
            left,
            merged_rings,
            how="left",
            predicate=predicate,
        )
        smallest = joined.groupby(joined.index)[ring_field].min()

        out = gdf.copy()
        out[value_col] = out.index.to_series().map(smallest).fillna(outside_value)

        sorted_vals = sorted(
            {float(v) for v in merged_rings[ring_field].dropna().unique()}
        )
        value_to_class = {v: i + 1 for i, v in enumerate(sorted_vals)}
        outside_class = len(sorted_vals) + 1

        def _klass(v: float) -> int:
            return value_to_class.get(float(v), outside_class)

        out[class_col] = out[value_col].map(_klass).astype("Int64")

        if palette is not None:
            from gispulse.capabilities.palettes import resolve_palette

            resolved = resolve_palette(palette, outside_class) or []
            if len(resolved) < outside_class:
                raise ValueError(
                    f"classify_by_ring: palette needs {outside_class} colors, "
                    f"got {len(resolved)}"
                )
            color_of = {i + 1: resolved[i] for i in range(outside_class)}
            out[color_col] = out[class_col].map(color_of)

        return out

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "ref_layers": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Aliases of the ring layers (order-independent; "
                        "smallest ring_field value wins)."
                    ),
                },
                "ring_field": {
                    "type": "string",
                    "default": "cost_budget",
                    "description": "Numeric field in ring layers identifying each ring.",
                },
                "outside_value": {
                    "type": "number",
                    "default": 99999.0,
                    "description": "Value assigned to features outside every ring.",
                },
                "class_col": {"type": "string", "default": "ring_class"},
                "color_col": {"type": "string", "default": "ring_color"},
                "value_col": {"type": "string", "default": "ring_value"},
                "palette": {
                    "type": ["string", "array", "null"],
                    "items": {"type": "string"},
                    "description": (
                        "Palette name (YlOrRd…) or list of hex colors; "
                        "length = N rings + 1 (outside)."
                    ),
                },
                "use_centroid": {
                    "type": "boolean",
                    "default": False,
                    "description": (
                        "When True, classify each input feature by its centroid "
                        "instead of polygon intersects. Orders of magnitude "
                        "faster when rings are huge multi-vertex polygons "
                        "(e.g. isochrones) and inputs are small footprints "
                        "(buildings, parcels)."
                    ),
                },
                "ring_simplify_tolerance": {
                    "type": "number",
                    "default": 0.0,
                    "description": (
                        "Geometric simplification tolerance applied to the ring "
                        "polygons before the join (CRS units, typically meters "
                        "in EPSG:2154). 0 disables. 5-20 m is enough to drop "
                        "tens of thousands of vertices on isochrone rings "
                        "without shifting class boundaries visibly."
                    ),
                },
            },
        }

