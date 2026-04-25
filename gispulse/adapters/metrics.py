"""
Re-export shim — MetricsCollector lives in core/observability.py.

This module is kept for backward compatibility. Import from
``core.observability`` in new code.
"""

from core.observability import MetricsCollector  # noqa: F401

__all__ = ["MetricsCollector"]
