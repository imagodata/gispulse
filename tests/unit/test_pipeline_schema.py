"""Tests for core.pipeline_schema — JSON Schema validation for v1/v2 pipelines.

This is the gateway between user-supplied JSON and the internal Pipeline
objects. A lax schema accepts bad data; a strict schema blocks valid ones.
Pin the full surface so regressions are caught.
"""
from __future__ import annotations


from core.pipeline_schema import (
    SCHEMA_V1,
    SCHEMA_V2,
    _pick_schema,
    _validate_basic,
    validate_pipeline_json,
)


class TestSchemaShapes:
    def test_v1_schema_identity(self):
        assert SCHEMA_V1["title"] == "GISPulse Rules v1"
        assert SCHEMA_V1["type"] == "array"

    def test_v2_schema_identity(self):
        assert SCHEMA_V2["title"] == "GISPulse Pipeline v2"
        assert SCHEMA_V2["type"] == "object"
        assert SCHEMA_V2["required"] == ["version", "steps"]

    def test_v2_has_predicate_defs(self):
        defs = SCHEMA_V2.get("$defs", {})
        assert "attr_predicate" in defs
        assert "geom_predicate" in defs
        assert "compound_predicate" in defs
        assert "any_predicate" in defs
        assert "step" in defs
        assert "trigger" in defs


# ---------------------------------------------------------------------------
# _pick_schema
# ---------------------------------------------------------------------------


class TestPickSchema:
    def test_list_picks_v1(self):
        assert _pick_schema([]) is SCHEMA_V1
        assert _pick_schema([{"name": "x"}]) is SCHEMA_V1

    def test_dict_picks_v2(self):
        assert _pick_schema({"version": 2}) is SCHEMA_V2

    def test_scalar_returns_none(self):
        assert _pick_schema("string") is None
        assert _pick_schema(42) is None
        assert _pick_schema(None) is None


# ---------------------------------------------------------------------------
# validate_pipeline_json — top-level dispatcher
# ---------------------------------------------------------------------------


class TestValidateTopLevel:
    def test_string_input_returns_error(self):
        errors = validate_pipeline_json("not a pipeline")
        assert len(errors) == 1
        assert "JSON object" in errors[0] or "array" in errors[0]

    def test_int_input_returns_error(self):
        errors = validate_pipeline_json(42)
        assert len(errors) == 1

    def test_none_input_returns_error(self):
        errors = validate_pipeline_json(None)
        assert len(errors) == 1


# ---------------------------------------------------------------------------
# V1 validation — flat rule list
# ---------------------------------------------------------------------------


class TestValidateV1:
    def test_empty_list_is_valid(self):
        assert validate_pipeline_json([]) == []

    def test_valid_v1_rule(self):
        data = [{"name": "buf", "capability": "buffer", "config": {"distance": 10}}]
        assert validate_pipeline_json(data) == []

    def test_v1_missing_capability_is_error(self):
        data = [{"name": "rule_without_cap"}]
        errors = validate_pipeline_json(data)
        assert any("capability" in e.lower() for e in errors)

    def test_v1_item_not_object_is_error(self):
        data = ["not a dict"]
        errors = validate_pipeline_json(data)
        assert errors

    def test_v1_enabled_bool_accepted(self):
        data = [{"capability": "buffer", "enabled": True}]
        assert validate_pipeline_json(data) == []

    def test_v1_extra_field_rejected(self):
        """additionalProperties: False should catch typos."""
        data = [{"capability": "buffer", "unknown_field": "x"}]
        errors = validate_pipeline_json(data)
        assert any("unknown_field" in e or "additional" in e.lower() for e in errors)


# ---------------------------------------------------------------------------
# V2 validation — pipeline object
# ---------------------------------------------------------------------------


class TestValidateV2:
    def test_minimal_valid_v2(self):
        data = {
            "version": 2,
            "steps": [{"id": "s1", "capability": "buffer", "params": {"distance": 10}}],
        }
        assert validate_pipeline_json(data) == []

    def test_v2_missing_version(self):
        data = {"steps": [{"id": "s1"}]}
        errors = validate_pipeline_json(data)
        assert any("version" in e.lower() for e in errors)

    def test_v2_wrong_version_value(self):
        data = {"version": 3, "steps": [{"id": "s1"}]}
        errors = validate_pipeline_json(data)
        assert any("version" in e.lower() or "2" in e for e in errors)

    def test_v2_missing_steps(self):
        data = {"version": 2}
        errors = validate_pipeline_json(data)
        assert any("steps" in e.lower() for e in errors)

    def test_v2_empty_steps_is_error(self):
        data = {"version": 2, "steps": []}
        errors = validate_pipeline_json(data)
        assert any("steps" in e.lower() or "minItems" in e for e in errors)

    def test_v2_step_missing_id(self):
        data = {"version": 2, "steps": [{"capability": "buffer"}]}
        errors = validate_pipeline_json(data)
        assert any("id" in e.lower() for e in errors)

    def test_v2_step_with_when_predicate(self):
        data = {
            "version": 2,
            "steps": [
                {
                    "id": "s1",
                    "capability": "buffer",
                    "when": {"type": "attr", "field": "zone", "op": "eq", "value": "urban"},
                }
            ],
        }
        assert validate_pipeline_json(data) == []

    def test_v2_invalid_attr_op_rejected(self):
        data = {
            "version": 2,
            "steps": [
                {
                    "id": "s1",
                    "capability": "buffer",
                    "when": {"type": "attr", "field": "x", "op": "INVALID"},
                }
            ],
        }
        errors = validate_pipeline_json(data)
        assert errors

    def test_v2_geom_predicate_valid(self):
        data = {
            "version": 2,
            "steps": [
                {
                    "id": "s1",
                    "capability": "filter",
                    "when": {"type": "geom", "op": "intersects", "ref_table": "zones"},
                }
            ],
        }
        assert validate_pipeline_json(data) == []

    def test_v2_compound_predicate_valid(self):
        data = {
            "version": 2,
            "steps": [
                {
                    "id": "s1",
                    "capability": "filter",
                    "when": {
                        "type": "compound",
                        "logic": "AND",
                        "predicates": [
                            {"type": "attr", "field": "x", "op": "eq", "value": 1},
                            {"type": "geom", "op": "within", "ref_table": "zones"},
                        ],
                    },
                }
            ],
        }
        assert validate_pipeline_json(data) == []

    def test_v2_compound_empty_predicates_rejected(self):
        data = {
            "version": 2,
            "steps": [
                {
                    "id": "s1",
                    "capability": "filter",
                    "when": {"type": "compound", "logic": "AND", "predicates": []},
                }
            ],
        }
        errors = validate_pipeline_json(data)
        assert errors

    def test_v2_trigger_valid(self):
        data = {
            "version": 2,
            "steps": [{"id": "s1", "capability": "buffer"}],
            "triggers": [
                {
                    "on": "dml:parcels:INSERT",
                    "then": "run_pipeline",
                    "when": [
                        {"type": "attr", "field": "area", "op": "gt", "value": 1000}
                    ],
                }
            ],
        }
        assert validate_pipeline_json(data) == []

    def test_v2_trigger_missing_on(self):
        data = {
            "version": 2,
            "steps": [{"id": "s1"}],
            "triggers": [{"then": "run_pipeline"}],
        }
        errors = validate_pipeline_json(data)
        assert any("on" in e.lower() for e in errors)

    def test_v2_trigger_missing_then(self):
        data = {
            "version": 2,
            "steps": [{"id": "s1"}],
            "triggers": [{"on": "manual"}],
        }
        errors = validate_pipeline_json(data)
        assert any("then" in e.lower() for e in errors)

    def test_v2_trigger_invalid_then_value(self):
        data = {
            "version": 2,
            "steps": [{"id": "s1"}],
            "triggers": [{"on": "manual", "then": "INVALID_ACTION"}],
        }
        errors = validate_pipeline_json(data)
        assert errors

    def test_v2_ref_layers(self):
        data = {
            "version": 2,
            "steps": [{"id": "s1"}],
            "ref_layers": {"zones": "/path/to/zones.gpkg"},
        }
        assert validate_pipeline_json(data) == []

    def test_v2_extra_top_level_field_rejected(self):
        data = {
            "version": 2,
            "steps": [{"id": "s1"}],
            "unknown_field": "x",
        }
        errors = validate_pipeline_json(data)
        assert any("unknown_field" in e or "additional" in e.lower() for e in errors)


# ---------------------------------------------------------------------------
# _validate_basic — fallback without jsonschema
# ---------------------------------------------------------------------------


class TestBasicFallback:
    def test_v1_basic_missing_name(self):
        errors = _validate_basic(
            [{"capability": "buffer"}], SCHEMA_V1
        )
        assert any("name" in e for e in errors)

    def test_v1_basic_missing_capability(self):
        errors = _validate_basic(
            [{"name": "x"}], SCHEMA_V1
        )
        assert any("capability" in e for e in errors)

    def test_v1_basic_non_dict_item(self):
        errors = _validate_basic(["not a dict"], SCHEMA_V1)
        assert errors

    def test_v2_basic_missing_version(self):
        errors = _validate_basic({"steps": [{"id": "s1"}]}, SCHEMA_V2)
        assert any("version" in e for e in errors)

    def test_v2_basic_wrong_version(self):
        errors = _validate_basic(
            {"version": 99, "steps": [{"id": "s1"}]}, SCHEMA_V2
        )
        assert any("version" in e for e in errors)

    def test_v2_basic_missing_steps(self):
        errors = _validate_basic({"version": 2}, SCHEMA_V2)
        assert any("steps" in e for e in errors)

    def test_v2_basic_empty_steps(self):
        errors = _validate_basic({"version": 2, "steps": []}, SCHEMA_V2)
        assert errors

    def test_v2_basic_steps_not_array(self):
        errors = _validate_basic(
            {"version": 2, "steps": "not array"}, SCHEMA_V2
        )
        assert errors

    def test_v2_basic_step_missing_id(self):
        errors = _validate_basic(
            {"version": 2, "steps": [{"capability": "buffer"}]}, SCHEMA_V2
        )
        assert any("id" in e.lower() for e in errors)

    def test_v2_basic_step_name_accepted_as_id_alias(self):
        errors = _validate_basic(
            {"version": 2, "steps": [{"name": "s1"}]}, SCHEMA_V2
        )
        # name is an accepted alias for id in basic fallback
        assert not any("id" in e.lower() for e in errors)

    def test_v2_basic_trigger_missing_on(self):
        errors = _validate_basic(
            {
                "version": 2,
                "steps": [{"id": "s1"}],
                "triggers": [{"then": "run_pipeline"}],
            },
            SCHEMA_V2,
        )
        assert any("on" in e for e in errors)

    def test_v2_basic_trigger_missing_then(self):
        errors = _validate_basic(
            {
                "version": 2,
                "steps": [{"id": "s1"}],
                "triggers": [{"on": "manual"}],
            },
            SCHEMA_V2,
        )
        assert any("then" in e for e in errors)
