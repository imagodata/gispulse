"""
Marketplace router for GISPulse.

Exposes REST endpoints so the Portal UI can browse, search, install, and
uninstall capability plugins.

Routes:
    GET    /marketplace/plugins         -- installed plugins (entry-points)
    GET    /marketplace/plugins/{name}  -- details of an installed plugin
    GET    /marketplace/registry        -- curated plugin catalogue
    GET    /marketplace/search          -- search PyPI for gispulse-cap-* packages
    POST   /marketplace/install         -- install a plugin (admin only)
    POST   /marketplace/uninstall       -- uninstall a plugin (admin only)
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from gispulse.adapters.http.auth import require_role

router = APIRouter(prefix="/marketplace", tags=["marketplace"])

_PLUGIN_PREFIX = "gispulse-cap-"
_SAFE_NAME_RE = re.compile(r"^[a-zA-Z0-9]+(-[a-zA-Z0-9]+)*$")
_REGISTRY_PATH = Path(__file__).resolve().parent.parent.parent.parent / "marketplace" / "registry.json"


# ----------------------------------------------------------------------
# Request / Response schemas
# ----------------------------------------------------------------------


class PluginAction(BaseModel):
    """Body for install / uninstall requests."""

    name: str = Field(
        ...,
        description="Plugin short name (e.g. 'ftth') or full package name (e.g. 'gispulse-cap-ftth').",
        min_length=1,
        max_length=80,
    )
    upgrade: bool = Field(False, description="Upgrade if already installed (install only).")


class PluginActionResponse(BaseModel):
    ok: bool
    package: str
    message: str


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _validate_plugin_name(name: str) -> str:
    """Validate and normalise a plugin name to a safe package identifier.

    Returns the full package name (``gispulse-cap-<name>``).

    Raises:
        HTTPException(400): If the name contains forbidden characters.
    """
    raw = name.strip()
    if raw.startswith(_PLUGIN_PREFIX):
        short = raw[len(_PLUGIN_PREFIX):]
    else:
        short = raw

    if not short or not _SAFE_NAME_RE.match(short):
        raise HTTPException(
            status_code=400,
            detail=(
                f"Invalid plugin name '{name}'. "
                "Only alphanumeric characters and hyphens are allowed."
            ),
        )

    return f"{_PLUGIN_PREFIX}{short}"


# ----------------------------------------------------------------------
# Read-only endpoints (no auth required)
# ----------------------------------------------------------------------


def _record_package(rec: Any) -> str:
    """Best-effort distribution name backing a plugin record's entry-point."""
    dist = getattr(rec.entry_point, "dist", None)
    name = getattr(dist, "name", None)
    return str(name).lower() if name else ""


@router.get("/plugins", summary="List installed plugins")
def list_installed_plugins() -> list[dict[str, Any]]:
    """Return metadata for all installed plugins from the unified PluginHub.

    Each entry carries the hub's classification — ``kind`` / ``tier`` /
    ``trust`` / ``origin`` / ``state`` — so the Portal UI can surface
    tier-gated (``locked``) plugins with an upgrade prompt. Curated
    registry metadata enriches the descriptive fields.
    """
    from core.plugin_hub import PluginHub

    registry_plugins = {p["package"]: p for p in _load_registry_plugins()}

    enriched = []
    for rec in PluginHub.get().records:
        package = _record_package(rec)
        registry_entry = registry_plugins.get(package, {})
        state = rec.state.value

        enriched.append({
            "id": registry_entry.get("id", package or rec.name),
            "name": registry_entry.get("name", rec.name),
            "description": registry_entry.get("description", ""),
            "author": registry_entry.get("author", ""),
            "version": registry_entry.get("version", "0.0.0"),
            "category": registry_entry.get("category", "utilities"),
            "kind": rec.kind.value,
            "tier": rec.tier_required.value,
            "trust": rec.trust.value,
            "origin": rec.origin.value,
            "state": state,
            "locked": state == "locked",
            "detail": rec.detail,
            "verified": rec.trust.value in ("verified", "first_party"),
            "requires_pro": rec.tier_required.value != "community",
            "tags": registry_entry.get("tags", []),
            "homepage_url": registry_entry.get("homepage_url"),
            "install_count": registry_entry.get("install_count", 0),
            "installed_at": None,  # Not tracked yet
            "enabled": state == "active",
        })

    return enriched


@router.get("/plugins/{name}", summary="Plugin details")
def get_plugin_details(name: str) -> dict[str, Any]:
    """Return metadata from an installed plugin package."""
    package = _validate_plugin_name(name)

    try:
        from importlib.metadata import metadata as pkg_metadata

        meta = pkg_metadata(package)
    except Exception:
        raise HTTPException(status_code=404, detail=f"Package '{package}' is not installed.")

    return {
        "name": meta["Name"],
        "version": meta["Version"],
        "summary": meta.get("Summary", ""),
        "author": meta.get("Author", meta.get("Author-email", "")),
        "license": meta.get("License", ""),
        "home_page": meta.get("Home-page", ""),
    }


@router.get("/registry", summary="Curated plugin catalogue (raw)")
def get_registry() -> dict[str, Any]:
    """Return the curated marketplace registry (``marketplace/registry.json``)."""
    if not _REGISTRY_PATH.exists():
        raise HTTPException(status_code=404, detail="Registry file not found.")

    try:
        return json.loads(_REGISTRY_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise HTTPException(status_code=500, detail=f"Failed to read registry: {exc}")


def _load_registry_plugins() -> list[dict[str, Any]]:
    """Load plugins from registry.json, returning empty list on failure."""
    if not _REGISTRY_PATH.exists():
        return []
    try:
        data = json.loads(_REGISTRY_PATH.read_text(encoding="utf-8"))
        return data.get("plugins", [])
    except (json.JSONDecodeError, OSError):
        return []


@router.get("/catalog", summary="Browse available plugins")
def get_catalog(
    q: str | None = Query(None, description="Search term"),
    category: str | None = Query(None, description="Filter by category"),
) -> list[dict[str, Any]]:
    """Return the full plugin catalog with optional filtering.

    This is the main endpoint used by the Portal marketplace UI.
    """
    plugins = _load_registry_plugins()

    if q:
        q_lower = q.lower()
        plugins = [
            p for p in plugins
            if q_lower in p.get("name", "").lower()
            or q_lower in p.get("description", "").lower()
            or any(q_lower in tag.lower() for tag in p.get("tags", []))
        ]

    if category:
        plugins = [p for p in plugins if p.get("category") == category]

    return plugins


@router.get("/search", summary="Search PyPI for plugins")
def search_plugins(q: str = Query(..., min_length=1, description="Search term")) -> list[str]:
    """Search PyPI Simple API for ``gispulse-cap-*`` packages matching *q*."""
    import urllib.request
    import urllib.error

    search_url = "https://pypi.org/simple/"
    matches: list[str] = []

    try:
        req = urllib.request.Request(
            search_url,
            headers={"Accept": "application/vnd.pypi.simple.v1+json"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            matches = [
                p["name"]
                for p in data.get("projects", [])
                if p["name"].startswith(_PLUGIN_PREFIX)
                and q.lower() in p["name"].lower()
            ]
    except Exception:
        # Fallback: search the local curated registry
        if _REGISTRY_PATH.exists():
            try:
                registry_data = json.loads(_REGISTRY_PATH.read_text(encoding="utf-8"))
                matches = [
                    p["package"]
                    for p in registry_data.get("plugins", [])
                    if q.lower() in p.get("name", "").lower()
                    or q.lower() in p.get("description", "").lower()
                ]
            except Exception:
                pass

    return matches


# ----------------------------------------------------------------------
# Write endpoints (admin only)
# ----------------------------------------------------------------------


@router.post(
    "/plugins/{plugin_id}/install",
    summary="Install a plugin by ID (admin only)",
    response_model=PluginActionResponse,
    dependencies=[Depends(require_role("admin"))],
)
def install_plugin_by_id(plugin_id: str) -> PluginActionResponse:
    """Install a plugin using its registry ID (e.g. ``gispulse-cap-ftth``).

    Looks up the package name from the registry, then delegates to pip.
    """
    # Resolve package name: the ID itself is usually the package name
    plugins = _load_registry_plugins()
    pkg = None
    for p in plugins:
        if p.get("id") == plugin_id or p.get("package") == plugin_id:
            pkg = p.get("package", plugin_id)
            break
    if not pkg:
        # Fall back to treating ID as a short name
        pkg = _validate_plugin_name(plugin_id)
    else:
        pkg = _validate_plugin_name(pkg)

    cmd = [sys.executable, "-m", "pip", "install", pkg]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)

    if result.returncode != 0:
        return PluginActionResponse(
            ok=False,
            package=pkg,
            message=f"pip install failed: {result.stderr.strip()}",
        )

    return PluginActionResponse(
        ok=True,
        package=pkg,
        message=f"Installed {pkg} successfully. Restart GISPulse to load.",
    )


@router.post(
    "/install",
    summary="Install a plugin (admin only)",
    response_model=PluginActionResponse,
    dependencies=[Depends(require_role("admin"))],
)
def install_plugin(body: PluginAction) -> PluginActionResponse:
    """Install a GISPulse capability plugin via pip.

    The package name is validated to prevent command injection: only
    alphanumeric characters and hyphens are accepted, and the
    ``gispulse-cap-`` prefix is enforced.
    """
    package = _validate_plugin_name(body.name)
    cmd = [sys.executable, "-m", "pip", "install", package]
    if body.upgrade:
        cmd.append("--upgrade")

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)

    if result.returncode != 0:
        return PluginActionResponse(
            ok=False,
            package=package,
            message=f"pip install failed: {result.stderr.strip()}",
        )

    return PluginActionResponse(
        ok=True,
        package=package,
        message=f"Installed {package} successfully. Restart GISPulse to load.",
    )


@router.delete(
    "/plugins/{plugin_id}/uninstall",
    summary="Uninstall a plugin by ID (admin only)",
    response_model=PluginActionResponse,
    dependencies=[Depends(require_role("admin"))],
)
def uninstall_plugin_by_id(plugin_id: str) -> PluginActionResponse:
    """Uninstall a plugin using its registry ID."""
    plugins = _load_registry_plugins()
    pkg = None
    for p in plugins:
        if p.get("id") == plugin_id or p.get("package") == plugin_id:
            pkg = p.get("package", plugin_id)
            break
    if not pkg:
        pkg = _validate_plugin_name(plugin_id)
    else:
        pkg = _validate_plugin_name(pkg)

    result = subprocess.run(
        [sys.executable, "-m", "pip", "uninstall", "-y", pkg],
        capture_output=True,
        text=True,
        timeout=60,
    )

    if result.returncode != 0:
        return PluginActionResponse(
            ok=False,
            package=pkg,
            message=f"pip uninstall failed: {result.stderr.strip()}",
        )

    return PluginActionResponse(
        ok=True,
        package=pkg,
        message=f"Uninstalled {pkg}.",
    )


@router.post(
    "/uninstall",
    summary="Uninstall a plugin (admin only)",
    response_model=PluginActionResponse,
    dependencies=[Depends(require_role("admin"))],
)
def uninstall_plugin(body: PluginAction) -> PluginActionResponse:
    """Uninstall a GISPulse capability plugin via pip.

    Same validation rules as install.
    """
    package = _validate_plugin_name(body.name)

    result = subprocess.run(
        [sys.executable, "-m", "pip", "uninstall", "-y", package],
        capture_output=True,
        text=True,
        timeout=60,
    )

    if result.returncode != 0:
        return PluginActionResponse(
            ok=False,
            package=package,
            message=f"pip uninstall failed: {result.stderr.strip()}",
        )

    return PluginActionResponse(
        ok=True,
        package=package,
        message=f"Uninstalled {package}.",
    )
