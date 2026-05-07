"""DuckDB spatial engine wrapper.

Lazy-installs and loads the ``spatial`` DuckDB extension on first use so the
DSL geom functions (``geom_area_m2``, ``geom_within``, …) can run without an
explicit pre-install step. The first call pays the network round-trip
(``INSTALL spatial`` ~10 s on a fresh house); subsequent calls within the
same process are no-ops because we cache the install state per executable.

Public surface (kept tiny on purpose):
    - :func:`get_spatial_connection` — return a DuckDB connection with the
      spatial extension loaded. Idempotent.
    - :func:`is_spatial_loaded` — cheap probe used by diagnostics.
    - :func:`verify_epsg_roundtrip` — sanity-check that bundled PROJ can
      transform between EPSG:4326 and ``target_epsg`` within tolerance.
      Used by ``gispulse doctor --install-spatial`` to flag silent grid
      failures (PROJ network is disabled in the bundled extension).
    - :class:`DuckDBSpatialUnavailable` — raised when install/load fails
      (offline, sandboxed network, …) with a hint pointing at
      ``gispulse doctor --install-spatial``.

The wrapper is intentionally engine-agnostic: callers ask for a connection
and run their own SQL. Higher-level helpers (DSL push-down, COPY/ATTACH for
PostGIS federation) live in their own modules.
"""

from __future__ import annotations

import math
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from duckdb import DuckDBPyConnection

_DOCTOR_HINT = (
    "Run `gispulse doctor --install-spatial` to pre-install the DuckDB "
    "spatial extension, or check network connectivity."
)


class DuckDBSpatialUnavailable(RuntimeError):
    """Raised when the DuckDB spatial extension cannot be installed/loaded."""


_install_lock = threading.Lock()
_install_done: dict[str, bool] = {}


def _key_for(executable: str) -> str:
    return executable or "default"


def _ensure_spatial_loaded(conn: "DuckDBPyConnection", executable: str) -> None:
    """Install + load the spatial extension on ``conn``.

    The DuckDB ``INSTALL`` command persists per executable (binary path), so
    we cache success keyed on the executable identifier; ``LOAD`` is cheap
    enough to run on every fresh connection and is required to expose the
    ``ST_*`` symbols in the connection's catalog.
    """
    key = _key_for(executable)
    with _install_lock:
        already_installed = _install_done.get(key, False)
        try:
            if not already_installed:
                conn.execute("INSTALL spatial;")
                _install_done[key] = True
            conn.execute("LOAD spatial;")
        except Exception as exc:
            raise DuckDBSpatialUnavailable(
                f"DuckDB spatial extension unavailable: {exc}. {_DOCTOR_HINT}"
            ) from exc


def get_spatial_connection(database: str = ":memory:") -> "DuckDBPyConnection":
    """Open a DuckDB connection with the ``spatial`` extension loaded.

    Parameters
    ----------
    database:
        DuckDB connection string. Defaults to ``:memory:`` for compute-only
        sessions; pass a ``.duckdb`` path for persistent snapshots (e.g. the
        file-blob CDC cache used by file-based adapters).
    """
    try:
        import duckdb
    except ImportError as exc:
        raise DuckDBSpatialUnavailable(
            "duckdb package not installed (this should not happen — duckdb "
            "is a hard dependency of gispulse). "
            "Reinstall via `pip install --force-reinstall gispulse`."
        ) from exc

    conn = duckdb.connect(database)
    _ensure_spatial_loaded(conn, getattr(duckdb, "__file__", ""))
    return conn


def is_spatial_loaded() -> bool:
    """Return True if a previous call to :func:`get_spatial_connection` succeeded.

    Used by ``gispulse doctor`` to report status without re-paying the install
    cost. A False return does not mean the extension is unavailable, only that
    we have not installed it yet in this process.
    """
    return any(_install_done.values())


def _reset_cache_for_tests() -> None:
    """Clear the install cache. Intended for the test suite only."""
    with _install_lock:
        _install_done.clear()


# ---------------------------------------------------------------------------
# EPSG roundtrip checks (gispulse doctor --install-spatial)
# ---------------------------------------------------------------------------

# Reference probes used by ``verify_epsg_roundtrip``. We pick a single FR
# point (Notre-Dame de Paris area) per CRS — pyproj computes the expected
# value at runtime so the test surfaces the exact deviation between bundled
# DuckDB-PROJ and the OS pyproj grid (which has the datum shifts).
_DEFAULT_PROBE_LONLAT: tuple[float, float] = (2.35, 48.85)
_DEFAULT_EPSG_CODES: tuple[int, ...] = (4326, 3857, 2154, 27572)
# Tolerance in target-CRS units. Lambert93 + Lambert II étendu use the
# NTF↔RGF93 datum shift; bundled DuckDB-PROJ misses the IGN grid and ends
# up ~1 km off, while pyproj on Linux pulls the grid from system packages.
_EPSG_TOLERANCE: dict[int, float] = {
    4326: 1e-6,
    3857: 5.0,  # meters
    2154: 250.0,  # meters — flag misses > IGN grid magnitude
    27572: 250.0,
}


@dataclass(frozen=True, slots=True)
class EPSGCheck:
    """Result of a single ``ST_Transform`` roundtrip check."""

    epsg: int
    ok: bool
    detail: str


def _expected_coords(target_epsg: int, lon: float, lat: float) -> tuple[float, float] | None:
    """Compute the expected ``(x, y)`` in ``target_epsg`` using pyproj.

    Returns ``None`` if pyproj cannot build the transformer (missing grid,
    unknown EPSG); the caller will report that as a check failure.
    """
    try:
        from pyproj import Transformer

        t = Transformer.from_crs("EPSG:4326", f"EPSG:{target_epsg}", always_xy=True)
        x, y = t.transform(lon, lat)
        if math.isnan(x) or math.isnan(y) or math.isinf(x) or math.isinf(y):
            return None
        return float(x), float(y)
    except Exception:  # noqa: BLE001
        return None


def verify_epsg_roundtrip(
    conn: "DuckDBPyConnection",
    epsg_codes: tuple[int, ...] | None = None,
    probe_lonlat: tuple[float, float] | None = None,
) -> list[EPSGCheck]:
    """Run ``ST_Transform`` against a fixed point per EPSG and check tolerance.

    The bundled DuckDB spatial extension ships PROJ statically with **network
    disabled**, so high-precision French datum shifts (NTF→RGF93) can fail
    silently or return slightly off coordinates. This helper surfaces those
    cases as ``ok=False`` with an explanation, instead of letting them rot
    behind a green ``LOAD spatial;``.

    Parameters
    ----------
    conn:
        A DuckDB connection with the spatial extension already loaded.
    epsg_codes:
        Tuple of target EPSG codes to check. Defaults to FR-relevant codes
        (``2154``, ``27572``) plus the universals (``3857``, ``4326``).
    probe_lonlat:
        Override the WGS84 probe point. Defaults to a Paris-area point that
        is in-bounds for every default EPSG.
    """
    codes = epsg_codes if epsg_codes is not None else _DEFAULT_EPSG_CODES
    lon, lat = probe_lonlat if probe_lonlat is not None else _DEFAULT_PROBE_LONLAT

    checks: list[EPSGCheck] = []
    for code in codes:
        try:
            row = conn.execute(
                "SELECT ST_X(p), ST_Y(p) FROM ("
                "SELECT ST_Transform(ST_Point(?, ?), 'EPSG:4326', ?, true) AS p"
                ")",
                [lon, lat, f"EPSG:{code}"],
            ).fetchone()
        except Exception as exc:  # noqa: BLE001 — duckdb errors are diverse
            checks.append(EPSGCheck(code, False, f"ST_Transform failed: {exc}"))
            continue

        if row is None or row[0] is None or row[1] is None:
            checks.append(EPSGCheck(code, False, "ST_Transform returned NULL"))
            continue

        x_got, y_got = float(row[0]), float(row[1])
        if math.isnan(x_got) or math.isnan(y_got):
            checks.append(EPSGCheck(code, False, "ST_Transform returned NaN"))
            continue

        expected = _expected_coords(code, lon, lat)
        if expected is None:
            checks.append(
                EPSGCheck(code, False, "pyproj could not compute reference (missing grid?)")
            )
            continue

        x_exp, y_exp = expected
        dx, dy = abs(x_got - x_exp), abs(y_got - y_exp)
        tol = _EPSG_TOLERANCE.get(code, 1.0)
        if dx > tol or dy > tol:
            checks.append(
                EPSGCheck(
                    code,
                    False,
                    f"off by ({dx:.1f}, {dy:.1f}) units > tol {tol} (PROJ grid missing?)",
                )
            )
        else:
            checks.append(EPSGCheck(code, True, f"Δ=({dx:.2f}, {dy:.2f}) ≤ tol {tol}"))
    return checks
