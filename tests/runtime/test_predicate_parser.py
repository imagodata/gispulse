"""Tests for ``gispulse.runtime.predicate_dsl``.

Coverage targets
----------------
* Parsing: every operator + AND/OR/NOT, parentheses, dotted attrs,
  IN list, IS [NOT] NULL, numeric / string / boolean / null literals.
* Evaluation: each operator on representative payloads.
* Errors: missing attr, invalid syntax, unknown ops, EOF mid-expression.
* Security: SQL injection attempts, dunder attrs, deep nesting,
  oversized input, NUL bytes, unicode control chars.
* Round-trip: AST ``to_dict`` is stable so YAML → AST → introspection
  stays deterministic.
"""

from __future__ import annotations

import pytest

from gispulse.runtime.predicate_dsl import (
    MAX_DEPTH,
    PredicateDepthError,
    PredicateNode,
    PredicateSyntaxError,
    build_update_payload,
    evaluate_predicate,
    parse_predicate,
    to_core_predicate,
)


# ---------------------------------------------------------------------------
# Parsing — happy paths
# ---------------------------------------------------------------------------


class TestParseScalarComparisons:
    """Each scalar comparison op produces the expected CMP node."""

    @pytest.mark.parametrize(
        "src,op,value",
        [
            ("x == 1", "==", 1),
            ("x != 1", "!=", 1),
            ("x > 1", ">", 1),
            ("x >= 1", ">=", 1),
            ("x < 1", "<", 1),
            ("x <= 1", "<=", 1),
            ("x == 'foo'", "==", "foo"),
            ('x == "foo"', "==", "foo"),
            ("x == 3.14", "==", 3.14),
            ("x == -5", "==", -5),
            ("x == 1e3", "==", 1000.0),
            ("x == true", "==", True),
            ("x == FALSE", "==", False),
            ("x == null", "==", None),
        ],
    )
    def test_parse_cmp(self, src: str, op: str, value: object) -> None:
        node = parse_predicate(src)
        d = node.to_dict()
        assert d == {"kind": "cmp", "attr": ["x"], "op": op, "value": value}


class TestParseBoolean:
    def test_and_chain(self) -> None:
        node = parse_predicate("a > 1 AND b < 2 AND c == 3")
        d = node.to_dict()
        assert d["kind"] == "and"
        assert len(d["children"]) == 3

    def test_or_chain(self) -> None:
        node = parse_predicate("a == 1 OR a == 2 OR a == 3")
        d = node.to_dict()
        assert d["kind"] == "or"
        assert len(d["children"]) == 3

    def test_precedence_and_over_or(self) -> None:
        # AND binds tighter than OR.
        node = parse_predicate("a == 1 OR b == 2 AND c == 3")
        d = node.to_dict()
        assert d["kind"] == "or"
        # right child is AND
        right = d["children"][1]
        assert right["kind"] == "and"

    def test_parens_override_precedence(self) -> None:
        node = parse_predicate("(a == 1 OR b == 2) AND c == 3")
        d = node.to_dict()
        assert d["kind"] == "and"
        left = d["children"][0]
        assert left["kind"] == "or"

    def test_not_unary(self) -> None:
        node = parse_predicate("NOT (a == 1)")
        d = node.to_dict()
        assert d["kind"] == "not"
        assert len(d["children"]) == 1
        assert d["children"][0]["kind"] == "cmp"

    def test_nested_not(self) -> None:
        node = parse_predicate("NOT NOT (a == 1)")
        # double-NOT collapses into nested ``not`` nodes — evaluation
        # gives us back True/True idempotently.
        assert evaluate_predicate(node, {"a": 1}) is True
        assert evaluate_predicate(node, {"a": 2}) is False


class TestParseNullAndIn:
    def test_is_null(self) -> None:
        node = parse_predicate("reviewer IS NULL")
        assert node.to_dict() == {"kind": "is_null", "attr": ["reviewer"]}

    def test_is_not_null(self) -> None:
        node = parse_predicate("reviewer IS NOT NULL")
        assert node.to_dict() == {"kind": "is_not_null", "attr": ["reviewer"]}

    def test_in_list(self) -> None:
        node = parse_predicate("status IN ['pending', 'draft', 'review']")
        d = node.to_dict()
        assert d == {
            "kind": "in",
            "attr": ["status"],
            "value": ["pending", "draft", "review"],
        }

    def test_not_in_list(self) -> None:
        node = parse_predicate("status NOT IN ['x', 'y']")
        d = node.to_dict()
        assert d == {"kind": "not_in", "attr": ["status"], "value": ["x", "y"]}

    def test_in_empty_list(self) -> None:
        node = parse_predicate("status IN []")
        assert evaluate_predicate(node, {"status": "anything"}) is False

    def test_in_mixed_types(self) -> None:
        node = parse_predicate("score IN [1, 2.5, 'NA']")
        assert evaluate_predicate(node, {"score": 1}) is True
        assert evaluate_predicate(node, {"score": 2.5}) is True
        assert evaluate_predicate(node, {"score": "NA"}) is True
        assert evaluate_predicate(node, {"score": 3}) is False


class TestParseDottedAttrs:
    def test_dotted_attr(self) -> None:
        node = parse_predicate("new.status == 'pending'")
        assert node.to_dict()["attr"] == ["new", "status"]

    def test_deep_dotted_attr(self) -> None:
        # Three-level path must round-trip.
        node = parse_predicate("a.b.c == 1")
        assert node.to_dict()["attr"] == ["a", "b", "c"]


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------


class TestEvaluation:
    def test_eq_str(self) -> None:
        n = parse_predicate("status == 'pending'")
        assert evaluate_predicate(n, {"status": "pending"}) is True
        assert evaluate_predicate(n, {"status": "ok"}) is False

    def test_gt_int(self) -> None:
        n = parse_predicate("valeur > 100")
        assert evaluate_predicate(n, {"valeur": 150}) is True
        assert evaluate_predicate(n, {"valeur": 100}) is False
        assert evaluate_predicate(n, {"valeur": 50}) is False

    def test_ge_float(self) -> None:
        n = parse_predicate("price >= 9.99")
        assert evaluate_predicate(n, {"price": 9.99}) is True
        assert evaluate_predicate(n, {"price": 10.0}) is True
        assert evaluate_predicate(n, {"price": 9.50}) is False

    def test_eq_bool(self) -> None:
        n = parse_predicate("active == true")
        assert evaluate_predicate(n, {"active": True}) is True
        assert evaluate_predicate(n, {"active": False}) is False

    def test_compound_and_or(self) -> None:
        n = parse_predicate(
            "valeur > 100 AND (status == 'pending' OR status == 'review')"
        )
        assert evaluate_predicate(n, {"valeur": 150, "status": "pending"}) is True
        assert evaluate_predicate(n, {"valeur": 150, "status": "review"}) is True
        assert evaluate_predicate(n, {"valeur": 150, "status": "ok"}) is False
        assert evaluate_predicate(n, {"valeur": 50, "status": "pending"}) is False

    def test_not_compound(self) -> None:
        n = parse_predicate("NOT (status == 'archived')")
        assert evaluate_predicate(n, {"status": "live"}) is True
        assert evaluate_predicate(n, {"status": "archived"}) is False

    def test_null_evaluation(self) -> None:
        n_null = parse_predicate("reviewer IS NULL")
        assert evaluate_predicate(n_null, {"reviewer": None}) is True
        assert evaluate_predicate(n_null, {"reviewer": "simon"}) is False
        assert evaluate_predicate(n_null, {}) is True  # missing == None

        n_not_null = parse_predicate("reviewer IS NOT NULL")
        assert evaluate_predicate(n_not_null, {"reviewer": "simon"}) is True
        assert evaluate_predicate(n_not_null, {"reviewer": None}) is False

    def test_eq_none_both_sides(self) -> None:
        n = parse_predicate("reviewer == null")
        assert evaluate_predicate(n, {"reviewer": None}) is True
        assert evaluate_predicate(n, {"reviewer": "x"}) is False

    def test_missing_attr_logs_warn_and_skips(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Comparing a missing attr with a non-null op resolves to False."""
        n = parse_predicate("missing_field > 100")
        with caplog.at_level("DEBUG"):
            result = evaluate_predicate(n, {"valeur": 200})
        assert result is False  # fail-safe non-match

    def test_type_mismatch_logs_warn(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """``str > int`` is a type mismatch — log WARN and treat as no-match."""
        n = parse_predicate("valeur > 100")
        with caplog.at_level("WARNING"):
            result = evaluate_predicate(n, {"valeur": "abc"})
        assert result is False
        assert any(
            "predicate_type_mismatch" in rec.message for rec in caplog.records
        )

    def test_none_predicate_is_always_true(self) -> None:
        """The runtime treats ``None`` as 'no predicate set' which matches
        everything (preserving pre-S4 behaviour)."""
        assert evaluate_predicate(None, {"any": "value"}) is True
        assert evaluate_predicate(None, {}) is True


class TestUpdatePayloadSemantics:
    """Verify ``old.*`` / ``new.*`` exposure for UPDATE rows."""

    def test_bare_attr_resolves_to_new(self) -> None:
        n = parse_predicate("status == 'final'")
        payload = build_update_payload(
            new_values={"status": "final"},
            old_values={"status": "draft"},
        )
        assert evaluate_predicate(n, payload) is True

    def test_explicit_new_namespace(self) -> None:
        n = parse_predicate("new.status == 'final'")
        payload = build_update_payload(
            new_values={"status": "final"},
            old_values={"status": "draft"},
        )
        assert evaluate_predicate(n, payload) is True

    def test_explicit_old_namespace(self) -> None:
        n = parse_predicate("old.status == 'draft'")
        payload = build_update_payload(
            new_values={"status": "final"},
            old_values={"status": "draft"},
        )
        assert evaluate_predicate(n, payload) is True

    def test_old_without_old_values_is_null(self) -> None:
        """INSERT exposes only ``new.*``; ``old.x`` should be None."""
        n = parse_predicate("old.status IS NULL")
        payload = build_update_payload(new_values={"status": "live"})
        assert evaluate_predicate(n, payload) is True


# ---------------------------------------------------------------------------
# Round-trip / introspection
# ---------------------------------------------------------------------------


class TestRoundTrip:
    def test_node_is_frozen(self) -> None:
        n = parse_predicate("a == 1")
        with pytest.raises(Exception):
            n.value = 2  # type: ignore[misc]

    def test_to_core_predicate_basic(self) -> None:
        from core.predicates import AttrPredicate

        n = parse_predicate("status == 'pending'")
        core = to_core_predicate(n)
        assert isinstance(core, AttrPredicate)
        assert core.field == "status"
        assert core.op == "eq"
        assert core.value == "pending"

    def test_to_core_predicate_compound(self) -> None:
        from core.predicates import CompoundPredicate

        n = parse_predicate("a == 1 AND b == 2")
        core = to_core_predicate(n)
        assert isinstance(core, CompoundPredicate)
        assert core.logic == "AND"
        assert len(core.predicates) == 2


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------


class TestSyntaxErrors:
    def test_unterminated_string(self) -> None:
        with pytest.raises(PredicateSyntaxError, match="unterminated"):
            parse_predicate("status == 'oops")

    def test_unknown_operator(self) -> None:
        with pytest.raises(PredicateSyntaxError):
            # ``!!`` is not in the operator alphabet — fail at lex.
            parse_predicate("a !! 1")

    def test_empty_predicate(self) -> None:
        with pytest.raises(PredicateSyntaxError, match="empty"):
            parse_predicate("   ")

    def test_dangling_and(self) -> None:
        with pytest.raises(PredicateSyntaxError):
            parse_predicate("a == 1 AND")

    def test_bare_keyword(self) -> None:
        # 'AND' as a bare attribute isn't allowed — it's a keyword.
        with pytest.raises(PredicateSyntaxError):
            parse_predicate("AND == 1")

    def test_unbalanced_parens(self) -> None:
        with pytest.raises(PredicateSyntaxError):
            parse_predicate("(a == 1")

    def test_function_call_rhs_rejected(self) -> None:
        # Functions on RHS aren't parsable — there's no tokenisation
        # path that accepts ``now()``. Should fail at literal parsing.
        with pytest.raises(PredicateSyntaxError):
            parse_predicate("ts > now()")

    def test_attr_path_with_function_rejected(self) -> None:
        with pytest.raises(PredicateSyntaxError):
            parse_predicate("len(x) > 5")

    def test_in_without_brackets(self) -> None:
        with pytest.raises(PredicateSyntaxError):
            parse_predicate("status IN 'pending'")

    def test_extra_token_after_predicate(self) -> None:
        with pytest.raises(PredicateSyntaxError, match="unexpected token"):
            parse_predicate("a == 1 b == 2")

    def test_non_string_input(self) -> None:
        with pytest.raises(PredicateSyntaxError):
            parse_predicate(123)  # type: ignore[arg-type]


class TestSecurityHardening:
    """Adversarial inputs must be rejected cleanly, never crash the parser."""

    def test_sql_injection_attempt(self) -> None:
        # Classic attempt — should fail at the very first parse step
        # (semicolon is not in the alphabet).
        with pytest.raises(PredicateSyntaxError):
            parse_predicate("1; DROP TABLE users")

    def test_drop_table_in_string(self) -> None:
        # OK — it's just a string literal, no SQL is ever executed.
        n = parse_predicate("note == '1; DROP TABLE users'")
        assert (
            evaluate_predicate(n, {"note": "1; DROP TABLE users"}) is True
        )

    def test_dunder_attr_rejected(self) -> None:
        with pytest.raises(PredicateSyntaxError, match="dunder"):
            parse_predicate("x.__class__ == 1")

    def test_dunder_only_in_segment(self) -> None:
        with pytest.raises(PredicateSyntaxError, match="dunder"):
            parse_predicate("__class__ == 1")

    def test_class_bases_chain_rejected(self) -> None:
        with pytest.raises(PredicateSyntaxError):
            parse_predicate("x.__class__.__bases__[0] == 1")

    def test_deep_nesting_rejected(self) -> None:
        # MAX_DEPTH is 32 — build something deeper.
        deep = "(" * 1000 + "a == 1" + ")" * 1000
        with pytest.raises(PredicateDepthError):
            parse_predicate(deep)

    def test_oversized_input_rejected(self) -> None:
        with pytest.raises(PredicateSyntaxError, match="exceeds"):
            parse_predicate("a == 1 AND " * 1000)

    def test_nul_byte_rejected(self) -> None:
        with pytest.raises(PredicateSyntaxError, match="NUL"):
            parse_predicate("a == 1\x00 AND b == 2")

    def test_control_char_rejected(self) -> None:
        with pytest.raises(PredicateSyntaxError, match="control"):
            parse_predicate("a == 1\x07 AND b == 2")

    def test_eval_depth_guard_at_runtime(self) -> None:
        """Hand-craft a node that bypasses the parser depth guard and
        verify the evaluator's own guard kicks in."""
        # Build an artificially deep AND tree manually.
        node: PredicateNode = parse_predicate("a == 1")
        for _ in range(MAX_DEPTH + 5):
            node = PredicateNode(
                kind=node.kind.__class__("and"),
                children=(node,),
            )
        with pytest.raises(PredicateDepthError):
            evaluate_predicate(node, {"a": 1})

    def test_unicode_identifiers_rejected(self) -> None:
        # We accept ASCII identifiers only. Non-ASCII attr names must
        # fail at lex time (no IDENT match).
        with pytest.raises(PredicateSyntaxError):
            parse_predicate("café == 1")

    def test_unicode_in_string_literal_ok(self) -> None:
        # But strings can carry any unicode payload (it's just data).
        n = parse_predicate("note == 'café — 你好'")
        assert evaluate_predicate(n, {"note": "café — 你好"}) is True


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_string_with_escape(self) -> None:
        n = parse_predicate(r"note == 'it\'s ok'")
        assert evaluate_predicate(n, {"note": "it's ok"}) is True

    def test_string_with_newline_escape(self) -> None:
        n = parse_predicate(r"note == 'a\nb'")
        assert evaluate_predicate(n, {"note": "a\nb"}) is True

    def test_signed_integer(self) -> None:
        n = parse_predicate("delta == -5")
        assert evaluate_predicate(n, {"delta": -5}) is True

    def test_scientific_notation(self) -> None:
        n = parse_predicate("threshold > 1e3")
        assert evaluate_predicate(n, {"threshold": 1500.0}) is True
        assert evaluate_predicate(n, {"threshold": 500.0}) is False

    def test_comment_is_skipped(self) -> None:
        n = parse_predicate("a == 1  # this is a comment")
        assert evaluate_predicate(n, {"a": 1}) is True

    def test_multiline_predicate(self) -> None:
        src = """
        a == 1
        AND b == 2
        """
        n = parse_predicate(src)
        assert evaluate_predicate(n, {"a": 1, "b": 2}) is True
        assert evaluate_predicate(n, {"a": 1, "b": 3}) is False
