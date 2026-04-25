"""
Tests for the GISPulse FastMCP facade (adapters/mcp/server.py).

FastMCP is an optional dependency; all tests are skipped when it is not
installed.
"""

from __future__ import annotations

import pytest

fastmcp = pytest.importorskip("fastmcp")

# Only import after the skip guard so the module-level ImportError in
# server.py is not triggered in environments without fastmcp.
from gispulse.adapters.mcp import server as mcp_server  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _clear_state() -> None:
    """Reset all in-memory repositories between tests."""
    mcp_server._rule_repo.clear()
    mcp_server._dataset_repo.clear()
    mcp_server._job_repo.clear()


@pytest.fixture(autouse=True)
def reset_state():
    """Auto-fixture: clear module-level repos before each test."""
    _clear_state()
    yield
    _clear_state()


# ---------------------------------------------------------------------------
# Server creation
# ---------------------------------------------------------------------------


def test_mcp_server_created():
    """FastMCP server instance exists and has the correct name."""
    assert mcp_server.mcp is not None
    assert mcp_server.mcp.name == "GISPulse"


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
        "create_rule",
        "list_rules",
        "validate_rule",
        "delete_rule",
        "list_datasets",
        "load_gpkg",
        "run_job",
    }
    missing = expected - tool_names
    assert not missing, f"Missing tools: {missing}"


# ---------------------------------------------------------------------------
# Capabilities tools
# ---------------------------------------------------------------------------


def test_list_capabilities_returns_list():
    """list_capabilities() returns a non-empty list of capability dicts."""
    caps = mcp_server.list_capabilities()

    assert isinstance(caps, list)
    assert len(caps) > 0

    # Each entry must have the expected keys
    for cap in caps:
        assert "name" in cap
        assert "description" in cap
        assert "schema" in cap


def test_list_capabilities_includes_buffer():
    """list_capabilities() includes the 'buffer' capability."""
    caps = mcp_server.list_capabilities()
    names = {c["name"] for c in caps}
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
# Rule tools
# ---------------------------------------------------------------------------


def test_create_rule_returns_id():
    """create_rule() persists a rule and returns its UUID."""
    result = mcp_server.create_rule(
        name="test_buffer_rule",
        capability="buffer",
        config={"distance": 50.0},
        description="A test buffer rule",
    )

    assert "error" not in result
    assert "rule_id" in result
    assert result["name"] == "test_buffer_rule"
    # Must be a valid UUID string
    from uuid import UUID
    UUID(result["rule_id"])  # raises if invalid


def test_list_rules_roundtrip():
    """create_rule() then list_rules() returns the created rule."""
    mcp_server.create_rule(
        name="rule_A",
        capability="buffer",
        config={"distance": 100.0},
    )
    mcp_server.create_rule(
        name="rule_B",
        capability="filter",
        config={"query": "pop > 1000"},
    )

    rules = mcp_server.list_rules()

    assert len(rules) == 2
    names = {r["name"] for r in rules}
    assert names == {"rule_A", "rule_B"}

    for rule in rules:
        assert "rule_id" in rule
        assert "capability" in rule
        assert "config" in rule
        assert "enabled" in rule


def test_list_rules_empty():
    """list_rules() returns an empty list when no rules are stored."""
    assert mcp_server.list_rules() == []


def test_validate_rule_valid():
    """validate_rule() returns valid=True for a correctly configured rule."""
    result = mcp_server.create_rule(
        name="valid_buffer",
        capability="buffer",
        config={"distance": 200.0},
    )
    rule_id = result["rule_id"]

    validation = mcp_server.validate_rule(rule_id)

    assert "error" not in validation
    assert validation["valid"] is True
    assert validation["errors"] == []


def test_validate_rule_invalid_missing_param():
    """validate_rule() returns valid=False when a required param is missing."""
    result = mcp_server.create_rule(
        name="broken_reproject",
        capability="reproject",
        config={},          # 'target_crs' is required by ReprojectCapability
    )
    rule_id = result["rule_id"]

    validation = mcp_server.validate_rule(rule_id)

    assert "error" not in validation
    assert validation["valid"] is False
    assert len(validation["errors"]) > 0
    fields = [e["field"] for e in validation["errors"]]
    assert any("target_crs" in f for f in fields)


def test_validate_rule_not_found():
    """validate_rule() returns an error dict for an unknown rule_id."""
    import uuid
    validation = mcp_server.validate_rule(str(uuid.uuid4()))

    assert "error" in validation


def test_validate_rule_bad_uuid():
    """validate_rule() returns an error dict for a malformed UUID string."""
    validation = mcp_server.validate_rule("not-a-uuid")

    assert "error" in validation


def test_delete_rule_existing():
    """delete_rule() removes a rule and returns deleted=True."""
    result = mcp_server.create_rule(
        name="to_delete",
        capability="buffer",
        config={"distance": 10.0},
    )
    rule_id = result["rule_id"]

    del_result = mcp_server.delete_rule(rule_id)
    assert del_result["deleted"] is True
    assert del_result["rule_id"] == rule_id

    # Confirm it is gone
    assert mcp_server.list_rules() == []


def test_delete_rule_nonexistent():
    """delete_rule() returns deleted=False for an unknown UUID."""
    import uuid
    del_result = mcp_server.delete_rule(str(uuid.uuid4()))

    assert del_result["deleted"] is False


def test_delete_rule_bad_uuid():
    """delete_rule() returns an error for a malformed UUID."""
    del_result = mcp_server.delete_rule("bad-uuid-value")

    assert "error" in del_result


# ---------------------------------------------------------------------------
# Dataset tools
# ---------------------------------------------------------------------------


def test_list_datasets_empty():
    """list_datasets() returns empty list when no datasets are loaded."""
    assert mcp_server.list_datasets() == []


def test_load_gpkg_missing_file():
    """load_gpkg() returns an error for a non-existent path."""
    result = mcp_server.load_gpkg("/tmp/does_not_exist_gispulse_test.gpkg")

    # Either fiona is missing or the file is not found — both return error
    assert "error" in result


# ---------------------------------------------------------------------------
# MCP Resources
# ---------------------------------------------------------------------------


def test_resource_capabilities_is_valid_json():
    """gispulse://capabilities resource returns valid JSON with capabilities."""
    import json
    payload = mcp_server.resource_capabilities()
    data = json.loads(payload)

    assert isinstance(data, list)
    assert len(data) > 0


def test_resource_rules_is_valid_json():
    """gispulse://rules resource returns valid JSON (empty by default)."""
    import json
    payload = mcp_server.resource_rules()
    data = json.loads(payload)

    assert isinstance(data, list)
    assert data == []


def test_resource_rules_reflects_created_rules():
    """gispulse://rules resource lists rules after create_rule() calls."""
    import json
    mcp_server.create_rule(
        name="res_rule",
        capability="buffer",
        config={"distance": 5.0},
    )

    payload = mcp_server.resource_rules()
    data = json.loads(payload)

    assert len(data) == 1
    assert data[0]["name"] == "res_rule"
