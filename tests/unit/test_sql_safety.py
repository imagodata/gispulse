"""Tests for core.sql_safety — identifier + expression + ref_filter validators.

This module is the foundation of GISPulse's defence against SQL injection.
All dynamic SQL paths (rules, triggers, relations, operation_executor,
portal_sql, capabilities/postgis_sql, persistence/bridge) rely on these
functions. Pin their contract tightly.
"""
from __future__ import annotations

import pytest

from core.sql_safety import (
    SAFE_IDENT_RE,
    SQL_BLOCKLIST,
    validate_expression,
    validate_identifier,
    validate_ref_filter,
)


class TestValidateIdentifierAccepts:
    @pytest.mark.parametrize(
        "name",
        [
            "parcels",
            "public.parcels",
            "schema_1.table_2",
            "_private",
            "a",
            "Zone_99",
            "T" + "a" * 126,  # 127 chars — max allowed
            "col.nested.deep",  # dots allowed (qualified references)
        ],
    )
    def test_accepts_safe(self, name: str):
        assert validate_identifier(name) == name


class TestValidateIdentifierRejects:
    @pytest.mark.parametrize(
        "name",
        [
            "",  # empty
            "1starts_with_digit",
            "has space",
            "has-dash",
            "has$dollar",
            "has;semi",
            "has'quote",
            'has"dquote',
            "has/slash",
            "a" * 200,  # too long (>127)
            "drop; SELECT 1",
            "name--comment",
            "user/**/or/**/1=1",
        ],
    )
    def test_rejects_unsafe(self, name: str):
        with pytest.raises(ValueError, match="Invalid SQL"):
            validate_identifier(name)

    def test_error_message_includes_label(self):
        with pytest.raises(ValueError, match="table"):
            validate_identifier("1bad", label="table")

    def test_error_message_includes_offending_value(self):
        with pytest.raises(ValueError) as exc:
            validate_identifier("ba$d")
        assert "'ba$d'" in str(exc.value) or "\"ba$d\"" in str(exc.value)


class TestValidateExpressionAccepts:
    @pytest.mark.parametrize(
        "expr",
        [
            "area > 1000",
            "population BETWEEN 100 AND 500",
            "name LIKE 'A%'",  # single quotes actually allowed in expressions
            "(x + y) * 2",
            "value IS NOT NULL",
            "",  # empty is OK — no blocklisted keyword
        ],
    )
    def test_accepts_benign(self, expr: str):
        assert validate_expression(expr) == expr


class TestValidateExpressionRejects:
    @pytest.mark.parametrize(
        "expr",
        [
            "area > 1000; DROP TABLE parcels",
            "DROP TABLE x",
            "drop table x",  # case-insensitive
            "1; ALTER TABLE y ADD COLUMN z int",
            "SELECT pg_read_file('/etc/passwd')",
            "; CREATE TABLE evil (x int)",
            "TRUNCATE users",
            "1 OR 1=1; DELETE FROM parcels",
            "INSERT INTO users VALUES ('x')",
            "UPDATE parcels SET area = 0",
            "GRANT ALL ON parcels TO public",
            "REVOKE SELECT ON parcels FROM guest",
            "COPY parcels FROM '/tmp/x'",
            "SELECT $$foo$$",  # dollar-quoting
            "SELECT 1;",
        ],
    )
    def test_rejects_unsafe(self, expr: str):
        with pytest.raises(ValueError, match="forbidden"):
            validate_expression(expr)

    def test_rejects_mixed_case_keywords(self):
        with pytest.raises(ValueError):
            validate_expression("DrOp TaBlE parcels")

    def test_error_message_truncates_long_payload(self):
        """Error message should truncate at 100 chars."""
        payload = "DROP " + "x" * 300
        with pytest.raises(ValueError) as exc:
            validate_expression(payload)
        # Error message should not leak the entire 300-char payload
        assert len(str(exc.value)) < 250


class TestValidateRefFilterAccepts:
    @pytest.mark.parametrize(
        "expr",
        [
            "categorie=N2000",
            "status=active AND zone=A",
            "value>100",
            "",
        ],
    )
    def test_accepts_simple(self, expr: str):
        assert validate_ref_filter(expr) == expr


class TestValidateRefFilterRejects:
    @pytest.mark.parametrize(
        "expr",
        [
            "categorie='N2000'",  # quotes rejected (stricter than validate_expression)
            'name="foo"',
            "x=1; DROP TABLE y",
            "x=1 OR 1=1--",
            "x=1 /* comment */",
            "x=1 */ injection",
            "x=$$foo$$",
            "DROP TABLE parcels",
            "EXEC xp_cmdshell",
            "CALL evil_proc()",
            "SELECT pg_read_file('/etc/hostname')",
            "1 OR 1=1 --",
        ],
    )
    def test_rejects_unsafe(self, expr: str):
        with pytest.raises(ValueError, match="Unsafe ref_filter"):
            validate_ref_filter(expr)


class TestPatternShapes:
    """Lock the compiled regex shapes so future refactors don't silently
    loosen the grammar."""

    def test_safe_ident_re_matches_expected(self):
        assert SAFE_IDENT_RE.match("valid_name")
        assert SAFE_IDENT_RE.match("public.table")
        assert not SAFE_IDENT_RE.match("9bad")
        assert not SAFE_IDENT_RE.match("has space")

    def test_sql_blocklist_is_case_insensitive(self):
        assert SQL_BLOCKLIST.search("DROP TABLE x")
        assert SQL_BLOCKLIST.search("drop table x")
        assert SQL_BLOCKLIST.search("DrOp")
        assert not SQL_BLOCKLIST.search("SELECT 1")
        assert not SQL_BLOCKLIST.search("area > 100")

    def test_blocklist_catches_pg_file_functions(self):
        """Explicit check that filesystem-access functions are blocked."""
        assert SQL_BLOCKLIST.search("pg_read_file('/etc/passwd')")
        assert SQL_BLOCKLIST.search("pg_write_file('/tmp/x', '')")
        assert SQL_BLOCKLIST.search("lo_import('/tmp/secret')")
        assert SQL_BLOCKLIST.search("lo_export(1, '/tmp/out')")
