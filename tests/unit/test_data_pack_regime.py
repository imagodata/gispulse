"""Tests for the ExtensionHub data-pack regime (v1.8.0 Chantier C).

Data packs are the declarative *data regime* of the ExtensionHub: YAML/
JSON manifests that ship templates / catalog sources / basemaps /
projections and never any code.
"""

from __future__ import annotations

import json

import pytest

from gispulse.core.plugin_hub import ExtensionHub
from gispulse.core.plugin_model import (
    DATA_PACK_CONTENTS,
    DataPackManifest,
    PluginKind,
    PluginState,
)


@pytest.fixture()
def fresh_hub():
    """Force a fresh ExtensionHub discovery, restored afterwards."""
    ExtensionHub.reset()
    yield
    ExtensionHub.reset()


# ---------------------------------------------------------------------------
# DataPackManifest model
# ---------------------------------------------------------------------------


def test_manifest_from_dict_minimal():
    m = DataPackManifest.from_dict({"name": "demo", "content": "template-pack"})
    assert m.name == "demo"
    assert m.content == "template-pack"
    assert m.display_name == "demo"  # defaults to name
    assert m.entries == []


def test_manifest_from_dict_full():
    m = DataPackManifest.from_dict(
        {
            "name": "pack",
            "content": "basemap-pack",
            "version": "2.1.0",
            "display_name": "A pack",
            "description": "desc",
            "tier": "pro",
            "entries": [{"name": "x"}],
        }
    )
    assert m.version == "2.1.0"
    assert m.tier.value == "pro"
    assert len(m.entries) == 1


def test_manifest_rejects_missing_name():
    with pytest.raises(ValueError, match="name"):
        DataPackManifest.from_dict({"content": "template-pack"})


def test_manifest_rejects_unknown_content():
    with pytest.raises(ValueError, match="content"):
        DataPackManifest.from_dict({"name": "x", "content": "not-a-content"})


def test_data_pack_contents_vocabulary():
    assert "template-pack" in DATA_PACK_CONTENTS
    assert "source-catalog" in DATA_PACK_CONTENTS


# ---------------------------------------------------------------------------
# Hub discovery — bundled templates data pack
# ---------------------------------------------------------------------------


def test_hub_discovers_bundled_templates_pack(fresh_hub):
    hub = ExtensionHub.get()
    packs = hub.records_by_kind(PluginKind.DATA_PACK)
    assert any(r.name == "gispulse-templates" for r in packs)

    rec = next(r for r in packs if r.name == "gispulse-templates")
    assert rec.state is PluginState.ACTIVE
    assert rec.kind is PluginKind.DATA_PACK
    assert isinstance(rec.obj, DataPackManifest)


def test_data_pack_manifests_filter_by_content(fresh_hub):
    hub = ExtensionHub.get()
    template_packs = hub.data_pack_manifests("template-pack")
    assert template_packs
    assert all(m.content == "template-pack" for m in template_packs)
    # The bundled pack indexes the built-in templates.
    assert any(m.name == "gispulse-templates" and m.entries for m in template_packs)


def test_data_packs_appear_in_records(fresh_hub):
    """Data packs land in the single unified inventory."""
    hub = ExtensionHub.get()
    names = {r.name for r in hub.records}
    assert "gispulse-templates" in names


# ---------------------------------------------------------------------------
# Hub discovery — user-dir manifests + tier gating
# ---------------------------------------------------------------------------


def test_user_dir_manifest_discovered(tmp_path, monkeypatch, fresh_hub):
    manifest = {
        "name": "user-pack",
        "content": "projection-pack",
        "entries": [{"name": "EPSG:2154"}],
    }
    (tmp_path / "pack.json").write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setenv("GISPULSE_DATA_PACKS_DIR", str(tmp_path))
    ExtensionHub.reset()

    hub = ExtensionHub.get()
    rec = next(
        (r for r in hub.records_by_kind(PluginKind.DATA_PACK) if r.name == "user-pack"),
        None,
    )
    assert rec is not None
    assert rec.origin.value == "external"
    assert rec.state is PluginState.ACTIVE


def test_enterprise_tier_pack_is_locked(tmp_path, monkeypatch, fresh_hub):
    """A pack above the licence tier is LOCKED, never ACTIVE."""
    manifest = {
        "name": "ent-pack",
        "content": "source-catalog",
        "tier": "enterprise",
        "entries": [],
    }
    (tmp_path / "ent.json").write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setenv("GISPULSE_DATA_PACKS_DIR", str(tmp_path))
    monkeypatch.delenv("GISPULSE_TIER", raising=False)
    ExtensionHub.reset()

    hub = ExtensionHub.get()
    rec = next(
        (r for r in hub.records_by_kind(PluginKind.DATA_PACK) if r.name == "ent-pack"),
        None,
    )
    assert rec is not None
    assert rec.state is PluginState.LOCKED
    # LOCKED packs are excluded from the active-manifest view.
    assert "ent-pack" not in {m.name for m in hub.data_pack_manifests()}


def test_malformed_manifest_is_skipped(tmp_path, monkeypatch, fresh_hub):
    """A bad manifest is logged and skipped — discovery never hard-fails."""
    (tmp_path / "bad.json").write_text("{ not json", encoding="utf-8")
    (tmp_path / "nameless.json").write_text(
        json.dumps({"content": "template-pack"}), encoding="utf-8"
    )
    monkeypatch.setenv("GISPULSE_DATA_PACKS_DIR", str(tmp_path))
    ExtensionHub.reset()

    hub = ExtensionHub.get()
    # The bundled pack still discovered; the bad files produced no record.
    assert any(
        r.name == "gispulse-templates"
        for r in hub.records_by_kind(PluginKind.DATA_PACK)
    )


# ---------------------------------------------------------------------------
# GISPulseApp — the data-pack regime on the application façade
# ---------------------------------------------------------------------------


def test_app_list_data_packs(fresh_hub):
    import gispulse

    packs = gispulse.GISPulseApp().list_data_packs()
    assert isinstance(packs, list)
    templates = next(p for p in packs if p["name"] == "gispulse-templates")
    assert templates["content"] == "template-pack"
    assert templates["entry_count"] > 0
    assert templates["tier"] == "community"


def test_app_list_templates_sourced_from_manifest(fresh_hub):
    """list_templates() now carries the manifest's ``steps`` field."""
    import gispulse

    templates = gispulse.GISPulseApp().list_templates()
    assert templates
    assert {"name", "title", "description", "steps"} <= set(templates[0])
