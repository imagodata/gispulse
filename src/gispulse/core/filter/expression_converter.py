"""
Expression Converter — translates filter expressions between SQL dialects.

Converts user-facing expressions to Pandas query strings, DuckDB spatial SQL,
or PostGIS SQL, handling spatial predicates and buffer wrapping.

Inspired by FilterMate core/services/expression_service.py.
"""

from __future__ import annotations

import re

from gispulse.core.filter.expression import Dialect, FilterExpression, SpatialPredicate

# Mapping: SpatialPredicate -> PostGIS function name
_POSTGIS_PREDICATES: dict[SpatialPredicate, str] = {
    SpatialPredicate.INTERSECTS: "ST_Intersects",
    SpatialPredicate.CONTAINS: "ST_Contains",
    SpatialPredicate.WITHIN: "ST_Within",
    SpatialPredicate.CROSSES: "ST_Crosses",
    SpatialPredicate.TOUCHES: "ST_Touches",
    SpatialPredicate.OVERLAPS: "ST_Overlaps",
    SpatialPredicate.DISJOINT: "ST_Disjoint",
    SpatialPredicate.EQUALS: "ST_Equals",
    SpatialPredicate.DWITHIN: "ST_DWithin",
}

# DuckDB spatial uses the same ST_* names
_DUCKDB_PREDICATES: dict[SpatialPredicate, str] = {
    SpatialPredicate.INTERSECTS: "ST_Intersects",
    SpatialPredicate.CONTAINS: "ST_Contains",
    SpatialPredicate.WITHIN: "ST_Within",
    SpatialPredicate.CROSSES: "ST_Crosses",
    SpatialPredicate.TOUCHES: "ST_Touches",
    SpatialPredicate.OVERLAPS: "ST_Overlaps",
    SpatialPredicate.DISJOINT: "ST_Disjoint",
    SpatialPredicate.EQUALS: "ST_Equals",
    SpatialPredicate.DWITHIN: "ST_DWithin",
}

# GeoPandas method names for Python strategy
_GEOPANDAS_PREDICATES: dict[SpatialPredicate, str] = {
    SpatialPredicate.INTERSECTS: "intersects",
    SpatialPredicate.CONTAINS: "contains",
    SpatialPredicate.WITHIN: "within",
    SpatialPredicate.CROSSES: "crosses",
    SpatialPredicate.TOUCHES: "touches",
    SpatialPredicate.OVERLAPS: "overlaps",
    SpatialPredicate.DISJOINT: "disjoint",
    SpatialPredicate.EQUALS: "geom_equals",
    SpatialPredicate.DWITHIN: "dwithin",
}

# Simple expression validation pattern
_DANGEROUS_PATTERNS = re.compile(
    r"\b(DROP|DELETE|INSERT|UPDATE|ALTER|CREATE|GRANT|TRUNCATE)\b",
    re.IGNORECASE,
)


class ExpressionConverter:
    """Converts FilterExpression to dialect-specific SQL or Pandas queries."""

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def validate(self, expression: str) -> tuple[bool, list[str]]:
        """Validate expression syntax. Returns (is_valid, errors)."""
        errors: list[str] = []

        if not expression or not expression.strip():
            errors.append("Expression is empty")
            return False, errors

        if _DANGEROUS_PATTERNS.search(expression):
            errors.append("Expression contains disallowed SQL keywords (DROP, DELETE, INSERT, etc.)")
            return False, errors

        # Check balanced parentheses
        depth = 0
        for ch in expression:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            if depth < 0:
                errors.append("Unbalanced parentheses")
                return False, errors
        if depth != 0:
            errors.append("Unbalanced parentheses")
            return False, errors

        return True, errors

    def to_pandas(self, expr: FilterExpression) -> str:
        """Convert to a Pandas DataFrame.query() string.

        Only handles attribute filters. Spatial filters are applied
        separately via GeoPandas geometry methods.
        """
        if expr.is_spatial and not expr.raw.startswith("Spatial filter:"):
            # Mixed: extract the attribute part (everything before spatial keywords)
            return expr.raw
        if expr.is_spatial:
            return ""  # Pure spatial — no pandas expression
        return expr.raw

    def to_duckdb_sql(
        self,
        expr: FilterExpression,
        table_name: str,
        geom_col: str = "geom",
    ) -> tuple[str, list]:
        """Convert to a DuckDB spatial SQL query.

        Returns:
            Tuple of (sql_with_placeholders, params_list).
        """
        where_clauses: list[str] = []
        all_params: list = []

        # Attribute filter
        attr_sql = self._attribute_to_sql(expr, dialect=Dialect.DUCKDB)
        if attr_sql:
            where_clauses.append(attr_sql)

        # Spatial predicate
        if expr.is_spatial and expr.ref_wkt and expr.spatial_predicates:
            spatial_clause, spatial_params = self._build_spatial_clause_duckdb(
                expr, geom_col,
            )
            if spatial_clause:
                where_clauses.append(spatial_clause)
                all_params.extend(spatial_params)

        where = " AND ".join(where_clauses) if where_clauses else "TRUE"
        return f"SELECT * FROM {table_name} WHERE {where}", all_params

    def to_postgis_sql(
        self,
        expr: FilterExpression,
        schema: str,
        table_name: str,
        geom_col: str = "geometry",
    ) -> tuple[str, list]:
        """Convert to a PostGIS SQL query.

        Returns:
            Tuple of (sql_with_placeholders, params_list).
        """
        where_clauses: list[str] = []
        all_params: list = []

        # Attribute filter
        attr_sql = self._attribute_to_sql(expr, dialect=Dialect.POSTGIS)
        if attr_sql:
            where_clauses.append(attr_sql)

        # Spatial predicate
        if expr.is_spatial and expr.ref_wkt and expr.spatial_predicates:
            spatial_clause, spatial_params = self._build_spatial_clause_postgis(
                expr, geom_col,
            )
            if spatial_clause:
                where_clauses.append(spatial_clause)
                all_params.extend(spatial_params)

        where = " AND ".join(where_clauses) if where_clauses else "TRUE"
        qualified = f'"{schema}"."{table_name}"' if schema else f'"{table_name}"'
        return f"SELECT * FROM {qualified} WHERE {where}", all_params

    def combine(
        self,
        expressions: list[str],
        operator: str = "AND",
    ) -> str:
        """Combine multiple SQL expressions with a logical operator."""
        non_empty = [e.strip() for e in expressions if e and e.strip()]
        if not non_empty:
            return ""
        if len(non_empty) == 1:
            return non_empty[0]
        return f" {operator.upper()} ".join(f"({e})" for e in non_empty)

    def get_spatial_predicate_name(
        self,
        predicate: SpatialPredicate,
        dialect: Dialect,
    ) -> str:
        """Get the dialect-specific function/method name for a spatial predicate."""
        if dialect == Dialect.PANDAS:
            return _GEOPANDAS_PREDICATES.get(predicate, predicate.value)
        if dialect == Dialect.DUCKDB:
            return _DUCKDB_PREDICATES.get(predicate, f"ST_{predicate.value.capitalize()}")
        return _POSTGIS_PREDICATES.get(predicate, f"ST_{predicate.value.capitalize()}")

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _attribute_to_sql(self, expr: FilterExpression, dialect: Dialect) -> str:
        """Extract the attribute filter portion of an expression."""
        if expr.raw.startswith("Spatial filter:"):
            return ""
        if expr.is_spatial:
            # Mixed expression — return raw for now
            return expr.raw
        raw = expr.raw.strip()
        if not raw:
            return ""
        return raw

    def _build_ref_geom_sql(
        self,
        expr: FilterExpression,
        *,
        st_prefix: str = "ST",
    ) -> tuple[str, list]:
        """Build SQL fragment for the reference geometry, with optional buffer.

        Returns:
            Tuple of (sql_fragment_with_placeholders, params_list).
        """
        srid = expr.ref_srid or 4326
        geom_sql = f"{st_prefix}_GeomFromText(%s, %s)"
        params: list = [expr.ref_wkt, srid]
        if expr.has_buffer:
            geom_sql = f"{st_prefix}_Buffer({geom_sql}, %s)"
            params.append(expr.buffer_value)
        return geom_sql, params

    def _build_spatial_clause_postgis(
        self,
        expr: FilterExpression,
        geom_col: str,
    ) -> tuple[str, list]:
        """Build WHERE clause for PostGIS spatial predicates.

        Returns:
            Tuple of (where_clause_with_placeholders, params_list).
        """
        ref_geom, params = self._build_ref_geom_sql(expr, st_prefix="ST")
        clauses: list[str] = []
        all_params: list = []

        for pred in expr.spatial_predicates:
            func = _POSTGIS_PREDICATES.get(pred)
            if func is None:
                continue
            if pred == SpatialPredicate.DWITHIN:
                dist = expr.buffer_value or 0
                clauses.append(f"{func}({geom_col}, {ref_geom}, %s)")
                all_params.extend(params)
                all_params.append(dist)
            else:
                clauses.append(f"{func}({geom_col}, {ref_geom})")
                all_params.extend(params)

        clause = " AND ".join(clauses) if clauses else ""
        return clause, all_params

    def _build_spatial_clause_duckdb(
        self,
        expr: FilterExpression,
        geom_col: str,
    ) -> tuple[str, list]:
        """Build WHERE clause for DuckDB spatial predicates.

        Returns:
            Tuple of (where_clause_with_placeholders, params_list).
        """
        ref_geom, params = self._build_ref_geom_sql(expr, st_prefix="ST")
        clauses: list[str] = []
        all_params: list = []

        for pred in expr.spatial_predicates:
            func = _DUCKDB_PREDICATES.get(pred)
            if func is None:
                continue
            if pred == SpatialPredicate.DWITHIN:
                dist = expr.buffer_value or 0
                clauses.append(f"{func}({geom_col}, {ref_geom}, %s)")
                all_params.extend(params)
                all_params.append(dist)
            else:
                clauses.append(f"{func}({geom_col}, {ref_geom})")
                all_params.extend(params)

        clause = " AND ".join(clauses) if clauses else ""
        return clause, all_params
