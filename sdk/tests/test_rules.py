"""Tests for the rules endpoint."""

from __future__ import annotations


from gispulse_sdk.models import RuleCreate, RuleResponse


RULE_JSON = {
    "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
    "name": "buffer_50m",
    "description": "Apply a 50 m buffer",
    "scope": "global",
    "capability": "buffer",
    "config": {"distance": 50},
    "enabled": True,
}


class TestRulesCRUD:
    def test_create(self, client, mock_api):
        mock_api.post("/rules").respond(200, json=RULE_JSON)
        rule = client.rules.create(
            RuleCreate(name="buffer_50m", capability="buffer", config={"distance": 50})
        )
        assert isinstance(rule, RuleResponse)
        assert rule.name == "buffer_50m"

    def test_list(self, client, mock_api):
        mock_api.get("/rules").respond(200, json=[RULE_JSON])
        rules = client.rules.list()
        assert len(rules) == 1

    def test_get(self, client, mock_api):
        mock_api.get("/rules/3fa85f64-5717-4562-b3fc-2c963f66afa6").respond(200, json=RULE_JSON)
        rule = client.rules.get("3fa85f64-5717-4562-b3fc-2c963f66afa6")
        assert rule.capability == "buffer"

    def test_delete(self, client, mock_api):
        mock_api.delete("/rules/3fa85f64-5717-4562-b3fc-2c963f66afa6").respond(
            200, json={"deleted": True}
        )
        resp = client.rules.delete("3fa85f64-5717-4562-b3fc-2c963f66afa6")
        assert resp["deleted"] is True
