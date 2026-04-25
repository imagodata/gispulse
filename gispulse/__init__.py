"""GISPulse — moteur geospatial modulaire avec regles metier."""

try:
    from importlib.metadata import version

    __version__ = version("gispulse")
except Exception:
    __version__ = "unknown"
