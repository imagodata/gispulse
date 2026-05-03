"""Unit tests for the Attach-trigger dock-widget state machine (v1.4-3).

Only the Qt-free `state` module is exercised here. The actual `QDockWidget`
needs a running QGIS / Qt loop and is covered by manual review on each
QGIS env (OSGeo4W, Standalone, Homebrew) per the issue acceptance.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from qgis_plugin.ui.state import (
    ALLOWED_RULES_SUFFIXES,
    CUSTOM_PROPERTY_KEY,
    AttachState,
    validate_rules_file,
)


@pytest.fixture
def yaml_file(tmp_path: Path) -> Path:
    p = tmp_path / "rules.yaml"
    p.write_text("rules:\n  - name: demo\n", encoding="utf-8")
    return p


@pytest.fixture
def yml_file(tmp_path: Path) -> Path:
    p = tmp_path / "rules.yml"
    p.write_text("rules: []\n", encoding="utf-8")
    return p


class TestValidateRulesFile:
    def test_none_is_invalid_with_empty_message(self) -> None:
        v = validate_rules_file(None)
        assert v.valid is False
        assert v.message == ""

    def test_empty_string_is_invalid_with_empty_message(self) -> None:
        v = validate_rules_file("")
        assert v.valid is False
        assert v.message == ""

    def test_missing_file(self, tmp_path: Path) -> None:
        v = validate_rules_file(tmp_path / "nope.yml")
        assert v.valid is False
        assert "not found" in v.message.lower()

    @pytest.mark.parametrize("suffix", [".txt", ".json", ".yamlx", ""])
    def test_wrong_suffix(self, tmp_path: Path, suffix: str) -> None:
        p = tmp_path / f"rules{suffix}"
        p.write_text("anything", encoding="utf-8")
        v = validate_rules_file(p)
        assert v.valid is False
        assert ".yml" in v.message or ".yaml" in v.message

    def test_yaml_file_is_valid(self, yaml_file: Path) -> None:
        assert validate_rules_file(yaml_file).valid is True

    def test_yml_file_is_valid(self, yml_file: Path) -> None:
        assert validate_rules_file(yml_file).valid is True

    def test_uppercase_extension_is_valid(self, tmp_path: Path) -> None:
        p = tmp_path / "rules.YML"
        p.write_text("x", encoding="utf-8")
        assert validate_rules_file(p).valid is True


class TestAttachState:
    def test_initial_state_cant_run(self) -> None:
        s = AttachState()
        assert s.can_run() is False
        assert s.layer_message() == ""
        assert s.rules_validation().valid is False

    def test_layer_only_cant_run(self) -> None:
        s = AttachState()
        s.set_layer("layer-1", is_vector=True)
        assert s.can_run() is False

    def test_rules_only_cant_run(self, yaml_file: Path) -> None:
        s = AttachState()
        s.set_rules_path(str(yaml_file))
        assert s.can_run() is False

    def test_vector_layer_plus_yaml_can_run(self, yaml_file: Path) -> None:
        s = AttachState()
        s.set_layer("layer-1", is_vector=True)
        s.set_rules_path(str(yaml_file))
        assert s.can_run() is True

    def test_non_vector_layer_blocks_run(self, yaml_file: Path) -> None:
        s = AttachState()
        s.set_layer("layer-raster", is_vector=False)
        s.set_rules_path(str(yaml_file))
        assert s.can_run() is False
        assert "vector" in s.layer_message().lower()

    def test_clearing_layer_blocks_run(self, yaml_file: Path) -> None:
        s = AttachState()
        s.set_layer("layer-1", is_vector=True)
        s.set_rules_path(str(yaml_file))
        assert s.can_run() is True
        s.set_layer(None, is_vector=False)
        assert s.can_run() is False
        assert s.layer_message() == ""

    def test_clearing_rules_blocks_run(self, yaml_file: Path) -> None:
        s = AttachState()
        s.set_layer("layer-1", is_vector=True)
        s.set_rules_path(str(yaml_file))
        assert s.can_run() is True
        s.set_rules_path(None)
        assert s.can_run() is False

    def test_invalid_rules_extension_blocks_run(self, tmp_path: Path) -> None:
        bad = tmp_path / "rules.txt"
        bad.write_text("x", encoding="utf-8")
        s = AttachState()
        s.set_layer("layer-1", is_vector=True)
        s.set_rules_path(str(bad))
        assert s.can_run() is False
        assert ".yml" in s.rules_validation().message


class TestModuleConstants:
    def test_custom_property_key_is_namespaced(self) -> None:
        # Anything stored on a QgsMapLayer survives project save/reload;
        # the key must stay stable across versions or restored properties
        # silently disappear.
        assert CUSTOM_PROPERTY_KEY == "gispulse/rules_yaml"

    def test_allowed_suffixes_lower_only(self) -> None:
        assert ALLOWED_RULES_SUFFIXES == (".yml", ".yaml")
