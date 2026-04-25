"""Geometry transform capabilities — affine, swap_xy, reverse_lines, Z/M dimension ops.

These capabilities transform geometries (coordinates, dimensionality,
orientation) without changing the schema or row count.
"""

from __future__ import annotations

import math

import geopandas as gpd
import pandas as pd
import shapely
from shapely import affinity as _affinity

from capabilities.base import Capability
from capabilities.registry import register


# ---------------------------------------------------------------------------
# Affine transform — translate / rotate / scale / skew
# ---------------------------------------------------------------------------


@register
class AffineTransformCapability(Capability):
    """Applies translate / rotate / scale / skew to all geometries.

    Operations are applied in order: ``translate``, then ``rotate``, then
    ``scale``, then ``skew``. ``rotate`` and ``skew`` are in degrees;
    ``origin`` is a (x, y) tuple, the keyword ``"center"``, or
    ``"centroid"``.

    Example::

        {"translate": [100.0, 50.0], "rotate": 45, "origin": "center"}
        {"scale": [2.0, 2.0], "origin": "centroid"}
    """

    name = "affine_transform"
    description = "Translate, rotate, scale, skew geometries (in declared order)."

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        translate: list[float] | tuple[float, float] | None = None,
        rotate: float | None = None,
        scale: list[float] | tuple[float, float] | None = None,
        skew: list[float] | tuple[float, float] | None = None,
        origin: str | list[float] | tuple[float, float] = "center",
        **_,
    ) -> gpd.GeoDataFrame:
        if gdf.empty:
            return gdf.copy()
        if all(v is None for v in (translate, rotate, scale, skew)):
            raise ValueError(
                "affine_transform requires at least one of translate/rotate/scale/skew.",
            )

        origin_arg = origin
        if isinstance(origin, list):
            origin_arg = tuple(origin)

        result = gdf.copy()
        geom = result.geometry

        if translate is not None:
            tx, ty = float(translate[0]), float(translate[1])
            geom = geom.apply(lambda g: _affinity.translate(g, xoff=tx, yoff=ty))
        if rotate is not None:
            angle = float(rotate)
            geom = geom.apply(lambda g: _affinity.rotate(g, angle, origin=origin_arg))
        if scale is not None:
            sx, sy = float(scale[0]), float(scale[1])
            geom = geom.apply(lambda g: _affinity.scale(g, xfact=sx, yfact=sy, origin=origin_arg))
        if skew is not None:
            xs, ys = float(skew[0]), float(skew[1])
            geom = geom.apply(lambda g: _affinity.skew(g, xs=xs, ys=ys, origin=origin_arg))

        result["geometry"] = geom
        return result

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "translate": {
                    "type": ["array", "null"],
                    "items": {"type": "number"},
                    "minItems": 2,
                    "maxItems": 2,
                    "description": "[dx, dy] translation in CRS units.",
                },
                "rotate": {
                    "type": ["number", "null"],
                    "description": "Rotation angle in degrees (counter-clockwise).",
                },
                "scale": {
                    "type": ["array", "null"],
                    "items": {"type": "number"},
                    "minItems": 2,
                    "maxItems": 2,
                    "description": "[sx, sy] scale factors.",
                },
                "skew": {
                    "type": ["array", "null"],
                    "items": {"type": "number"},
                    "minItems": 2,
                    "maxItems": 2,
                    "description": "[xs, ys] skew angles in degrees.",
                },
                "origin": {
                    "type": ["string", "array"],
                    "description": "'center', 'centroid', or [x, y] reference point.",
                    "default": "center",
                },
            },
        }


# ---------------------------------------------------------------------------
# Swap XY — flip axis order (fix WGS84 lat/lon vs lon/lat issues)
# ---------------------------------------------------------------------------


@register
class SwapXYCapability(Capability):
    """Swaps the X and Y coordinates of every geometry.

    Useful to repair layers digitised with the wrong axis order
    (latitude-longitude vs longitude-latitude).

    Example::

        {}
    """

    name = "swap_xy"
    description = "Swaps the X and Y coordinates of every geometry."

    def execute(self, gdf: gpd.GeoDataFrame, **_) -> gpd.GeoDataFrame:
        if gdf.empty:
            return gdf.copy()
        result = gdf.copy()
        result["geometry"] = shapely.transform(
            result.geometry.to_numpy(),
            _swap_xy_coords,
            include_z=False,
        )
        return result

    def get_schema(self) -> dict:
        return {"type": "object", "properties": {}}


def _swap_xy_coords(coords):
    coords = coords.copy()
    coords[..., [0, 1]] = coords[..., [1, 0]]
    return coords


# ---------------------------------------------------------------------------
# Reverse lines — flip the direction of LineStrings / MultiLineStrings
# ---------------------------------------------------------------------------


@register
class ReverseLinesCapability(Capability):
    """Reverses the vertex order of LineString / MultiLineString features.

    Non-line geometries are passed through unchanged unless
    ``ignore_non_lines=False`` (then a TypeError is raised).

    Example::

        {"ignore_non_lines": true}
    """

    name = "reverse_lines"
    description = "Reverses the vertex order of LineString/MultiLineString features."

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        ignore_non_lines: bool = True,
        **_,
    ) -> gpd.GeoDataFrame:
        from shapely.geometry import LineString, MultiLineString

        if gdf.empty:
            return gdf.copy()
        result = gdf.copy()
        new_geoms = []
        for g in result.geometry:
            if g is None or g.is_empty:
                new_geoms.append(g)
                continue
            if isinstance(g, LineString):
                new_geoms.append(LineString(list(g.coords)[::-1]))
            elif isinstance(g, MultiLineString):
                new_geoms.append(
                    MultiLineString([LineString(list(line.coords)[::-1]) for line in g.geoms]),
                )
            else:
                if not ignore_non_lines:
                    raise TypeError(
                        f"reverse_lines does not support {g.geom_type}. "
                        f"Set ignore_non_lines=true to skip silently.",
                    )
                new_geoms.append(g)
        result["geometry"] = new_geoms
        return result

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "ignore_non_lines": {
                    "type": "boolean",
                    "default": True,
                    "description": "Pass through non-line geometries instead of raising.",
                },
            },
        }


# ---------------------------------------------------------------------------
# Z / M dimension ops
# ---------------------------------------------------------------------------


@register
class AddZCapability(Capability):
    """Adds a Z dimension to all geometries (constant or from a column).

    Pass either ``z`` (constant) or ``from_column`` (per-feature value).

    Example::

        {"z": 0.0}
        {"from_column": "altitude"}
    """

    name = "add_z"
    description = "Adds a Z dimension to all geometries (constant or column-driven)."

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        z: float | None = None,
        from_column: str | None = None,
        **_,
    ) -> gpd.GeoDataFrame:
        if z is None and from_column is None:
            raise ValueError("add_z requires 'z' or 'from_column'.")
        if z is not None and from_column is not None:
            raise ValueError("add_z takes either 'z' or 'from_column', not both.")
        if from_column is not None and from_column not in gdf.columns:
            raise KeyError(f"from_column '{from_column}' not in layer.")
        if gdf.empty:
            return gdf.copy()

        result = gdf.copy()
        # NaN / None / pd.NA all coerce to 0.0 — shapely 2.x rejects NaN Z literals.
        z_values = (
            [float(z)] * len(result) if z is not None
            else [0.0 if pd.isna(v) else float(v) for v in result[from_column]]
        )
        new_geoms = []
        for geom, zval in zip(result.geometry, z_values):
            if geom is None or geom.is_empty:
                new_geoms.append(geom)
                continue
            new_geoms.append(_force_z(geom, zval))
        result["geometry"] = new_geoms
        return result

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "z": {
                    "type": ["number", "null"],
                    "description": "Constant Z value applied to every geometry.",
                },
                "from_column": {
                    "type": ["string", "null"],
                    "description": (
                        "Column providing the per-feature Z value. "
                        "NaN / null entries are coerced to 0.0 (shapely rejects NaN Z)."
                    ),
                },
            },
        }


@register
class DropZCapability(Capability):
    """Strips the Z dimension from all geometries.

    Example::

        {}
    """

    name = "drop_z"
    description = "Strips the Z dimension from every geometry (returns 2D layer)."

    def execute(self, gdf: gpd.GeoDataFrame, **_) -> gpd.GeoDataFrame:
        if gdf.empty:
            return gdf.copy()
        result = gdf.copy()
        result["geometry"] = shapely.force_2d(result.geometry.to_numpy())
        return result

    def get_schema(self) -> dict:
        return {"type": "object", "properties": {}}


@register
class AddMCapability(Capability):
    """Adds an M (measure) dimension to all geometries.

    Shapely 2.x supports M values via ``set_coordinates`` on (X, Y, Z, M)
    arrays. Geometries without Z get Z=0 set as a side-effect.

    Example::

        {"m": 0.0}
        {"from_column": "chainage"}
    """

    name = "add_m"
    description = "Adds an M (measure) dimension to all geometries."

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        m: float | None = None,
        from_column: str | None = None,
        **_,
    ) -> gpd.GeoDataFrame:
        if m is None and from_column is None:
            raise ValueError("add_m requires 'm' or 'from_column'.")
        if m is not None and from_column is not None:
            raise ValueError("add_m takes either 'm' or 'from_column', not both.")
        if from_column is not None and from_column not in gdf.columns:
            raise KeyError(f"from_column '{from_column}' not in layer.")
        if gdf.empty:
            return gdf.copy()

        result = gdf.copy()
        m_values = (
            [float(m)] * len(result) if m is not None
            else [0.0 if pd.isna(v) else float(v) for v in result[from_column]]
        )
        new_geoms = []
        for geom, mval in zip(result.geometry, m_values):
            if geom is None or geom.is_empty:
                new_geoms.append(geom)
                continue
            new_geoms.append(_set_constant_m(geom, mval))
        result["geometry"] = new_geoms
        return result

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "m": {
                    "type": ["number", "null"],
                    "description": "Constant M value applied to every geometry.",
                },
                "from_column": {
                    "type": ["string", "null"],
                    "description": "Column providing the per-feature M value.",
                },
            },
        }


@register
class DropMCapability(Capability):
    """Strips the M dimension from all geometries.

    Example::

        {}
    """

    name = "drop_m"
    description = "Strips the M dimension from every geometry."

    def execute(self, gdf: gpd.GeoDataFrame, **_) -> gpd.GeoDataFrame:
        if gdf.empty:
            return gdf.copy()
        result = gdf.copy()
        # shapely 2.x: dropping M is done by re-building without the 4th coord
        # via shapely.transform with include_m=False (where supported) or the
        # safer get_coordinates / set_coordinates dance below.
        new_geoms = []
        for geom in result.geometry:
            if geom is None or geom.is_empty:
                new_geoms.append(geom)
                continue
            new_geoms.append(_strip_m(geom))
        result["geometry"] = new_geoms
        return result

    def get_schema(self) -> dict:
        return {"type": "object", "properties": {}}


# ---------------------------------------------------------------------------
# Helpers — Z / M coordinate manipulation
# ---------------------------------------------------------------------------


_M_SUPPORTED_TYPES = {"Point", "LineString", "MultiPoint", "MultiLineString"}


def _force_z(geom, z: float):
    """Return a copy of *geom* with constant Z value (preserving M when present)."""
    return shapely.force_3d(geom, z=z)


def _set_constant_m(geom, m: float):
    """Return a copy of *geom* with M coords set to a constant value.

    shapely 2.x has no in-place setter for M, so we rebuild via WKT. Polygon
    M support is not implemented (rare in practice; raises NotImplementedError
    when encountered).
    """
    gtype = geom.geom_type
    if gtype not in _M_SUPPORTED_TYPES:
        raise NotImplementedError(
            f"add_m/drop_m support is currently limited to {sorted(_M_SUPPORTED_TYPES)}. "
            f"Got {gtype}.",
        )
    has_z = shapely.has_z(geom)
    return shapely.from_wkt(_geom_to_wkt_with_m(geom, m, include_z=has_z))


def _strip_m(geom):
    """Return a copy of *geom* without the M dimension."""
    if not shapely.has_m(geom):
        return geom
    gtype = geom.geom_type
    if gtype not in _M_SUPPORTED_TYPES:
        raise NotImplementedError(
            f"drop_m support is currently limited to {sorted(_M_SUPPORTED_TYPES)}. "
            f"Got {gtype}.",
        )
    if shapely.has_z(geom):
        return shapely.force_3d(geom)
    return shapely.force_2d(geom)


def _coords_with_m(coords_iter, m: float, include_z: bool) -> str:
    """Render coords as a space-separated WKT chunk with M (and optional Z)."""
    parts = []
    for c in coords_iter:
        if include_z:
            x, y, z = c[0], c[1], (c[2] if len(c) >= 3 else 0.0)
            parts.append(f"{x} {y} {z} {m}")
        else:
            parts.append(f"{c[0]} {c[1]} {m}")
    return ", ".join(parts)


def _geom_to_wkt_with_m(geom, m: float, *, include_z: bool) -> str:
    """Render Point / LineString / Multi* WKT with M (and optional Z) annotation."""
    suffix = "ZM" if include_z else "M"
    gtype = geom.geom_type
    if gtype == "Point":
        chunk = _coords_with_m([geom.coords[0]], m, include_z)
        return f"POINT {suffix} ({chunk})"
    if gtype == "LineString":
        chunk = _coords_with_m(list(geom.coords), m, include_z)
        return f"LINESTRING {suffix} ({chunk})"
    if gtype == "MultiPoint":
        parts = ", ".join(
            f"({_coords_with_m([p.coords[0]], m, include_z)})" for p in geom.geoms
        )
        return f"MULTIPOINT {suffix} ({parts})"
    if gtype == "MultiLineString":
        parts = ", ".join(
            f"({_coords_with_m(list(line.coords), m, include_z)})" for line in geom.geoms
        )
        return f"MULTILINESTRING {suffix} ({parts})"
    raise NotImplementedError(f"M WKT rendering not implemented for {gtype}.")
