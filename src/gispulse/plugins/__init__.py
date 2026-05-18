"""Curated plugin-author API for GISPulse extensions."""

from gispulse.plugins.api import (
    __all__ as _API_ALL,
    Capability as Capability,
    PluginHostContext as PluginHostContext,
    register_capability as register_capability,
)


def __getattr__(name: str) -> object:
    from gispulse.plugins import api

    try:
        value = getattr(api, name)
    except AttributeError:
        raise AttributeError(f"module 'gispulse.plugins' has no attribute {name!r}") from None
    globals()[name] = value
    return value

__all__ = list(_API_ALL)
