"""Self-update command and startup update notice for the GISPulse CLI."""

from __future__ import annotations

from pathlib import Path

import typer
from pydantic import BaseModel, ConfigDict, Field

_GITHUB_RELEASES_URL = "https://api.github.com/repos/imagodata/gispulse/releases/latest"
_UPDATE_CHECK_CACHE = Path("~/.gispulse/update-check.json").expanduser()
_UPDATE_CHECK_INTERVAL_SECONDS = 86400  # 24 h


class ReleaseAsset(BaseModel):
    """Subset of a GitHub release asset used by binary self-update."""

    model_config = ConfigDict(extra="ignore")

    name: str
    browser_download_url: str


class GitHubRelease(BaseModel):
    """Subset of GitHub's latest-release API response used by the CLI."""

    model_config = ConfigDict(extra="ignore")

    tag_name: str
    body: str | None = None
    assets: list[ReleaseAsset] = Field(default_factory=list)


def _get_installed_version() -> str:
    """Return the installed version of gispulse."""
    from importlib.metadata import version as pkg_version

    try:
        return pkg_version("gispulse")
    except Exception:
        return "0.1.0"


def _fetch_latest_release() -> GitHubRelease | None:
    """Fetch latest release info from GitHub API. Returns None on failure."""
    import json
    import urllib.request

    req = urllib.request.Request(
        _GITHUB_RELEASES_URL,
        headers={"Accept": "application/vnd.github.v3+json", "User-Agent": "gispulse-cli"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            payload = json.loads(resp.read().decode())
        return GitHubRelease.model_validate(payload)
    except Exception:
        return None


def _parse_version(v: str):
    """Parse a version string, stripping leading 'v' if present."""
    from packaging.version import Version

    return Version(v.lstrip("v"))


def _detect_install_mode() -> str:
    """Detect how gispulse was installed: 'pip', 'homebrew', or 'binary'."""
    import shutil
    import subprocess
    import sys

    # pip: running from a site-packages environment
    if "site-packages" in (sys.executable or ""):
        return "pip"

    # homebrew: brew is available and gispulse is in its list
    brew = shutil.which("brew")
    if brew:
        try:
            result = subprocess.run(
                [brew, "list", "--formula"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if "gispulse" in result.stdout.split():
                return "homebrew"
        except Exception:
            pass

    return "binary"


def _truncate_changelog(body: str | None, max_lines: int = 12) -> str:
    """Truncate release body for display."""
    if not body:
        return "(no changelog)"
    lines = body.strip().splitlines()
    if len(lines) <= max_lines:
        return body.strip()
    return "\n".join(lines[:max_lines]) + f"\n  ... ({len(lines) - max_lines} more lines)"


def cmd_update(
    check: bool = typer.Option(False, "--check", help="Check only, do not install."),
    force: bool = typer.Option(False, "--force", help="Update even if already at latest version."),
) -> None:
    """Check for updates and self-update GISPulse."""
    import shutil
    import subprocess
    import sys
    import tempfile

    current = _get_installed_version()
    typer.echo(f"Current version: v{current}")

    release = _fetch_latest_release()
    if release is None:
        typer.echo("Error: could not reach GitHub API (no network or rate-limited).", err=True)
        raise typer.Exit(1)

    tag = release.tag_name
    if not tag:
        typer.echo("Error: no tag found in latest release.", err=True)
        raise typer.Exit(1)

    try:
        current_v = _parse_version(current)
        latest_v = _parse_version(tag)
    except Exception as e:
        typer.echo(f"Error parsing versions: {e}", err=True)
        raise typer.Exit(1)

    typer.echo(f"Latest version:  v{latest_v}")

    is_outdated = latest_v > current_v

    if check:
        if is_outdated:
            typer.echo(f"\nUpdate available: v{current} -> v{latest_v}")
            typer.echo("Run `gispulse update` to upgrade.")
            raise typer.Exit(1)
        typer.echo(f"\nGISPulse v{current} is up to date.")
        raise typer.Exit(0)

    if not is_outdated and not force:
        typer.echo(f"\nGISPulse v{current} is up to date.")
        return

    changelog = _truncate_changelog(release.body)
    typer.echo(f"\nChangelog:\n  {changelog.replace(chr(10), chr(10) + '  ')}")

    if not force:
        confirm = typer.confirm(f"\nUpgrade to v{latest_v}?")
        if not confirm:
            typer.echo("Cancelled.")
            raise typer.Exit(0)

    mode = _detect_install_mode()
    typer.echo(f"\nInstall mode: {mode}")

    if mode == "pip":
        typer.echo(f"Running: pip install --upgrade gispulse=={latest_v}")
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--upgrade", f"gispulse=={latest_v}"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            typer.echo(f"pip upgrade failed:\n{result.stderr}", err=True)
            raise typer.Exit(1)
        typer.echo(f"Successfully updated to v{latest_v}.")

    elif mode == "homebrew":
        brew = shutil.which("brew")
        if brew is None:
            typer.echo("brew executable not found.", err=True)
            raise typer.Exit(1)
        typer.echo("Running: brew upgrade gispulse")
        result = subprocess.run(
            [brew, "upgrade", "gispulse"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            typer.echo(f"brew upgrade failed:\n{result.stderr}", err=True)
            raise typer.Exit(1)
        typer.echo(f"Successfully updated to v{latest_v}.")

    elif mode == "binary":
        import platform
        import urllib.request

        system = platform.system().lower()  # linux, darwin, windows
        machine = platform.machine().lower()  # x86_64, arm64, aarch64

        matching = [
            asset
            for asset in release.assets
            if system in asset.name.lower()
            and (
                machine in asset.name.lower()
                or ("amd64" in asset.name.lower() and machine == "x86_64")
                or ("arm64" in asset.name.lower() and machine == "aarch64")
            )
        ]

        if not matching:
            typer.echo(
                f"Error: no binary asset found for {system}/{machine} in release v{latest_v}.\n"
                f"Available assets: {[asset.name for asset in release.assets]}",
                err=True,
            )
            typer.echo("Try: pip install --upgrade gispulse", err=True)
            raise typer.Exit(1)

        asset = matching[0]
        typer.echo(f"Downloading {asset.name} ...")

        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=Path(asset.name).suffix) as tmp:
                req = urllib.request.Request(
                    asset.browser_download_url,
                    headers={"User-Agent": "gispulse-cli"},
                )
                with urllib.request.urlopen(req, timeout=120) as resp:
                    tmp.write(resp.read())
                tmp_path = Path(tmp.name)
        except Exception as e:
            typer.echo(f"Download failed: {e}", err=True)
            raise typer.Exit(1)

        current_bin = Path(shutil.which("gispulse") or sys.executable)
        try:
            import stat

            tmp_path.chmod(current_bin.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            backup = current_bin.with_suffix(".bak")
            shutil.copy2(current_bin, backup)
            shutil.move(str(tmp_path), str(current_bin))
            typer.echo(f"Successfully updated to v{latest_v}.")
            typer.echo(f"Backup saved as {backup}")
        except PermissionError:
            typer.echo(
                f"Permission denied. Try:\n  sudo cp {tmp_path} {current_bin}",
                err=True,
            )
            raise typer.Exit(1)

    _write_update_cache(str(latest_v))


def _write_update_cache(latest_version: str) -> None:
    """Write update check result to cache file."""
    import json
    import time

    _UPDATE_CHECK_CACHE.parent.mkdir(parents=True, exist_ok=True)
    cache = {
        "checked_at": time.time(),
        "latest_version": latest_version,
    }
    try:
        _UPDATE_CHECK_CACHE.write_text(json.dumps(cache))
    except OSError:
        pass  # non-critical


def _read_update_cache() -> dict | None:
    """Read cached update check result. Returns None if stale or missing."""
    import json
    import time

    if not _UPDATE_CHECK_CACHE.exists():
        return None
    try:
        cache = json.loads(_UPDATE_CHECK_CACHE.read_text())
        if time.time() - cache.get("checked_at", 0) > _UPDATE_CHECK_INTERVAL_SECONDS:
            return None
        return cache
    except (json.JSONDecodeError, OSError, KeyError):
        return None


def startup_update_check() -> None:
    """Non-blocking update check at CLI startup.

    Respects GISPULSE_NO_UPDATE_CHECK=1 env var.
    Caches results for 24h in ~/.gispulse/update-check.json.
    """
    from gispulse.core.config import settings as _cfg3

    if _cfg3.telemetry.no_update_check:
        return

    cache = _read_update_cache()
    if cache is None:
        release = _fetch_latest_release()
        if release is None:
            return
        tag = release.tag_name
        if not tag:
            return
        latest_str = tag.lstrip("v")
        _write_update_cache(latest_str)
    else:
        latest_str = cache["latest_version"]

    try:
        current_v = _parse_version(_get_installed_version())
        latest_v = _parse_version(latest_str)
    except Exception:
        return

    if latest_v > current_v:
        typer.echo(
            f"\nA new version of GISPulse is available: v{latest_v} (current: v{current_v}).\n"
            f"Run `gispulse update` to upgrade.\n",
            err=True,
        )


__all__ = ["cmd_update", "startup_update_check"]
