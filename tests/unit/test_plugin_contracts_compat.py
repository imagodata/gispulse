"""Backward-compat regression guard for ``gispulse.core.plugin_contracts``.

B-4 of the breaking-changes audit for the 2.0.0 release
(memory: ``audit_breaking_changes_v200_2026_05_19``).

The v1.8.0 consolidation introduced ``core.plugin_model`` as the import-free
source of truth for the plugin vocabulary. Only ``PROTOCOL_VERSION``
physically moved out of ``plugin_contracts`` — it is re-exported there for
compat. These tests pin the *public surface a 1.6.2 consumer relied on* so a
future refactor cannot silently break ``from gispulse.core.plugin_contracts
import <X>`` for downstream code.

Provenance of the frozen list: verified against the published wheel
``gispulse==1.6.2`` — its ``core/plugin_contracts.py`` exposed exactly these
eight names and shipped no ``__all__``.
"""

from __future__ import annotations

import importlib

import pytest

# The exact public surface of plugin_contracts.py in the 1.6.2 wheel.
# Importability of every one of these MUST hold for the whole 2.x line.
_LEGACY_1_6_2_SURFACE = (
    "PROTOCOL_VERSION",
    "RouterFactory",
    "MiddlewareFactory",
    "AuthProvider",
    "BillingProvider",
    "LicenceState",
    "LicenceProvider",
    "Connector",
)


@pytest.mark.parametrize("symbol", _LEGACY_1_6_2_SURFACE)
def test_legacy_1_6_2_symbol_is_importable(symbol: str) -> None:
    """Each 1.6.2 public symbol resolves from ``plugin_contracts``."""
    module = importlib.import_module("gispulse.core.plugin_contracts")
    assert hasattr(module, symbol), (
        f"{symbol!r} must stay importable from gispulse.core.plugin_contracts "
        f"(B-4 compat guarantee); see audit_breaking_changes_v200_2026_05_19"
    )


def test_legacy_surface_is_in_dunder_all() -> None:
    """The 1.6.2 surface is explicitly listed in ``__all__``.

    1.6.2 shipped no ``__all__``; 2.0.0 adds one. Every legacy name must be
    in it so ``from ... import *`` and tooling keep seeing them.
    """
    module = importlib.import_module("gispulse.core.plugin_contracts")
    exported = set(getattr(module, "__all__", ()))
    missing = set(_LEGACY_1_6_2_SURFACE) - exported
    assert not missing, f"missing from plugin_contracts.__all__: {sorted(missing)}"


def test_protocol_version_is_the_plugin_model_value() -> None:
    """``PROTOCOL_VERSION`` is a re-export, not a divergent copy.

    The single source of truth is ``core.plugin_model``; ``plugin_contracts``
    must alias it, never redefine it.
    """
    from gispulse.core import plugin_contracts, plugin_model

    assert plugin_contracts.PROTOCOL_VERSION is plugin_model.PROTOCOL_VERSION


def test_via_legacy_top_level_shim() -> None:
    """The ``core.plugin_contracts`` legacy path (``_compat`` shim) also works.

    A 1.6.2 consumer that did ``from core.plugin_contracts import LicenceState``
    is redirected by the ``_compat`` meta-path finder; the symbol must resolve.
    """
    import gispulse  # noqa: F401 - ensures _compat.install() has run

    with pytest.warns(DeprecationWarning):
        legacy = importlib.import_module("core.plugin_contracts")
    for symbol in _LEGACY_1_6_2_SURFACE:
        assert hasattr(legacy, symbol), f"{symbol!r} unreachable via core.* shim"
