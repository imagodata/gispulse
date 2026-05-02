"""
Pyogrio emits a RuntimeWarning for every layer that declares a GeoPackage
extension it cannot interpret. We declare the ``gispulse`` extension on our
internal ``_gispulse_*`` tables (rules, jobs, change_log, ...), so opening
a tracked GPKG produces ~11 warnings per command pointed at end users that
cannot act on them.

The filter narrows on the warning ``message`` text instead of the broad
``RuntimeWarning`` category so unrelated runtime warnings (encoding errors,
mixed CRS, etc.) keep surfacing.
"""

from __future__ import annotations

import warnings

# Matches the canonical pyogrio message :
#   "Layer _gispulse_rules relies on the 'gispulse' (https://gispulse.dev/...)
#    extension that should be implemented in order to read it safely, ..."
_GISPULSE_EXTENSION_PATTERN = r".*'gispulse'.*extension.*"


def silence_gispulse_extension_warnings() -> None:
    """Install a one-shot global filter for the gispulse extension warning.

    Idempotent — calling twice does not stack filters (Python deduplicates
    identical entries in ``warnings.filters``). Library callers that want to
    re-enable the warning can ``warnings.resetwarnings()`` afterwards.
    """
    warnings.filterwarnings(
        "ignore",
        message=_GISPULSE_EXTENSION_PATTERN,
        category=RuntimeWarning,
    )
