"""Unit tests for the GISPulse validation layer (rules/validation.py)."""

from __future__ import annotations


import geopandas as gpd
import pytest
from shapely.geometry import Point

from core.models import (
    AttrPredicate,
    GeomPredicate,
    Rule,
    Trigger,
    TriggerEvent,
    TriggerType,
)
from rules.validation import (
    ValidationError,
    ValidationResult,
    validate_rule,
    validate_rules_batch,
    validate_trigger,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def valid_rule() -> Rule:
    """Rule that references an existing capability with all required params."""
    return Rule(
        name="buffer_zone",
        capability="buffer",
        config={"distance": 100.0},
    )


@pytest.fixture
def valid_trigger(valid_rule: Rule) -> Trigger:
    """Trigger that references an existing rule and a valid event."""
    return Trigger(
        name="on_geometry_change",
        event=TriggerEvent.GEOMETRY_CHANGED,
        trigger_type=TriggerType.DML,
        rule_id=valid_rule.id,
    )


# ---------------------------------------------------------------------------
# ValidationResult and ValidationError basics
# ---------------------------------------------------------------------------


class TestValidationTypes:
    def test_validation_error_has_field_and_message(self):
        err = ValidationError(field="capability", message="not found")
        assert err.field == "capability"
        assert err.message == "not found"

    def test_validation_result_valid(self):
        result = ValidationResult(valid=True)
        assert result.valid is True
        assert result.errors == []

    def test_validation_result_invalid_with_errors(self):
        err = ValidationError(field="name", message="must not be empty")
        result = ValidationResult(valid=False, errors=[err])
        assert result.valid is False
        assert len(result.errors) == 1


# ---------------------------------------------------------------------------
# validate_rule — happy paths
# ---------------------------------------------------------------------------


class TestValidateRuleValid:
    def test_valid_buffer_rule_passes(self, valid_rule: Rule):
        result = validate_rule(valid_rule)
        assert result.valid is True
        assert result.errors == []

    def test_valid_reproject_rule_passes(self):
        rule = Rule(
            name="to_lambert",
            capability="reproject",
            config={"target_crs": "EPSG:2154"},
        )
        result = validate_rule(rule)
        assert result.valid is True

    def test_valid_filter_rule_passes(self):
        rule = Rule(
            name="big_features",
            capability="filter",
            config={"expression": "area > 100"},
        )
        result = validate_rule(rule)
        assert result.valid is True

    def test_valid_union_rule_no_required_params(self):
        """Union has no required params — empty config is valid."""
        rule = Rule(name="merge_all", capability="union", config={})
        result = validate_rule(rule)
        assert result.valid is True

    def test_valid_dissolve_rule_no_required_params(self):
        rule = Rule(name="dissolve", capability="dissolve", config={})
        result = validate_rule(rule)
        assert result.valid is True

    def test_optional_params_do_not_trigger_errors(self):
        """crs_meters is optional in buffer — providing it should still pass."""
        rule = Rule(
            name="buf",
            capability="buffer",
            config={"distance": 50.0, "crs_meters": "EPSG:2154"},
        )
        result = validate_rule(rule)
        assert result.valid is True


# ---------------------------------------------------------------------------
# validate_rule — failure cases
# ---------------------------------------------------------------------------


class TestValidateRuleInvalid:
    def test_empty_name_fails(self):
        rule = Rule(name="", capability="buffer", config={"distance": 10.0})
        result = validate_rule(rule)
        assert result.valid is False
        fields = [e.field for e in result.errors]
        assert "name" in fields

    def test_whitespace_name_fails(self):
        rule = Rule(name="   ", capability="buffer", config={"distance": 10.0})
        result = validate_rule(rule)
        assert result.valid is False
        assert any(e.field == "name" for e in result.errors)

    def test_unknown_capability_fails(self):
        rule = Rule(
            name="bad_rule",
            capability="nonexistent_capability",
            config={},
        )
        result = validate_rule(rule)
        assert result.valid is False
        assert any(e.field == "capability" for e in result.errors)

    def test_unknown_capability_error_lists_available(self):
        rule = Rule(name="r", capability="does_not_exist", config={})
        result = validate_rule(rule)
        error = next(e for e in result.errors if e.field == "capability")
        assert "buffer" in error.message  # registered capabilities listed

    def test_missing_required_param_fails(self):
        """reproject requires 'target_crs' — omitting it must fail."""
        rule = Rule(name="rp", capability="reproject", config={})
        result = validate_rule(rule)
        assert result.valid is False
        assert any(e.field == "config.target_crs" for e in result.errors)

    def test_missing_required_param_error_message_is_clear(self):
        rule = Rule(name="rp", capability="reproject", config={})
        result = validate_rule(rule)
        error = next(e for e in result.errors if e.field == "config.target_crs")
        assert "target_crs" in error.message
        assert "reproject" in error.message

    def test_wrong_type_for_required_param_fails(self):
        """reproject.target_crs must be a string — passing an int should fail."""
        rule = Rule(
            name="rp",
            capability="reproject",
            config={"target_crs": 4326},  # int instead of string
        )
        result = validate_rule(rule)
        assert result.valid is False
        assert any(e.field == "config.target_crs" for e in result.errors)

    def test_wrong_type_error_message_mentions_expected_type(self):
        rule = Rule(
            name="rp",
            capability="reproject",
            config={"target_crs": 4326},
        )
        result = validate_rule(rule)
        error = next(e for e in result.errors if e.field == "config.target_crs")
        assert "string" in error.message

    def test_filter_with_no_params_is_valid(self):
        """filter now accepts optional params (expression or spatial_predicate)."""
        rule = Rule(name="f", capability="filter", config={})
        result = validate_rule(rule)
        assert result.valid is True

    def test_missing_required_param_for_reproject(self):
        """reproject requires 'target_crs'."""
        rule = Rule(name="r", capability="reproject", config={})
        result = validate_rule(rule)
        assert result.valid is False
        assert any(e.field == "config.target_crs" for e in result.errors)

    def test_multiple_errors_accumulated(self):
        """Empty name + missing required param should produce 2+ errors."""
        rule = Rule(name="", capability="reproject", config={})
        result = validate_rule(rule)
        assert result.valid is False
        assert len(result.errors) >= 2


# ---------------------------------------------------------------------------
# validate_trigger — happy paths
# ---------------------------------------------------------------------------


class TestValidateTriggerValid:
    def test_valid_trigger_passes(self, valid_trigger: Trigger):
        result = validate_trigger(valid_trigger)
        assert result.valid is True
        assert result.errors == []

    def test_all_trigger_events_are_valid(self, valid_rule: Rule):
        for event in TriggerEvent:
            trigger = Trigger(
                name="t",
                event=event,
                rule_id=valid_rule.id,
            )
            result = validate_trigger(trigger)
            assert result.valid is True, f"event={event} should be valid"

    def test_trigger_with_geom_predicate_passes(self, valid_rule: Rule):
        trigger = Trigger(
            name="t",
            event=TriggerEvent.GEOMETRY_CHANGED,
            rule_id=valid_rule.id,
            predicates=[
                GeomPredicate(op="intersects", ref_table="public.zones_n2000")
            ],
            predicate_logic="AND",
        )
        result = validate_trigger(trigger)
        assert result.valid is True

    def test_trigger_with_attr_predicate_passes(self, valid_rule: Rule):
        trigger = Trigger(
            name="t",
            event=TriggerEvent.DATA_CHANGED,
            rule_id=valid_rule.id,
            predicates=[
                AttrPredicate(field="status", op="eq", value="active")
            ],
            predicate_logic="OR",
        )
        result = validate_trigger(trigger)
        assert result.valid is True

    def test_trigger_without_predicates_does_not_check_logic(self, valid_rule: Rule):
        """predicate_logic validation only applies when predicates are present."""
        trigger = Trigger(
            name="t",
            event=TriggerEvent.MANUAL,
            rule_id=valid_rule.id,
            predicates=[],
            predicate_logic="AND",
        )
        result = validate_trigger(trigger)
        assert result.valid is True


# ---------------------------------------------------------------------------
# validate_trigger — failure cases
# ---------------------------------------------------------------------------


class TestValidateTriggerInvalid:
    def test_missing_rule_id_fails(self):
        trigger = Trigger(
            name="t",
            event=TriggerEvent.MANUAL,
            rule_id=None,
        )
        result = validate_trigger(trigger)
        assert result.valid is False
        assert any(e.field == "rule_id" for e in result.errors)

    def test_invalid_geom_predicate_op_fails(self, valid_rule: Rule):
        trigger = Trigger(
            name="t",
            event=TriggerEvent.GEOMETRY_CHANGED,
            rule_id=valid_rule.id,
            predicates=[
                GeomPredicate(op="invalid_op", ref_table="public.zones")
            ],
        )
        result = validate_trigger(trigger)
        assert result.valid is False
        assert any("op" in e.field for e in result.errors)

    def test_geom_predicate_distance_op_without_distance_fails(self, valid_rule: Rule):
        trigger = Trigger(
            name="t",
            event=TriggerEvent.GEOMETRY_CHANGED,
            rule_id=valid_rule.id,
            predicates=[
                GeomPredicate(op="distance_lt", ref_table="public.zones", distance=None)
            ],
        )
        result = validate_trigger(trigger)
        assert result.valid is False
        assert any("distance" in e.field for e in result.errors)

    def test_geom_predicate_empty_ref_table_fails(self, valid_rule: Rule):
        trigger = Trigger(
            name="t",
            event=TriggerEvent.GEOMETRY_CHANGED,
            rule_id=valid_rule.id,
            predicates=[
                GeomPredicate(op="intersects", ref_table="")
            ],
        )
        result = validate_trigger(trigger)
        assert result.valid is False
        assert any("ref_table" in e.field for e in result.errors)

    def test_attr_predicate_invalid_op_fails(self, valid_rule: Rule):
        trigger = Trigger(
            name="t",
            event=TriggerEvent.DATA_CHANGED,
            rule_id=valid_rule.id,
            predicates=[
                AttrPredicate(field="status", op="INVALID", value="x")
            ],
        )
        result = validate_trigger(trigger)
        assert result.valid is False

    def test_attr_predicate_empty_field_fails(self, valid_rule: Rule):
        trigger = Trigger(
            name="t",
            event=TriggerEvent.DATA_CHANGED,
            rule_id=valid_rule.id,
            predicates=[
                AttrPredicate(field="", op="eq", value="x")
            ],
        )
        result = validate_trigger(trigger)
        assert result.valid is False
        assert any("field" in e.field for e in result.errors)

    def test_invalid_predicate_logic_fails(self, valid_rule: Rule):
        trigger = Trigger(
            name="t",
            event=TriggerEvent.DATA_CHANGED,
            rule_id=valid_rule.id,
            predicates=[AttrPredicate(field="x", op="eq", value=1)],
            predicate_logic="XOR",  # invalid
        )
        result = validate_trigger(trigger)
        assert result.valid is False
        assert any(e.field == "predicate_logic" for e in result.errors)

    def test_trigger_or_logic_is_valid(self, valid_rule: Rule):
        trigger = Trigger(
            name="t",
            event=TriggerEvent.DATA_CHANGED,
            rule_id=valid_rule.id,
            predicates=[AttrPredicate(field="x", op="eq", value=1)],
            predicate_logic="OR",
        )
        result = validate_trigger(trigger)
        assert result.valid is True


# ---------------------------------------------------------------------------
# validate_rules_batch
# ---------------------------------------------------------------------------


class TestValidateRulesBatch:
    def test_batch_returns_result_per_rule(self):
        rules = [
            Rule(name="buf", capability="buffer", config={"distance": 10.0}),
            Rule(name="reproj", capability="reproject", config={"target_crs": "EPSG:2154"}),
        ]
        results = validate_rules_batch(rules)
        assert len(results) == 2
        for rule in rules:
            assert str(rule.id) in results

    def test_batch_all_valid(self):
        rules = [
            Rule(name="buf", capability="buffer", config={"distance": 10.0}),
            Rule(name="union", capability="union", config={}),
        ]
        results = validate_rules_batch(rules)
        for rule in rules:
            assert results[str(rule.id)].valid is True

    def test_batch_mixed_valid_and_invalid(self):
        valid = Rule(name="buf", capability="buffer", config={"distance": 10.0})
        invalid = Rule(name="bad", capability="nonexistent", config={})
        results = validate_rules_batch([valid, invalid])
        assert results[str(valid.id)].valid is True
        assert results[str(invalid.id)].valid is False

    def test_batch_all_invalid(self):
        rules = [
            Rule(name="", capability="nonexistent_1", config={}),
            Rule(name="r2", capability="nonexistent_2", config={}),
        ]
        results = validate_rules_batch(rules)
        for rule in rules:
            assert results[str(rule.id)].valid is False

    def test_batch_empty_list(self):
        results = validate_rules_batch([])
        assert results == {}

    def test_batch_result_ids_are_strings(self):
        rule = Rule(name="buf", capability="buffer", config={"distance": 5.0})
        results = validate_rules_batch([rule])
        key = next(iter(results))
        assert isinstance(key, str)


# ---------------------------------------------------------------------------
# Integration: RuleEngine raises ValueError for invalid rules
# ---------------------------------------------------------------------------


class TestRuleEngineValidationIntegration:
    """Verify that RuleEngine.apply() raises ValueError on invalid rules."""

    def _make_gdf(self) -> gpd.GeoDataFrame:
        return gpd.GeoDataFrame(
            {"geometry": [Point(2.35, 48.85)]},
            crs="EPSG:4326",
        )

    def test_engine_raises_value_error_for_unknown_capability(self):
        from rules.engine import RuleEngine

        engine = RuleEngine()
        rule = Rule(name="bad", capability="nonexistent", config={})
        with pytest.raises(ValueError, match="failed validation"):
            engine.apply(rule, self._make_gdf())

    def test_engine_raises_value_error_for_missing_param(self):
        from rules.engine import RuleEngine

        engine = RuleEngine()
        rule = Rule(name="rp", capability="reproject", config={})  # missing target_crs
        with pytest.raises(ValueError, match="failed validation"):
            engine.apply(rule, self._make_gdf())

    def test_engine_error_message_includes_field_name(self):
        from rules.engine import RuleEngine

        engine = RuleEngine()
        rule = Rule(name="rp", capability="reproject", config={})
        with pytest.raises(ValueError) as exc_info:
            engine.apply(rule, self._make_gdf())
        assert "config.target_crs" in str(exc_info.value)

    def test_engine_applies_valid_rule_without_error(self):
        from rules.engine import RuleEngine

        engine = RuleEngine()
        rule = Rule(name="buf", capability="buffer", config={"distance": 100.0})
        result = engine.apply(rule, self._make_gdf())
        assert len(result) == 1
