"""Pricing catalog invariants: all capabilities are Community, Pro = operations."""

from __future__ import annotations

import pytest

from gispulse.core.plugin_hub import _features_for_tier
from gispulse.persistence.tier import TierError, enforce_feature

PRO_ONLY = {
    "postgis",
    "hybrid",
    "esb_triggers",
    "dag_executor",
    "visual_editor",
    "monitoring",
    "cron_pipelines",
}


def test_raster_and_network_are_community_features():
    community = _features_for_tier("community")
    assert {"raster", "network"} <= community


def test_pro_only_features_not_in_community():
    community = _features_for_tier("community")
    assert not (PRO_ONLY & community)
    assert PRO_ONLY <= _features_for_tier("pro")


@pytest.mark.parametrize("feature", ["raster", "network"])
def test_enforce_feature_allows_raster_network_in_community(monkeypatch, feature):
    monkeypatch.setenv("GISPULSE_TIER", "community")
    monkeypatch.delenv("GISPULSE_LICENSE_KEY", raising=False)
    enforce_feature(feature)


@pytest.mark.parametrize("feature", ["postgis", "esb_triggers", "dag_executor"])
def test_enforce_feature_still_blocks_pro_features_in_community(monkeypatch, feature):
    monkeypatch.setenv("GISPULSE_TIER", "community")
    monkeypatch.delenv("GISPULSE_LICENSE_KEY", raising=False)
    with pytest.raises(TierError):
        enforce_feature(feature)
