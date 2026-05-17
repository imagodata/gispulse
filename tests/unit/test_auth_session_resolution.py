"""Contract tests for ``_resolve_user_from_session`` (issue #505 OIDC 401 part).

The legacy implementation hard-coded ``from gispulse.adapters.http.oidc import …``
which no longer exists post OSS split, so OIDC session resolution silently
always returned ``None`` (→ 401 in tests). The refactor delegates to
``ExtensionHub.auth_providers``.

This test guards the new contract:

* No AuthProvider registered → ``None``.
* A provider returns claims → user resolved via auth_repo.
* A provider raises → fall through (does not break API key auth path).
* Provider claims valid but auth_repo missing or user inactive → ``None``.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from gispulse.core import plugin_hub
from gispulse.adapters.http.auth import _resolve_user_from_session
from gispulse.persistence.auth_models import User


@pytest.fixture(autouse=True)
def _reset_hub():
    plugin_hub.ExtensionHub.reset()
    yield
    plugin_hub.ExtensionHub.reset()


def _make_request(*, app_state=None, cookie: str | None = None) -> MagicMock:
    request = MagicMock()
    request.app.state = app_state or MagicMock()
    request.state = MagicMock()
    request.cookies = {"gispulse_session": cookie} if cookie else {}
    return request


def _install_providers(monkeypatch: pytest.MonkeyPatch, providers: dict) -> None:
    hub = plugin_hub.ExtensionHub.get()
    monkeypatch.setattr(hub, "auth_providers", providers)


@pytest.mark.asyncio
async def test_no_providers_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_providers(monkeypatch, {})
    result = await _resolve_user_from_session(_make_request())
    assert result is None


@pytest.mark.asyncio
async def test_provider_returns_claims_resolves_user(monkeypatch: pytest.MonkeyPatch) -> None:
    user = User(id="u-123", email="alice@example.com", role="editor", is_active=True)
    auth_repo = MagicMock()
    auth_repo.get_user.return_value = user

    provider = MagicMock()
    provider.authenticate = AsyncMock(return_value={"sub": "u-123"})
    _install_providers(monkeypatch, {"oidc": provider})

    state = MagicMock(auth_repo=auth_repo)
    request = _make_request(app_state=state)

    result = await _resolve_user_from_session(request)
    assert result is user
    auth_repo.get_user.assert_called_once_with("u-123")
    assert request.state.user is user
    assert request.state.api_key_scopes == ["read", "write"]


@pytest.mark.asyncio
async def test_provider_raises_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = MagicMock()
    provider.authenticate = AsyncMock(side_effect=RuntimeError("boom"))
    _install_providers(monkeypatch, {"oidc": provider})

    result = await _resolve_user_from_session(_make_request())
    assert result is None


@pytest.mark.asyncio
async def test_provider_returns_none_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = MagicMock()
    provider.authenticate = AsyncMock(return_value=None)
    _install_providers(monkeypatch, {"oidc": provider})

    result = await _resolve_user_from_session(_make_request())
    assert result is None


@pytest.mark.asyncio
async def test_first_provider_with_claims_short_circuits(monkeypatch: pytest.MonkeyPatch) -> None:
    user = User(id="u-1", role="viewer", is_active=True)
    auth_repo = MagicMock()
    auth_repo.get_user.return_value = user

    first = MagicMock()
    first.authenticate = AsyncMock(return_value={"sub": "u-1"})
    second = MagicMock()
    second.authenticate = AsyncMock(return_value={"sub": "should-not-call"})
    _install_providers(monkeypatch, {"oidc": first, "saml": second})

    state = MagicMock(auth_repo=auth_repo)
    result = await _resolve_user_from_session(_make_request(app_state=state))
    assert result is user
    second.authenticate.assert_not_called()


@pytest.mark.asyncio
async def test_no_auth_repo_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = MagicMock()
    provider.authenticate = AsyncMock(return_value={"sub": "u-1"})
    _install_providers(monkeypatch, {"oidc": provider})

    state = MagicMock(spec=[])  # no auth_repo attribute
    result = await _resolve_user_from_session(_make_request(app_state=state))
    assert result is None


@pytest.mark.asyncio
async def test_inactive_user_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    user = User(id="u-1", is_active=False)
    auth_repo = MagicMock()
    auth_repo.get_user.return_value = user

    provider = MagicMock()
    provider.authenticate = AsyncMock(return_value={"sub": "u-1"})
    _install_providers(monkeypatch, {"oidc": provider})

    state = MagicMock(auth_repo=auth_repo)
    result = await _resolve_user_from_session(_make_request(app_state=state))
    assert result is None
