"""Statement builders for the SQL-pushed geometry capabilities.

ELT Lot 1 (issue #244, ADR 0005). One builder per capability that used
to emit inline SQL — ``buffer`` / ``clip`` / ``intersects``. Each is
parameterised by a :class:`~gispulse.persistence.sql_dialect.SQLDialect`
and reproduces, statement for statement, the SQL the capability
strategies emitted as hand-written f-strings — so pointing a strategy at
a builder is a behaviour-preserving refactor.

This sits one layer above :mod:`gispulse.persistence.sql_dialect`: the
dialect spells *expressions* (a buffer, an intersection, a geometry
reference), the builders here assemble whole ``SELECT`` *statements*.

All table names go through :func:`validate_identifier`; geometry columns
and aliases are validated inside the dialect. No value is inlined raw.
"""

from __future__ import annotations

from dataclasses import dataclass

from gispulse.core.sql_safety import validate_identifier
from gispulse.persistence.sql_dialect import BufferStyle, SQLDialect

__all__ = [
    "GeneratedQuery",
    "buffer_select",
    "clip_select",
    "intersects_select",
]


@dataclass(frozen=True)
class GeneratedQuery:
    """A generated SQL statement plus the result's geometry column.

    Attributes:
        sql:         SQL statement ready to hand to ``sql_to_gdf``.
        geom_column: Name of the geometry column in the result set — the
            column a strategy should ``set_geometry`` on. For queries
            that only filter rows (``intersects``) this is the dialect's
            pass-through geometry column.
    """

    sql: str
    geom_column: str


def buffer_select(
    dialect: SQLDialect,
    *,
    source_table: str,
    distance: float,
    style: BufferStyle | None = None,
) -> GeneratedQuery:
    """Build the ``buffer`` push-down query.

    Emits ``SELECT <projection> FROM <source_table>`` where the buffered
    geometry is derived via :meth:`SQLDialect.project_with_geometry` —
    ``* REPLACE`` on DuckDB (so the result decoder finds the canonical
    ``__wkb`` column), an appended ``geometry_buf`` column on PostGIS.

    Raises:
        UnsupportedInDialect: when *style* is non-default and the dialect
            cannot express a styled buffer (DuckDB) — the caller falls
            back to the Python strategy.
    """
    validate_identifier(source_table, "source table")
    buffered = dialect.st_buffer_styled(dialect.geom_ref(), distance, style)
    projection, geom_col = dialect.project_with_geometry(
        None, buffered, result_suffix="buf"
    )
    sql = f"SELECT {projection} FROM {source_table}"
    return GeneratedQuery(sql=sql, geom_column=geom_col)


def clip_select(
    dialect: SQLDialect,
    *,
    source_table: str,
    mask_table: str,
    source_alias: str = "i",
    mask_alias: str = "m",
) -> GeneratedQuery:
    """Build the ``clip`` push-down query.

    Emits an ``ST_Intersection`` of every source feature against the
    (already unioned) mask, keeping only features that actually
    intersect. The derived geometry is projected via
    :meth:`SQLDialect.project_with_geometry` — ``* REPLACE`` on DuckDB so
    the result keeps the canonical ``__wkb`` name, an appended
    ``geometry_clip`` column on PostGIS.
    """
    validate_identifier(source_table, "source table")
    validate_identifier(mask_table, "mask table")
    src = dialect.geom_ref(table=source_alias)
    msk = dialect.geom_ref(table=mask_alias)
    projection, geom_col = dialect.project_with_geometry(
        source_alias, dialect.st_intersection(src, msk), result_suffix="clip"
    )
    sql = (
        f"SELECT {projection} "
        f"FROM {source_table} {source_alias}, {mask_table} {mask_alias} "
        f"WHERE {dialect.st_intersects(src, msk)}"
    )
    return GeneratedQuery(sql=sql, geom_column=geom_col)


def intersects_select(
    dialect: SQLDialect,
    *,
    source_table: str,
    ref_table: str,
    source_alias: str = "i",
    ref_alias: str = "r",
) -> GeneratedQuery:
    """Build the ``intersects`` push-down query.

    Emits ``SELECT <src>.* FROM <source> <src>, <ref> <ref> WHERE
    ST_Intersects(...)`` — a row filter that keeps source features
    intersecting the reference geometry. No geometry is derived, so the
    result geometry column is the dialect's pass-through column.
    """
    validate_identifier(source_table, "source table")
    validate_identifier(ref_table, "reference table")
    src = dialect.geom_ref(table=source_alias)
    ref = dialect.geom_ref(table=ref_alias)
    sql = (
        f"SELECT {source_alias}.* "
        f"FROM {source_table} {source_alias}, {ref_table} {ref_alias} "
        f"WHERE {dialect.st_intersects(src, ref)}"
    )
    return GeneratedQuery(sql=sql, geom_column=dialect.geom_column)
