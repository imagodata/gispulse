"""Tests for the GISPulse FastMCP facade (adapters/mcp/server.py).

Since v1.8.0 the server is a thin, stateless adapter over
:class:`gispulse.app.GISPulseApp` — every tool is a one-liner onto a
GISPulseApp use-case. FastMCP is an optional dependency; all tests are
skipped when it is not installed.
"""

from __future__ import annotations

import json

import pytest

fastmcp = pytest.importorskip("fastmcp")

# Only import after the skip guard so the module-level ImportError in
# server.py is not triggered in environments without fastmcp.
from gispulse.adapters.mcp import server as mcp_server  # noqa: E402
from gispulse.core import plugin_hub  # noqa: E402


# ---------------------------------------------------------------------------
# Server creation & statelessness
# ---------------------------------------------------------------------------


def test_mcp_server_created():
    """FastMCP server instance exists and has the correct name."""
    assert mcp_server.mcp is not None
    assert mcp_server.mcp.name == "GISPulse"


def test_no_inmemory_repository():
    """Regression guard: the pre-v1.8.0 in-memory repos must stay gone."""
    for attr in ("_rule_repo", "_dataset_repo", "_job_repo"):
        assert not hasattr(mcp_server, attr), (
            f"{attr} re-introduced — the MCP adapter must stay stateless "
            "and delegate to GISPulseApp."
        )


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------


def test_tools_registered():
    """All expected tools are registered on the FastMCP server."""
    import asyncio

    tools = asyncio.run(mcp_server.mcp.list_tools())
    tool_names = {t.name for t in tools}

    expected = {
        "list_capabilities",
        "get_capability_info",
        "browse_catalog",
        "get_catalog_entry",
        "list_templates",
        "get_template",
        "inspect_dataset",
        "validate_pipeline",
        "list_plugins",
        # v1.8.0 MCP epic (#202) — trigger / change-log / source surface.
        "list_sources",
        "load_triggers",
        "list_triggers",
        "validate_triggers",
        "inspect_changelog",
        "watch_status",
        "dryrun_trigger",
        # v1.9.0 worldwide aggregator (A13, #239) — data.gouv.fr probe.
        "refresh_worldwide_catalog",
    }
    missing = expected - tool_names
    assert not missing, f"Missing tools: {missing}"


def test_refresh_worldwide_catalog_tool(monkeypatch):
    """The A13 (#239) catalogue-freshness tool runs without touching the net."""
    from gispulse.plugins import datagouv_refresh

    monkeypatch.setattr(
        datagouv_refresh, "_probe_datagouv", lambda ref, *, base: "2026-01-01"
    )
    report = mcp_server.refresh_worldwide_catalog()
    assert "error" not in report
    assert report["checked"] >= 1
    assert all("id" in rec for rec in report["entries"])


def test_plugin_tool_registered(monkeypatch):
    """MCP tool entry-points can extend a freshly-created server."""
    import asyncio

    class FakeEntryPoint:
        name = "fake-mcp-tool"

        def load(self):
            class FakeToolFactory:
                name = "fake-mcp-tool"

                def register(self, mcp):
                    @mcp.tool()
                    def plugin_ping() -> str:
                        return "pong"

            return FakeToolFactory

    def fake_entry_points(group: str | None = None, **_kwargs):
        if group == "gispulse.mcp_tools":
            return [FakeEntryPoint()]
        return []

    monkeypatch.setattr(plugin_hub, "entry_points", fake_entry_points)
    plugin_hub.ExtensionHub.reset()

    server = mcp_server.create_mcp_server()
    tools = asyncio.run(server.list_tools())
    tool_names = {t.name for t in tools}

    assert "plugin_ping" in tool_names

    plugin_hub.ExtensionHub.reset()


# ---------------------------------------------------------------------------
# Capability tools
# ---------------------------------------------------------------------------


def test_list_capabilities_returns_list():
    """list_capabilities() returns a non-empty list of capability dicts."""
    caps = mcp_server.list_capabilities()

    assert isinstance(caps, list)
    assert len(caps) > 0
    for cap in caps:
        assert {"name", "description", "schema"} <= set(cap)


def test_list_capabilities_includes_buffer():
    """list_capabilities() includes the 'buffer' capability."""
    names = {c["name"] for c in mcp_server.list_capabilities()}
    assert "buffer" in names


def test_get_capability_info_known():
    """get_capability_info() returns correct metadata for 'buffer'."""
    info = mcp_server.get_capability_info("buffer")

    assert "error" not in info
    assert info["name"] == "buffer"
    assert "description" in info
    assert "schema" in info


def test_get_capability_info_unknown():
    """get_capability_info() returns an error dict for unknown capabilities."""
    info = mcp_server.get_capability_info("nonexistent_capability_xyz")

    assert "error" in info
    assert "available" in info


# ---------------------------------------------------------------------------
# Catalog tools
# ---------------------------------------------------------------------------


def test_browse_catalog_returns_list():
    """browse_catalog() returns a list of catalog entry dicts."""
    entries = mcp_server.browse_catalog(limit=5)
    assert isinstance(entries, list)


def test_browse_catalog_invalid_domain():
    """browse_catalog() rejects an unknown domain with an error entry."""
    result = mcp_server.browse_catalog(domain="not-a-domain")
    assert result and "error" in result[0]
    assert "available" in result[0]


def test_get_catalog_entry_unknown():
    """get_catalog_entry() returns an error dict for an unknown id."""
    result = mcp_server.get_catalog_entry("__no_such_entry__")
    assert "error" in result


# ---------------------------------------------------------------------------
# Template tools
# ---------------------------------------------------------------------------


def test_list_templates_finds_builtins():
    """list_templates() exposes the bundled pipeline templates."""
    templates = mcp_server.list_templates()
    assert isinstance(templates, list)
    assert templates, "expected built-in templates"
    assert {"name", "title", "description"} <= set(templates[0])


def test_get_template_known():
    """get_template() returns the raw JSON of a known template."""
    name = mcp_server.list_templates()[0]["name"]
    tpl = mcp_server.get_template(name)
    assert "error" not in tpl


def test_get_template_unknown():
    """get_template() returns an error dict for an unknown template."""
    result = mcp_server.get_template("__no_such_template__")
    assert "error" in result


# ---------------------------------------------------------------------------
# Dataset / pipeline tools
# ---------------------------------------------------------------------------


def test_inspect_dataset_missing_file():
    """inspect_dataset() returns an error for a non-existent path."""
    result = mcp_server.inspect_dataset("/tmp/does_not_exist_gispulse_test.gpkg")
    assert "error" in result


def test_validate_pipeline_missing_file():
    """validate_pipeline() reports a missing file as invalid."""
    result = mcp_server.validate_pipeline("/tmp/__no_pipeline__.json")
    assert result["valid"] is False
    assert result["errors"]


def test_validate_pipeline_valid(monkeypatch, tmp_path):
    """validate_pipeline() accepts a well-formed v2 pipeline file.

    The MCP workdir is pinned to ``tmp_path`` — since #204 every
    path-bound tool only reads inside the configured workdir.
    """
    monkeypatch.setenv("GISPULSE_MCP_WORKDIR", str(tmp_path))
    spec = {
        "version": 2,
        "name": "smoke",
        "steps": [
            {
                "id": "s1",
                "type": "capability",
                "capability": "buffer",
                "params": {"distance": 5},
            }
        ],
    }
    pipeline_file = tmp_path / "pipeline.json"
    pipeline_file.write_text(json.dumps(spec), encoding="utf-8")

    result = mcp_server.validate_pipeline("pipeline.json")
    assert result["valid"] is True
    assert result["steps"] == 1


def test_validate_pipeline_rejects_path_outside_workdir(monkeypatch, tmp_path):
    """validate_pipeline() refuses a path that escapes the MCP workdir (#204)."""
    monkeypatch.setenv("GISPULSE_MCP_WORKDIR", str(tmp_path))
    result = mcp_server.validate_pipeline("../../../etc/passwd")
    assert result["valid"] is False
    assert any("workdir" in e for e in result["errors"])


def test_inspect_dataset_rejects_path_outside_workdir(monkeypatch, tmp_path):
    """inspect_dataset() refuses a path that escapes the MCP workdir (#204)."""
    monkeypatch.setenv("GISPULSE_MCP_WORKDIR", str(tmp_path))
    result = mcp_server.inspect_dataset("/etc/passwd")
    assert "error" in result
    assert "workdir" in result["error"]


# ---------------------------------------------------------------------------
# Plugin tools
# ---------------------------------------------------------------------------


def test_list_plugins_returns_list():
    """list_plugins() returns JSON-safe plugin record dicts."""
    plugins = mcp_server.list_plugins()
    assert isinstance(plugins, list)
    # Must be JSON-serialisable (PluginRecord.as_dict omits entry_point/obj).
    json.dumps(plugins)


# ---------------------------------------------------------------------------
# MCP Resources
# ---------------------------------------------------------------------------


def test_resource_capabilities_is_valid_json():
    """gispulse://capabilities resource returns valid JSON with capabilities."""
    data = json.loads(mcp_server.resource_capabilities())
    assert isinstance(data, list)
    assert len(data) > 0


def test_resource_templates_is_valid_json():
    """gispulse://templates resource returns valid JSON with templates."""
    data = json.loads(mcp_server.resource_templates())
    assert isinstance(data, list)
    assert len(data) > 0


def test_resource_sources_is_valid_json():
    """gispulse://sources resource returns a valid JSON list."""
    data = json.loads(mcp_server.resource_sources())
    assert isinstance(data, list)


# ===========================================================================
# Trigger / change-log surface (#202) + FS scoping (#204)
# ===========================================================================


@pytest.fixture()
def tracked_gpkg(tmp_path):
    """A real GPKG with a change-tracked ``parcels`` table + one pending row."""
    import sqlite3

    from gispulse.persistence.gpkg_engine import GeoPackageEngine

    gpkg_path = tmp_path / "data.gpkg"
    engine = GeoPackageEngine(path=gpkg_path)
    engine.open()
    try:
        conn = engine._get_conn()  # noqa: SLF001
        conn.execute(
            'CREATE TABLE IF NOT EXISTS "parcels" '
            "(fid INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, status TEXT)"
        )
        conn.commit()
        engine.enable_change_tracking("parcels")
    finally:
        engine.close()
    # Fire the native triggers from a separate connection.
    c = sqlite3.connect(str(gpkg_path))
    try:
        c.execute('INSERT INTO "parcels"(name, status) VALUES (?, ?)', ("a", "pending"))
        c.commit()
    finally:
        c.close()
    return gpkg_path


@pytest.fixture()
def triggers_yaml(tmp_path, tracked_gpkg):
    """A valid triggers.yaml referencing ``tracked_gpkg`` with a webhook action."""
    cfg = tmp_path / "triggers.yaml"
    cfg.write_text(
        "version: 1\n"
        f"gpkg: {tracked_gpkg}\n"
        "triggers:\n"
        "  - name: enrich\n"
        "    table: parcels\n"
        "    when: [INSERT]\n"
        "    actions:\n"
        "      - type: webhook\n"
        "        url: https://example.com/hook\n",
        encoding="utf-8",
    )
    return cfg


@pytest.fixture()
def in_workdir(monkeypatch, tmp_path):
    """Pin the MCP workdir to tmp_path so path-bound tools accept fixtures."""
    monkeypatch.setenv("GISPULSE_MCP_WORKDIR", str(tmp_path))
    return tmp_path


def test_list_sources_returns_list():
    """list_sources() returns a JSON-safe list of data-source plugin dicts."""
    sources = mcp_server.list_sources()
    assert isinstance(sources, list)
    json.dumps(sources)


def test_load_triggers_summary(in_workdir, triggers_yaml):
    """load_triggers() summarises a YAML config."""
    result = mcp_server.load_triggers("triggers.yaml")
    assert "error" not in result
    assert result["trigger_count"] == 1
    t = result["triggers"][0]
    assert t["id"] == "enrich"
    assert t["table"] == "parcels"
    assert t["action_types"] == ["webhook"]
    assert t["enabled"] is True


def test_load_triggers_rejects_path_outside_workdir(in_workdir):
    """load_triggers() refuses a path that escapes the MCP workdir (#204)."""
    result = mcp_server.load_triggers("../../../etc/passwd")
    assert "error" in result
    assert "workdir" in result["error"]


def test_list_triggers_detailed(in_workdir, triggers_yaml):
    """list_triggers() returns per-trigger detail incl. actions."""
    result = mcp_server.list_triggers("triggers.yaml")
    assert "error" not in result
    assert result["trigger_count"] == 1
    trig = result["triggers"][0]
    assert trig["when"] == ["INSERT"]
    assert trig["actions"][0]["type"] == "webhook"


def test_validate_triggers_valid(in_workdir, triggers_yaml):
    """validate_triggers() accepts a config whose table exists in the GPKG."""
    result = mcp_server.validate_triggers("triggers.yaml")
    assert result["valid"] is True
    assert result["errors"] == []


def test_validate_triggers_reports_unknown_table(in_workdir, tmp_path, tracked_gpkg):
    """validate_triggers() flags a trigger pointing at a missing layer."""
    cfg = tmp_path / "bad.yaml"
    cfg.write_text(
        "version: 1\n"
        f"gpkg: {tracked_gpkg}\n"
        "triggers:\n"
        "  - name: x\n"
        "    table: no_such_layer\n"
        "    when: [INSERT]\n"
        "    actions: [{type: log_event}]\n",
        encoding="utf-8",
    )
    result = mcp_server.validate_triggers("bad.yaml")
    assert result["valid"] is False
    assert result["errors"]


def test_validate_triggers_rejects_path_outside_workdir(in_workdir):
    """validate_triggers() refuses a path outside the workdir (#204)."""
    result = mcp_server.validate_triggers("/etc/passwd")
    assert result["valid"] is False
    assert any("workdir" in e for e in result["errors"])


def test_inspect_changelog_tracked(in_workdir, tracked_gpkg):
    """inspect_changelog() reports the pending change-log row."""
    result = mcp_server.inspect_changelog("data.gpkg")
    assert result["tracked"] is True
    assert result["pending"] == 1
    assert result["latest_seq"] >= 1
    assert result["recent"]
    assert result["recent"][0]["table_name"] == "parcels"


def test_inspect_changelog_rejects_path_outside_workdir(in_workdir):
    """inspect_changelog() refuses a path outside the workdir (#204)."""
    result = mcp_server.inspect_changelog("../../../etc/passwd")
    assert "error" in result
    assert "workdir" in result["error"]


def test_watch_status_reports_tracked_layers(in_workdir, tracked_gpkg):
    """watch_status() lists the tracked layers and pending count."""
    result = mcp_server.watch_status("data.gpkg")
    assert "error" not in result
    assert "parcels" in result["tracked_layers"]
    assert set(result["tracked_layers"]["parcels"]) == {"insert", "update", "delete"}
    assert result["pending"] == 1


def test_dryrun_trigger_has_no_side_effects(in_workdir, triggers_yaml, tracked_gpkg):
    """dryrun_trigger() evaluates the config without consuming the change-log
    or firing real webhooks."""
    result = mcp_server.dryrun_trigger("triggers.yaml")
    assert "error" not in result
    assert result["trigger_count"] == 1
    assert result["rows_evaluated"] == 1
    # The webhook is *captured*, not sent.
    assert result["webhook_actions"]
    assert result["webhook_actions"][0]["url"] == "https://example.com/hook"
    # The change-log row stays pending — a real `gispulse watch` still sees it.
    after = mcp_server.inspect_changelog("data.gpkg")
    assert after["pending"] == 1


def test_dryrun_trigger_rejects_path_outside_workdir(in_workdir):
    """dryrun_trigger() refuses a path outside the workdir (#204)."""
    result = mcp_server.dryrun_trigger("../../secrets.yaml")
    assert "error" in result
    assert "workdir" in result["error"]


def test_resource_triggers_template(in_workdir, triggers_yaml):
    """gispulse://triggers/{path} resource template returns the config summary."""
    data = json.loads(mcp_server.resource_triggers("triggers.yaml"))
    assert data["trigger_count"] == 1


def test_resource_changelog_template(in_workdir, tracked_gpkg):
    """gispulse://changelog/{path} resource template returns change-log status."""
    data = json.loads(mcp_server.resource_changelog("data.gpkg"))
    assert data["tracked"] is True
    assert data["pending"] == 1
