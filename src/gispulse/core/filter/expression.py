"""
Filter Expression Value Object.

Immutable representation of a validated filter expression
with dialect-aware SQL conversion support.

Ported from FilterMate core/domain/filter_expression.py,
adapted for GISPulse (no QGIS dependencies, 3 SQL dialects).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class Dialect(str, Enum):
    """Supported SQL dialects for expression conversion."""

    PANDAS = "pandas"
    DUCKDB = "duckdb"
    POSTGIS = "postgis"


class SpatialPredicate(str, Enum):
    """Standard OGC spatial predicates for filtering."""

    INTERSECTS = "intersects"
    CONTAINS = "contains"
    WITHIN = "within"
    CROSSES = "crosses"
    TOUCHES = "touches"
    OVERLAPS = "overlaps"
    DISJOINT = "disjoint"
    EQUALS = "equals"
    DWITHIN = "dwithin"


@dataclass(frozen=True)
class FilterExpression:
    """Immutable value object representing a validated filter expression.

    Encapsulates the original expression, dialect-specific SQL, spatial
    predicate metadata, and optional buffer parameters.

    Use factory methods ``create()`` or ``create_spatial()`` for proper
    validation.

    Attributes:
        raw:                User-facing expression string.
        sql:                Dialect-specific SQL (may be empty if not yet converted).
        dialect:            Target SQL dialect.
        is_spatial:         Whether expression involves spatial predicates.
        spatial_predicates: Tuple of spatial predicates used.
        source_layer:       Source layer key (``"dataset_id::layer_name"``).
        target_layer:       Target layer key.
        buffer_value:       Buffer distance (layer units) if applicable.
        buffer_segments:    Number of segments for buffer curves.
        ref_wkt:            WKT of the reference geometry (for spatial filters).
        ref_srid:           SRID of the reference geometry.
    """

    raw: str
    sql: str = ""
    dialect: Dialect = Dialect.PANDAS
    is_spatial: bool = False
    spatial_predicates: tuple[SpatialPredicate, ...] = field(default_factory=tuple)
    source_layer: str = ""
    target_layer: str = ""
    buffer_value: Optional[float] = None
    buffer_segments: int = 5
    ref_wkt: Optional[str] = None
    ref_srid: int = 4326

    def __post_init__(self) -> None:
        if not self.raw or not self.raw.strip():
            raise ValueError("Expression cannot be empty")
        if self.buffer_value is not None and self.buffer_value < 0:
            raise ValueError("Buffer value cannot be negative")
        if self.buffer_segments < 1:
            raise ValueError("Buffer segments must be at least 1")

    # ------------------------------------------------------------------
    # Factory methods
    # ------------------------------------------------------------------

    @classmethod
    def create(
        cls,
        raw: str,
        *,
        dialect: Dialect = Dialect.PANDAS,
        source_layer: str = "",
        target_layer: str = "",
        buffer_value: Optional[float] = None,
        buffer_segments: int = 5,
        sql: Optional[str] = None,
        ref_wkt: Optional[str] = None,
        ref_srid: int = 4326,
    ) -> FilterExpression:
        """Create a validated filter expression with auto-detection of spatial predicates."""
        spatial_predicates = cls._detect_spatial_predicates(raw)
        is_spatial = len(spatial_predicates) > 0
        return cls(
            raw=raw.strip(),
            sql=sql if sql is not None else raw.strip(),
            dialect=dialect,
            is_spatial=is_spatial,
            spatial_predicates=tuple(spatial_predicates),
            source_layer=source_layer,
            target_layer=target_layer,
            buffer_value=buffer_value,
            buffer_segments=buffer_segments,
            ref_wkt=ref_wkt,
            ref_srid=ref_srid,
        )

    @classmethod
    def create_spatial(
        cls,
        predicates: list[SpatialPredicate],
        *,
        buffer_value: float = 0.0,
        dialect: Dialect = Dialect.PANDAS,
        source_layer: str = "",
        target_layer: str = "",
        ref_wkt: Optional[str] = None,
        ref_srid: int = 4326,
    ) -> FilterExpression:
        """Create a spatial-only filter expression from predicates."""
        predicate_names = [p.value for p in predicates]
        raw = f"Spatial filter: {', '.join(predicate_names)}"
        if buffer_value and buffer_value > 0:
            raw += f", buffer {buffer_value}m"

        return cls(
            raw=raw,
            sql="",  # Backend must build actual SQL
            dialect=dialect,
            is_spatial=True,
            spatial_predicates=tuple(predicates),
            source_layer=source_layer,
            target_layer=target_layer,
            buffer_value=buffer_value if buffer_value and buffer_value > 0 else None,
            buffer_segments=5,
            ref_wkt=ref_wkt,
            ref_srid=ref_srid,
        )

    # ------------------------------------------------------------------
    # Immutable builders
    # ------------------------------------------------------------------

    def with_sql(self, sql: str) -> FilterExpression:
        """Return new expression with updated SQL."""
        return FilterExpression(
            raw=self.raw, sql=sql, dialect=self.dialect,
            is_spatial=self.is_spatial, spatial_predicates=self.spatial_predicates,
            source_layer=self.source_layer, target_layer=self.target_layer,
            buffer_value=self.buffer_value, buffer_segments=self.buffer_segments,
            ref_wkt=self.ref_wkt, ref_srid=self.ref_srid,
        )

    def with_buffer(self, value: float, segments: int = 5) -> FilterExpression:
        """Return new expression with buffer applied."""
        return FilterExpression(
            raw=self.raw, sql=self.sql, dialect=self.dialect,
            is_spatial=True, spatial_predicates=self.spatial_predicates,
            source_layer=self.source_layer, target_layer=self.target_layer,
            buffer_value=value, buffer_segments=segments,
            ref_wkt=self.ref_wkt, ref_srid=self.ref_srid,
        )

    def with_dialect(self, dialect: Dialect) -> FilterExpression:
        """Return new expression targeting a different dialect."""
        return FilterExpression(
            raw=self.raw, sql=self.sql, dialect=dialect,
            is_spatial=self.is_spatial, spatial_predicates=self.spatial_predicates,
            source_layer=self.source_layer, target_layer=self.target_layer,
            buffer_value=self.buffer_value, buffer_segments=self.buffer_segments,
            ref_wkt=self.ref_wkt, ref_srid=self.ref_srid,
        )

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def has_buffer(self) -> bool:
        return self.buffer_value is not None and self.buffer_value > 0

    @property
    def is_simple(self) -> bool:
        """True when expression is a simple attribute filter (no spatial, no buffer)."""
        return not self.is_spatial and not self.has_buffer

    @property
    def predicate_names(self) -> list[str]:
        return [p.value for p in self.spatial_predicates]

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _detect_spatial_predicates(expression: str) -> list[SpatialPredicate]:
        predicates: list[SpatialPredicate] = []
        expr_lower = expression.lower()
        for predicate in SpatialPredicate:
            if predicate.value in expr_lower:
                predicates.append(predicate)
        return predicates

    def __str__(self) -> str:
        buffer_info = f" (buffer: {self.buffer_value})" if self.has_buffer else ""
        spatial_info = " [spatial]" if self.is_spatial else ""
        preview = self.raw[:50] + "..." if len(self.raw) > 50 else self.raw
        return f"FilterExpression({self.dialect.value}){spatial_info}: {preview}{buffer_info}"
