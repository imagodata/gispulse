"""Whitelisted geometry functions exposed by the GISPulse DSL.

Each entry maps a user-facing function name (``geom_area_m2``,
``geom_centroid_x``, …) to a SQL template that the expression compiler
emits against DuckDB. The templates use named placeholders that the
compiler fills with values pulled from the function call's keyword
arguments and from the surrounding :class:`CompilationContext`:

============  =======================================================
Placeholder   Substituted with
============  =======================================================
``{geom}``    The geometry column reference (e.g. ``"geom"``). Quoted
              by the compiler.
``{epsg}``    The target EPSG code as ``EPSG:NNNN``. Defaults to the
              context's metric CRS (``EPSG:2154`` when unspecified)
              for measure functions; overridable via ``epsg=...``.
``{src}``     The source EPSG of the dataset, taken verbatim from the
              context. Always quoted as a string literal.
============  =======================================================

The templates intentionally call ``ST_Transform(... , true)`` so the
``always_xy`` axis convention matches PROJ-system pyproj behaviour we
already exercise in :func:`gispulse.runtime.duckdb_engine.verify_epsg_roundtrip`.

Adding a new geom function = one entry here and one round-trip test.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ReturnType = Literal["double", "integer", "boolean"]


@dataclass(frozen=True, slots=True)
class GeomFunctionSpec:
    """Specification of a single geom function exposed in the DSL."""

    name: str
    return_type: ReturnType
    sql_template: str
    crs_aware: bool
    """True when ``epsg=`` keyword is meaningful (measure / centroid fns)."""
    accepted_kwargs: tuple[str, ...] = ()


_TEMPLATE_AREA_M2 = "ST_Area(ST_Transform({geom}, {src}, '{epsg}', true))"
_TEMPLATE_PERIMETER_M = "ST_Perimeter(ST_Transform({geom}, {src}, '{epsg}', true))"
_TEMPLATE_LENGTH_M = "ST_Length(ST_Transform({geom}, {src}, '{epsg}', true))"
_TEMPLATE_CENTROID_X = "ST_X(ST_Transform(ST_Centroid({geom}), {src}, '{epsg}', true))"
_TEMPLATE_CENTROID_Y = "ST_Y(ST_Transform(ST_Centroid({geom}), {src}, '{epsg}', true))"
_TEMPLATE_NPOINTS = "ST_NPoints({geom})"
_TEMPLATE_IS_VALID = "ST_IsValid({geom})"


GEOM_FUNCTIONS: dict[str, GeomFunctionSpec] = {
    "geom_area_m2": GeomFunctionSpec(
        name="geom_area_m2",
        return_type="double",
        sql_template=_TEMPLATE_AREA_M2,
        crs_aware=True,
        accepted_kwargs=("epsg",),
    ),
    "geom_perimeter_m": GeomFunctionSpec(
        name="geom_perimeter_m",
        return_type="double",
        sql_template=_TEMPLATE_PERIMETER_M,
        crs_aware=True,
        accepted_kwargs=("epsg",),
    ),
    "geom_length_m": GeomFunctionSpec(
        name="geom_length_m",
        return_type="double",
        sql_template=_TEMPLATE_LENGTH_M,
        crs_aware=True,
        accepted_kwargs=("epsg",),
    ),
    "geom_centroid_x": GeomFunctionSpec(
        name="geom_centroid_x",
        return_type="double",
        sql_template=_TEMPLATE_CENTROID_X,
        crs_aware=True,
        accepted_kwargs=("epsg",),
    ),
    "geom_centroid_y": GeomFunctionSpec(
        name="geom_centroid_y",
        return_type="double",
        sql_template=_TEMPLATE_CENTROID_Y,
        crs_aware=True,
        accepted_kwargs=("epsg",),
    ),
    "geom_npoints": GeomFunctionSpec(
        name="geom_npoints",
        return_type="integer",
        sql_template=_TEMPLATE_NPOINTS,
        crs_aware=False,
    ),
    "geom_is_valid": GeomFunctionSpec(
        name="geom_is_valid",
        return_type="boolean",
        sql_template=_TEMPLATE_IS_VALID,
        crs_aware=False,
    ),
}


def is_geom_function(name: str) -> bool:
    """Return True if ``name`` is a whitelisted DSL geom function."""
    return name in GEOM_FUNCTIONS
