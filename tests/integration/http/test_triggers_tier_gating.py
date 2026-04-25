"""Tier-gating tests for the triggers router (Lot 1 — local_triggers).

Validates the Community vs Pro split for the triggers HTTP API:

  * Community has the ``local_triggers`` feature with caps:
      - max 5 active triggers
      - no webhook / cron / DLQ / cascade>1 in conditions
  * Pro has the parallel ``esb_triggers`` feature, uncapped.

The default test tier is "pro" (cf. ``tests/conftest.py::_reset_tier_env``);
each test below explicitly switches to "community" via ``monkeypatch.setenv``
to override the autouse fixture for that test only.
"""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from gispulse.adapters.http.app import create_app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _use_memory_storage(monkeypatch):
    """Force the in-memory trigger repo so each test starts empty."""
    monkeypatch.setenv("GISPULSE_STORAGE", "memory")


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """Slowapi Limiter is a module-level singleton; its in-memory storage
    persists across TestClient instances. Reset it between tests so the
    ``30/minute`` cap on POST /triggers does not bleed across cases.
    """
    from gispulse.adapters.http.rate_limit import limiter

    try:
        limiter.reset()
    except Exception:
        pass
    yield


@pytest.fixture()
def client() -> TestClient:
    os.environ["GISPULSE_STORAGE"] = "memory"
    return TestClient(create_app())


def _community(monkeypatch) -> None:
    """Switch the active tier to Community for the current test."""
    monkeypatch.setenv("GISPULSE_TIER", "community")
    monkeypatch.delenv("GISPULSE_LICENSE_KEY", raising=False)


def _trigger_payload(name: str = "t1", **overrides):
    payload = {
        "name": name,
        "description": "test",
        "event": "manual",
        "trigger_type": "api",
        "category": "data",
        "severity": "info",
        "conditions": {"table": "parcels"},
        "enabled": True,
        "auto_eval": False,
    }
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# Community caps
# ---------------------------------------------------------------------------


class TestCommunityActiveTriggerCap:
    """Community is capped at 5 active triggers; the 6th create returns 402."""

    def test_community_allows_first_five_triggers(self, client, monkeypatch):
        _community(monkeypatch)
        for i in range(5):
            r = client.post("/triggers", json=_trigger_payload(name=f"trig_{i}"))
            assert r.status_code == 201, r.text

    def test_community_blocks_sixth_active_trigger(self, client, monkeypatch):
        _community(monkeypatch)
        for i in range(5):
            assert client.post("/triggers", json=_trigger_payload(name=f"trig_{i}")).status_code == 201

        r = client.post("/triggers", json=_trigger_payload(name="trig_6"))
        assert r.status_code == 402, r.text
        assert "Upgrade to Pro" in r.text or "esb_triggers" in r.text

    def test_community_disabled_triggers_do_not_count_toward_cap(self, client, monkeypatch):
        _community(monkeypatch)
        # 5 disabled triggers → 6th enabled trigger is still allowed.
        for i in range(5):
            assert (
                client.post(
                    "/triggers", json=_trigger_payload(name=f"trig_{i}", enabled=False)
                ).status_code
                == 201
            )
        r = client.post("/triggers", json=_trigger_payload(name="trig_active", enabled=True))
        assert r.status_code == 201, r.text


class TestCommunityForbiddenConfig:
    """Webhook / cron / DLQ / cascade>1 are rejected with 402 in Community."""

    @pytest.mark.parametrize(
        "bad_conditions",
        [
            {"table": "parcels", "webhook": "https://example.com/hook"},
            {"table": "parcels", "outbound_action": {"url": "..."}},
            {"table": "parcels", "cron_schedule": "*/5 * * * *"},
            {"table": "parcels", "dlq_enabled": True},
            {"table": "parcels", "cascade_depth": 3},
        ],
    )
    def test_community_rejects_advanced_config(self, client, monkeypatch, bad_conditions):
        _community(monkeypatch)
        r = client.post(
            "/triggers", json=_trigger_payload(conditions=bad_conditions)
        )
        assert r.status_code == 402, r.text

    def test_community_rejects_webhook_action(self, client, monkeypatch):
        _community(monkeypatch)
        conditions = {
            "table": "parcels",
            "actions": [{"action_type": "webhook", "config": {"url": "..."}}],
        }
        r = client.post("/triggers", json=_trigger_payload(conditions=conditions))
        assert r.status_code == 402, r.text

    def test_community_accepts_simple_conditions(self, client, monkeypatch):
        _community(monkeypatch)
        r = client.post(
            "/triggers",
            json=_trigger_payload(conditions={"table": "parcels", "operations": []}),
        )
        assert r.status_code == 201, r.text


class TestCommunityUpdateRespectsCaps:
    """PUT /triggers/{id} is also gated by the same caps."""

    def test_community_update_can_swap_within_cap(self, client, monkeypatch):
        _community(monkeypatch)
        created = client.post("/triggers", json=_trigger_payload(name="t1")).json()
        # Updating same trigger → does not violate the active-count cap.
        r = client.put(
            f"/triggers/{created['id']}",
            json=_trigger_payload(name="t1_renamed"),
        )
        assert r.status_code == 200, r.text

    def test_community_update_rejects_forbidden_config(self, client, monkeypatch):
        _community(monkeypatch)
        created = client.post("/triggers", json=_trigger_payload(name="t1")).json()
        r = client.put(
            f"/triggers/{created['id']}",
            json=_trigger_payload(
                name="t1",
                conditions={"table": "parcels", "webhook": "https://x"},
            ),
        )
        assert r.status_code == 402, r.text


# ---------------------------------------------------------------------------
# Pro (no caps)
# ---------------------------------------------------------------------------


class TestProTierUncapped:
    """Pro tier has the esb_triggers feature — no caps applied."""

    def test_pro_can_exceed_five_triggers(self, client):
        # autouse conftest fixture already pins tier=pro.
        for i in range(7):
            r = client.post("/triggers", json=_trigger_payload(name=f"prot_{i}"))
            assert r.status_code == 201, r.text

    def test_pro_accepts_webhook_config(self, client):
        r = client.post(
            "/triggers",
            json=_trigger_payload(
                name="pro_webhook",
                conditions={"table": "parcels", "webhook": "https://hook"},
            ),
        )
        assert r.status_code == 201, r.text

    def test_pro_accepts_cron_schedule(self, client):
        r = client.post(
            "/triggers",
            json=_trigger_payload(
                name="pro_cron",
                conditions={"table": "parcels", "cron_schedule": "* * * * *"},
            ),
        )
        assert r.status_code == 201, r.text


# ---------------------------------------------------------------------------
# enforce_feature unit-level smoke
# ---------------------------------------------------------------------------


class TestEnforceFeatureGate:
    """Direct unit test for ``persistence.tier.enforce_feature``."""

    def test_local_triggers_allowed_in_community(self, monkeypatch):
        from persistence.tier import enforce_feature

        _community(monkeypatch)
        # Should not raise.
        enforce_feature("local_triggers")

    def test_local_triggers_allowed_in_pro(self):
        from persistence.tier import enforce_feature

        # Default test tier == pro (community features inherited).
        enforce_feature("local_triggers")

    def test_esb_triggers_blocked_in_community(self, monkeypatch):
        from persistence.tier import TierError, enforce_feature

        _community(monkeypatch)
        with pytest.raises(TierError):
            enforce_feature("esb_triggers")

    def test_esb_triggers_allowed_in_pro(self):
        from persistence.tier import enforce_feature

        enforce_feature("esb_triggers")
