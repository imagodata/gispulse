"""GISPulse DSL — declarative expression layer for ``triggers.yaml``.

The DSL exposes a tiny safe surface so user-written ``set_field`` /
``validate`` rules can run as DuckDB SQL push-down without an ``eval``
escape hatch:

- :mod:`gispulse.dsl.geom_fcts` lists the whitelisted geometry functions
  (``geom_area_m2``, ``geom_centroid_x``, ``geom_is_valid`` …) and maps
  each to its ``ST_*`` SQL template.
- :mod:`gispulse.dsl.expression_parser` parses the user expression into
  a Python AST, validates it against a strict allowlist, and compiles it
  into safe DuckDB SQL. No bytecode is ever executed.

Public surface kept tiny on purpose: callers ask for ``compile_expression``
and either get SQL or :class:`DSLError`.
"""

from __future__ import annotations

from gispulse.dsl.expression_parser import (
    CompilationContext,
    DSLError,
    DSLValidationError,
    compile_expression,
)
from gispulse.dsl.geom_fcts import (
    GEOM_FUNCTIONS,
    GeomFunctionSpec,
    is_geom_function,
)

__all__ = [
    "CompilationContext",
    "DSLError",
    "DSLValidationError",
    "GEOM_FUNCTIONS",
    "GeomFunctionSpec",
    "compile_expression",
    "is_geom_function",
]
