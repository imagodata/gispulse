"""Backward-compat import shim for the v1.8.0 ``src/`` consolidation.

Before v1.8.0 GISPulse shipped seven top-level packages (``core``,
``capabilities``, ``rules``, ``orchestration``, ``persistence``,
``catalog``). They are now subpackages of :mod:`gispulse`. This meta-path
finder transparently redirects legacy imports — ``import core``,
``from capabilities.registry import REGISTRY``, … — to their ``gispulse.*``
location and emits a :class:`DeprecationWarning` once per root package.

It is installed by :mod:`gispulse`'s package ``__init__``, so it only ever
resolves names *after* ``import gispulse`` has run — no top-level module is
added to the wheel and there is no PyPI namespace collision.

TRANSITIONAL — to be removed in v1.9.0 once ``gispulse-enterprise`` and any
out-of-tree consumers have migrated to the ``gispulse.*`` paths.
"""

from __future__ import annotations

import importlib
import importlib.util
import sys
import warnings
from importlib.abc import Loader, MetaPathFinder
from types import ModuleType
from typing import Sequence

# Legacy top-level package names that moved under ``gispulse.`` in v1.8.0.
_LEGACY_ROOTS: frozenset[str] = frozenset(
    {"core", "capabilities", "rules", "orchestration", "persistence", "catalog"}
)
_warned: set[str] = set()


class _AliasLoader(Loader):
    """Loader that resolves a legacy name to its ``gispulse.*`` module."""

    def __init__(self, target: str) -> None:
        self._target = target

    def create_module(self, spec: importlib.machinery.ModuleSpec) -> ModuleType:
        return importlib.import_module(self._target)

    def exec_module(self, module: ModuleType) -> None:
        # ``import_module`` in ``create_module`` already executed the module.
        return None


class _LegacyTopLevelFinder(MetaPathFinder):
    """Redirect ``<root>`` and ``<root>.<sub>`` imports to ``gispulse.<...>``."""

    def find_spec(
        self,
        fullname: str,
        path: Sequence[str] | None = None,
        target: ModuleType | None = None,
    ) -> importlib.machinery.ModuleSpec | None:
        root = fullname.split(".", 1)[0]
        if root not in _LEGACY_ROOTS:
            return None
        if root not in _warned:
            _warned.add(root)
            warnings.warn(
                f"Importing the top-level `{root}` package is deprecated since "
                f"GISPulse 1.8.0 — import `gispulse.{root}` instead. This "
                f"compatibility shim will be removed in 1.9.0.",
                DeprecationWarning,
                stacklevel=2,
            )
        return importlib.util.spec_from_loader(
            fullname, _AliasLoader(f"gispulse.{fullname}")
        )


def install() -> None:
    """Append the legacy finder to ``sys.meta_path`` (idempotent)."""
    if any(isinstance(finder, _LegacyTopLevelFinder) for finder in sys.meta_path):
        return
    # Appended last: real packages always win; the shim only catches names
    # that no genuine finder could resolve.
    sys.meta_path.append(_LegacyTopLevelFinder())
