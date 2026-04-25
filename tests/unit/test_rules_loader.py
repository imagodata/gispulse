"""Tests for the JSON rules loader."""

import json

import pytest

from rules.loader import load_rules


@pytest.fixture
def rules_file(tmp_path):
    """Create a sample rules JSON file."""
    rules = [
        {
            "name": "buffer_50m",
            "capability": "buffer",
            "config": {"distance": 50},
            "enabled": True,
        },
        {
            "name": "filter_large",
            "capability": "filter",
            "config": {"expression": "value > 10"},
            "enabled": True,
        },
        {
            "name": "disabled_rule",
            "capability": "union",
            "config": {},
            "enabled": False,
        },
    ]
    path = tmp_path / "rules.json"
    path.write_text(json.dumps(rules), encoding="utf-8")
    return path


class TestLoadRules:
    def test_load_rules(self, rules_file):
        rules = load_rules(rules_file)
        assert len(rules) == 3
        assert rules[0].name == "buffer_50m"
        assert rules[0].capability == "buffer"
        assert rules[0].config["distance"] == 50
        assert rules[1].capability == "filter"
        assert rules[2].enabled is False

    def test_auto_order(self, rules_file):
        rules = load_rules(rules_file)
        assert rules[0].order == 0
        assert rules[1].order == 1
        # order should no longer leak into config
        assert "order" not in rules[0].config

    def test_missing_file(self):
        with pytest.raises(FileNotFoundError):
            load_rules("/nonexistent/rules.json")

    def test_invalid_json(self, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text("not json", encoding="utf-8")
        with pytest.raises(json.JSONDecodeError):
            load_rules(bad)

    def test_not_array(self, tmp_path):
        path = tmp_path / "obj.json"
        path.write_text('{"name": "x"}', encoding="utf-8")
        with pytest.raises(ValueError, match="JSON array"):
            load_rules(path)

    def test_empty_array(self, tmp_path):
        path = tmp_path / "empty.json"
        path.write_text("[]", encoding="utf-8")
        rules = load_rules(path)
        assert rules == []

    def test_minimal_rule(self, tmp_path):
        path = tmp_path / "min.json"
        path.write_text('[{"capability": "buffer", "config": {"distance": 10}}]')
        rules = load_rules(path)
        assert rules[0].name == "rule_0"
        assert rules[0].enabled is True
