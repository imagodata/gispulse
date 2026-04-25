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
# Force geometry type — convert between Multi*/single, Point/LineString, etc.
# ---------------------------------------------------------------------------


_SINGLE_TO_MULTI = {
    "Point": "MultiPoint",
    "LineString": "MultiLineString",
    "Polygon": "MultiPolygon",
}
_VALID_TARGETS = {
    "Point", "MultiPoint",
    "LineString", "MultiLineString",
    "Polygon", "MultiPolygon",
    "GeometryCollection",
}


@register
class ForceGeometryTypeCapability(Capability):
    """Coerces all geometries to a target type (Multi/single, single/multi).

    Promotion (Point → MultiPoint, etc.) wraps each geometry in its Multi*
    counterpart. Demotion (Multi* → single) explodes Multi* features into
    multiple rows when ``on_multi='explode'`` (default). With
    ``on_multi='first'``, only the first child is kept.

    Unsupported coercions (e.g. Polygon → Point) raise unless
    ``on_invalid='drop'`` (silently drop rows) or ``'skip'`` (leave geometry
    unchanged).

    Example::

        {"target": "MultiPolygon"}
        {"target": "Polygon", "on_multi": "explode"}
    """

    name = "force_geometry_type"
    description = (
        "Coerces all geometries to a target type (Multi/single promotion or "
        "demotion via explode)."
    )

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        target: str = "",
        on_multi: str = "explode",
        on_invalid: str = "raise",
        **_,
    ) -> gpd.GeoDataFrame:
        from shapely.geometry import (
            MultiLineString,
            MultiPoint,
            MultiPolygon,
        )

        if not target:
            raise ValueError("force_geometry_type requires 'target'.")
        if target not in _VALID_TARGETS:
            raise ValueError(f"Unsupported target '{target}'. Use one of {sorted(_VALID_TARGETS)}.")
        if on_multi not in {"explode", "first"}:
            raise ValueError("on_multi must be 'explode' or 'first'.")
        if on_invalid not in {"raise", "drop", "skip"}:
            raise ValueError("on_invalid must be 'raise', 'drop', or 'skip'.")
        if gdf.empty:
            return gdf.copy()

        from shapely.geometry import GeometryCollection

        geom_col = gdf.geometry.name
        is_multi_target = target.startswith("Multi")
        is_collection_target = target == "GeometryCollection"
        single_form = target[5:] if is_multi_target else target  # e.g. "Polygon"
        multi_form = _SINGLE_TO_MULTI.get(single_form)

        # Empty multi-counterparts used to coerce empty inputs cleanly when
        # promoting (avoids the layer claiming a Multi* type but holding a
        # singular EMPTY geometry — bug P1-1 from the 2026-04-24 beta-test).
        _EMPTY_MULTI = {
            "MultiPoint": MultiPoint(),
            "MultiLineString": MultiLineString(),
            "MultiPolygon": MultiPolygon(),
            "GeometryCollection": GeometryCollection(),
        }

        skipped_count = 0
        rows: list[dict] = []
        for _, row in gdf.iterrows():
            geom = row.geometry
            if geom is None or geom.is_empty:
                if on_invalid == "drop":
                    continue
                # Promote empties to the target's empty-multi so the resulting
                # layer is geometry-type pure.
                if is_multi_target or is_collection_target:
                    new_row = row.to_dict()
                    new_row[geom_col] = _EMPTY_MULTI[target]
                    rows.append(new_row)
                else:
                    rows.append(row.to_dict())
                continue
            gtype = geom.geom_type

            # GeometryCollection target: wrap singletons, pass through collections.
            if is_collection_target:
                new_row = row.to_dict()
                new_row[geom_col] = (
                    geom if gtype == "GeometryCollection" else GeometryCollection([geom])
                )
                rows.append(new_row)
                continue

            base_compatible = gtype == single_form or gtype == multi_form
            if not base_compatible:
                if on_invalid == "raise":
                    raise ValueError(
                        f"Cannot coerce {gtype} to {target}. "
                        f"Use on_invalid='drop' or 'skip' to bypass.",
                    )
                if on_invalid == "drop":
                    continue
                # 'skip' — leave the geometry untouched but flag for warning.
                skipped_count += 1
                rows.append(row.to_dict())
                continue

            if is_multi_target:
                # Promote single → multi (or pass-through if already multi).
                if gtype == multi_form or gtype == target:
                    rows.append(row.to_dict())
                else:
                    if gtype == "Point":
                        new_geom = MultiPoint([geom])
                    elif gtype == "LineString":
                        new_geom = MultiLineString([geom])
                    elif gtype == "Polygon":
                        new_geom = MultiPolygon([geom])
                    else:
                        # Should be unreachable thanks to base_compatible check.
                        rows.append(row.to_dict())
                        continue
                    new_row = row.to_dict()
                    new_row[geom_col] = new_geom
                    rows.append(new_row)
            else:
                # Demote multi → single.
                if gtype == single_form:
                    rows.append(row.to_dict())
                    continue
                children = list(geom.geoms)
                if not children:
                    if on_invalid != "drop":
                        rows.append(row.to_dict())
                    continue
                if on_multi == "first":
                    new_row = row.to_dict()
                    new_row[geom_col] = children[0]
                    rows.append(new_row)
                else:  # explode
                    for child in children:
                        new_row = row.to_dict()
                        new_row[geom_col] = child
                        rows.append(new_row)

        # P1-2: warn loudly when on_invalid='skip' left wrong-type geoms in
        # a layer that nominally is a single type — silent passthrough used
        # to be a footgun for downstream GPKG writers.
        if skipped_count > 0:
            import warnings as _warnings
            _warnings.warn(
                f"force_geometry_type: {skipped_count} feature(s) skipped — "
                f"output layer is not geometry-pure ({target}).",
                UserWarning,
                stacklevel=2,
            )

        if not rows:
            return gpd.GeoDataFrame(columns=list(gdf.columns), geometry=geom_col, crs=gdf.crs)
        return gpd.GeoDataFrame(rows, geometry=geom_col, crs=gdf.crs)

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "enum": sorted(_VALID_TARGETS),
                    "description": "Target geometry type.",
                },
                "on_multi": {
                    "type": "string",
                    "enum": ["explode", "first"],
                    "default": "explode",
                    "description": "When demoting Multi* → single: explode all parts or keep the first only.",
                },
                "on_invalid": {
                    "type": "string",
                    "enum": ["raise", "drop", "skip"],
                    "default": "raise",
                    "description": "How to handle geometries that cannot be coerced.",
                },
            },
            "required": ["target"],
        }
