"""Regression tests for the v1.8.0 legacy import shim (`gispulse/_compat.py`).

These tests pin the behaviour expected by gispulse-enterprise consumers
that still import from the pre-consolidation top-level packages
(``persistence.*``, ``core.*``, …). The most important guarantee is
**class identity across namespaces** — see imagodata/gispulse#333.
"""
from __future__ import annotations

import importlib
import sys
import warnings

import pytest

# Importing ``gispulse`` triggers ``_compat.install()`` which registers the
# meta-path finder that aliases the legacy roots. Tests below rely on it.
import gispulse  # noqa: F401  (side-effect import)


@pytest.fixture(autouse=True)
def _silence_deprecation():
    # The shim raises a DeprecationWarning on first import of each legacy
    # root. We exercise that path on purpose; silence it for clarity.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        yield


class TestStorageErrorClassIdentity:
    """imagodata/gispulse#333 — StorageError must be the same class object
    whether imported via the legacy or the canonical namespace."""

    def test_module_object_is_shared(self):
        legacy = importlib.import_module("persistence.storage")
        canonical = importlib.import_module("gispulse.persistence.storage")
        assert legacy is canonical, (
            "Legacy and canonical module objects must be the same; otherwise "
            "their classes are duplicated and isinstance() breaks."
        )

    def test_storage_error_class_is_shared(self):
        from persistence.storage import StorageError as Legacy
        from gispulse.persistence.storage import StorageError as Canonical
        assert Legacy is Canonical

    def test_pytest_raises_legacy_matches_canonical_raise(self):
        """The original failure mode of imagodata/gispulse#333: a test
        importing ``StorageError`` from the legacy path could not catch
        an error raised from the canonical implementation."""
        from persistence.storage import StorageError as Legacy
        from gispulse.persistence.storage import validate_storage_key
        with pytest.raises(Legacy, match="Null byte"):
            validate_storage_key("foo\x00bar")

    def test_validate_storage_key_function_is_shared(self):
        """Functions exported by both namespaces are the same object."""
        from persistence.storage import validate_storage_key as legacy
        from gispulse.persistence.storage import validate_storage_key as canonical
        assert legacy is canonical


class TestLegacyRoots:
    """Every legacy root (``core``, ``capabilities``, …) aliases cleanly."""

    LEGACY_ROOTS = ["core", "capabilities", "rules", "orchestration",
                    "persistence", "catalog"]

    @pytest.mark.parametrize("root", LEGACY_ROOTS)
    def test_root_alias_shares_module(self, root):
        legacy = importlib.import_module(root)
        canonical = importlib.import_module(f"gispulse.{root}")
        assert legacy is canonical, f"`{root}` and `gispulse.{root}` diverged"


class TestSysModulesConsistency:
    """sys.modules must hold the same object under both keys."""

    def test_sys_modules_share_object(self):
        importlib.import_module("persistence.storage")
        importlib.import_module("gispulse.persistence.storage")
        assert sys.modules["persistence.storage"] is sys.modules[
            "gispulse.persistence.storage"
        ]
