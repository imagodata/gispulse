"""Unit tests for the SQL push-down infrastructure (ELT Lot 2, #245).

Covers the pure helpers of ``gispulse.capabilities.sql_pushdown`` —
identifier quoting, SQL-literal rendering, and the pandas-expression →
SQL translator, including its rejection of everything outside the
SQL-pure subset.
"""

from __future__ import annotations

import datetime as dt

import pytest

from gispulse.capabilities.sql_pushdown import (
    Untranslatable,
    qi,
    sql_literal,
    translate_expression,
)


# --- qi ---------------------------------------------------------------------


def test_qi_quotes_identifier():
    assert qi("population") == '"population"'
    assert qi("code_insee") == '"code_insee"'
    assert qi("__wkb") == '"__wkb"'


def test_qi_rejects_embedded_quote_and_empty():
    with pytest.raises(ValueError):
        qi('evil" OR 1=1 --')
    with pytest.raises(ValueError):
        qi("")


# --- sql_literal ------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, "NULL"),
        (True, "TRUE"),
        (False, "FALSE"),
        (42, "42"),
        (3.5, "3.5"),
        ("hello", "'hello'"),
        ("O'Brien", "'O''Brien'"),
        (dt.date(2026, 5, 19), "'2026-05-19'"),
    ],
)
def test_sql_literal(value, expected):
    assert sql_literal(value) == expected


def test_sql_literal_rejects_unsupported():
    with pytest.raises(Untranslatable):
        sql_literal(["a", "b"])
    with pytest.raises(Untranslatable):
        sql_literal({"k": "v"})


# --- translate_expression — accepted ---------------------------------------


@pytest.mark.parametrize(
    ("expr", "expected"),
    [
        ("pop > 100", '"pop" > 100'),
        ("name == 'Paris'", '"name" = \'Paris\''),
        ("a != 5", '"a" <> 5'),
        ("pop / area * 100", '"pop" / "area" * 100'),
        ("price * 1.5", '"price" * 1.5'),
        ("a > 1 and b < 2", '"a" > 1 AND "b" < 2'),
        ("not active", 'NOT "active"'),
        ("x == true", '"x" = TRUE'),
        ("(a + b) * 2", '("a" + "b") * 2'),
        ('label == "x"', '"label" = \'x\''),
    ],
)
def test_translate_expression_accepted(expr, expected):
    assert translate_expression(expr) == expected


# --- translate_expression — rejected ---------------------------------------


@pytest.mark.parametrize(
    "expr",
    [
        "name.str.contains('a')",  # attribute access / method call
        "@threshold > 1",          # injected variable
        "pop ** 2",                # exponent — not portable
        "total // 3",              # floor division
        "col[0]",                  # indexing
        "abs(value)",              # function call
        "pop > 1; DROP TABLE t",   # statement break
        "",                        # empty
        "`weird col` > 1",         # backtick
    ],
)
def test_translate_expression_rejects(expr):
    with pytest.raises(Untranslatable):
        translate_expression(expr)
