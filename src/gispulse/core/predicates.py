"""Predicate type definitions for GISPulse trigger system.

Predicates are declarative conditions evaluated against spatial data.
Three types: GeomPredicate, AttrPredicate, CompoundPredicate.

NOTE: This module defines the *types*. Evaluation logic lives in
``rules/predicates.py`` (PredicateEvaluator, ShapelyPredicateEvaluator).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Union


@dataclass
class GeomPredicate:
    """Prédicat géométrique évalué côté PostGIS.

    Exemple: "la nouvelle géométrie intersecte la table zones_protegees"
    """

    op: Literal[
        "intersects", "within", "contains", "crosses",
        "overlaps", "touches", "covers", "covered_by",
        "disjoint", "equals",
        "distance_lt", "distance_gt", "dwithin",
    ]
    ref_table: str
    ref_filter: str | None = None
    ref_geom_col: str = "geom"
    distance: float | None = None
    buffer_m: float | None = None


@dataclass
class AttrPredicate:
    """Prédicat attributaire évalué en Python à partir du payload DML.

    Temporal ops (``age_gt``, ``age_lt``, ``before``, ``after``, ``between``)
    expect an ISO-8601 datetime string in the payload field, or a numeric
    timestamp in seconds. For ``age_gt``/``age_lt`` the ``value`` is an
    age threshold in seconds ("how long ago"); for ``before``/``after`` the
    ``value`` is an ISO-8601 string; for ``between`` it is a 2-element list
    of ISO-8601 strings.
    """

    field: str
    op: Literal[
        "eq", "neq", "gt", "lt", "gte", "lte", "in", "like",
        "is_null", "not_null",
        "age_gt", "age_lt", "before", "after", "between",
    ]
    value: Any = None


@dataclass
class CompoundPredicate:
    """Combinaison logique de prédicats (AND / OR / NOT)."""

    logic: Literal["AND", "OR", "NOT"]
    predicates: list[Union[GeomPredicate, AttrPredicate, "CompoundPredicate"]] = field(
        default_factory=list
    )


AnyPredicate = Union[GeomPredicate, AttrPredicate, CompoundPredicate]
