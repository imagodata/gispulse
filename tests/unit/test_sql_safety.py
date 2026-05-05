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
    slug_identifier,
    validate_expression,
    validate_identifier,
    validate_layer_name,
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


class TestValidateLayerNameAccepts:
    """B-05 (v1.5.3): permissive validator for QGIS-friendly layer/column
    names — accepts spaces, accents, dashes, dots, leading digits."""

    @pytest.mark.parametrize(
        "name",
        [
            "parcels",
            "Parcelles",
            "Parcelles cadastrales 2024",   # spaces (B-05)
            "voies-rapides",                 # dash (B-05)
            "couche.qgis",                   # dot (B-05)
            "1starts_with_digit",            # leading digit (safe inside "...")
            "café",
            "naïve_layer",
            "couche QGIS éàüç-2024",
            "_internal",
            "T" + "x" * 127,                  # 128 chars — max allowed
        ],
    )
    def test_accepts(self, name: str) -> None:
        assert validate_layer_name(name) == name


class TestValidateLayerNameRejects:
    """B-05: SQLi vectors must still raise. Forbidden: ``"`` ``'`` ``;``
    ``\\`` plus control chars."""

    @pytest.mark.parametrize(
        "name",
        [
            "",                              # empty
            "a';DROP TABLE x;--",
            'a"; DROP TABLE x; --',
            "evil'); DROP TABLE _gispulse_change_log; --",
            ";",
            "back\\slash",                  # backslash escape
            "table\nname",
            "table\rname",
            "tab\tname",
            "\x00null",
            "x" * 129,                       # over 128 chars
        ],
    )
    def test_rejects(self, name: str) -> None:
        with pytest.raises(ValueError):
            validate_layer_name(name)

    def test_label_in_message(self) -> None:
        with pytest.raises(ValueError, match="layer"):
            validate_layer_name("a'", label="layer")
        with pytest.raises(ValueError, match="field"):
            validate_layer_name("a'", label="field")

    def test_rejects_non_str(self) -> None:
        with pytest.raises(ValueError):
            validate_layer_name(42)  # type: ignore[arg-type]


class TestSlugIdentifier:
    """B-05: ``slug_identifier`` returns a stable ASCII slug usable as
    a SQL trigger / index name. Pre-B-05 ASCII identifiers must round-trip
    unchanged so existing GPKGs keep matching their trigger names."""

    @pytest.mark.parametrize(
        "name",
        ["parcels", "parcelles_2024", "_internal", "MixedCase_99"],
    )
    def test_legacy_ascii_unchanged(self, name: str) -> None:
        """ASCII-strict identifiers must NOT be hashed — preserves
        backward-compat with v1.5.x triggers named ``_gispulse_trg_<layer>_<op>``.
        """
        assert slug_identifier(name) == name

    def test_unicode_layer_gets_hashed_slug(self) -> None:
        """Layer names with spaces / accents → ``<safe>_<hash8>``."""
        slug = slug_identifier("Parcelles cadastrales 2024")
        assert slug != "Parcelles cadastrales 2024"
        # ASCII-only output, no spaces, no slashes
        assert all(c.isalnum() or c == "_" for c in slug)
        # Suffix is 8-hex-char hash
        assert len(slug.split("_")[-1]) == 8

    def test_slug_is_deterministic(self) -> None:
        a = slug_identifier("Parcelles cadastrales 2024")
        b = slug_identifier("Parcelles cadastrales 2024")
        assert a == b

    def test_slug_different_for_different_names(self) -> None:
        a = slug_identifier("Parcelles 2024")
        b = slug_identifier("Parcelles 2025")
        assert a != b

    def test_slug_handles_pure_punctuation(self) -> None:
        """An input made entirely of unsafe-prefix characters still
        returns a valid slug (no empty prefix)."""
        slug = slug_identifier("---")
        assert slug.startswith("x_")
        assert len(slug) > 2

    def test_slug_rejects_empty(self) -> None:
        with pytest.raises(ValueError):
            slug_identifier("")


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
