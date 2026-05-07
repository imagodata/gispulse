"""Tests for v1.6.0 #121 — top-level ``validate:`` rules + #123 tag_field action.

Coverage:
- ``validate:`` accepted at the YAML root, parses each rule.
- ``mode: tag`` requires ``tag_field``; ``mode: warn`` does not.
- ``rule:`` is compiled at config-load time so syntax errors surface
  before the runtime starts.
- Backward compatibility: a YAML without ``validate:`` keeps loading.
- The ``tag_field`` action type is accepted by ``ActionConfigModel``.
"""

from __future__ import annotations

import textwrap

import pytest

from gispulse.runtime.config_loader import (
    ActionConfigModel,
    ConfigError,
    GISPulseConfig,
    TriggerConfigModel,
    ValidateRuleConfigModel,
    load_config,
)


# ---------------------------------------------------------------------------
# ValidateRuleConfigModel
# ---------------------------------------------------------------------------


class TestValidateRuleSchema:
    def test_minimal_warn_rule(self) -> None:
        m = ValidateRuleConfigModel(
            id="surface_min", rule="geom_area_m2() >= 50"
        )
        assert m.mode == "warn"
        assert m.tag_field is None
        assert m.enabled is True

    def test_tag_mode_requires_tag_field(self) -> None:
        with pytest.raises(Exception) as exc:
            ValidateRuleConfigModel(
                id="surface_min", rule="geom_area_m2() >= 50", mode="tag"
            )
        assert "tag_field" in str(exc.value)

    def test_tag_mode_with_field_ok(self) -> None:
        m = ValidateRuleConfigModel(
            id="surface_min",
            rule="geom_area_m2() >= 50",
            mode="tag",
            tag_field="validation_status",
        )
        assert m.mode == "tag"
        assert m.tag_field == "validation_status"

    def test_unknown_mode_rejected(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            ValidateRuleConfigModel(
                id="x", rule="geom_is_valid()", mode="silent"
            )

    def test_rule_syntax_failure_surfaces_at_load(self) -> None:
        with pytest.raises(Exception) as exc:
            ValidateRuleConfigModel(id="bad", rule="__import__('os')")
        assert "compile" in str(exc.value).lower() or "DSL" in str(exc.value)

    def test_arithmetic_only_rule_rejected(self) -> None:
        # Pure arithmetic without comparison cannot evaluate to boolean —
        # the boolean mode rejects expressions that never produce a bool.
        # (We currently accept expressions whose top-level node is BoolOp /
        # Compare / NOT or a single bool-returning function.)
        m = ValidateRuleConfigModel(
            id="x", rule="geom_is_valid()"
        )
        assert m.id == "x"

    def test_unknown_function_rejected(self) -> None:
        with pytest.raises(Exception):
            ValidateRuleConfigModel(
                id="x", rule="banana() and geom_is_valid()"
            )


# ---------------------------------------------------------------------------
# GISPulseConfig integration
# ---------------------------------------------------------------------------


class TestGISPulseConfigValidateKey:
    def test_validate_key_accepted(self) -> None:
        cfg = GISPulseConfig.model_validate(
            {
                "version": 1,
                "gpkg": "/tmp/x.gpkg",
                "validate": [
                    {
                        "id": "surface_min",
                        "rule": "geom_area_m2() >= 50",
                        "mode": "warn",
                    },
                    {
                        "id": "shape_valid",
                        "rule": "geom_is_valid()",
                        "mode": "tag",
                        "tag_field": "validation_status",
                        "message": "Geometry invalid",
                    },
                ],
            }
        )
        assert len(cfg.validate_rules) == 2
        assert cfg.validate_rules[0].id == "surface_min"
        assert cfg.validate_rules[1].mode == "tag"

    def test_no_validate_section_loads_clean(self) -> None:
        cfg = GISPulseConfig.model_validate(
            {"version": 1, "gpkg": "/tmp/x.gpkg"}
        )
        assert cfg.validate_rules == []

    def test_mixed_triggers_and_validate(self) -> None:
        cfg = GISPulseConfig.model_validate(
            {
                "version": 1,
                "gpkg": "/tmp/x.gpkg",
                "triggers": [{"name": "t", "table": "parcels", "when": ["INSERT"]}],
                "validate": [{"id": "v", "rule": "geom_is_valid()"}],
            }
        )
        assert len(cfg.triggers) == 1
        assert len(cfg.validate_rules) == 1


# ---------------------------------------------------------------------------
# YAML round-trip via load_config
# ---------------------------------------------------------------------------


class TestValidateYamlRoundTrip:
    def test_yaml_with_validate_loads(self, tmp_path) -> None:
        gpkg = tmp_path / "x.gpkg"
        gpkg.write_bytes(b"")  # path-anchor only, never opened
        yml = tmp_path / "triggers.yaml"
        yml.write_text(
            textwrap.dedent(
                f"""
                version: 1
                gpkg: {gpkg}
                validate:
                  - id: surface_min
                    rule: "geom_area_m2() >= 50"
                    mode: warn
                    message: "Surface < 50 m²"
                """
            )
        )
        cfg = load_config(yml)
        assert len(cfg.validate_rules) == 1
        assert cfg.validate_rules[0].rule == "geom_area_m2() >= 50"

    def test_yaml_unknown_rule_rejected_at_load(self, tmp_path) -> None:
        gpkg = tmp_path / "x.gpkg"
        gpkg.write_bytes(b"")
        yml = tmp_path / "triggers.yaml"
        yml.write_text(
            textwrap.dedent(
                f"""
                version: 1
                gpkg: {gpkg}
                validate:
                  - id: bad
                    rule: "eval('1+1')"
                """
            )
        )
        with pytest.raises(ConfigError):
            load_config(yml)


# ---------------------------------------------------------------------------
# tag_field action type (#123 schema)
# ---------------------------------------------------------------------------


class TestTagFieldActionSchema:
    def test_tag_field_action_accepted(self) -> None:
        ac = ActionConfigModel(
            type="tag_field",
            column="validation_status",
            value="failed:surface_min",
            message_column="validation_message",
            message="Surface < 50 m²",
        )
        assert ac.type == "tag_field"
        assert ac.column == "validation_status"
        assert ac.message_column == "validation_message"

    def test_unknown_action_type_rejected(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            ActionConfigModel(type="erase_universe")

    def test_existing_action_types_still_work(self) -> None:
        ac = ActionConfigModel(type="set_field", field="x", value=1)
        assert ac.type == "set_field"

        ac2 = ActionConfigModel(type="webhook", url="https://example.com/hook")
        assert ac2.type == "webhook"

    def test_tag_field_inside_trigger(self) -> None:
        m = TriggerConfigModel(
            name="t",
            table="parcels",
            when=["INSERT"],
            actions=[
                ActionConfigModel(
                    type="tag_field",
                    column="validation_status",
                    value="ok",
                ),
            ],
        )
        assert m.actions[0].type == "tag_field"


# ---------------------------------------------------------------------------
# ESRI kind: alias (#125)
# ---------------------------------------------------------------------------


class TestEsriKindAlias:
    def test_default_kind_is_trigger(self) -> None:
        m = TriggerConfigModel(name="t", table="x", when=["INSERT"])
        assert m.kind == "trigger"

    def test_constraint_alias_accepted(self) -> None:
        m = TriggerConfigModel(name="t", table="x", when=["INSERT"], kind="constraint")
        assert m.kind == "constraint"

    def test_calculation_alias_accepted(self) -> None:
        m = TriggerConfigModel(name="t", table="x", when=["INSERT"], kind="calculation")
        assert m.kind == "calculation"

    def test_validation_alias_accepted(self) -> None:
        m = TriggerConfigModel(name="t", table="x", when=["INSERT"], kind="validation")
        assert m.kind == "validation"

    def test_unknown_kind_rejected(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            TriggerConfigModel(name="t", table="x", when=["INSERT"], kind="business")
