"""GISPulse capability plugin — example.

This module is the entry-point for the plugin. The ``register`` function is
called automatically by GISPulse when it discovers this package via the
``gispulse.capabilities`` entry-point group.
"""

from __future__ import annotations


def register() -> None:
    """Register all capabilities provided by this plugin."""
    # Import the module so the @register decorators fire
    from gispulse_cap_example import capabilities  # noqa: F401
