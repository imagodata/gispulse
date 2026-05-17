"""
Validation layer for Rule and Trigger configurations.

Validates configurations BEFORE execution to provide clear, structured
error messages rather than cryptic runtime crashes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from gispulse.capabilities.registry import REGISTRY
from gispulse.core.models import AnyPredicate, AttrPredicate, CompoundPredicate, GeomPredicate, Rule, Trigger, TriggerEvent


def _ensure_capabilities_registered() -> None:
    """Lazily register default capabilities if the registry is empty."""
    if not REGISTRY:
        import gispulse.capabilities.vector  # noqa: F401, PLC0415


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class ValidationError:
    """Describes a single validation failure."""

    field: str
    message: str

    def __repr__(self) -> str:
        return f"ValidationError(field={self.field!r}, message={self.message!r})"


@dataclass
class ValidationResult:
    """Aggregates all validation errors for a single Rule or Trigger."""

    valid: bool
    errors: list[ValidationError] = field(default_factory=list)

    def __repr__(self) -> str:
        return f"ValidationResult(valid={self.valid}, errors={self.errors!r})"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


_JSON_SCHEMA_TYPE_MAP: dict[str, type | tuple[type, ...]] = {
    "string": str,
    "number": (int, float),
    "integer": int,
    "boolean": bool,
    "object": dict,
    "array": list,
    "null": type(None),
}


def _check_json_type(value: Any, type_spec: str | list[str]) -> bool:
    """Return True if *value* matches the JSON Schema *type_spec*.

    *type_spec* may be a single string (``"number"``) or a list
    (``["string", "null"]``).
    """
    types_to_check: list[str] = (
        type_spec if isinstance(type_spec, list) else [type_spec]
    )
    for t in types_to_check:
        expected = _JSON_SCHEMA_TYPE_MAP.get(t)
        if expected is None:
            # Unknown type in schema — skip type enforcement
            return True
        if isinstance(value, expected):
            return True
    return False


def _validate_predicate(pred: AnyPredicate, index: int) -> list[ValidationError]:
    """Recursively validate a single predicate or compound predicate."""
    errors: list[ValidationError] = []
    prefix = f"predicates[{index}]"

    if isinstance(pred, GeomPredicate):
        valid_ops = {
            "intersects", "within", "contains", "crosses",
            "overlaps", "touches", "covers", "covered_by",
            "disjoint", "equals",
            "distance_lt", "distance_gt", "dwithin",
        }
        if pred.op not in valid_ops:
            errors.append(
                ValidationError(
                    field=f"{prefix}.op",
                    message=(
                        f"GeomPredicate op '{pred.op}' is not valid. "
                        f"Expected one of: {sorted(valid_ops)}"
                    ),
                )
            )
        if pred.op in {"distance_lt", "distance_gt", "dwithin"} and pred.distance is None:
            errors.append(
                ValidationError(
                    field=f"{prefix}.distance",
                    message=(
                        f"GeomPredicate with op='{pred.op}' requires a 'distance' value."
                    ),
                )
            )
        if not pred.ref_table or not pred.ref_table.strip():
            errors.append(
                ValidationError(
                    field=f"{prefix}.ref_table",
                    message="GeomPredicate 'ref_table' must not be empty.",
                )
            )

    elif isinstance(pred, AttrPredicate):
        valid_ops = {
            "eq", "neq", "gt", "lt", "gte", "lte", "in", "like",
            "is_null", "not_null",
            "age_gt", "age_lt", "before", "after", "between",
        }
        if pred.op not in valid_ops:
            errors.append(
                ValidationError(
                    field=f"{prefix}.op",
                    message=(
                        f"AttrPredicate op '{pred.op}' is not valid. "
                        f"Expected one of: {sorted(valid_ops)}"
                    ),
                )
            )
        if pred.op in {"age_gt", "age_lt"} and pred.value is None:
            errors.append(
                ValidationError(
                    field=f"{prefix}.value",
                    message=(
                        f"AttrPredicate op='{pred.op}' requires a numeric "
                        "value (age threshold in seconds)."
                    ),
                )
            )
        if pred.op == "between" and (
            not isinstance(pred.value, (list, tuple)) or len(pred.value) != 2
        ):
            errors.append(
                ValidationError(
                    field=f"{prefix}.value",
                    message=(
                        "AttrPredicate op='between' requires a 2-element list "
                        "[low, high] of ISO-8601 datetimes."
                    ),
                )
            )
        if not pred.field or not pred.field.strip():
            errors.append(
                ValidationError(
                    field=f"{prefix}.field",
                    message="AttrPredicate 'field' must not be empty.",
                )
            )

    elif isinstance(pred, CompoundPredicate):
        valid_logic = {"AND", "OR", "NOT"}
        if pred.logic not in valid_logic:
            errors.append(
                ValidationError(
                    field=f"{prefix}.logic",
                    message=(
                        f"CompoundPredicate logic '{pred.logic}' is not valid. "
                        f"Expected one of: {sorted(valid_logic)}"
                    ),
                )
            )
        for sub_index, sub_pred in enumerate(pred.predicates):
            sub_errors = _validate_predicate(sub_pred, sub_index)
            # Prefix sub-errors to reflect nesting
            for err in sub_errors:
                errors.append(
                    ValidationError(
                        field=f"{prefix}.predicates[{sub_index}].{err.field.split('.', 1)[-1]}",
                        message=err.message,
                    )
                )

    return errors


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


_PLUMBING_KEYS: frozenset[str] = frozenset({"ref_gdf", "ref_layer", "ref_gdfs", "ref_layers"})


def _validate_params_against_schema(
    schema: dict[str, Any],
    params: dict[str, Any],
    field_prefix: str,
    capability_name: str,
) -> list[ValidationError]:
    errors: list[ValidationError] = []
    required_params: list[str] = schema.get("required", [])
    properties: dict[str, Any] = schema.get("properties", {})

    effective = params if params is not None else {}

    for param in required_params:
        if param not in effective:
            errors.append(
                ValidationError(
                    field=f"{field_prefix}.{param}",
                    message=(
                        f"Required parameter '{param}' is missing for capability "
                        f"'{capability_name}'."
                    ),
                )
            )
            continue
        prop_schema = properties.get(param, {})
        declared_type = prop_schema.get("type")
        if declared_type is not None:
            value = effective[param]
            if not _check_json_type(value, declared_type):
                errors.append(
                    ValidationError(
                        field=f"{field_prefix}.{param}",
                        message=(
                            f"Parameter '{param}' has type {type(value).__name__!r} "
                            f"but the schema requires type '{declared_type}'."
                        ),
                    )
                )
    return errors


def validate_capability_params(
    capability_name: str,
    params: dict[str, Any],
    field_prefix: str = "config",
) -> ValidationResult:
    """Validate params against a registered capability's JSON Schema.

    Looks up the capability in the global registry. Used for Rule.config
    validation where the capability name is the source of truth.
    """
    errors: list[ValidationError] = []

    _ensure_capabilities_registered()
    if not capability_name or capability_name not in REGISTRY:
        registered = sorted(REGISTRY.keys())
        errors.append(
            ValidationError(
                field="capability",
                message=(
                    f"Capability '{capability_name}' is not registered. "
                    f"Available capabilities: {registered}"
                ),
            )
        )
        return ValidationResult(valid=False, errors=errors)

    schema = REGISTRY[capability_name]().get_schema()
    errors.extend(
        _validate_params_against_schema(schema, params, field_prefix, capability_name)
    )
    return ValidationResult(valid=len(errors) == 0, errors=errors)


def validate_params_for_instance(
    capability_instance: Any,
    params: dict[str, Any],
    field_prefix: str = "params",
) -> ValidationResult:
    """Validate params against an already-resolved capability instance.

    Used by PipelineExecutor / GraphExecutor which hold the capability
    instance via their injected ``capability_getter`` (which may be a test
    stub, not the global registry).
    """
    if capability_instance is None or not hasattr(capability_instance, "get_schema"):
        return ValidationResult(valid=True, errors=[])
    try:
        schema = capability_instance.get_schema()
    except Exception:
        return ValidationResult(valid=True, errors=[])
    name = getattr(capability_instance, "name", type(capability_instance).__name__)
    errors = _validate_params_against_schema(schema, params, field_prefix, name)
    return ValidationResult(valid=len(errors) == 0, errors=errors)


def validate_rule(rule: Rule) -> ValidationResult:
    """Validate a Rule before execution.

    Checks:
    - ``rule.name`` is not empty
    - ``rule.capability`` is registered in the capability registry
    - ``rule.config`` contains all params declared as ``required`` in the
      capability's JSON Schema
    - Each required param's value matches the declared JSON Schema type

    Args:
        rule: Rule domain object to validate.

    Returns:
        :class:`ValidationResult` with ``valid=True`` when no errors are found,
        otherwise ``valid=False`` with a populated ``errors`` list.
    """
    errors: list[ValidationError] = []

    if not rule.name or not rule.name.strip():
        errors.append(
            ValidationError(field="name", message="Rule 'name' must not be empty.")
        )

    cap_result = validate_capability_params(
        rule.capability, rule.config, field_prefix="config"
    )
    errors.extend(cap_result.errors)

    return ValidationResult(valid=len(errors) == 0, errors=errors)


def validate_trigger(trigger: Trigger) -> ValidationResult:
    """Validate a Trigger before activation.

    Checks:
    - ``trigger.event`` is a valid :class:`~core.models.TriggerEvent` value
    - ``trigger.rule_id`` is provided (not None)
    - ``trigger.predicates`` are structurally valid (if present)
    - ``trigger.predicate_logic`` is ``"AND"`` or ``"OR"`` (if predicates are present)

    Args:
        trigger: Trigger domain object to validate.

    Returns:
        :class:`ValidationResult` with ``valid=True`` when no errors are found,
        otherwise ``valid=False`` with a populated ``errors`` list.
    """
    errors: list[ValidationError] = []

    # 1. event must be a recognised TriggerEvent
    valid_events = {e.value for e in TriggerEvent}
    event_value = trigger.event.value if isinstance(trigger.event, TriggerEvent) else trigger.event
    if event_value not in valid_events:
        errors.append(
            ValidationError(
                field="event",
                message=(
                    f"TriggerEvent '{event_value}' is not valid. "
                    f"Expected one of: {sorted(valid_events)}"
                ),
            )
        )

    # 2. rule_id required only for trigger types that reference rules
    _RULE_OPTIONAL_TYPES = {"schedule", "api", "esb_event", "webhook_in"}
    tt_val = trigger.trigger_type.value if hasattr(trigger.trigger_type, "value") else str(trigger.trigger_type)
    if trigger.rule_id is None and tt_val not in _RULE_OPTIONAL_TYPES:
        errors.append(
            ValidationError(
                field="rule_id",
                message="Trigger 'rule_id' must not be None (required for this trigger type).",
            )
        )

    # 3. Validate predicates structure (if any)
    if trigger.predicates:
        for idx, pred in enumerate(trigger.predicates):
            pred_errors = _validate_predicate(pred, idx)
            errors.extend(pred_errors)

        # 4. predicate_logic must be AND or OR when predicates are present
        valid_logic = {"AND", "OR"}
        if trigger.predicate_logic not in valid_logic:
            errors.append(
                ValidationError(
                    field="predicate_logic",
                    message=(
                        f"predicate_logic '{trigger.predicate_logic}' is not valid. "
                        f"Expected one of: {sorted(valid_logic)}"
                    ),
                )
            )

    return ValidationResult(valid=len(errors) == 0, errors=errors)


def validate_rules_batch(rules: list[Rule]) -> dict[str, ValidationResult]:
    """Validate a list of rules and return a result per rule ID.

    Args:
        rules: List of :class:`~core.models.Rule` objects to validate.

    Returns:
        Dict mapping ``str(rule.id)`` to its :class:`ValidationResult`.
    """
    return {str(rule.id): validate_rule(rule) for rule in rules}
