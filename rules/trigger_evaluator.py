"""
TriggerEvaluator — évalue les Triggers contre des ChangeRecords.

Dispatches evaluation by trigger_type:
  - DML:               table + operation + session_id matching
  - THRESHOLD:         aggregate metric check (count, area, sum)
  - VALIDATION:        data quality rules (not_null, unique, range, geometry_valid)
  - BUSINESS_RULE:     SQL expression evaluation
  - TOPOLOGY:          topological integrity checks
  - SPATIAL_CONSTRAINT: distance/zone constraints
  - COMPOSITE:         combines multiple triggers (all/any/sequence)
  - SCHEDULE:          always matches (cron scheduling handled externally)
  - API / ESB_EVENT / WEBHOOK_IN: always matches (external trigger source)

After basic condition matching, structured predicates (AttrPredicate, GeomPredicate,
CompoundPredicate) are evaluated via PredicateEvaluator when present.

P-8 #86: Cascade depth limiter — max 3 niveaux de cascade.
"""
from __future__ import annotations

import time
from typing import Any, Callable

from core.models import (
    BusinessRuleConditions,
    ChangeRecord,
    CompositeConditions,
    DMLConditions,
    FiredTrigger,
    SpatialConstraintConditions,
    ThresholdConditions,
    TopologyConditions,
    Trigger,
    ValidationConditions,
    parse_conditions,
)
from core.sql_safety import validate_expression as _validate_business_expression
from core.sql_safety import validate_layer_name as _validate_identifier  # B-05
from rules.predicates import PredicateEvaluator

MAX_CASCADE_DEPTH = 3


class CascadeDepthExceeded(Exception):
    """Levée quand une cascade de triggers dépasse MAX_CASCADE_DEPTH niveaux."""

    def __init__(self, depth: int, max_depth: int = MAX_CASCADE_DEPTH) -> None:
        self.depth = depth
        self.max_depth = max_depth
        super().__init__(
            f"Cascade depth {depth} exceeds maximum allowed depth of {max_depth}. "
            f"Check for circular trigger dependencies."
        )


class TriggerEvaluator:
    """Évalue les triggers contre les enregistrements de changement."""

    def __init__(
        self,
        postgis_conn: Any | None = None,
        trigger_resolver: Callable[[str], Trigger | None] | None = None,
    ) -> None:
        """
        Args:
            postgis_conn: PostGIS connection for GeomPredicate / spatial evaluation.
            trigger_resolver: Callable to resolve trigger by ID (for composite triggers).
        """
        self._predicate_eval = PredicateEvaluator(postgis_conn)
        self._postgis = postgis_conn
        self._trigger_resolver = trigger_resolver

    def evaluate(
        self,
        change_record: ChangeRecord,
        triggers: list[Trigger],
        depth: int = 1,
    ) -> list[FiredTrigger]:
        """Évalue tous les triggers contre un ChangeRecord."""
        if depth > MAX_CASCADE_DEPTH:
            raise CascadeDepthExceeded(depth)

        results: list[FiredTrigger] = []
        for trigger in triggers:
            if not trigger.enabled:
                continue
            t0 = time.perf_counter()
            matched = self._dispatch(change_record, trigger)
            elapsed_ms = (time.perf_counter() - t0) * 1000
            ft = FiredTrigger(
                trigger_id=trigger.id,
                change_record_id=change_record.id,
                matched=matched,
                actions_dispatched=(
                    [a.action_type for a in trigger.actions] if matched else []
                ),
                eval_time_ms=round(elapsed_ms, 3),
                result_summary={
                    "operation": change_record.operation,
                    "table": change_record.table_name,
                    "trigger_name": trigger.name,
                    "trigger_type": (
                        trigger.trigger_type.value
                        if hasattr(trigger.trigger_type, "value")
                        else str(trigger.trigger_type)
                    ),
                },
                cascade_depth=depth,
            )
            results.append(ft)
        return results

    def evaluate_changeset_records(
        self,
        records: list[ChangeRecord],
        triggers: list[Trigger],
        depth: int = 1,
    ) -> list[FiredTrigger]:
        """Évalue tous les triggers pour une liste de ChangeRecords."""
        all_fired: list[FiredTrigger] = []
        for record in records:
            all_fired.extend(self.evaluate(record, triggers, depth=depth))
        return all_fired

    def evaluate_cascade(
        self,
        initial_records: list[ChangeRecord],
        triggers: list[Trigger],
        next_records_fn: Callable[[list[FiredTrigger]], list[ChangeRecord]],
    ) -> list[FiredTrigger]:
        """Évalue les triggers en cascade sur plusieurs rounds."""
        all_fired: list[FiredTrigger] = []
        current_records = initial_records
        depth = 1

        while current_records:
            fired = self.evaluate_changeset_records(current_records, triggers, depth=depth)
            all_fired.extend(fired)

            matched = [ft for ft in fired if ft.matched]
            if not matched:
                break

            next_depth = depth + 1
            if next_depth > MAX_CASCADE_DEPTH:
                next_records = next_records_fn(matched)
                if next_records:
                    raise CascadeDepthExceeded(next_depth)
                break

            current_records = next_records_fn(matched)
            depth = next_depth

        return all_fired

    # ------------------------------------------------------------------
    # Dispatch by trigger type
    # ------------------------------------------------------------------

    def _dispatch(self, record: ChangeRecord, trigger: Trigger) -> bool:
        """Route evaluation to the appropriate handler based on trigger_type."""
        tt = trigger.trigger_type
        if isinstance(tt, str):
            tt_val = tt
        else:
            tt_val = tt.value

        # Parse conditions into typed dataclass (backward-compatible)
        typed_cond = parse_conditions(tt_val, trigger.conditions)

        # Basic condition matching first (table, operation, session_id)
        if not self._matches_conditions(record, trigger, typed_cond):
            return False

        # Type-specific evaluation
        handler = self._TYPE_HANDLERS.get(tt_val, self._eval_generic)
        if not handler(self, record, trigger, typed_cond):
            return False

        # DSL predicate AST (Mode 1 / CLI triggers — S4).
        # Compiled by ``runtime.config_loader.to_triggers`` and stashed
        # on ``conditions["predicate_ast"]``. We evaluate it against a
        # payload that exposes both ``old.*`` and ``new.*`` so DSL
        # authors can reference either snapshot on UPDATE.
        ast_node = (trigger.conditions or {}).get("predicate_ast")
        if ast_node is not None:
            try:
                from gispulse.runtime.predicate_dsl import (
                    PredicateError,
                    build_update_payload,
                    evaluate_predicate,
                )

                ast_payload = build_update_payload(
                    new_values=record.new_values or {},
                    old_values=record.old_values or {},
                    extra=(
                        {"geom": record.new_geom_wkt}
                        if record.new_geom_wkt
                        else None
                    ),
                )
                if not evaluate_predicate(ast_node, ast_payload):
                    return False
            except PredicateError as exc:
                # Fail-safe: a runtime predicate error must not crash
                # the watcher tick. Log + skip the row.
                import logging as _logging

                _logging.getLogger(__name__).warning(
                    "trigger_predicate_eval_failed trigger=%r error=%s",
                    getattr(trigger, "name", trigger.id),
                    exc,
                )
                return False

        # Structured predicates (if any)
        if trigger.predicates:
            payload = {**record.new_values}
            if record.new_geom_wkt:
                payload["geom"] = record.new_geom_wkt
            if not self._predicate_eval.evaluate(
                trigger.predicates,
                trigger.predicate_logic,
                payload,
            ):
                return False

        return True

    def _matches_conditions(self, record: ChangeRecord, trigger: Trigger, typed_cond: Any = None) -> bool:
        """Basic condition matching — table, operation, session_id.

        Works with both typed conditions (DMLConditions etc.) and raw dicts
        for backward compatibility.
        """
        cond = trigger.conditions
        if not cond:
            return True

        # Use typed conditions if available, fall back to raw dict
        if isinstance(typed_cond, DMLConditions):
            if typed_cond.table and typed_cond.table != record.table_name:
                return False
            if record.operation.value not in typed_cond.events:
                return False
            if typed_cond.session_id and typed_cond.session_id != record.session_id:
                return False
            return True

        # Fallback for non-DML types or raw dicts: use generic dict access
        if "table" in cond and cond["table"] != record.table_name:
            return False
        if "operation" in cond and str(cond["operation"]).upper() != record.operation.value:
            return False
        if "events" in cond:
            events = cond["events"]
            if isinstance(events, list) and record.operation.value not in events:
                return False
        if "session_id" in cond and cond["session_id"] != record.session_id:
            return False
        return True

    # ------------------------------------------------------------------
    # Type-specific handlers
    # ------------------------------------------------------------------

    def _eval_dml(self, record: ChangeRecord, trigger: Trigger, typed_cond: Any = None) -> bool:
        """DML: basic condition matching is sufficient."""
        return True

    def _eval_threshold(self, record: ChangeRecord, trigger: Trigger, typed_cond: Any = None) -> bool:
        """THRESHOLD: check if aggregate metric crosses threshold."""
        if isinstance(typed_cond, ThresholdConditions):
            tc = typed_cond
        else:
            cond = trigger.conditions
            tc = ThresholdConditions(
                table=cond.get("table", record.table_name),
                metric=cond.get("metric", "feature_count"),
                operator=cond.get("operator", "gt"),
                threshold_value=cond.get("threshold_value", 0),
                field=cond.get("field"),
            )

        if self._postgis is None:
            return True

        table = tc.table or record.table_name
        try:
            table = _validate_identifier(table)
            if tc.metric == "feature_count":
                sql = f"SELECT COUNT(*) AS val FROM {table}"
            elif tc.metric == "total_area":
                sql = f"SELECT COALESCE(SUM(ST_Area(geom::geography)), 0) AS val FROM {table}"
            elif tc.metric == "total_length":
                sql = f"SELECT COALESCE(SUM(ST_Length(geom::geography)), 0) AS val FROM {table}"
            elif tc.metric in ("sum_value", "avg_value", "max_value", "min_value"):
                field = _validate_identifier(tc.field or "value")
                agg = tc.metric.split("_")[0].upper()
                sql = f"SELECT COALESCE({agg}({field}), 0) AS val FROM {table}"
            else:
                return True

            rows = self._postgis.execute(sql)
            if not rows:
                return False
            val = float(rows[0].get("val", 0))
            return _compare(val, tc.operator, float(tc.threshold_value))
        except Exception:
            return False

    def _eval_validation(self, record: ChangeRecord, trigger: Trigger, typed_cond: Any = None) -> bool:
        """VALIDATION: check data quality rules against the changed record."""
        if isinstance(typed_cond, ValidationConditions):
            rules = typed_cond.validation_rules
        else:
            rules = trigger.conditions.get("validation_rules", [])

        if not rules:
            return True

        for rule in rules:
            rule_type = rule.get("rule", "")
            field = rule.get("field", "")
            value = record.new_values.get(field)

            if rule_type == "not_null" and value is None:
                return True
            if rule_type == "geometry_valid" and record.new_geom_wkt:
                if self._postgis:
                    try:
                        rows = self._postgis.execute(
                            "SELECT ST_IsValid(ST_GeomFromText(%s)) AS valid",
                            (record.new_geom_wkt,),
                        )
                        if rows and not rows[0].get("valid", True):
                            return True
                    except Exception:
                        pass

        return False

    def _eval_business_rule(self, record: ChangeRecord, trigger: Trigger, typed_cond: Any = None) -> bool:
        """BUSINESS_RULE: evaluate SQL expression — fires on violation (FALSE)."""
        if isinstance(typed_cond, BusinessRuleConditions):
            expression = typed_cond.expression
            table = typed_cond.table or record.table_name
        else:
            cond = trigger.conditions
            expression = cond.get("expression", "")
            table = cond.get("table", record.table_name)

        if not expression:
            return True
        if self._postgis is None:
            return True

        fid = record.feature_id or record.new_values.get("id")
        if not fid:
            return True

        try:
            table = _validate_identifier(table)
            _validate_business_expression(expression)
            sql = f"SELECT NOT ({expression}) AS violated FROM {table} WHERE id = %s"
            rows = self._postgis.execute(sql, (str(fid),))
            if rows and rows[0].get("violated", False):
                return True
        except Exception:
            return False

        return False

    def _eval_topology(self, record: ChangeRecord, trigger: Trigger, typed_cond: Any = None) -> bool:
        """TOPOLOGY: check topological integrity."""
        if isinstance(typed_cond, TopologyConditions):
            tc = typed_cond
        else:
            cond = trigger.conditions
            tc = TopologyConditions(
                table=cond.get("table", record.table_name),
                topo_check=cond.get("topo_check", ""),
                ref_table=cond.get("ref_table", ""),
                tolerance=cond.get("tolerance", 0.001),
            )

        if self._postgis is None or not record.new_geom_wkt:
            return True

        table = tc.table or record.table_name
        try:
            table = _validate_identifier(table)
            geom_param = "ST_GeomFromText(%s, 4326)"
            params: list[Any] = [record.new_geom_wkt]
            if tc.topo_check == "no_overlap":
                params.append(str(record.feature_id))
                sql = f"SELECT EXISTS (SELECT 1 FROM {table} WHERE ST_Overlaps(geom, {geom_param}) AND id != %s) AS violated"
            elif tc.topo_check == "no_gap":
                return False
            elif tc.topo_check == "must_not_cross":
                params.append(str(record.feature_id))
                sql = f"SELECT EXISTS (SELECT 1 FROM {table} WHERE ST_Crosses(geom, {geom_param}) AND id != %s) AS violated"
            elif tc.topo_check == "must_be_inside":
                if not tc.ref_table:
                    return False
                ref_table = _validate_identifier(tc.ref_table)
                sql = f"SELECT NOT EXISTS (SELECT 1 FROM {ref_table} WHERE ST_Contains(geom, {geom_param})) AS violated"
            elif tc.topo_check == "must_not_overlap_with":
                if not tc.ref_table:
                    return False
                ref_table = _validate_identifier(tc.ref_table)
                sql = f"SELECT EXISTS (SELECT 1 FROM {ref_table} WHERE ST_Overlaps(geom, {geom_param})) AS violated"
            else:
                return False

            rows = self._postgis.execute(sql, tuple(params))
            return bool(rows and rows[0].get("violated", False))
        except Exception:
            return False

    def _eval_spatial_constraint(self, record: ChangeRecord, trigger: Trigger, typed_cond: Any = None) -> bool:
        """SPATIAL_CONSTRAINT: distance/zone checks."""
        if isinstance(typed_cond, SpatialConstraintConditions):
            sc = typed_cond
        else:
            cond = trigger.conditions
            sc = SpatialConstraintConditions(
                table=cond.get("table", record.table_name),
                ref_table=cond.get("ref_table", ""),
                spatial_type=cond.get("spatial_type", "min_distance"),
                distance=float(cond.get("distance", 0)),
            )

        if self._postgis is None or not record.new_geom_wkt or not sc.ref_table:
            return True

        try:
            ref_table = _validate_identifier(sc.ref_table)
            geom_param = "ST_GeomFromText(%s, 4326)"
            params: list[Any] = [record.new_geom_wkt]
            if sc.spatial_type == "min_distance":
                params.append(sc.distance)
                sql = (
                    f"SELECT EXISTS (SELECT 1 FROM {ref_table} "
                    f"WHERE ST_Distance(geom::geography, ({geom_param})::geography) < %s) AS violated"
                )
            elif sc.spatial_type == "max_distance":
                params.append(sc.distance)
                sql = (
                    f"SELECT NOT EXISTS (SELECT 1 FROM {ref_table} "
                    f"WHERE ST_Distance(geom::geography, ({geom_param})::geography) <= %s) AS violated"
                )
            elif sc.spatial_type == "must_be_within":
                sql = (
                    f"SELECT NOT EXISTS (SELECT 1 FROM {ref_table} "
                    f"WHERE ST_Contains(geom, {geom_param})) AS violated"
                )
            elif sc.spatial_type == "exclusion_zone":
                sql = (
                    f"SELECT EXISTS (SELECT 1 FROM {ref_table} "
                    f"WHERE ST_Intersects(geom, {geom_param})) AS violated"
                )
            else:
                return False

            rows = self._postgis.execute(sql, tuple(params))
            return bool(rows and rows[0].get("violated", False))
        except Exception:
            return False

    def _eval_composite(self, record: ChangeRecord, trigger: Trigger, typed_cond: Any = None) -> bool:
        """COMPOSITE: evaluate child triggers based on mode (all/any/sequence)."""
        if isinstance(typed_cond, CompositeConditions):
            cc = typed_cond
        else:
            cond = trigger.conditions
            cc = CompositeConditions(
                trigger_ids=cond.get("trigger_ids", []),
                composite_mode=cond.get("composite_mode", "all"),
            )

        if not cc.trigger_ids or not self._trigger_resolver:
            return True

        children = []
        for tid in cc.trigger_ids:
            child = self._trigger_resolver(tid)
            if child:
                children.append(child)

        if not children:
            return True

        results = []
        for child in children:
            matched = self._dispatch(record, child)
            results.append(matched)

        if cc.composite_mode == "all":
            return all(results)
        elif cc.composite_mode == "any":
            return any(results)
        elif cc.composite_mode == "sequence":
            return all(results)  # Sequence = all must match in order
        return True

    def _eval_generic(self, record: ChangeRecord, trigger: Trigger, typed_cond: Any = None) -> bool:
        """Generic handler for schedule, api, esb_event, webhook_in — always matches."""
        return True

    def _eval_source_changed(
        self, record: ChangeRecord, trigger: Trigger, typed_cond: Any = None
    ) -> bool:
        """SOURCE_CHANGED: fire when a watched external source has a new revision.

        The event ``record`` carries the source identity and its current
        revision token in ``new_values`` (keys ``source`` and
        ``revision``) — emitted by the source watcher (issue #187). The
        trigger fires when:

        - the event source matches the trigger's configured ``source``
          (when one is set), and
        - the event ``revision`` differs from the trigger's last-seen
          ``last_revision`` (an absent ``last_revision`` means this is
          the first observation, which fires).

        An event without a ``revision`` token never fires — there is
        nothing to compare against.
        """
        cond = trigger.conditions or {}
        values = record.new_values or {}

        want_source = cond.get("source")
        event_source = values.get("source")
        if want_source and event_source and want_source != event_source:
            return False

        new_revision = values.get("revision")
        if new_revision is None:
            return False
        return new_revision != cond.get("last_revision")

    # Handler dispatch table
    _TYPE_HANDLERS: dict[str, Callable[..., bool]] = {
        "dml": _eval_dml,
        "threshold": _eval_threshold,
        "validation": _eval_validation,
        "business_rule": _eval_business_rule,
        "topology": _eval_topology,
        "spatial_constraint": _eval_spatial_constraint,
        "composite": _eval_composite,
        "schedule": _eval_generic,
        "api": _eval_generic,
        "esb_event": _eval_generic,
        "webhook_in": _eval_generic,
        "source_changed": _eval_source_changed,
    }


def _compare(value: float, op: str, threshold: float) -> bool:
    """Compare value to threshold with given operator."""
    ops = {
        "gt": lambda a, b: a > b,
        "gte": lambda a, b: a >= b,
        "lt": lambda a, b: a < b,
        "lte": lambda a, b: a <= b,
        "eq": lambda a, b: a == b,
        "neq": lambda a, b: a != b,
    }
    fn = ops.get(op)
    return fn(value, threshold) if fn else False
