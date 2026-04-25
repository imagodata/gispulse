"""
Re-export shim — PredicateEvaluator (Shapely backend) now lives in rules/predicates.py.

Kept for backward compatibility with adapters/esb/event_router.py.
Import from ``rules.predicates`` in new code.
"""

from rules.predicates import (  # noqa: F401
    RefLoader,
    ShapelyPredicateEvaluator as PredicateEvaluator,
    _noop_loader,
)

__all__ = ["PredicateEvaluator", "RefLoader", "_noop_loader"]
