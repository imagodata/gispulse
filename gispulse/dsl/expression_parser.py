"""Safe expression parser for ``triggers.yaml`` ``set_field`` / ``validate``.

The parser is built on top of Python's :mod:`ast` so we can lean on the
mature tokeniser without ever calling :func:`compile`/:func:`eval`. The
input is parsed in ``mode='eval'`` and walked by :class:`_Validator`
which rejects everything outside the strict allowlist:

==============================  ========================================
Allowed                         Forbidden
==============================  ========================================
int / float literals            string / bytes / list / dict literals
column references (``Name``)    attribute access (``foo.bar``)
``+ - * / %`` binary operators  ``** << >> & | ^`` and any other op
unary ``-`` / ``+``             ``not`` / boolean ops
parentheses                     comparisons, comprehensions, lambdas
calls to whitelisted geom fcts  any other call (incl. user functions,
                                ``__import__``, ``eval``, ``globals``)
keyword args limited per spec   star args / **kwargs
==============================  ========================================

The output is a pure SQL string suitable for splicing into a DuckDB
``UPDATE``/``SELECT``/``CASE WHEN`` clause. Column names are quoted with
double quotes after a strict ``[A-Za-z_][A-Za-z0-9_]*`` validation; geom
functions are expanded via :data:`gispulse.dsl.geom_fcts.GEOM_FUNCTIONS`.

The compiler does not introspect the dataset schema — column references
are quoted but not checked. The runtime catches typos at execution time
when DuckDB raises ``Binder Error: column not found``.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from typing import Literal

from gispulse.dsl.geom_fcts import GEOM_FUNCTIONS, GeomFunctionSpec

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")
_EPSG_RE = re.compile(r"^EPSG:\d{3,6}$", re.IGNORECASE)


class DSLError(ValueError):
    """Base class for any DSL error surfaced by this module."""


class DSLValidationError(DSLError):
    """Raised when an expression violates the DSL allowlist.

    The message points at the AST node by line/column when available so
    operators can fix their YAML quickly.
    """


@dataclass(frozen=True, slots=True)
class CompilationContext:
    """Context passed to :func:`compile_expression`.

    Parameters
    ----------
    geom_column:
        SQL identifier of the geometry column. Must be a valid identifier
        — quoted with double quotes by the compiler. Defaults to
        ``"geom"`` to match the GeoPackage convention.
    source_epsg:
        EPSG code of the dataset's geometry column (e.g. ``"EPSG:4326"``).
        Required when an expression uses any CRS-aware geom function so
        the emitted ``ST_Transform`` knows the source CRS.
    default_metric_epsg:
        Fallback EPSG used by measure functions (``geom_area_m2`` …) when
        the user does not pass ``epsg=...``. Defaults to ``"EPSG:2154"``
        (Lambert 93) — appropriate for FR-centric datasets; override to
        ``"EPSG:3857"`` for global Web Mercator.
    """

    geom_column: str = "geom"
    source_epsg: str | None = None
    default_metric_epsg: str = "EPSG:2154"

    def __post_init__(self) -> None:
        if not _IDENT_RE.match(self.geom_column):
            raise DSLValidationError(
                f"invalid geom_column identifier {self.geom_column!r}"
            )
        if self.source_epsg is not None and not _EPSG_RE.match(self.source_epsg):
            raise DSLValidationError(
                f"source_epsg must look like 'EPSG:NNNN', got {self.source_epsg!r}"
            )
        if not _EPSG_RE.match(self.default_metric_epsg):
            raise DSLValidationError(
                f"default_metric_epsg must look like 'EPSG:NNNN', got "
                f"{self.default_metric_epsg!r}"
            )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


CompileMode = Literal["scalar", "boolean"]


def compile_expression(
    expr: str,
    ctx: CompilationContext | None = None,
    *,
    mode: "CompileMode" = "scalar",
) -> str:
    """Compile ``expr`` to safe DuckDB SQL.

    Parameters
    ----------
    expr:
        The user-written expression, e.g. ``"geom_area_m2() / 10000"``.
    ctx:
        Compilation context. Defaults to a no-source context — fine for
        CRS-agnostic expressions (``geom_npoints()``, plain arithmetic on
        columns); CRS-aware functions raise :class:`DSLValidationError`
        when invoked without a source EPSG.
    mode:
        ``"scalar"`` (default) — arithmetic only, used for ``set_field``
        expressions. ``"boolean"`` — also accepts ``==`` ``!=`` ``<``
        ``<=`` ``>`` ``>=`` comparisons plus ``and`` / ``or`` / ``not``
        and rejects pure-arithmetic expressions; used for ``validate:``
        rules and trigger ``predicate:`` clauses.

    Returns
    -------
    str
        A SQL fragment safe to splice (no user input is interpolated
        verbatim — column names are validated against ``[A-Za-z_]\\w*``,
        EPSG codes against ``EPSG:NNNN``).

    Raises
    ------
    DSLValidationError
        On parse failure or any allowlist violation.
    """
    if ctx is None:
        ctx = CompilationContext()
    if mode not in ("scalar", "boolean"):
        raise DSLValidationError(f"unsupported mode {mode!r}")
    if not isinstance(expr, str):
        raise DSLValidationError(
            f"expression must be a string, got {type(expr).__name__}"
        )
    if not expr.strip():
        raise DSLValidationError("expression is empty")
    if "\x00" in expr:
        raise DSLValidationError("expression contains NUL byte")
    if len(expr) > 4096:
        raise DSLValidationError(
            f"expression too long ({len(expr)} chars > 4096)"
        )
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        raise DSLValidationError(
            f"syntax error at line {exc.lineno}, col {exc.offset}: {exc.msg}"
        ) from exc
    return _Compiler(ctx, mode=mode).visit(tree.body)


# ---------------------------------------------------------------------------
# Compiler implementation
# ---------------------------------------------------------------------------


_BIN_OPS: dict[type[ast.operator], str] = {
    ast.Add: "+",
    ast.Sub: "-",
    ast.Mult: "*",
    ast.Div: "/",
    ast.Mod: "%",
}

_UNARY_OPS: dict[type[ast.unaryop], str] = {
    ast.USub: "-",
    ast.UAdd: "+",
}

# Boolean-mode-only allowlists. ``compile_expression(..., mode="boolean")``
# unlocks comparisons + and/or/not for use inside ``validate:`` rules.
_CMP_OPS: dict[type[ast.cmpop], str] = {
    ast.Eq: "=",
    ast.NotEq: "<>",
    ast.Lt: "<",
    ast.LtE: "<=",
    ast.Gt: ">",
    ast.GtE: ">=",
}

_BOOL_OPS: dict[type[ast.boolop], str] = {
    ast.And: "AND",
    ast.Or: "OR",
}


class _Compiler:
    """Walks the AST produced by :func:`ast.parse` and emits SQL."""

    def __init__(self, ctx: CompilationContext, *, mode: "CompileMode" = "scalar") -> None:
        self.ctx = ctx
        self.mode = mode

    def visit(self, node: ast.AST) -> str:
        method = getattr(self, f"visit_{type(node).__name__}", None)
        if method is None:
            raise DSLValidationError(
                self._explain(node, f"unsupported node {type(node).__name__}")
            )
        return method(node)

    # -- literals -------------------------------------------------------------

    def visit_Constant(self, node: ast.Constant) -> str:
        if isinstance(node.value, bool):
            return "TRUE" if node.value else "FALSE"
        if isinstance(node.value, int):
            return str(node.value)
        if isinstance(node.value, float):
            return repr(node.value)
        raise DSLValidationError(
            self._explain(
                node,
                f"unsupported literal type {type(node.value).__name__}; "
                "only int / float / bool are allowed",
            )
        )

    # -- column reference -----------------------------------------------------

    def visit_Name(self, node: ast.Name) -> str:
        ident = node.id
        if not _IDENT_RE.match(ident):
            raise DSLValidationError(
                self._explain(node, f"invalid identifier {ident!r}")
            )
        if ident in GEOM_FUNCTIONS:
            raise DSLValidationError(
                self._explain(
                    node,
                    f"{ident!r} is a function and must be called as "
                    f"{ident}(...) — bare name reference rejected",
                )
            )
        # Reject reserved DuckDB keywords-via-bareword as best-effort hygiene.
        if ident.upper() in {"SELECT", "FROM", "WHERE", "OR", "AND", "NOT"}:
            raise DSLValidationError(
                self._explain(node, f"{ident!r} is a SQL keyword; rename the column")
            )
        return f'"{ident}"'

    # -- arithmetic -----------------------------------------------------------

    def visit_BinOp(self, node: ast.BinOp) -> str:
        op_cls = type(node.op)
        op_sym = _BIN_OPS.get(op_cls)
        if op_sym is None:
            raise DSLValidationError(
                self._explain(node, f"binary operator {op_cls.__name__} not allowed")
            )
        left = self.visit(node.left)
        right = self.visit(node.right)
        return f"({left} {op_sym} {right})"

    def visit_UnaryOp(self, node: ast.UnaryOp) -> str:
        op_cls = type(node.op)
        if op_cls is ast.Not:
            if self.mode != "boolean":
                raise DSLValidationError(
                    self._explain(node, "'not' requires boolean mode")
                )
            operand = self.visit(node.operand)
            return f"(NOT {operand})"
        op_sym = _UNARY_OPS.get(op_cls)
        if op_sym is None:
            raise DSLValidationError(
                self._explain(node, f"unary operator {op_cls.__name__} not allowed")
            )
        operand = self.visit(node.operand)
        return f"({op_sym}{operand})"

    # -- comparisons + boolean ops (boolean mode only) -----------------------

    def visit_Compare(self, node: ast.Compare) -> str:
        if self.mode != "boolean":
            raise DSLValidationError(
                self._explain(node, "comparisons require boolean mode")
            )
        if len(node.ops) != 1 or len(node.comparators) != 1:
            raise DSLValidationError(
                self._explain(
                    node,
                    "chained comparisons (e.g. ``a < b < c``) are not allowed",
                )
            )
        op_cls = type(node.ops[0])
        op_sym = _CMP_OPS.get(op_cls)
        if op_sym is None:
            raise DSLValidationError(
                self._explain(
                    node, f"comparison operator {op_cls.__name__} not allowed"
                )
            )
        left = self.visit(node.left)
        right = self.visit(node.comparators[0])
        return f"({left} {op_sym} {right})"

    def visit_BoolOp(self, node: ast.BoolOp) -> str:
        if self.mode != "boolean":
            raise DSLValidationError(
                self._explain(node, "'and'/'or' require boolean mode")
            )
        op_cls = type(node.op)
        op_sym = _BOOL_OPS.get(op_cls)
        if op_sym is None:
            raise DSLValidationError(
                self._explain(node, f"boolean op {op_cls.__name__} not allowed")
            )
        parts = [self.visit(v) for v in node.values]
        return "(" + f" {op_sym} ".join(parts) + ")"

    # -- function calls -------------------------------------------------------

    def visit_Call(self, node: ast.Call) -> str:
        if not isinstance(node.func, ast.Name):
            raise DSLValidationError(
                self._explain(
                    node,
                    "only direct function calls are allowed "
                    "(no method calls, no attribute access)",
                )
            )
        fn_name = node.func.id
        spec = GEOM_FUNCTIONS.get(fn_name)
        if spec is None:
            raise DSLValidationError(
                self._explain(
                    node,
                    f"function {fn_name!r} is not in the DSL whitelist; "
                    f"allowed: {sorted(GEOM_FUNCTIONS)}",
                )
            )
        if node.args:
            raise DSLValidationError(
                self._explain(
                    node,
                    f"{fn_name}() takes no positional arguments; "
                    "use keyword arguments only",
                )
            )
        kwargs = self._collect_kwargs(node, spec)
        return self._render_geom_call(spec, kwargs, node)

    def _collect_kwargs(
        self, node: ast.Call, spec: GeomFunctionSpec
    ) -> dict[str, str]:
        out: dict[str, str] = {}
        for kw in node.keywords:
            if kw.arg is None:
                raise DSLValidationError(
                    self._explain(node, "**kwargs splat is not allowed")
                )
            if kw.arg not in spec.accepted_kwargs:
                raise DSLValidationError(
                    self._explain(
                        node,
                        f"{spec.name}() does not accept keyword "
                        f"{kw.arg!r}; allowed: {list(spec.accepted_kwargs)}",
                    )
                )
            if not isinstance(kw.value, ast.Constant) or not isinstance(
                kw.value.value, str
            ):
                raise DSLValidationError(
                    self._explain(
                        node,
                        f"{spec.name}({kw.arg}=...) requires a string literal",
                    )
                )
            out[kw.arg] = kw.value.value
        return out

    def _render_geom_call(
        self,
        spec: GeomFunctionSpec,
        kwargs: dict[str, str],
        node: ast.AST,
    ) -> str:
        epsg = kwargs.get("epsg", self.ctx.default_metric_epsg)
        if spec.crs_aware:
            if not _EPSG_RE.match(epsg):
                raise DSLValidationError(
                    self._explain(
                        node,
                        f"epsg must look like 'EPSG:NNNN', got {epsg!r}",
                    )
                )
            if self.ctx.source_epsg is None:
                raise DSLValidationError(
                    self._explain(
                        node,
                        f"{spec.name}() needs the dataset CRS — pass "
                        "source_epsg in CompilationContext",
                    )
                )
            sub = {
                "geom": f'"{self.ctx.geom_column}"',
                "epsg": epsg,
                "src": f"'{self.ctx.source_epsg}'",
            }
        else:
            sub = {"geom": f'"{self.ctx.geom_column}"'}
        return spec.sql_template.format(**sub)

    # -- error helpers --------------------------------------------------------

    @staticmethod
    def _explain(node: ast.AST, msg: str) -> str:
        line = getattr(node, "lineno", "?")
        col = getattr(node, "col_offset", "?")
        return f"line {line}, col {col}: {msg}"
