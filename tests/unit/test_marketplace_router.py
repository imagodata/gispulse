"""Tests for the marketplace router."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from gispulse.adapters.http.routers.marketplace_router import (
    _SAFE_NAME_RE,
    _validate_plugin_name,
    router,
)

# We test the router endpoints via a test client.
# Import is deferred to avoid heavy app-factory bootstrap.


# ----------------------------------------------------------------------
# _validate_plugin_name
# ----------------------------------------------------------------------


class TestValidatePluginName:
    def test_short_name(self):
        assert _validate_plugin_name("ftth") == "gispulse-cap-ftth"

    def test_full_name(self):
        assert _validate_plugin_name("gispulse-cap-ftth") == "gispulse-cap-ftth"

    def test_with_hyphens(self):
        assert _validate_plugin_name("my-plugin") == "gispulse-cap-my-plugin"

    def test_strips_whitespace(self):
        assert _validate_plugin_name("  ftth  ") == "gispulse-cap-ftth"

    def test_rejects_empty(self):
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            _validate_plugin_name("")
        assert exc_info.value.status_code == 400

    def test_rejects_special_chars(self):
        from fastapi import HTTPException

        for bad in ["foo;bar", "ftth && rm -rf /", "my_plugin", "a b", "../etc"]:
            with pytest.raises(HTTPException) as exc_info:
                _validate_plugin_name(bad)
            assert exc_info.value.status_code == 400, f"Should reject: {bad!r}"

    def test_rejects_leading_hyphen(self):
        from fastapi import HTTPException

        with pytest.raises(HTTPException):
            _validate_plugin_name("-ftth")

    def test_rejects_trailing_hyphen(self):
        from fastapi import HTTPException

        with pytest.raises(HTTPException):
            _validate_plugin_name("ftth-")


# ----------------------------------------------------------------------
# _SAFE_NAME_RE
# ----------------------------------------------------------------------


class TestSafeNameRegex:
    @pytest.mark.parametrize(
        "name",
        ["ftth", "my-plugin", "a1", "gis123", "a-b-c"],
    )
    def test_valid_names(self, name: str):
        assert _SAFE_NAME_RE.match(name)

    @pytest.mark.parametrize(
        "name",
        ["-start", "end-", "a--b", "foo bar", "semi;colon", "dot.dot", "under_score"],
    )
    def test_invalid_names(self, name: str):
        assert not _SAFE_NAME_RE.match(name)


# ----------------------------------------------------------------------
# Endpoint tests (using FastAPI TestClient)
# ----------------------------------------------------------------------


@pytest.fixture
def client():
    """Minimal test client with the marketplace router mounted."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


class TestListPlugins:
    """GET /marketplace/plugins — now sourced from the unified PluginHub (#181)."""

    @staticmethod
    def _hub_with(records):
        from types import SimpleNamespace

        from core.plugin_hub import PluginHub

        return patch.object(
            PluginHub, "get", classmethod(lambda cls: SimpleNamespace(records=records))
        )

    @staticmethod
    def _record(name, dist, *, tier="community", trust="community", state="active",
                detail=""):
        from core.plugin_model import (
            PluginKind,
            PluginRecord,
            PluginState,
            Tier,
            Trust,
        )
        from types import SimpleNamespace

        return PluginRecord(
            name=name,
            kind=PluginKind.CAPABILITY,
            tier_required=Tier(tier),
            trust=Trust(trust),
            state=PluginState(state),
            detail=detail,
            entry_point=SimpleNamespace(dist=SimpleNamespace(name=dist)),
        )

    def test_empty(self, client):
        with self._hub_with([]):
            resp = client.get("/marketplace/plugins")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_with_plugins(self, client):
        rec = self._record("ftth", "gispulse-cap-ftth", tier="pro", trust="verified")
        with self._hub_with([rec]):
            resp = client.get("/marketplace/plugins")
        assert resp.status_code == 200
        data = resp.json()[0]
        assert data["id"] == "gispulse-cap-ftth"
        assert data["kind"] == "capability"
        assert data["tier"] == "pro"
        assert data["trust"] == "verified"
        assert data["enabled"] is True
        assert data["locked"] is False

    def test_locked_plugin_is_flagged(self, client):
        rec = self._record(
            "ftth", "gispulse-cap-ftth", tier="pro", state="locked",
            detail="requires the 'pro' tier",
        )
        with self._hub_with([rec]):
            resp = client.get("/marketplace/plugins")
        data = resp.json()[0]
        assert data["state"] == "locked"
        assert data["locked"] is True
        assert data["enabled"] is False
        assert "pro" in data["detail"]


class TestGetPluginDetails:
    def test_installed(self, client):
        fake_meta = {
            "Name": "gispulse-cap-ftth",
            "Version": "0.1.0",
            "Summary": "FTTH plugin",
            "Author": "Test",
            "License": "MIT",
            "Home-page": "https://example.com",
        }
        with patch("importlib.metadata.metadata", return_value=fake_meta):
            resp = client.get("/marketplace/plugins/ftth")
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "gispulse-cap-ftth"
        assert data["version"] == "0.1.0"

    def test_not_installed(self, client):
        from importlib.metadata import PackageNotFoundError

        with patch("importlib.metadata.metadata", side_effect=PackageNotFoundError("nope")):
            resp = client.get("/marketplace/plugins/nonexistent")
        assert resp.status_code == 404

    def test_invalid_name(self, client):
        resp = client.get("/marketplace/plugins/foo;bar")
        assert resp.status_code == 400


class TestGetRegistry:
    def test_returns_json(self, client, tmp_path):
        registry_data = {"version": 1, "plugins": [{"name": "test", "package": "gispulse-cap-test"}]}
        fake_path = tmp_path / "registry.json"
        fake_path.write_text(json.dumps(registry_data))

        with patch("gispulse.adapters.http.routers.marketplace_router._REGISTRY_PATH", fake_path):
            resp = client.get("/marketplace/registry")
        assert resp.status_code == 200
        assert resp.json()["version"] == 1

    def test_missing_file(self, client, tmp_path):
        fake_path = tmp_path / "does_not_exist.json"
        with patch("gispulse.adapters.http.routers.marketplace_router._REGISTRY_PATH", fake_path):
            resp = client.get("/marketplace/registry")
        assert resp.status_code == 404


class TestSearch:
    def test_missing_query(self, client):
        resp = client.get("/marketplace/search")
        assert resp.status_code == 422  # validation error

    def test_fallback_to_registry(self, client, tmp_path):
        registry_data = {
            "plugins": [
                {"name": "ftth", "package": "gispulse-cap-ftth", "description": "FTTH stuff"},
                {"name": "urban", "package": "gispulse-cap-urban", "description": "Urban stuff"},
            ]
        }
        fake_path = tmp_path / "registry.json"
        fake_path.write_text(json.dumps(registry_data))

        # Simulate PyPI being unreachable
        with patch("gispulse.adapters.http.routers.marketplace_router._REGISTRY_PATH", fake_path):
            with patch("urllib.request.urlopen", side_effect=Exception("network error")):
                resp = client.get("/marketplace/search?q=ftth")
        assert resp.status_code == 200
        assert "gispulse-cap-ftth" in resp.json()


class TestCatalog:
    def test_returns_all_plugins(self, client, tmp_path):
        registry_data = {
            "version": 2,
            "plugins": [
                {"id": "gispulse-cap-ftth", "name": "FibreFlow", "package": "gispulse-cap-ftth",
                 "description": "FTTH", "category": "analysis", "tags": ["ftth"]},
                {"id": "gispulse-cap-h3", "name": "H3", "package": "gispulse-cap-h3",
                 "description": "H3 analysis", "category": "analysis", "tags": ["h3"]},
            ],
        }
        fake_path = tmp_path / "registry.json"
        fake_path.write_text(json.dumps(registry_data))

        with patch("gispulse.adapters.http.routers.marketplace_router._REGISTRY_PATH", fake_path):
            resp = client.get("/marketplace/catalog")
        assert resp.status_code == 200
        assert len(resp.json()) == 2

    def test_filter_by_category(self, client, tmp_path):
        registry_data = {
            "version": 2,
            "plugins": [
                {"id": "a", "name": "A", "category": "analysis", "tags": []},
                {"id": "b", "name": "B", "category": "import", "tags": []},
            ],
        }
        fake_path = tmp_path / "registry.json"
        fake_path.write_text(json.dumps(registry_data))

        with patch("gispulse.adapters.http.routers.marketplace_router._REGISTRY_PATH", fake_path):
            resp = client.get("/marketplace/catalog?category=import")
        assert resp.status_code == 200
        assert len(resp.json()) == 1
        assert resp.json()[0]["id"] == "b"

    def test_search_by_query(self, client, tmp_path):
        registry_data = {
            "version": 2,
            "plugins": [
                {"id": "a", "name": "FibreFlow", "description": "FTTH stuff", "category": "analysis", "tags": ["ftth"]},
                {"id": "b", "name": "H3 Grid", "description": "Hexagonal", "category": "analysis", "tags": ["h3"]},
            ],
        }
        fake_path = tmp_path / "registry.json"
        fake_path.write_text(json.dumps(registry_data))

        with patch("gispulse.adapters.http.routers.marketplace_router._REGISTRY_PATH", fake_path):
            resp = client.get("/marketplace/catalog?q=ftth")
        assert resp.status_code == 200
        assert len(resp.json()) == 1
        assert resp.json()[0]["id"] == "a"

    def test_empty_registry(self, client, tmp_path):
        fake_path = tmp_path / "no_such_file.json"
        with patch("gispulse.adapters.http.routers.marketplace_router._REGISTRY_PATH", fake_path):
            resp = client.get("/marketplace/catalog")
        assert resp.status_code == 200
        assert resp.json() == []


class TestInstallPluginById:
    def test_install_by_id(self, client, tmp_path):
        registry_data = {
            "plugins": [{"id": "gispulse-cap-ftth", "package": "gispulse-cap-ftth", "name": "FTTH"}]
        }
        fake_path = tmp_path / "registry.json"
        fake_path.write_text(json.dumps(registry_data))
        fake_result = MagicMock(returncode=0, stderr="", stdout="ok")

        with patch("gispulse.adapters.http.routers.marketplace_router._REGISTRY_PATH", fake_path):
            with patch("subprocess.run", return_value=fake_result):
                resp = client.post("/marketplace/plugins/gispulse-cap-ftth/install")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        assert resp.json()["package"] == "gispulse-cap-ftth"

    def test_install_unknown_id(self, client, tmp_path):
        registry_data = {"plugins": []}
        fake_path = tmp_path / "registry.json"
        fake_path.write_text(json.dumps(registry_data))
        fake_result = MagicMock(returncode=0, stderr="", stdout="ok")

        with patch("gispulse.adapters.http.routers.marketplace_router._REGISTRY_PATH", fake_path):
            with patch("subprocess.run", return_value=fake_result):
                resp = client.post("/marketplace/plugins/myplug/install")
        assert resp.status_code == 200
        assert resp.json()["package"] == "gispulse-cap-myplug"


class TestUninstallPluginById:
    def test_uninstall_by_id(self, client, tmp_path):
        registry_data = {
            "plugins": [{"id": "gispulse-cap-ftth", "package": "gispulse-cap-ftth", "name": "FTTH"}]
        }
        fake_path = tmp_path / "registry.json"
        fake_path.write_text(json.dumps(registry_data))
        fake_result = MagicMock(returncode=0, stderr="", stdout="ok")

        with patch("gispulse.adapters.http.routers.marketplace_router._REGISTRY_PATH", fake_path):
            with patch("subprocess.run", return_value=fake_result):
                resp = client.delete("/marketplace/plugins/gispulse-cap-ftth/uninstall")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True


class TestInstallPlugin:
    """Install requires admin role.

    In our minimal test app the require_role dependency is active but no
    auth repo is attached, so in legacy/dev mode the check is a no-op
    (returns None). We test the happy path and the validation.
    """

    def test_install_success(self, client):
        fake_result = MagicMock(returncode=0, stderr="", stdout="ok")
        with patch("subprocess.run", return_value=fake_result):
            resp = client.post(
                "/marketplace/install",
                json={"name": "ftth"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["package"] == "gispulse-cap-ftth"

    def test_install_failure(self, client):
        fake_result = MagicMock(returncode=1, stderr="not found", stdout="")
        with patch("subprocess.run", return_value=fake_result):
            resp = client.post(
                "/marketplace/install",
                json={"name": "ftth"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False

    def test_install_invalid_name(self, client):
        resp = client.post(
            "/marketplace/install",
            json={"name": "foo;rm -rf /"},
        )
        assert resp.status_code == 400

    def test_install_with_upgrade(self, client):
        fake_result = MagicMock(returncode=0, stderr="", stdout="ok")
        with patch("subprocess.run", return_value=fake_result) as mock_run:
            resp = client.post(
                "/marketplace/install",
                json={"name": "ftth", "upgrade": True},
            )
        assert resp.status_code == 200
        # Verify --upgrade was passed
        call_args = mock_run.call_args[0][0]
        assert "--upgrade" in call_args


class TestUninstallPlugin:
    def test_uninstall_success(self, client):
        fake_result = MagicMock(returncode=0, stderr="", stdout="ok")
        with patch("subprocess.run", return_value=fake_result):
            resp = client.post(
                "/marketplace/uninstall",
                json={"name": "ftth"},
            )
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_uninstall_invalid_name(self, client):
        resp = client.post(
            "/marketplace/uninstall",
            json={"name": "../../etc/passwd"},
        )
        assert resp.status_code == 400
