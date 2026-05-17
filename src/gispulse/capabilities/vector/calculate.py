from __future__ import annotations

import ast as _ast
import re as _re

import geopandas as gpd
import numpy as np

from gispulse.capabilities.base import Capability
from gispulse.capabilities.registry import register



# ---------------------------------------------------------------------------
# Calculation capabilities
# ---------------------------------------------------------------------------

# Patterns forbidden in user-supplied expressions (calculate + filter)
_DANGEROUS_EXPR_RE = _re.compile(
    r"(__\w+__|import\s*\(|exec\s*\(|eval\s*\(|compile\s*\(|globals\s*\(|"
    r"locals\s*\(|getattr\s*\(|setattr\s*\(|delattr\s*\(|open\s*\(|"
    r"__builtins__|__class__|__subclasses__|__import__)",
    _re.IGNORECASE,
)


# AST node types allowed in calculate expressions (arithmetic + attribute access)
_CALC_SAFE_NODES = (
    _ast.Expression, _ast.Module, _ast.Expr,
    _ast.BinOp, _ast.UnaryOp, _ast.BoolOp,
    _ast.Add, _ast.Sub, _ast.Mult, _ast.Div, _ast.FloorDiv, _ast.Mod, _ast.Pow,
    _ast.USub, _ast.UAdd,
    _ast.Compare, _ast.Eq, _ast.NotEq, _ast.Lt, _ast.LtE, _ast.Gt, _ast.GtE,
    _ast.And, _ast.Or, _ast.Not,
    _ast.IfExp,
    _ast.Constant, _ast.Name, _ast.Load,
    _ast.Call, _ast.Attribute,  # Allow np.log(...) style calls
    _ast.keyword,
    _ast.Subscript, _ast.Slice, _ast.Index,
)


def _validate_calc_expression(expr: str, columns: set[str]) -> None:
    """Validate a calculate expression via AST — reject code injection patterns.

    Only allows arithmetic, comparisons, function calls on whitelisted names,
    and attribute access (e.g. np.log). Blocks all dunder access, imports,
    comprehensions, lambdas, assignments, etc.

    Raises:
        ValueError: If expression contains dangerous patterns.
    """
    if _DANGEROUS_EXPR_RE.search(expr):
        raise ValueError(
            f"Expression contains forbidden pattern (potential code injection): {expr[:100]!r}"
        )
    if ";" in expr:
        raise ValueError(f"Expression contains forbidden character ';': {expr[:100]!r}")

    try:
        tree = _ast.parse(expr, mode="eval")
    except SyntaxError as e:
        raise ValueError(f"Invalid expression syntax: {e}") from e

    for node in _ast.walk(tree):
        if not isinstance(node, _CALC_SAFE_NODES):
            raise ValueError(
                f"Expression contains forbidden construct '{type(node).__name__}': {expr[:100]!r}"
            )
        # Block dunder attribute access (e.g. __class__, __subclasses__)
        if isinstance(node, _ast.Attribute) and node.attr.startswith("__"):
            raise ValueError(
                f"Expression contains forbidden dunder attribute '{node.attr}': {expr[:100]!r}"
            )


def _validate_query_expression(expr: str) -> None:
    """Validate a pandas.query() expression — reject code injection patterns.

    Raises:
        ValueError: If expression contains dangerous patterns.
    """
    if _DANGEROUS_EXPR_RE.search(expr):
        raise ValueError(
            f"Filter expression contains forbidden pattern: {expr[:100]!r}"
        )
    # Backticks in pandas.query can call Python — block them
    if "`" in expr:
        raise ValueError(
            f"Filter expression contains forbidden backtick character: {expr[:100]!r}"
        )


# Allowed names in calculate expressions — pure-math ufuncs + safe builtins.
# Exposing the full ``np`` module gave eval'd expressions access to
# ``np.save("/tmp/pwn", ...)`` and ``np.fromfile("/etc/passwd")`` — arbitrary
# file read/write. We now hand a curated namespace of pure-math ufuncs only.
_CALC_NP_UFUNCS = (
    "log", "log2", "log10", "log1p",
    "exp", "expm1",
    "sqrt", "cbrt", "square", "power",
    "sin", "cos", "tan", "arcsin", "arccos", "arctan", "arctan2",
    "sinh", "cosh", "tanh",
    "abs", "absolute", "sign", "ceil", "floor", "trunc", "round",
    "minimum", "maximum", "clip",
    "isnan", "isfinite", "isinf",
    "where",
    "pi", "e",
)


class _SafeNamespace:
    """Read-only attribute proxy exposing a fixed set of numpy ufuncs."""

    __slots__ = ("_attrs",)

    def __init__(self, source: object, allowed: tuple[str, ...]) -> None:
        self._attrs = {name: getattr(source, name) for name in allowed}

    def __getattr__(self, name: str) -> object:
        try:
            return self._attrs[name]
        except KeyError as exc:
            raise AttributeError(
                f"calculate: attribute 'np.{name}' is not allowed.",
            ) from exc


_CALC_ALLOWED: dict[str, object] = {
    "abs": abs,
    "round": round,
    "min": min,
    "max": max,
    "np": _SafeNamespace(np, _CALC_NP_UFUNCS),
}


@register
class CalculateCapability(Capability):
    """Computes new columns from expressions on existing fields.

    Expressions are evaluated per-row using pandas eval (numexpr engine)
    or, for complex expressions, via DataFrame.assign with a safe namespace.

    Examples::

        {"expressions": {"density": "population / area_m2"}}
        {"expressions": {"label": "commune + ' - ' + departement"}}
        {"expressions": {"ratio": "area_m2 / total_area * 100"}}
    """

    name = "calculate"
    description = "Computes new columns from arithmetic or string expressions on existing fields."

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        expressions: dict[str, str] | None = None,
        **_,
    ) -> gpd.GeoDataFrame:
        """
        Args:
            gdf:         Input GeoDataFrame.
            expressions: Mapping of {new_column_name: expression_string}.
                         Expressions can reference existing column names and
                         use arithmetic operators (+, -, *, /, //, %, **).

        Returns:
            GeoDataFrame with new/updated columns.
        """
        if not expressions:
            return gdf

        result = gdf.copy()
        for col_name, expr in expressions.items():
            _validate_calc_expression(expr, set(result.columns))
            # Build a safe namespace: only column Series + allowed math helpers
            namespace: dict[str, object] = {
                c: result[c] for c in result.columns if c != result.geometry.name
            }
            namespace.update(_CALC_ALLOWED)
            namespace["__builtins__"] = {}
            # AST-validated eval: expression structure is verified above,
            # namespace is restricted to columns + math helpers only
            result[col_name] = eval(expr, namespace)  # noqa: S307
        return result

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "expressions": {
                    "type": "object",
                    "additionalProperties": {"type": "string"},
                    "description": (
                        "Mapping of column_name -> expression. "
                        "Example: {\"density\": \"population / area_m2\"}"
                    ),
                },
            },
            "required": ["expressions"],
        }
