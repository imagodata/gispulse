from __future__ import annotations


def classFactory(iface):
    from .main_plugin import GISPulsePlugin

    return GISPulsePlugin(iface)
