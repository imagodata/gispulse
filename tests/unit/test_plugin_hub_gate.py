"""Unit tests for the PluginHub tier/trust activation gate (issue #182)."""

from __future__ import annotations

import pytest

from gispulse.core import plugin_hub
from gispulse.core.plugin_contracts import LicenceState
from gispulse.core.plugin_hub import PluginHub
from gispulse.core.plugin_model import (
    Origin,
    PluginKind,
    PluginRecord,
    PluginState,
    Tier,
    Trust,
)


class FakeDist:
    def __init__(self, name: str) -> None:
        self.name = name


class FakeEP:
    def __init__(self, name: str, dist: str | None = None, loader=None) -> None:
        self.name = name
        self.value = f"{name}:register"
        self.dist = FakeDist(dist) if dist else None
        self._loader = loader or (lambda: object())

    def load(self):
        return self._loader()


class FakeLicence:
    name = "fake"

    def __init__(self, tier: str) -> None:
        self._tier = tier

    def current(self) -> LicenceState:
        return LicenceState(org_id=None, tier=self._tier, valid=True)


@pytest.fixture(autouse=True)
def _reset_hub():
    PluginHub.reset()
    yield
    PluginHub.reset()


def _record(name="x", kind=PluginKind.CAPABILITY, **kw) -> PluginRecord:
    return PluginRecord(name=name, kind=kind, **kw)


# --------------------------------------------------------------------------
# _resolve — origin / trust / tier from the curated registry
# --------------------------------------------------------------------------


def test_resolve_external_plugin_from_registry(monkeypatch) -> None:
    monkeypatch.setattr(
        plugin_hub,
        "_curated_registry",
        lambda: {"gispulse-cap-ftth": {"tier": Tier.PRO, "trust": Trust.VERIFIED}},
    )
    hub = PluginHub()
    rec = _record(entry_point=FakeEP("ftth", dist="gispulse-cap-ftth"))
    hub._resolve(rec)
    assert rec.origin is Origin.EXTERNAL
    assert rec.trust is Trust.VERIFIED
    assert rec.tier_required is Tier.PRO


def test_resolve_first_party_distribution(monkeypatch) -> None:
    monkeypatch.setattr(plugin_hub, "_curated_registry", dict)
    hub = PluginHub()
    rec = _record("admin", PluginKind.EXTENSION, entry_point=FakeEP("admin", dist="gispulse-enterprise"))
    hub._resolve(rec)
    assert rec.origin is Origin.INTERNAL
    assert rec.trust is Trust.FIRST_PARTY


def test_resolve_unknown_plugin_keeps_community_defaults(monkeypatch) -> None:
    monkeypatch.setattr(plugin_hub, "_curated_registry", dict)
    hub = PluginHub()
    rec = _record(entry_point=FakeEP("rando", dist="gispulse-cap-rando"))
    hub._resolve(rec)
    assert rec.trust is Trust.COMMUNITY
    assert rec.tier_required is Tier.COMMUNITY


# --------------------------------------------------------------------------
# _gate — tier gate
# --------------------------------------------------------------------------


def test_gate_locks_pro_plugin_under_community_licence() -> None:
    hub = PluginHub()
    hub._licence_tier = Tier.COMMUNITY
    rec = _record(tier_required=Tier.PRO)
    assert hub._gate(rec) is False
    assert "pro" in rec.detail


def test_gate_allows_when_licence_tier_sufficient() -> None:
    hub = PluginHub()
    hub._licence_tier = Tier.ENTERPRISE
    assert hub._gate(_record(tier_required=Tier.PRO)) is True


def test_gate_allows_community_plugin_on_community_licence() -> None:
    hub = PluginHub()
    hub._licence_tier = Tier.COMMUNITY
    assert hub._gate(_record(tier_required=Tier.COMMUNITY)) is True


# --------------------------------------------------------------------------
# _gate — trust gate (GISPULSE_PLUGINS_ALLOW_UNVERIFIED)
# --------------------------------------------------------------------------


def test_gate_allows_unverified_by_default(monkeypatch) -> None:
    monkeypatch.delenv("GISPULSE_PLUGINS_ALLOW_UNVERIFIED", raising=False)
    hub = PluginHub()
    hub._licence_tier = Tier.COMMUNITY
    assert hub._gate(_record(kind=PluginKind.SOURCE, trust=Trust.COMMUNITY)) is True


def test_gate_blocks_unverified_when_disabled(monkeypatch) -> None:
    monkeypatch.setenv("GISPULSE_PLUGINS_ALLOW_UNVERIFIED", "false")
    hub = PluginHub()
    hub._licence_tier = Tier.COMMUNITY
    rec = _record(kind=PluginKind.SOURCE, trust=Trust.COMMUNITY)
    assert hub._gate(rec) is False
    assert "GISPULSE_PLUGINS_ALLOW_UNVERIFIED" in rec.detail


def test_gate_keeps_verified_plugin_when_unverified_disabled(monkeypatch) -> None:
    monkeypatch.setenv("GISPULSE_PLUGINS_ALLOW_UNVERIFIED", "false")
    hub = PluginHub()
    hub._licence_tier = Tier.COMMUNITY
    assert hub._gate(_record(kind=PluginKind.SOURCE, trust=Trust.VERIFIED)) is True


# --------------------------------------------------------------------------
# Integration — a pro plugin is LOCKED, its code never loaded
# --------------------------------------------------------------------------


def test_discover_locks_pro_plugin_under_community(monkeypatch) -> None:
    monkeypatch.setattr(
        plugin_hub,
        "_curated_registry",
        lambda: {"gispulse-cap-ftth": {"tier": Tier.PRO, "trust": Trust.VERIFIED}},
    )
    loaded: list[str] = []
    ep = FakeEP("ftth", dist="gispulse-cap-ftth", loader=lambda: loaded.append("x"))
    monkeypatch.setattr(
        plugin_hub,
        "entry_points",
        lambda group: [ep] if group == "gispulse.capabilities" else [],
    )
    hub = PluginHub()
    hub.licence_provider = FakeLicence("community")
    hub._discover_records()

    rec = hub.records[0]
    assert rec.state is PluginState.LOCKED
    assert rec.tier_required is Tier.PRO
    assert rec.obj is None
    assert loaded == []  # gate refused before the entry-point was loaded


def test_discover_activates_pro_plugin_under_enterprise(monkeypatch) -> None:
    monkeypatch.setattr(
        plugin_hub,
        "_curated_registry",
        lambda: {"gispulse-cap-ftth": {"tier": Tier.PRO, "trust": Trust.VERIFIED}},
    )
    sentinel = object()
    ep = FakeEP("ftth", dist="gispulse-cap-ftth", loader=lambda: sentinel)
    monkeypatch.setattr(
        plugin_hub,
        "entry_points",
        lambda group: [ep] if group == "gispulse.capabilities" else [],
    )
    hub = PluginHub()
    hub.licence_provider = FakeLicence("enterprise")
    hub._discover_records()

    rec = hub.records[0]
    assert rec.state is PluginState.ACTIVE
    assert rec.obj is sentinel


def test_protocol_mismatch_warns_but_still_activates(monkeypatch) -> None:
    class Obj:
        requires_protocol = ">=99.0"  # unsatisfiable — warn-only, not a gate

    ep = FakeEP("x", loader=Obj)
    monkeypatch.setattr(plugin_hub, "_curated_registry", dict)
    monkeypatch.setattr(
        plugin_hub,
        "entry_points",
        lambda group: [ep] if group == "gispulse.protocols" else [],
    )
    hub = PluginHub()
    hub.licence_provider = FakeLicence("community")
    hub._discover_records()
    assert hub.records[0].state is PluginState.ACTIVE
