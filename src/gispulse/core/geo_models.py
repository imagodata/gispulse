"""Generic geo-spatial domain dataclasses.

These models capture the common spatial primitives used across GISPulse
integrations: geocoded addresses, cadastral parcels, building footprints,
partial-failure records, pipeline trace entries, and spatial overlap
measurements.

All dataclasses use ``frozen=True``, which blocks attribute rebinding after
construction.  Note that ``frozen`` does **not** make nested ``dict`` values
immutable — they remain mutable in place — and instances that carry ``dict``
fields are **not hashable** (``dict`` is not hashable).  Use ``frozen`` for
structural sharing and to catch accidental rebinding, not as a deep-immutability
guarantee.

Literal-typed fields (``lookup_method``, ``context``, ``status``, ``relation``)
are validated at construction time via ``__post_init__``; invalid values raise
:exc:`ValueError` immediately.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from gispulse.utils.errors import ErrorContext

# Allowed values for each validated Literal field — defined once to keep
# __post_init__ bodies DRY and to make the sets introspectable in tests.
_LOOKUP_METHODS: frozenset[str] = frozenset({"point", "buffered_bbox", "missing"})
_ERROR_CONTEXTS: frozenset[str] = frozenset(
    {"network", "parse", "missing_data", "config", "truncation"}
)
_TRACE_STATUSES: frozenset[str] = frozenset({"ok", "partial", "error", "skipped"})
_IMPACT_STATUSES: frozenset[str] = frozenset({"measured", "not_measured"})
_IMPACT_RELATIONS: frozenset[str] = frozenset(
    {"full_overlap", "partial_overlap", "not_measured"}
)


def _check(field_name: str, value: str, allowed: frozenset[str]) -> None:
    if value not in allowed:
        raise ValueError(
            f"Invalid value {value!r} for field {field_name!r}. "
            f"Allowed: {sorted(allowed)}"
        )

# ---------------------------------------------------------------------------
# Geo coordinates
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GeocodedAddress:
    """A geocoding result for a free-text address query.

    Attributes:
        label: Human-readable normalised address string returned by the
            geocoder.
        longitude: WGS-84 longitude in decimal degrees.
        latitude: WGS-84 latitude in decimal degrees.
        score: Geocoder confidence score in [0, 1], or ``None`` when not
            provided.
        city: City name, or ``None``.
        postcode: Postal code, or ``None``.
        citycode: Municipal INSEE code (France) or equivalent, or ``None``.
        raw: Original geocoder response dict for downstream consumers.
    """

    label: str
    longitude: float
    latitude: float
    score: float | None = None
    city: str | None = None
    postcode: str | None = None
    citycode: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Cadastre / land registry
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Parcel:
    """A cadastral parcel record.

    Attributes:
        code: Full parcel identifier (e.g. ``"75056000AA0042"``), or ``None``.
        code_insee: INSEE municipality code, or ``None``.
        section: Cadastral section code, or ``None``.
        numero: Parcel number within the section, or ``None``.
        surface_m2: Declared area in square metres, or ``None``.
        geometry: GeoJSON geometry dict (``Polygon`` or ``MultiPolygon``),
            or ``None``.
        bbox: ``[minlon, minlat, maxlon, maxlat]`` bounding box, or ``None``.
        lookup_method: How the parcel was resolved — one of ``"point"``,
            ``"buffered_bbox"``, or ``"missing"``.
        raw: Original source response dict.
    """

    code: str | None = None
    code_insee: str | None = None
    section: str | None = None
    numero: str | None = None
    surface_m2: float | None = None
    geometry: dict[str, Any] | None = None
    bbox: tuple[float, float, float, float] | None = None
    lookup_method: Literal["point", "buffered_bbox", "missing"] = "point"
    raw: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _check("lookup_method", self.lookup_method, _LOOKUP_METHODS)


# ---------------------------------------------------------------------------
# Buildings
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Building:
    """A building footprint record.

    Attributes:
        id: Source identifier, or ``None``.
        nature: Building nature / category label, or ``None``.
        geometry: GeoJSON geometry dict, or ``None``.
        bbox: ``[minlon, minlat, maxlon, maxlat]`` bounding box, or ``None``.
        source: Identifier of the data source that provided this record.
            No default is imposed — callers must supply the actual source
            name (e.g. ``"ign-pci-express-batiment"``).
        raw: Original source response dict.
    """

    id: str | None = None
    nature: str | None = None
    geometry: dict[str, Any] | None = None
    bbox: tuple[float, float, float, float] | None = None
    source: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Error bookkeeping
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PartialError:
    """Records a recoverable failure for a single pipeline stage.

    Attributes:
        stage: Logical name of the pipeline stage that failed
            (e.g. ``"geocoding"`` or ``"cadastre"``).
        message: Human-readable description of the failure.
        context: :data:`~gispulse.utils.errors.ErrorContext` category.
    """

    stage: str
    message: str
    context: ErrorContext = "missing_data"

    def __post_init__(self) -> None:
        _check("context", self.context, _ERROR_CONTEXTS)


# ---------------------------------------------------------------------------
# Pipeline observability
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AnalysisTrace:
    """Execution record for a single pipeline stage.

    Attributes:
        stage: Logical stage identifier.
        label: Human-readable stage label.
        status: Outcome — one of ``"ok"``, ``"partial"``, ``"error"``,
            ``"skipped"``.
        source: Data source identifier, or ``None``.
        duration_ms: Wall-clock duration of the stage in milliseconds.
        details: Arbitrary structured metadata for debugging.
    """

    stage: str
    label: str
    status: Literal["ok", "partial", "error", "skipped"]
    source: str | None = None
    duration_ms: int = 0
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _check("status", self.status, _TRACE_STATUSES)


# ---------------------------------------------------------------------------
# Spatial overlap
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SpatialImpact:
    """Measurement of the spatial overlap between a subject geometry and a layer feature.

    Attributes:
        status: Whether the measurement was carried out — ``"measured"``
            or ``"not_measured"``.
        relation: Topological relationship — ``"full_overlap"``,
            ``"partial_overlap"``, or ``"not_measured"``.
        overlap_m2: Overlapping area in square metres, or ``None``.
        parcel_percent: Share of the subject geometry covered, or ``None``.
        clipped_geometry: GeoJSON geometry of the intersection, or ``None``.
        method: Description of the measurement method used.
    """

    status: Literal["measured", "not_measured"]
    relation: Literal["full_overlap", "partial_overlap", "not_measured"]
    method: str
    overlap_m2: float | None = None
    parcel_percent: float | None = None
    clipped_geometry: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        _check("status", self.status, _IMPACT_STATUSES)
        _check("relation", self.relation, _IMPACT_RELATIONS)
