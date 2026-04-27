"""GISPulse headless runtime.

Provides a FastAPI-free wiring of the trigger pipeline so the CLI
(``gispulse triggers run``) and embedded integrations (QGIS / ArcGIS /
SDK) can drive the same plumbing as the HTTP server without booting
uvicorn.

The runtime mirrors the lifespan wiring at ``adapters/http/app.py``
(``ChangeLogWatcher`` + ``ActionDispatcher`` + a no-op ``EventHub``)
so behaviour stays identical between modes.

Public API:
    - :func:`build_runtime`        — factory that wires everything.
    - :class:`HeadlessRuntime`     — handle returned by the factory.
    - :class:`NullEventHub`        — drop-in EventHub stub for headless mode.
"""

from __future__ import annotations

from gispulse.runtime.config_loader import (
    ConfigError,
    GISPulseConfig,
    load_config,
    to_triggers,
    validate_against_gpkg,
)
from gispulse.runtime.headless_runtime import (
    HeadlessRuntime,
    NullEventHub,
    build_runtime,
)

__all__ = [
    "ConfigError",
    "GISPulseConfig",
    "HeadlessRuntime",
    "NullEventHub",
    "build_runtime",
    "load_config",
    "to_triggers",
    "validate_against_gpkg",
]
