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

TRANSITIONAL — retained through the whole 2.0.x line and removed in v2.1.0,
once ``gispulse-enterprise`` and any out-of-tree consumers have migrated to
the ``gispulse.*`` paths. (The earlier "1.9.0" deadline was superseded: the
1.7.x/1.8.x/1.9.x line was never published — the consolidation ships
directly in the major release 2.0.0, so the shim must outlive 2.0.)
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
    """Loader that aliases a legacy name to its ``gispulse.*`` module.

    CRITICAL — class identity (see imagodata/gispulse#333): legacy and
    canonical names MUST point to the *same* module object so that
    ``from persistence.storage import StorageError`` yields the same class
    as ``from gispulse.persistence.storage import StorageError``. Without
    this, ``isinstance(err, StorageError)`` and ``pytest.raises(...)``
    silently fail across the namespace boundary.

    Implementation: ``exec_module`` overwrites ``sys.modules[spec.name]``
    with the canonical module *after* Python's import machinery has placed
    its freshly-created (empty) module there. This is the only point at
    which the override sticks — doing it in ``create_module`` is reverted
    by the import system because the returned module's ``__name__`` is the
    canonical one, not ``spec.name``.
    """

    def __init__(self, target: str) -> None:
        self._target = target

    def create_module(self, spec: importlib.machinery.ModuleSpec) -> ModuleType | None:
        # Returning None lets Python allocate a default empty module under
        # ``spec.name``; we replace it in ``exec_module``.
        return None

    def exec_module(self, module: ModuleType) -> None:
        # Resolve the canonical module (may already be cached) and
        # overwrite the sys.modules entry the import machinery just
        # installed. From this point on, ``import <legacy>`` and
        # ``import gispulse.<legacy>`` resolve to the same object, so
        # class identity holds across both namespaces.
        target = importlib.import_module(self._target)
        sys.modules[module.__name__] = target


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
                f"compatibility shim will be removed in 2.1.0.",
                DeprecationWarning,
                stacklevel=2,
            )
        return importlib.util.spec_from_loader(
            fullname, _AliasLoader(f"gispulse.{fullname}")
        )


def install() -> None:
    """Insert the legacy finder at the front of ``sys.meta_path`` (idempotent).

    The finder MUST run before ``PathFinder``: once we alias
    ``sys.modules['persistence'] = gispulse.persistence``, the legacy
    package inherits ``__path__`` from the canonical one. If a standard
    ``PathFinder`` reached a sub-import (``persistence.storage``) before
    our shim, it would discover ``gispulse/persistence/storage.py`` via
    the parent's ``__path__`` and execute it a *second* time under the
    legacy name — duplicating every class (StorageError, …) and breaking
    ``isinstance()`` checks (imagodata/gispulse#333).

    Prepending guarantees the alias hook intercepts every legacy import
    before any path-based finder runs, so ``sys.modules`` ends up with
    one shared module object per (root, sub) tuple.
    """
    if any(isinstance(finder, _LegacyTopLevelFinder) for finder in sys.meta_path):
        return
    sys.meta_path.insert(0, _LegacyTopLevelFinder())
