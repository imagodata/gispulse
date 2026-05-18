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

ReturnType = Literal["double", "integer", "boolean", "scalar"]


@dataclass(frozen=True, slots=True)
class GeomFunctionSpec:
    """Specification of a single geom function exposed in the DSL."""

    name: str
    return_type: ReturnType
    sql_template: str
    crs_aware: bool
    """True when ``epsg=`` keyword is meaningful (measure / centroid fns)."""
    accepted_kwargs: tuple[str, ...] = ()
    is_subquery: bool = False
    """True for cross-layer fcts (``geom_within``, ``geom_overlaps_any``,
    ``layer_lookup``).

    Subquery fcts emit either ``EXISTS (SELECT 1 FROM ...)`` (boolean) or
    ``(SELECT ... FROM ... LIMIT 1)`` (scalar — :func:`layer_lookup`) and
    need a richer compilation context (``current_table``, ``pk_col``) to
    resolve self-references (``layer='self'``, ``exclude_self=true``).
    """


_TEMPLATE_AREA_M2 = "ST_Area(ST_Transform({geom}, {src}, '{epsg}', true))"
_TEMPLATE_PERIMETER_M = "ST_Perimeter(ST_Transform({geom}, {src}, '{epsg}', true))"
_TEMPLATE_LENGTH_M = "ST_Length(ST_Transform({geom}, {src}, '{epsg}', true))"
_TEMPLATE_CENTROID_X = "ST_X(ST_Transform(ST_Centroid({geom}), {src}, '{epsg}', true))"
_TEMPLATE_CENTROID_Y = "ST_Y(ST_Transform(ST_Centroid({geom}), {src}, '{epsg}', true))"
_TEMPLATE_NPOINTS = "ST_NPoints({geom})"
_TEMPLATE_IS_VALID = "ST_IsValid({geom})"

# v1.6.0 #122: cross-layer subquery fcts. The compiler fills ``layer``
# with the cross-source layer name (or the current table when the user
# passes ``layer='self'``), ``layer_geom`` with the layer's geometry
# column (defaults to ``geom``), and the optional clauses
# ``match_clause`` / ``exclude_self_clause`` based on the kwargs.
_TEMPLATE_GEOM_WITHIN = (
    'EXISTS (SELECT 1 FROM "{layer}" AS _L '
    'WHERE ST_Within({geom}, _L."{layer_geom}"){match_clause})'
)
_TEMPLATE_GEOM_OVERLAPS_ANY = (
    'EXISTS (SELECT 1 FROM "{layer}" AS _L '
    'WHERE ST_Overlaps({geom}, _L."{layer_geom}"){exclude_self_clause})'
)

# v1.6.x #124: scalar lookup of an attribute from a (cross-source) layer.
# ``match_clause`` resolves to one of:
#   - ST_Within({geom}, _L."{layer_geom}")          (match='spatial_within')
#   - ST_Intersects({geom}, _L."{layer_geom}")      (match='spatial_intersects')
#   - "{self_col}" = _L."{layer_col}"               (match='self.col=layer.col')
# The compiler emits a deterministic ``LIMIT 1`` so multi-match cases pick the
# first row rather than raising a cardinality error.
_TEMPLATE_LAYER_LOOKUP = (
    '(SELECT _L."{take}" FROM "{layer}" AS _L '
    'WHERE {match_clause} LIMIT 1)'
)


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
    "geom_within": GeomFunctionSpec(
        name="geom_within",
        return_type="boolean",
        sql_template=_TEMPLATE_GEOM_WITHIN,
        crs_aware=False,
        accepted_kwargs=("layer", "match", "layer_geom"),
        is_subquery=True,
    ),
    "geom_overlaps_any": GeomFunctionSpec(
        name="geom_overlaps_any",
        return_type="boolean",
        sql_template=_TEMPLATE_GEOM_OVERLAPS_ANY,
        crs_aware=False,
        accepted_kwargs=("layer", "exclude_self", "layer_geom"),
        is_subquery=True,
    ),
    "layer_lookup": GeomFunctionSpec(
        name="layer_lookup",
        return_type="scalar",
        sql_template=_TEMPLATE_LAYER_LOOKUP,
        crs_aware=False,
        accepted_kwargs=("layer", "match", "take", "layer_geom"),
        is_subquery=True,
    ),
}


def is_geom_function(name: str) -> bool:
    """Return True if ``name`` is a whitelisted DSL geom function."""
    return name in GEOM_FUNCTIONS
