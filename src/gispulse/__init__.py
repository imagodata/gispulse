"""GISPulse — moteur geospatial modulaire avec regles metier."""

try:
    from importlib.metadata import version

    __version__ = version("gispulse")
except Exception:
    __version__ = "unknown"

# Transitional (v1.8.0 -> v1.9.0): redirect legacy top-level imports
# (`core`, `capabilities`, …) to their `gispulse.*` location. See _compat.
from gispulse import _compat as _compat

_compat.install()

