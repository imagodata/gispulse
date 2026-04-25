"""GISPulse SDK — Python client library for the GISPulse geospatial engine."""

from gispulse_sdk.client import GISPulseClient
from gispulse_sdk.exceptions import (
    GISPulseError,
    AuthError,
    NotFoundError,
    ConflictError,
    ValidationError,
    ServerError,
)

__version__ = "0.1.0"

__all__ = [
    "GISPulseClient",
    "GISPulseAsyncClient",
    "GISPulseError",
    "AuthError",
    "NotFoundError",
    "ConflictError",
    "ValidationError",
    "ServerError",
]


def __getattr__(name: str):
    # Lazy import to avoid pulling in async deps when not needed
    if name == "GISPulseAsyncClient":
        from gispulse_sdk.async_client import GISPulseAsyncClient
        return GISPulseAsyncClient
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
