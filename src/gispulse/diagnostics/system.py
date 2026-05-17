"""Runtime / install diagnostics — pure functions consumed by CLI and HTTP.

Each check returns a :class:`CheckResult` with a stable ``name`` (used as the
selector in ``run_checks(names=…)`` and as the JSON key over the wire) and a
human-readable ``detail`` string. The CLI renders these with rich; the HTTP
surface returns the dataclass as JSON.

Status conventions:
    ok       — feature present and working
    warning  — feature missing but non-blocking (optional dep, fallback ok)
    error    — feature missing/broken and the runtime cannot work without it
    skipped  — feature intentionally not configured (e.g. PostGIS without DSN)
"""

from __future__ import annotations

import os
import platform
import shutil
import sqlite3
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Literal

CheckStatus = Literal["ok", "warning", "error", "skipped"]


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: CheckStatus
    detail: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class DoctorResult:
    summary: dict[str, int]
    checks: list[CheckResult]
    ran_at: str
    has_critical: bool = False

    def to_dict(self) -> dict:
        return {
            "summary": self.summary,
            "checks": [c.to_dict() for c in self.checks],
            "ran_at": self.ran_at,
            "has_critical": self.has_critical,
        }


def _check_gispulse() -> CheckResult:
    try:
        from importlib.metadata import version as pkg_version
        return CheckResult("gispulse", "ok", f"v{pkg_version('gispulse')}")
    except Exception:
        return CheckResult("gispulse", "ok", "v0.1.0 (source)")


def _check_python() -> CheckResult:
    py_ver = platform.python_version()
    if sys.version_info[:2] >= (3, 10):
        return CheckResult("python", "ok", f"v{py_ver}")
    return CheckResult("python", "error", f"v{py_ver} (>= 3.10 required)")


def _check_gdal() -> CheckResult:
    try:
        from osgeo import gdal
        return CheckResult("gdal", "ok", f"v{gdal.VersionInfo('RELEASE_NAME')}")
    except ImportError:
        return CheckResult("gdal", "warning", "not installed (optional, needed for raster)")


def _check_duckdb() -> CheckResult:
    try:
        import duckdb
    except ImportError:
        return CheckResult("duckdb", "error", "not installed (required)")
    detail = f"v{duckdb.__version__}"
    from gispulse.runtime.duckdb_engine import (
        DuckDBSpatialUnavailable,
        get_spatial_connection,
    )
    try:
        conn = get_spatial_connection()
        conn.execute("SELECT ST_Point(0, 0);")
        conn.close()
        detail += " + spatial extension"
        return CheckResult("duckdb", "ok", detail)
    except DuckDBSpatialUnavailable:
        detail += " (spatial extension NOT available — `gispulse doctor --install-spatial`)"
        return CheckResult("duckdb", "warning", detail)
    except Exception:
        detail += " (spatial extension NOT available)"
        return CheckResult("duckdb", "warning", detail)


def _check_postgis() -> CheckResult:
    from gispulse.core.config import settings as _cfg
    db_url = _cfg.database.dsn or None
    if not db_url:
        return CheckResult("postgis", "skipped", "GISPULSE_DATABASE_URL not set (optional)")
    try:
        import psycopg2
    except ImportError:
        return CheckResult("postgis", "warning", "psycopg2 not installed (pip install gispulse[postgis])")
    try:
        conn = psycopg2.connect(db_url)
        cur = conn.cursor()
        cur.execute("SELECT PostGIS_Version();")
        pg_ver = cur.fetchone()[0]
        cur.close()
        conn.close()
        return CheckResult("postgis", "ok", f"v{pg_ver}")
    except Exception as e:
        return CheckResult("postgis", "error", f"connection failed: {e}")


def _check_disk() -> CheckResult:
    try:
        usage = shutil.disk_usage(os.getcwd())
        free_gb = usage.free / (1024**3)
        if free_gb < 1.0:
            return CheckResult("disk", "warning", f"{free_gb:.1f} GB free (< 1 GB warning)")
        return CheckResult("disk", "ok", f"{free_gb:.1f} GB free")
    except OSError:
        return CheckResult("disk", "warning", "unable to check")


def _make_optional_dep_check(display_name: str, module_name: str) -> Callable[[], CheckResult]:
    def _check() -> CheckResult:
        try:
            mod = __import__(module_name)
            ver = getattr(mod, "__version__", getattr(mod, "gdal_version", "?"))
            return CheckResult(display_name, "ok", f"v{ver}")
        except ImportError:
            return CheckResult(display_name, "warning", "not installed (optional)")
    return _check


def _check_spatialite() -> CheckResult:
    # mod_spatialite is only meaningful on Linux SQLite builds. Tracked GPKGs
    # install SQLite triggers calling SpatiaLite functions — without the
    # loadable extension, DML on tracked layers crashes with
    # "no such function: ST_IsEmpty".
    if platform.system() != "Linux":
        return CheckResult("spatialite", "skipped", f"not applicable on {platform.system()}")
    conn = sqlite3.connect(":memory:")
    try:
        conn.enable_load_extension(True)
        try:
            conn.load_extension("mod_spatialite")
            return CheckResult("spatialite", "ok", "loadable")
        except sqlite3.OperationalError:
            return CheckResult(
                "spatialite",
                "warning",
                "not loadable — apt install libsqlite3-mod-spatialite (needed for tracked GPKG DML)",
            )
    except (AttributeError, sqlite3.NotSupportedError):
        return CheckResult("spatialite", "warning", "sqlite3 built without load_extension support")
    finally:
        conn.close()


def _check_oidc() -> CheckResult:
    from gispulse.core.config import settings as _cfg
    issuer = _cfg.oidc.issuer.strip()
    secret = _cfg.session.secret.strip()
    if not issuer:
        return CheckResult("oidc", "skipped", "not configured (optional)")
    if secret:
        return CheckResult("oidc", "ok", "issuer + session secret set")
    return CheckResult("oidc", "error", "GISPULSE_SESSION_SECRET not set — OIDC will refuse to start")


def _check_assets() -> CheckResult:
    # src/gispulse/diagnostics/system.py → parents[3] gives the repo root
    portal_dist = Path(__file__).resolve().parents[3] / "portal" / "dist"
    if portal_dist.exists() and any(portal_dist.iterdir()):
        return CheckResult("assets", "ok", str(portal_dist))
    return CheckResult("assets", "warning", "portal/dist/ not found (run: cd portal && npm run build)")


# Public, ordered registry. Names are stable and used as the API selector.
KNOWN_CHECKS: dict[str, Callable[[], CheckResult]] = {
    "gispulse": _check_gispulse,
    "python": _check_python,
    "gdal": _check_gdal,
    "duckdb": _check_duckdb,
    "postgis": _check_postgis,
    "disk": _check_disk,
    "geopandas": _make_optional_dep_check("geopandas", "geopandas"),
    "shapely": _make_optional_dep_check("shapely", "shapely"),
    "fiona": _make_optional_dep_check("fiona", "fiona"),
    "pyogrio": _make_optional_dep_check("pyogrio", "pyogrio"),
    "rasterio": _make_optional_dep_check("rasterio", "rasterio"),
    "spatialite": _check_spatialite,
    "oidc": _check_oidc,
    "assets": _check_assets,
}


def run_checks(names: list[str] | None = None) -> DoctorResult:
    """Run the named checks (or all when ``names`` is None) and return a result.

    Pure function: no logging, no I/O beyond what each check needs. Both the
    CLI and the HTTP endpoint render the returned :class:`DoctorResult`.

    Unknown names in ``names`` are silently skipped — the caller is expected
    to validate against :data:`KNOWN_CHECKS` before passing in user input.
    """
    selected = names if names is not None else list(KNOWN_CHECKS.keys())
    results: list[CheckResult] = []
    for name in selected:
        check = KNOWN_CHECKS.get(name)
        if check is None:
            continue
        try:
            results.append(check())
        except Exception as e:  # defensive: a buggy check should not abort the rest
            results.append(CheckResult(name, "error", f"check raised {type(e).__name__}: {e}"))

    summary = {"ok": 0, "warning": 0, "error": 0, "skipped": 0}
    for r in results:
        summary[r.status] = summary.get(r.status, 0) + 1
    has_critical = summary["error"] > 0

    return DoctorResult(
        summary=summary,
        checks=results,
        ran_at=datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        has_critical=has_critical,
    )
