"""
Système de prédicats dynamiques pour les triggers GISPulse.

Deux backends d'évaluation :
- ``PredicateEvaluator``       : évaluation SQL via PostGIS (connexion requise)
- ``ShapelyPredicateEvaluator``: évaluation en mémoire via Shapely (portable/ESB)

Les deux évaluent les mêmes types : AttrPredicate, GeomPredicate,
CompoundPredicate (de core/models.py).
"""

from __future__ import annotations

import operator
import re
from typing import Any

from gispulse.core.models import AnyPredicate, AttrPredicate, CompoundPredicate, GeomPredicate
from gispulse.core.sql_safety import validate_identifier as _safe_ident, validate_ref_filter


# ---------------------------------------------------------------------------
# Opérateurs attributaires
# ---------------------------------------------------------------------------

_ATTR_OPS = {
    "eq": operator.eq,
    "neq": operator.ne,
    "gt": operator.gt,
    "lt": operator.lt,
    "gte": operator.ge,
    "lte": operator.le,
}

_TEMPORAL_OPS = {"age_gt", "age_lt", "before", "after", "between"}


def _parse_dt(value: Any) -> "float | None":
    """Best-effort coercion of *value* to a POSIX timestamp (seconds).

    Accepts datetime, ISO-8601 string (with 'Z' suffix), epoch int/float.
    Returns None when the value is not parseable.
    """
    import datetime as _dt

    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, _dt.datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=_dt.timezone.utc)
        return value.timestamp()
    if isinstance(value, str):
        try:
            cleaned = value.replace("Z", "+00:00")
            dt = _dt.datetime.fromisoformat(cleaned)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=_dt.timezone.utc)
            return dt.timestamp()
        except ValueError:
            return None
    return None


def _eval_temporal(pred: AttrPredicate, payload: dict[str, Any]) -> bool:
    """Evaluate a temporal attribute predicate.

    Compares the payload's field value (ISO-8601, datetime, or epoch) against
    the predicate's value. Returns False on parse failure so upstream code
    can treat it as a non-match rather than an exception.
    """
    import time

    field_ts = _parse_dt(payload.get(pred.field))
    if field_ts is None:
        return False

    if pred.op in {"age_gt", "age_lt"}:
        try:
            threshold_s = float(pred.value)
        except (TypeError, ValueError):
            return False
        age = time.time() - field_ts
        return age > threshold_s if pred.op == "age_gt" else age < threshold_s

    if pred.op in {"before", "after"}:
        target = _parse_dt(pred.value)
        if target is None:
            return False
        return field_ts < target if pred.op == "before" else field_ts > target

    if pred.op == "between":
        if not isinstance(pred.value, (list, tuple)) or len(pred.value) != 2:
            return False
        low = _parse_dt(pred.value[0])
        high = _parse_dt(pred.value[1])
        if low is None or high is None:
            return False
        return low <= field_ts <= high

    return False


def _eval_attr(pred: AttrPredicate, payload: dict[str, Any]) -> bool:
    """Évalue un prédicat attributaire contre le payload d'un événement DML."""
    # Handle null-checking ops before the early-exit on None
    if pred.op == "is_null":
        return payload.get(pred.field) is None

    if pred.op == "not_null":
        return payload.get(pred.field) is not None

    if pred.op in _TEMPORAL_OPS:
        return _eval_temporal(pred, payload)

    value = payload.get(pred.field)
    if value is None:
        return False

    if pred.op in _ATTR_OPS:
        try:
            return _ATTR_OPS[pred.op](value, pred.value)
        except TypeError:
            return False

    if pred.op == "in":
        return value in pred.value

    if pred.op == "like":
        # Convertit le pattern SQL LIKE en regex Python (% → .*, _ → .)
        regex = ""
        for ch in str(pred.value):
            if ch == "%":
                regex += ".*"
            elif ch == "_":
                regex += "."
            else:
                regex += re.escape(ch)
        return bool(re.fullmatch(regex, str(value), re.IGNORECASE))

    raise ValueError(f"Unknown AttrPredicate op: {pred.op!r}")


# ---------------------------------------------------------------------------
# Opérateurs géométriques
# ---------------------------------------------------------------------------

# Map op -> ST_* function name
_GEOM_OPS: dict[str, str] = {
    "intersects": "ST_Intersects",
    "within": "ST_Within",
    "contains": "ST_Contains",
    "crosses": "ST_Crosses",
    "overlaps": "ST_Overlaps",
    "touches": "ST_Touches",
    "covers": "ST_Covers",
    "covered_by": "ST_CoveredBy",
    "disjoint": "ST_Disjoint",
    "equals": "ST_Equals",
}

# Distance-based ops — both sides need a numeric ``distance`` in meters.
# ``dwithin`` uses PostGIS ST_DWithin (index-aware, native) rather than the
# legacy buffer trick; ``distance_lt``/``distance_gt`` keep ST_Distance so
# strict > comparisons remain expressible.
_DISTANCE_OPS = {"distance_lt", "distance_gt", "dwithin"}


def _build_geom_sql(pred: GeomPredicate, geom_wkt: str, srid: int = 4326) -> tuple[str, dict[str, Any]]:
    """Génère le SQL PostGIS paramétré qui retourne TRUE/FALSE pour un GeomPredicate.

    Returns a (sql, params) tuple. The SQL uses :param placeholders for user
    data (geom_wkt, srid, buffer_m, distance) and validated identifiers for
    table/column names.
    """
    # Validate identifiers to prevent injection
    ref_table = _safe_ident(pred.ref_table, "ref_table")
    ref_geom_col = _safe_ident(pred.ref_geom_col, "ref_geom_col")

    params: dict[str, Any] = {"geom_wkt": geom_wkt, "srid": srid}

    # Géométrie entrante — on applique un buffer si demandé
    if pred.buffer_m:
        params["buffer_m"] = float(pred.buffer_m)
        incoming = (
            "ST_Buffer(ST_Transform(ST_SetSRID(ST_GeomFromText(:geom_wkt), :srid), 3857), "
            ":buffer_m)"
        )
    else:
        incoming = "ST_SetSRID(ST_GeomFromText(:geom_wkt), :srid)"

    # ref_filter: only allow simple column comparisons, reject dangerous patterns
    ref_filter_clause = ""
    if pred.ref_filter:
        validate_ref_filter(pred.ref_filter)
        ref_filter_clause = f"WHERE {pred.ref_filter}"

    if pred.op in _GEOM_OPS:
        st_func = _GEOM_OPS[pred.op]
        sql = (
            f"SELECT EXISTS ("
            f"  SELECT 1 FROM {ref_table} r "
            f"  {ref_filter_clause} "
            f"  WHERE {st_func}({incoming}, r.{ref_geom_col})"
            f") AS result"
        )
        return sql, params

    if pred.op in _DISTANCE_OPS:
        if pred.distance is None:
            raise ValueError(f"GeomPredicate op '{pred.op}' requires 'distance' (meters).")
        params["distance"] = float(pred.distance)

        if pred.op == "dwithin":
            # ST_DWithin is index-aware when a GIST index exists on ref.geom.
            # Use ::geography so distance is in meters regardless of CRS.
            sql = (
                f"SELECT EXISTS ("
                f"  SELECT 1 FROM {ref_table} r "
                f"  {ref_filter_clause} "
                f"  WHERE ST_DWithin("
                f"    ({incoming})::geography, "
                f"    (r.{ref_geom_col})::geography, "
                f"    :distance"
                f"  )"
                f") AS result"
            )
            return sql, params

        comparator = "<" if pred.op == "distance_lt" else ">"
        sql = (
            f"SELECT EXISTS ("
            f"  SELECT 1 FROM {ref_table} r "
            f"  {ref_filter_clause} "
            f"  WHERE ST_Distance("
            f"    ({incoming})::geography, "
            f"    (r.{ref_geom_col})::geography"
            f"  ) {comparator} :distance"
            f") AS result"
        )
        return sql, params

    raise ValueError(f"Unknown GeomPredicate op: {pred.op!r}")


# ---------------------------------------------------------------------------
# PredicateEvaluator
# ---------------------------------------------------------------------------


class PredicateEvaluator:
    """Évalue une liste de prédicats contre un payload DML.

    Usage:
        evaluator = PredicateEvaluator(postgis_conn)
        fired = evaluator.evaluate(trigger.predicates, trigger.predicate_logic, payload)
    """

    def __init__(self, postgis_conn: Any | None = None) -> None:
        """
        Args:
            postgis_conn: Instance de `PostGISConnection` (persistence/postgis.py).
                          Requis uniquement si des `GeomPredicate` sont utilisés.
        """
        self._conn = postgis_conn

    def evaluate(
        self,
        predicates: list[AnyPredicate],
        logic: str = "AND",
        payload: dict[str, Any] | None = None,
        geom_field: str = "geom",
        srid: int = 4326,
    ) -> bool:
        """Évalue la liste de prédicats contre le payload.

        Args:
            predicates:  Liste de prédicats à évaluer.
            logic:       'AND' — tous doivent être vrais / 'OR' — au moins un.
            payload:     Dict représentant la nouvelle ligne (champs + valeurs).
            geom_field:  Clé dans `payload` contenant la géométrie WKT.
            srid:        SRID de la géométrie dans le payload.

        Returns:
            True si les prédicats sont satisfaits.
        """
        payload = payload or {}

        if not predicates:
            return True  # Aucun prédicat → trigger always-on

        results = [
            self._eval_one(pred, payload, geom_field, srid)
            for pred in predicates
        ]

        if logic == "AND":
            return all(results)
        return any(results)

    def _eval_one(
        self,
        pred: AnyPredicate,
        payload: dict[str, Any],
        geom_field: str,
        srid: int,
    ) -> bool:
        if isinstance(pred, AttrPredicate):
            return _eval_attr(pred, payload)

        if isinstance(pred, GeomPredicate):
            return self._eval_geom(pred, payload, geom_field, srid)

        if isinstance(pred, CompoundPredicate):
            return self._eval_compound(pred, payload, geom_field, srid)

        raise TypeError(f"Unknown predicate type: {type(pred)}")

    def _eval_geom(
        self,
        pred: GeomPredicate,
        payload: dict[str, Any],
        geom_field: str,
        srid: int,
    ) -> bool:
        if self._conn is None:
            raise RuntimeError(
                "GeomPredicate evaluation requires a PostGISConnection. "
                "Pass postgis_conn to PredicateEvaluator."
            )

        geom_wkt = payload.get(geom_field)
        if not geom_wkt:
            return False

        sql, params = _build_geom_sql(pred, str(geom_wkt), srid)
        rows = self._conn.execute(sql, params)
        if not rows:
            return False
        return bool(rows[0].get("result", False))

    def _eval_compound(
        self,
        pred: CompoundPredicate,
        payload: dict[str, Any],
        geom_field: str,
        srid: int,
    ) -> bool:
        if pred.logic == "NOT":
            if len(pred.predicates) != 1:
                raise ValueError("CompoundPredicate NOT must have exactly 1 child predicate.")
            return not self._eval_one(pred.predicates[0], payload, geom_field, srid)

        results = [self._eval_one(p, payload, geom_field, srid) for p in pred.predicates]

        if pred.logic == "AND":
            return all(results)
        return any(results)


# ---------------------------------------------------------------------------
# ShapelyPredicateEvaluator — in-memory backend (portable/ESB mode)
# ---------------------------------------------------------------------------

from typing import Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from shapely.geometry.base import BaseGeometry

RefLoader = Callable[[str, "str | None", str], "list[BaseGeometry]"]


def _noop_loader(table: str, filt: "str | None", col: str) -> "list":
    """Default ref loader — logs a warning and returns an empty list."""
    import logging
    logging.getLogger(__name__).warning("predicate_no_ref_loader table=%s", table)
    return []


class ShapelyPredicateEvaluator:
    """Evaluate predicate trees using Shapely (in-memory, no DB required).

    Used by the ESB event router and portable-mode sessions.

    Args:
        state_store: Optional StateStore for ENTER/EXIT transition detection.
        ref_loader:  Callable ``(table, filter, geom_col) -> list[BaseGeometry]``
                     that loads reference geometries for spatial predicates.
    """

    def __init__(
        self,
        state_store: Any | None = None,
        ref_loader: RefLoader | None = None,
    ) -> None:
        self._state_store = state_store
        self._ref_loader: RefLoader = ref_loader or _noop_loader

    def evaluate(
        self,
        predicate: "AnyPredicate",
        new_geom: "BaseGeometry | None",
        new_attrs: dict[str, Any],
        old_geom: "BaseGeometry | None" = None,
        old_attrs: dict[str, Any] | None = None,
    ) -> "Any":
        """Evaluate a predicate tree and return an EvalResult."""
        import time
        try:
            from gispulse.core.models import EvalResult, SpatialState
        except ImportError:
            EvalResult = None  # type: ignore[assignment,misc]

        t0 = time.monotonic()
        matched = self._eval(predicate, new_geom, new_attrs)
        elapsed = (time.monotonic() - t0) * 1000.0

        if EvalResult is not None:
            return EvalResult(matched=matched, eval_time_ms=round(elapsed, 3))
        return matched

    def evaluate_with_transition(
        self,
        predicate: "AnyPredicate",
        object_id: Any,
        predicate_id: Any,
        new_geom: "BaseGeometry | None",
        new_attrs: dict[str, Any],
    ) -> "Any":
        """Evaluate and detect ENTER/EXIT transitions via StateStore."""
        import time
        from gispulse.core.models import EvalResult, SpatialState

        t0 = time.monotonic()
        matched = self._eval(predicate, new_geom, new_attrs)

        new_spatial = SpatialState.INSIDE if matched else SpatialState.OUTSIDE
        transition = None
        if self._state_store is not None:
            transition = self._state_store.update_state(object_id, predicate_id, new_spatial)

        elapsed = (time.monotonic() - t0) * 1000.0
        return EvalResult(matched=matched, transition=transition, eval_time_ms=round(elapsed, 3))

    # ------------------------------------------------------------------
    # Internal dispatch
    # ------------------------------------------------------------------

    def _eval(
        self,
        predicate: "AnyPredicate",
        geom: "BaseGeometry | None",
        attrs: dict[str, Any],
    ) -> bool:
        if isinstance(predicate, GeomPredicate):
            return self._eval_geom(predicate, geom)
        if isinstance(predicate, AttrPredicate):
            return _eval_attr(predicate, attrs)
        if isinstance(predicate, CompoundPredicate):
            return self._eval_compound(predicate, geom, attrs)
        return False

    def _eval_geom(self, pred: GeomPredicate, geom: "BaseGeometry | None") -> bool:
        if geom is None:
            return False

        ref_geoms = self._ref_loader(pred.ref_table, pred.ref_filter, pred.ref_geom_col)
        if not ref_geoms:
            return False

        test_geom = geom
        if pred.buffer_m and pred.buffer_m > 0:
            # Buffer in native CRS units. When CRS info is available at a
            # higher level (e.g., CapabilityExecutor), the caller should
            # reproject to a metric CRS before calling the evaluator.
            # The PostGIS evaluator handles this server-side via ::geography.
            test_geom = geom.buffer(pred.buffer_m)

        for ref in ref_geoms:
            if self._geom_op(pred.op, test_geom, ref, pred.distance):
                return True
        return False

    @staticmethod
    def _geom_op(op: str, a: "BaseGeometry", b: "BaseGeometry", distance: float | None) -> bool:
        if op == "intersects":
            return a.intersects(b)
        if op == "within":
            return a.within(b)
        if op == "contains":
            return a.contains(b)
        if op == "crosses":
            return a.crosses(b)
        if op == "overlaps":
            return a.overlaps(b)
        if op == "touches":
            return a.touches(b)
        if op == "covers":
            return a.covers(b)
        if op == "covered_by":
            return a.covered_by(b)
        if op == "disjoint":
            return a.disjoint(b)
        if op == "equals":
            return a.equals(b)
        if op == "distance_lt":
            return a.distance(b) < (distance or 0)
        if op == "distance_gt":
            return a.distance(b) > (distance or 0)
        if op == "dwithin":
            # Portable fallback: dwithin = distance <= d.
            # Note: unlike PostGIS, Shapely .distance uses the layer's native
            # CRS units (usually meters after reprojection upstream).
            return a.distance(b) <= (distance or 0)
        return False

    def _eval_compound(
        self,
        pred: CompoundPredicate,
        geom: "BaseGeometry | None",
        attrs: dict[str, Any],
    ) -> bool:
        if pred.logic == "AND":
            return all(self._eval(p, geom, attrs) for p in pred.predicates)
        if pred.logic == "OR":
            return any(self._eval(p, geom, attrs) for p in pred.predicates)
        if pred.logic == "NOT":
            if pred.predicates:
                return not self._eval(pred.predicates[0], geom, attrs)
            return True
        return False
