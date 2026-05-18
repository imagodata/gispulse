"""
GISPulse logging configuration via structlog.

Provides a single ``get_logger(name)`` entry point that returns a bound
structlog logger.  The output format and level are controlled by two
environment variables:

- ``GISPULSE_LOG_LEVEL``  — standard level name (default: ``INFO``).
- ``GISPULSE_LOG_FORMAT`` — ``"console"`` (default, coloured dev output)
                             or ``"json"`` (structured JSON for production).

Configuration is applied once at first import through ``_configure()``.
Subsequent calls to ``get_logger`` are cheap — they only bind the *logger*
context variable.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog

from gispulse.core.config import settings

__all__ = ["get_logger"]

_CONFIGURED = False


def _inject_logger_name(
    logger: Any,
    method_name: str,
    event_dict: structlog.types.EventDict,
) -> structlog.types.EventDict:
    """Processor: promote the bound ``logger`` key to event_dict.

    Compatible with both ``PrintLoggerFactory`` and ``stdlib.LoggerFactory``
    because it reads the value from the existing bound context rather than
    from the logger object itself.
    """
    # The name is already bound via get_logger().bind(logger=name); keep it.
    # If not present, fall back to the class name of the underlying logger.
    if "logger" not in event_dict:
        event_dict["logger"] = type(logger).__name__
    return event_dict


def _configure() -> None:
    """Configure structlog and the stdlib root logger.

    Called automatically on first use.  Idempotent — subsequent calls
    are no-ops.
    """
    global _CONFIGURED
    if _CONFIGURED:
        return

    log_level_name: str = settings.logging.log_level
    log_format: str = settings.logging.log_format

    log_level: int = getattr(logging, log_level_name, logging.INFO)

    # Configure stdlib root logger so that third-party libraries that use
    # logging are captured at the same level.
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=log_level,
    )

    # Shared processors applied to every log record regardless of format.
    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        _inject_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
    ]

    if log_format == "json":
        # Production: machine-readable JSON.
        processors: list[structlog.types.Processor] = shared_processors + [
            structlog.processors.dict_tracebacks,
            structlog.processors.JSONRenderer(),
        ]
    else:
        # Development: human-readable coloured console output.
        processors = shared_processors + [
            structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty() or sys.stdout.isatty()),
        ]

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=False,
    )

    _CONFIGURED = True


def get_logger(name: str) -> structlog.types.FilteringBoundLogger:
    """Return a structlog logger bound with the given *name*.

    The logging framework is configured on first call.

    Args:
        name: Logical name for the logger, typically ``__name__`` of the
              calling module (e.g. ``"orchestration.runner"``).

    Returns:
        A structlog bound logger with ``logger=name`` in its context.

    Example::

        from gispulse.core.logging import get_logger
        log = get_logger(__name__)
        log.info("job_started", job_id=str(job.id))
    """
    _configure()
    return structlog.get_logger(name).bind(logger=name)
