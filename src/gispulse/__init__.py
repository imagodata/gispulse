"""GISPulse — moteur géospatial modulaire avec règles métier.

The pip façade of the v1.8.0 "Foundations" refonte. Importing GISPulse is
cheap — the heavy subsystems (geopandas, the engines, FastAPI) load only
when an application object is actually touched:

    >>> import gispulse
    >>> app = gispulse.GISPulseApp()
    >>> app.list_capabilities()          # capabilities, lazily wired
    >>> result = gispulse.apply("buffer", gdf, distance=10.0)

``GISPulseApp`` is the single in-process entry point (see
:mod:`gispulse.app`); ``apply`` / ``run`` are verb-shaped shortcuts onto a
process-wide default instance.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

try:
    from importlib.metadata import version

    __version__ = version("gispulse")
except Exception:
    __version__ = "unknown"

# Transitional (v1.8.0 -> v1.9.0): redirect legacy top-level imports
# (`core`, `capabilities`, …) to their `gispulse.*` location. See _compat.
from gispulse import _compat as _compat

_compat.install()

__all__ = ["__version__", "GISPulseApp", "get_app", "apply", "run"]

if TYPE_CHECKING:  # pragma: no cover - typing only
    from gispulse.app import GISPulseApp, apply, get_app, run


def __getattr__(name: str):
    """Lazily expose the application façade (:pep:`562`).

    Keeps ``import gispulse`` free of heavy imports — ``gispulse.app`` and
    its subsystems are pulled only when the façade is first accessed.
    """
    if name in {"GISPulseApp", "get_app", "apply", "run"}:
        from gispulse import app as _app

        return getattr(_app, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(__all__)
