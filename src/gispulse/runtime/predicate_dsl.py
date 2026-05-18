"""Predicate DSL parser for ``gispulse triggers`` (S4).

Why a dedicated parser
----------------------
The trigger YAML config (Mode 1) lets the operator filter rows with a
human-readable predicate string::

    triggers:
      - name: high_value_parcels
        table: parcels
        predicate: "valeur > 100 AND status == 'pending'"

The HTTP path uses *structured* predicates (``AttrPredicate`` /
``CompoundPredicate`` dataclasses defined in ``core/predicates.py``). The
in-memory evaluator in ``rules/predicates.py`` already knows how to walk
that AST against a row payload. This module bridges the two: it turns
the DSL string into the same structured AST so the runtime reuses the
existing evaluator without parallel logic.

Grammar (LL(1), recursive-descent)
----------------------------------

::

    predicate  := or_expr
    or_expr    := and_expr ("OR" and_expr)*
    and_expr   := not_expr ("AND" not_expr)*
    not_expr   := "NOT" not_expr | comparison
    comparison := attr op literal
                | attr "IS" "NOT"? "NULL"
                | attr ("NOT" "IN" | "IN") list_literal
                | "(" or_expr ")"
    attr       := identifier ("." identifier)*
    op         := "==" | "!=" | ">" | ">=" | "<" | "<="
    literal    := number | string | boolean | "null"
    list_literal := "[" literal ("," literal)* "]"

Security guarantees
-------------------
* No ``eval`` / ``exec`` / ``compile`` / ``simpleeval`` — the parser is
  100% explicit, hand-written recursive descent.
* Operator alphabet is a closed whitelist. Unknown tokens raise
  ``PredicateSyntaxError`` with line/column context.
* Right-hand side of comparisons is *literal only*. Function calls,
  attribute access on RHS, and dunder identifiers are rejected.
* Identifiers are restricted to ``[A-Za-z_][A-Za-z0-9_]*`` segments
  joined by dots. Dunder names (``__class__`` etc.) are refused.
* Maximum nesting depth (default 32) protects against stack-blowing
  payloads. Configurable via ``MAX_DEPTH``.
* NUL bytes and non-printable control characters in the input are
  rejected before parsing.

UPDATE row semantics
--------------------
For UPDATE rows, the runtime supplies a payload that exposes both the
old and new values:

* Bare attributes (``status``)        → ``new.status`` (familiar SQL feel).
* Explicit ``new.status``             → new row column.
* Explicit ``old.status``             → old row column.

INSERT and DELETE rows expose only one snapshot; ``old.*`` on INSERT
returns ``None`` (and therefore comparisons fail unless the operator is
``is_null``).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only
    from gispulse.core.predicates import AnyPredicate

log = logging.getLogger(__name__)

# Maximum recursion depth allowed during parsing AND evaluation. The
# parser nests once per left-paren / NOT / binary-op; 32 covers any
# reasonable hand-written predicate while safely rejecting adversarial
# 1000-deep payloads in <1 ms.
MAX_DEPTH = 32

# Reserved keywords (case-insensitive). They cannot appear as bare
# identifiers, so a column literally called ``and`` would have to be
# quoted — fine for a YAML config DSL.
_KEYWORDS: frozenset[str] = frozenset(
    {"AND", "OR", "NOT", "IN", "IS", "NULL", "TRUE", "FALSE"}
)

# Identifier segment ([A-Za-z_][A-Za-z0-9_]*). We also reject any segment
# starting OR ending with double-underscore to keep dunders out (defense
# in depth — even though we never call getattr on the row dict).
_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class PredicateError(ValueError):
    """Base class for DSL errors (parsing or evaluation)."""


class PredicateSyntaxError(PredicateError):
    """Raised when the DSL source is syntactically invalid.

    Carries ``line``/``col`` so CLI callers can format a
    ``triggers validate`` style error message.
    """

    def __init__(self, message: str, *, line: int = 1, col: int = 1) -> None:
        super().__init__(f"line {line}, col {col}: {message}")
        self.line = line
        self.col = col


class PredicateDepthError(PredicateError):
    """Raised when the parser/evaluator detects unsafe nesting."""


class PredicateEvalError(PredicateError):
    """Raised at evaluation time (e.g. type mismatch with ``WARN`` policy
    disabled)."""


# ---------------------------------------------------------------------------
# Token model
# ---------------------------------------------------------------------------


class _TokenKind(str, Enum):
    LPAREN = "("
    RPAREN = ")"
    LBRACKET = "["
    RBRACKET = "]"
    COMMA = ","
    OP = "op"          # ==, !=, >, >=, <, <=
    IDENT = "ident"    # bare identifier or attribute path
    NUMBER = "num"
    STRING = "str"
    KEYWORD = "kw"     # AND/OR/NOT/IN/IS/NULL/TRUE/FALSE
    EOF = "eof"


@dataclass(frozen=True)
class _Token:
    kind: _TokenKind
    value: Any
    line: int
    col: int


# ---------------------------------------------------------------------------
# Lexer
# ---------------------------------------------------------------------------


_OP_TWO_CHAR = {"==", "!=", ">=", "<="}
_OP_ONE_CHAR = {">", "<"}


class _Lexer:
    """Hand-rolled tokenizer for the DSL.

    Drops whitespace and ``#`` line comments. Tracks line/column for
    diagnostic messages.
    """

    def __init__(self, source: str) -> None:
        self._src = source
        self._n = len(source)
        self._i = 0
        self._line = 1
        self._col = 1

    def tokenize(self) -> list[_Token]:
        """Return the full token list (including a trailing EOF)."""
        # Hard guard: NUL byte / non-printable control chars are a
        # smell (binary smuggle, copy-paste from PDF, etc.).
        for ch in self._src:
            if ch == "\x00":
                raise PredicateSyntaxError("NUL byte in predicate")
            if ord(ch) < 32 and ch not in ("\n", "\r", "\t"):
                raise PredicateSyntaxError(
                    f"non-printable control char U+{ord(ch):04X}"
                )

        tokens: list[_Token] = []
        while self._i < self._n:
            self._skip_ws_and_comments()
            if self._i >= self._n:
                break
            ch = self._src[self._i]
            if ch == "(":
                tokens.append(self._emit(_TokenKind.LPAREN, "("))
                self._advance()
            elif ch == ")":
                tokens.append(self._emit(_TokenKind.RPAREN, ")"))
                self._advance()
            elif ch == "[":
                tokens.append(self._emit(_TokenKind.LBRACKET, "["))
                self._advance()
            elif ch == "]":
                tokens.append(self._emit(_TokenKind.RBRACKET, "]"))
                self._advance()
            elif ch == ",":
                tokens.append(self._emit(_TokenKind.COMMA, ","))
                self._advance()
            elif ch in ("'", '"'):
                tokens.append(self._read_string(ch))
            elif ch.isdigit() or (
                ch in ("-", "+")
                and self._i + 1 < self._n
                and self._src[self._i + 1].isdigit()
                and self._can_start_number(tokens)
            ):
                tokens.append(self._read_number())
            elif self._starts_with_two(ch):
                tokens.append(self._emit(_TokenKind.OP, self._src[self._i : self._i + 2]))
                self._advance()
                self._advance()
            elif ch in _OP_ONE_CHAR:
                tokens.append(self._emit(_TokenKind.OP, ch))
                self._advance()
            elif _IDENT_RE.match(self._src, self._i):
                tokens.append(self._read_ident_or_keyword())
            else:
                raise PredicateSyntaxError(
                    f"unexpected character {ch!r}", line=self._line, col=self._col
                )

        tokens.append(_Token(_TokenKind.EOF, None, self._line, self._col))
        return tokens

    # -- helpers -----------------------------------------------------------

    def _emit(self, kind: _TokenKind, value: Any) -> _Token:
        return _Token(kind, value, self._line, self._col)

    def _advance(self) -> None:
        if self._i < self._n and self._src[self._i] == "\n":
            self._line += 1
            self._col = 1
        else:
            self._col += 1
        self._i += 1

    def _skip_ws_and_comments(self) -> None:
        while self._i < self._n:
            ch = self._src[self._i]
            if ch in " \t\r\n":
                self._advance()
            elif ch == "#":
                # Line comment: skip until newline.
                while self._i < self._n and self._src[self._i] != "\n":
                    self._advance()
            else:
                return

    def _starts_with_two(self, ch: str) -> bool:
        if self._i + 1 >= self._n:
            return False
        two = self._src[self._i : self._i + 2]
        return two in _OP_TWO_CHAR

    def _can_start_number(self, tokens: list[_Token]) -> bool:
        """Return True when a leading ``-``/``+`` belongs to a numeric
        literal (after an op/comma/lparen/lbracket) rather than being
        part of a (currently unsupported) arithmetic expression.
        """
        if not tokens:
            return True
        last = tokens[-1]
        return last.kind in (
            _TokenKind.OP,
            _TokenKind.COMMA,
            _TokenKind.LPAREN,
            _TokenKind.LBRACKET,
            _TokenKind.KEYWORD,
        )

    def _read_string(self, quote: str) -> _Token:
        start_line, start_col = self._line, self._col
        self._advance()  # skip opening quote
        buf: list[str] = []
        while self._i < self._n:
            ch = self._src[self._i]
            if ch == "\\":
                # Minimal escape support: \\, \', \", \n, \t, \r
                self._advance()
                if self._i >= self._n:
                    raise PredicateSyntaxError(
                        "unterminated escape", line=start_line, col=start_col
                    )
                esc = self._src[self._i]
                buf.append({"n": "\n", "t": "\t", "r": "\r"}.get(esc, esc))
                self._advance()
                continue
            if ch == quote:
                self._advance()
                return _Token(_TokenKind.STRING, "".join(buf), start_line, start_col)
            buf.append(ch)
            self._advance()
        raise PredicateSyntaxError(
            "unterminated string literal", line=start_line, col=start_col
        )

    def _read_number(self) -> _Token:
        start_line, start_col = self._line, self._col
        start = self._i
        if self._src[self._i] in ("-", "+"):
            self._advance()
        while self._i < self._n and (
            self._src[self._i].isdigit() or self._src[self._i] in "._eE+-"
        ):
            # We accept '.', 'e', 'E' and signed exponents; we'll let
            # ``float()`` validate. Keep the loop conservative so we
            # don't swallow a trailing comma.
            ch = self._src[self._i]
            if ch in "+-":
                # Only accept after exponent char.
                prev = self._src[self._i - 1]
                if prev not in "eE":
                    break
            self._advance()
        text = self._src[start : self._i]
        try:
            if any(c in text for c in ".eE"):
                value: Any = float(text)
            else:
                value = int(text)
        except ValueError as exc:
            raise PredicateSyntaxError(
                f"invalid number {text!r}", line=start_line, col=start_col
            ) from exc
        return _Token(_TokenKind.NUMBER, value, start_line, start_col)

    def _read_ident_or_keyword(self) -> _Token:
        start_line, start_col = self._line, self._col
        start = self._i
        # Identifiers may be dotted: foo.bar.baz
        while self._i < self._n:
            m = _IDENT_RE.match(self._src, self._i)
            if not m:
                break
            seg = m.group(0)
            # Reject dunder segments (defense in depth).
            if seg.startswith("__") or seg.endswith("__"):
                raise PredicateSyntaxError(
                    f"dunder identifier rejected: {seg!r}",
                    line=start_line,
                    col=start_col,
                )
            self._i = m.end()
            self._col += len(seg)
            if self._i < self._n and self._src[self._i] == ".":
                self._advance()
                continue
            break
        text = self._src[start : self._i]
        upper = text.upper()
        if upper in _KEYWORDS:
            return _Token(_TokenKind.KEYWORD, upper, start_line, start_col)
        # ``true``/``false``/``null`` (lowercase) get folded into keywords too.
        if upper in {"TRUE", "FALSE", "NULL"}:
            return _Token(_TokenKind.KEYWORD, upper, start_line, start_col)
        return _Token(_TokenKind.IDENT, text, start_line, start_col)


# ---------------------------------------------------------------------------
# AST nodes (independent of core.predicates so the parser stays self-contained
# and core remains domain-only). We compile to core dataclasses lazily.
# ---------------------------------------------------------------------------


class _NodeKind(str, Enum):
    AND = "and"
    OR = "or"
    NOT = "not"
    CMP = "cmp"
    IS_NULL = "is_null"
    IS_NOT_NULL = "is_not_null"
    IN = "in"
    NOT_IN = "not_in"


@dataclass(frozen=True)
class PredicateNode:
    """Compiled, immutable AST node."""

    kind: _NodeKind
    # For CMP/IS_NULL/IN: the attribute path (e.g. ['new', 'status']).
    attr: tuple[str, ...] = ()
    # For CMP: the comparison operator (``==``/``!=``/``>``/...).
    op: str = ""
    # For CMP: literal (scalar). For IN: tuple of literals.
    value: Any = None
    # For AND/OR: child predicates.
    children: tuple["PredicateNode", ...] = ()
    # For NOT: single child wrapped in children[0].

    # ---- evaluation --------------------------------------------------

    def evaluate(
        self, payload: dict[str, Any], *, _depth: int = 0
    ) -> bool:
        """Evaluate this node against a payload row dict.

        ``payload`` is expected to expose:
            * bare column keys (``"status": "pending"``)
            * ``new.<col>`` keys for UPDATE rows (handled by the runtime)
            * ``old.<col>`` keys for UPDATE rows (handled by the runtime)
        """
        if _depth > MAX_DEPTH:
            raise PredicateDepthError(
                f"evaluation depth exceeded {MAX_DEPTH}"
            )

        if self.kind == _NodeKind.AND:
            return all(c.evaluate(payload, _depth=_depth + 1) for c in self.children)
        if self.kind == _NodeKind.OR:
            return any(c.evaluate(payload, _depth=_depth + 1) for c in self.children)
        if self.kind == _NodeKind.NOT:
            return not self.children[0].evaluate(payload, _depth=_depth + 1)

        # Leaf: pull the attribute value once.
        attr_value = _resolve_attr(self.attr, payload)

        if self.kind == _NodeKind.IS_NULL:
            return attr_value is None
        if self.kind == _NodeKind.IS_NOT_NULL:
            return attr_value is not None
        if self.kind == _NodeKind.IN:
            return attr_value in self.value
        if self.kind == _NodeKind.NOT_IN:
            return attr_value not in self.value
        if self.kind == _NodeKind.CMP:
            return _compare(attr_value, self.op, self.value)

        raise PredicateEvalError(f"unknown node kind: {self.kind!r}")  # pragma: no cover

    # ---- introspection (for tests / round-trip) ----------------------

    def to_dict(self) -> dict[str, Any]:
        """Stable dict representation, used for golden tests / debug."""
        out: dict[str, Any] = {"kind": self.kind.value}
        if self.attr:
            out["attr"] = list(self.attr)
        if self.kind == _NodeKind.CMP:
            out["op"] = self.op
            out["value"] = self.value
        if self.kind in (_NodeKind.IN, _NodeKind.NOT_IN):
            out["value"] = list(self.value)
        if self.children:
            out["children"] = [c.to_dict() for c in self.children]
        return out


def _resolve_attr(path: tuple[str, ...], payload: dict[str, Any]) -> Any:
    """Walk a dotted attribute path against the payload.

    The runtime exposes ``new.*`` / ``old.*`` keys as flat strings
    (``"new.status"``) for UPDATE rows. We try both shapes:
        1. Flat key: ``payload["new.status"]``.
        2. Nested:    ``payload["new"]["status"]``.

    Returns ``None`` when the attribute is missing — the evaluator
    then makes that a fail-safe non-match for value comparisons (and a
    *match* for ``IS NULL``).
    """
    if not path:
        return None

    # Flat dotted key first (the runtime builds this for old.*/new.*).
    flat = ".".join(path)
    if flat in payload:
        return payload[flat]

    # Nested fallback. Stops as soon as a level is missing or non-mapping.
    cursor: Any = payload
    for seg in path:
        if isinstance(cursor, dict) and seg in cursor:
            cursor = cursor[seg]
        else:
            return None
    return cursor


def _compare(left: Any, op: str, right: Any) -> bool:
    """Evaluate ``left op right`` with type-tolerant comparison.

    A ``None`` on either side resolves to a non-match (mirrors SQL
    ``NULL`` semantics where ``NULL = anything`` is unknown). The only
    exception is ``==`` between two ``None`` values, which is True.
    """
    if op == "==":
        return left == right
    if op == "!=":
        return left != right

    if left is None or right is None:
        return False

    try:
        if op == ">":
            return left > right
        if op == ">=":
            return left >= right
        if op == "<":
            return left < right
        if op == "<=":
            return left <= right
    except TypeError:
        # Mixed types (e.g. str > int) — log once at WARN level and
        # treat as a non-match. Callers downstream (CLI / API) flag
        # this with a counter but never crash the tick.
        log.warning("predicate_type_mismatch op=%s left=%r right=%r", op, left, right)
        return False

    raise PredicateEvalError(f"unknown comparison operator: {op!r}")  # pragma: no cover


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


_VALID_OPS = {"==", "!=", ">", ">=", "<", "<="}


class _Parser:
    """Recursive-descent parser. One instance per ``parse_predicate`` call."""

    def __init__(self, tokens: list[_Token]) -> None:
        self._tokens = tokens
        self._pos = 0
        self._depth = 0

    # ---- public ---------------------------------------------------------

    def parse(self) -> PredicateNode:
        node = self._parse_or()
        if self._peek().kind != _TokenKind.EOF:
            tok = self._peek()
            raise PredicateSyntaxError(
                f"unexpected token {tok.value!r} after predicate",
                line=tok.line,
                col=tok.col,
            )
        return node

    # ---- helpers --------------------------------------------------------

    def _peek(self) -> _Token:
        return self._tokens[self._pos]

    def _consume(self) -> _Token:
        tok = self._tokens[self._pos]
        self._pos += 1
        return tok

    def _expect_keyword(self, kw: str) -> _Token:
        tok = self._peek()
        if tok.kind != _TokenKind.KEYWORD or tok.value != kw:
            raise PredicateSyntaxError(
                f"expected {kw!r}, got {tok.value!r}", line=tok.line, col=tok.col
            )
        return self._consume()

    def _enter(self) -> None:
        self._depth += 1
        if self._depth > MAX_DEPTH:
            tok = self._peek()
            raise PredicateDepthError(
                f"max nesting depth {MAX_DEPTH} exceeded at line {tok.line}, col {tok.col}"
            )

    def _exit(self) -> None:
        self._depth -= 1

    # ---- grammar --------------------------------------------------------

    def _parse_or(self) -> PredicateNode:
        self._enter()
        try:
            left = self._parse_and()
            terms = [left]
            while (
                self._peek().kind == _TokenKind.KEYWORD and self._peek().value == "OR"
            ):
                self._consume()
                terms.append(self._parse_and())
            if len(terms) == 1:
                return left
            return PredicateNode(kind=_NodeKind.OR, children=tuple(terms))
        finally:
            self._exit()

    def _parse_and(self) -> PredicateNode:
        self._enter()
        try:
            left = self._parse_not()
            terms = [left]
            while (
                self._peek().kind == _TokenKind.KEYWORD and self._peek().value == "AND"
            ):
                self._consume()
                terms.append(self._parse_not())
            if len(terms) == 1:
                return left
            return PredicateNode(kind=_NodeKind.AND, children=tuple(terms))
        finally:
            self._exit()

    def _parse_not(self) -> PredicateNode:
        if (
            self._peek().kind == _TokenKind.KEYWORD
            and self._peek().value == "NOT"
            # Disambiguate from ``NOT IN`` / ``NOT NULL`` which are
            # part of comparison productions.
            and not self._lookahead_is_in_or_null()
        ):
            self._consume()
            self._enter()
            try:
                child = self._parse_not()
            finally:
                self._exit()
            return PredicateNode(kind=_NodeKind.NOT, children=(child,))
        return self._parse_comparison()

    def _lookahead_is_in_or_null(self) -> bool:
        """``NOT`` is part of ``NOT IN`` / ``IS NOT NULL`` only when an
        ``IN``/``NULL`` keyword follows. Pure ``NOT <expr>`` does not.
        """
        # We're on a NOT token. The previous token is either start, OR, AND,
        # LPAREN, NOT — never an IDENT — so prefix ``NOT IN`` is impossible
        # at this position. Safe to always treat as standalone NOT.
        return False

    def _parse_comparison(self) -> PredicateNode:
        tok = self._peek()
        # Parenthesised sub-expression
        if tok.kind == _TokenKind.LPAREN:
            self._consume()
            self._enter()
            try:
                inner = self._parse_or()
            finally:
                self._exit()
            close = self._consume()
            if close.kind != _TokenKind.RPAREN:
                raise PredicateSyntaxError(
                    "expected ')'", line=close.line, col=close.col
                )
            return inner

        # Comparison: attr op literal | attr IS [NOT] NULL | attr [NOT] IN list
        attr_path = self._parse_attr_path()

        nxt = self._peek()
        if nxt.kind == _TokenKind.OP:
            op = self._consume().value
            if op not in _VALID_OPS:
                raise PredicateSyntaxError(
                    f"unknown operator {op!r}", line=nxt.line, col=nxt.col
                )
            value = self._parse_literal()
            return PredicateNode(
                kind=_NodeKind.CMP, attr=attr_path, op=op, value=value
            )

        if nxt.kind == _TokenKind.KEYWORD and nxt.value == "IS":
            self._consume()
            negated = False
            if self._peek().kind == _TokenKind.KEYWORD and self._peek().value == "NOT":
                self._consume()
                negated = True
            self._expect_keyword("NULL")
            return PredicateNode(
                kind=_NodeKind.IS_NOT_NULL if negated else _NodeKind.IS_NULL,
                attr=attr_path,
            )

        if nxt.kind == _TokenKind.KEYWORD and nxt.value in {"IN", "NOT"}:
            negated = False
            if nxt.value == "NOT":
                self._consume()
                self._expect_keyword("IN")
                negated = True
            else:
                self._consume()  # IN
            values = self._parse_list_literal()
            return PredicateNode(
                kind=_NodeKind.NOT_IN if negated else _NodeKind.IN,
                attr=attr_path,
                value=values,
            )

        raise PredicateSyntaxError(
            f"expected comparison operator after attribute, got {nxt.value!r}",
            line=nxt.line,
            col=nxt.col,
        )

    def _parse_attr_path(self) -> tuple[str, ...]:
        tok = self._peek()
        if tok.kind != _TokenKind.IDENT:
            raise PredicateSyntaxError(
                f"expected attribute identifier, got {tok.value!r}",
                line=tok.line,
                col=tok.col,
            )
        self._consume()
        # The lexer already handles dotted segments inside one IDENT
        # token, so we just split.
        segments = tuple(str(tok.value).split("."))
        for seg in segments:
            if not seg or seg.startswith("__") or seg.endswith("__"):
                raise PredicateSyntaxError(
                    f"invalid attribute segment {seg!r}",
                    line=tok.line,
                    col=tok.col,
                )
        return segments

    def _parse_literal(self) -> Any:
        tok = self._peek()
        if tok.kind == _TokenKind.NUMBER or tok.kind == _TokenKind.STRING:
            self._consume()
            return tok.value
        if tok.kind == _TokenKind.KEYWORD and tok.value in {"TRUE", "FALSE", "NULL"}:
            self._consume()
            if tok.value == "TRUE":
                return True
            if tok.value == "FALSE":
                return False
            return None
        raise PredicateSyntaxError(
            f"expected literal, got {tok.value!r}", line=tok.line, col=tok.col
        )

    def _parse_list_literal(self) -> tuple[Any, ...]:
        tok = self._consume()
        if tok.kind != _TokenKind.LBRACKET:
            raise PredicateSyntaxError(
                f"expected '[' after IN, got {tok.value!r}",
                line=tok.line,
                col=tok.col,
            )
        items: list[Any] = []
        if self._peek().kind == _TokenKind.RBRACKET:
            self._consume()
            return tuple(items)
        while True:
            items.append(self._parse_literal())
            nxt = self._consume()
            if nxt.kind == _TokenKind.COMMA:
                continue
            if nxt.kind == _TokenKind.RBRACKET:
                break
            raise PredicateSyntaxError(
                f"expected ',' or ']' in list literal, got {nxt.value!r}",
                line=nxt.line,
                col=nxt.col,
            )
        return tuple(items)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


# Hard cap on input size — we never expect a 10 KB predicate string,
# rejecting upfront protects us from pathological inputs.
_MAX_SOURCE_LEN = 4096


def parse_predicate(source: str) -> PredicateNode:
    """Parse a DSL string into a compiled :class:`PredicateNode`.

    Raises :class:`PredicateSyntaxError` on malformed input,
    :class:`PredicateDepthError` on adversarially-deep nesting.

    The returned node is immutable (``frozen=True`` on the dataclass)
    and safe to share across threads.
    """
    if not isinstance(source, str):
        raise PredicateSyntaxError(
            f"predicate source must be a string, got {type(source).__name__}"
        )
    if len(source) > _MAX_SOURCE_LEN:
        raise PredicateSyntaxError(
            f"predicate source exceeds {_MAX_SOURCE_LEN} characters"
        )
    if not source.strip():
        raise PredicateSyntaxError("predicate source is empty")

    tokens = _Lexer(source).tokenize()
    return _Parser(tokens).parse()


def evaluate_predicate(
    node: PredicateNode | None, payload: dict[str, Any]
) -> bool:
    """Evaluate a compiled predicate against a payload row.

    A ``None`` predicate is treated as *always-true* — this preserves the
    pre-S4 behaviour where missing predicates fall through to
    ``matched=True`` in the runtime.
    """
    if node is None:
        return True
    return node.evaluate(payload)


def build_update_payload(
    *,
    new_values: dict[str, Any],
    old_values: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the payload dict consumed by :func:`evaluate_predicate`.

    For UPDATE rows we expose three views::

        {
            "status": ...,           # bare = new.<col> for SQL-feel
            "new.status": ...,
            "old.status": ...,
        }

    INSERT rows have no ``old`` snapshot; we still emit ``new.*`` so
    explicit predicates work the same way across all DML ops.
    """
    payload: dict[str, Any] = {}
    if extra:
        payload.update(extra)
    # Bare keys: new wins (familiar SQL convention).
    payload.update(new_values)
    for k, v in new_values.items():
        payload[f"new.{k}"] = v
    if old_values:
        for k, v in old_values.items():
            payload[f"old.{k}"] = v
    return payload


# ---------------------------------------------------------------------------
# Bridge to ``core.predicates`` AST (so the structured PredicateEvaluator
# in rules/predicates.py can consume the parsed tree). Currently used by
# tests / introspection — the runtime path uses ``evaluate_predicate``
# directly because it knows how to feed ``old.*``/``new.*`` payloads.
# ---------------------------------------------------------------------------


def to_core_predicate(node: PredicateNode) -> "AnyPredicate":
    """Best-effort conversion to ``core.predicates`` dataclasses.

    Limitation: dotted attribute paths (``new.status``) get flattened
    into the ``field`` string because ``AttrPredicate.field`` is a
    plain identifier. The structured evaluator doesn't traverse dots,
    so this only round-trips cleanly for *bare* attributes.
    """
    from gispulse.core.predicates import AttrPredicate, CompoundPredicate

    if node.kind == _NodeKind.AND:
        return CompoundPredicate(
            logic="AND",
            predicates=[to_core_predicate(c) for c in node.children],
        )
    if node.kind == _NodeKind.OR:
        return CompoundPredicate(
            logic="OR",
            predicates=[to_core_predicate(c) for c in node.children],
        )
    if node.kind == _NodeKind.NOT:
        return CompoundPredicate(
            logic="NOT",
            predicates=[to_core_predicate(node.children[0])],
        )

    field = ".".join(node.attr)
    if node.kind == _NodeKind.IS_NULL:
        return AttrPredicate(field=field, op="is_null")
    if node.kind == _NodeKind.IS_NOT_NULL:
        return AttrPredicate(field=field, op="not_null")
    if node.kind == _NodeKind.IN:
        return AttrPredicate(field=field, op="in", value=list(node.value))
    if node.kind == _NodeKind.NOT_IN:
        return CompoundPredicate(
            logic="NOT",
            predicates=[
                AttrPredicate(field=field, op="in", value=list(node.value))
            ],
        )
    if node.kind == _NodeKind.CMP:
        op_map = {"==": "eq", "!=": "neq", ">": "gt", ">=": "gte", "<": "lt", "<=": "lte"}
        # ``AttrPredicate.op`` is a closed Literal[…]; the map values
        # are statically inside that set, but mypy can't prove it from
        # a dict lookup. We narrow with ``cast``.
        from typing import cast
        from typing import Any as _Any  # local alias to avoid TYPE_CHECKING shuffle

        return AttrPredicate(field=field, op=cast(_Any, op_map[node.op]), value=node.value)
    raise PredicateError(f"cannot convert node kind {node.kind!r}")  # pragma: no cover


__all__ = [
    "MAX_DEPTH",
    "PredicateDepthError",
    "PredicateError",
    "PredicateEvalError",
    "PredicateNode",
    "PredicateSyntaxError",
    "build_update_payload",
    "evaluate_predicate",
    "parse_predicate",
    "to_core_predicate",
]
