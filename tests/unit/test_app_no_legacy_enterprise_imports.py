"""Regression guard for issue #505 (post-split legacy import recurrence).

The OSS ``gispulse/adapters/http/app.py`` must NOT import enterprise-only
modules directly. Admin and billing routers live in ``gispulse-enterprise``
and are mounted by ``ExtensionHub`` via the ``gispulse.routers`` entry-points.

Direct legacy imports are silently swallowed by ``except ImportError`` and
break tests with 404/401 because the ExtensionHub graceful-skip path requires
``app.state.auth_repo`` / ``app.state.oidc_provider`` to be wired — which
never happens when the legacy import path is taken instead.

This test fails fast if anyone re-introduces the pattern.
"""
from pathlib import Path


LEGACY_PATTERNS = (
    "gispulse.adapters.billing",
    "gispulse.adapters.http.routers.admin_router",
    "gispulse.adapters.http.routers.billing_router",
    "gispulse.adapters.http.middleware.production_auth",
)


def test_app_py_has_no_legacy_enterprise_imports() -> None:
    app_py = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "gispulse"
        / "adapters"
        / "http"
        / "app.py"
    )
    src = app_py.read_text(encoding="utf-8")
    found = [pat for pat in LEGACY_PATTERNS if pat in src]
    assert not found, (
        f"Legacy enterprise imports re-introduced in app.py: {found}. "
        "Use ExtensionHub entry-points (gispulse.routers) — see docs/PLUGIN_CONTRACT.md."
    )
