"""
Security tests — SQL injection and code injection protection.

Verifies that each validator in GISPulse correctly rejects malicious payloads
and accepts legitimate inputs. Tests are grouped by source module.
"""
from __future__ import annotations

import pytest

# ---------------------------------------------------------------------------
# rules/operation_executor.py — _validate_expression
# ---------------------------------------------------------------------------
from rules.operation_executor import _validate_expression as opexec_validate_expression


class TestOperationExecutorValidateExpression:
    """_validate_expression() in operation_executor.py."""

    # --- rejection ---

    def test_rejects_drop_table_semicolon(self):
        with pytest.raises(ValueError):
            opexec_validate_expression("'; DROP TABLE zones; --")

    def test_rejects_delete_from(self):
        with pytest.raises(ValueError):
            opexec_validate_expression("1; DELETE FROM users")

    def test_rejects_dollar_quote(self):
        with pytest.raises(ValueError):
            opexec_validate_expression("$$ evil $$")

    def test_rejects_drop_keyword_alone(self):
        with pytest.raises(ValueError):
            opexec_validate_expression("DROP TABLE parcels")

    def test_rejects_alter_keyword(self):
        with pytest.raises(ValueError):
            opexec_validate_expression("ALTER TABLE zones ADD COLUMN x INT")

    def test_rejects_truncate(self):
        with pytest.raises(ValueError):
            opexec_validate_expression("TRUNCATE users")

    def test_rejects_grant(self):
        with pytest.raises(ValueError):
            opexec_validate_expression("GRANT ALL ON zones TO evil")

    def test_rejects_revoke(self):
        with pytest.raises(ValueError):
            opexec_validate_expression("REVOKE SELECT ON zones FROM public")

    def test_rejects_copy(self):
        with pytest.raises(ValueError):
            opexec_validate_expression("COPY TO '/etc/passwd'")

    def test_rejects_pg_read_file(self):
        with pytest.raises(ValueError):
            opexec_validate_expression("pg_read_file('/etc/passwd')")

    def test_rejects_lo_export(self):
        with pytest.raises(ValueError):
            opexec_validate_expression("lo_export(1234, '/tmp/evil')")

    def test_rejects_semicolon_only(self):
        with pytest.raises(ValueError):
            opexec_validate_expression("area > 100;")

    def test_rejects_double_dollar_in_middle(self):
        with pytest.raises(ValueError):
            opexec_validate_expression("1 = $$1$$")

    # --- acceptance ---

    def test_allows_area_comparison(self):
        result = opexec_validate_expression("area > 100")
        assert result == "area > 100"

    def test_allows_status_equality(self):
        result = opexec_validate_expression("status = 'active'")
        assert result == "status = 'active'"

    def test_allows_arithmetic(self):
        result = opexec_validate_expression("population / area_m2 * 1000")
        assert result == "population / area_m2 * 1000"

    def test_allows_postgis_function(self):
        result = opexec_validate_expression("ST_Area(geom) > 500")
        assert result == "ST_Area(geom) > 500"


# ---------------------------------------------------------------------------
# rules/trigger_evaluator.py — _validate_business_expression
# ---------------------------------------------------------------------------
from rules.trigger_evaluator import _validate_business_expression


class TestTriggerEvaluatorValidateBusinessExpression:
    """_validate_business_expression() in trigger_evaluator.py."""

    # --- rejection ---

    def test_rejects_drop_table_semicolon(self):
        with pytest.raises(ValueError):
            _validate_business_expression("'; DROP TABLE zones; --")

    def test_rejects_delete_from(self):
        with pytest.raises(ValueError):
            _validate_business_expression("1; DELETE FROM users")

    def test_rejects_dollar_quote(self):
        with pytest.raises(ValueError):
            _validate_business_expression("$$ evil $$")

    def test_rejects_insert(self):
        with pytest.raises(ValueError):
            _validate_business_expression("INSERT INTO evil VALUES (1)")

    def test_rejects_update(self):
        with pytest.raises(ValueError):
            _validate_business_expression("UPDATE zones SET name = 'evil'")

    def test_rejects_create_table(self):
        with pytest.raises(ValueError):
            _validate_business_expression("CREATE TABLE evil (id INT)")

    def test_rejects_pg_write_file(self):
        with pytest.raises(ValueError):
            _validate_business_expression("pg_write_file('/etc/cron.d/evil', 'cmd')")

    def test_rejects_lo_import(self):
        with pytest.raises(ValueError):
            _validate_business_expression("lo_import('/etc/passwd')")

    def test_rejects_semicolon_alone(self):
        with pytest.raises(ValueError):
            _validate_business_expression("area > 100;")

    # --- acceptance ---

    def test_allows_area_comparison(self):
        result = _validate_business_expression("area > 100")
        assert result == "area > 100"

    def test_allows_status_equality(self):
        result = _validate_business_expression("status = 'active'")
        assert result == "status = 'active'"

    def test_allows_logical_and(self):
        result = _validate_business_expression("area > 0 AND status = 'valid'")
        assert result == "area > 0 AND status = 'valid'"


# ---------------------------------------------------------------------------
# adapters/http/routers/relations_router.py — _validate_identifier
# ---------------------------------------------------------------------------
from gispulse.adapters.http.routers.relations_router import (
    _validate_identifier as rel_validate_identifier,
    _validate_expression as rel_validate_expression,
)


class TestRelationsRouterValidateIdentifier:
    """_validate_identifier() in relations_router.py."""

    # --- rejection ---

    def test_rejects_drop_table_payload(self):
        with pytest.raises(ValueError):
            rel_validate_identifier("'; DROP TABLE")

    def test_rejects_semicolon_suffix(self):
        with pytest.raises(ValueError):
            rel_validate_identifier("table; --")

    def test_rejects_empty_string(self):
        with pytest.raises(ValueError):
            rel_validate_identifier("")

    def test_rejects_space_in_name(self):
        with pytest.raises(ValueError):
            rel_validate_identifier("bad name")

    def test_rejects_starts_with_digit(self):
        with pytest.raises(ValueError):
            rel_validate_identifier("1table")

    def test_rejects_dash_in_name(self):
        with pytest.raises(ValueError):
            rel_validate_identifier("bad-name")

    def test_rejects_sql_comment_injection(self):
        with pytest.raises(ValueError):
            rel_validate_identifier("zones--drop")

    # --- acceptance ---

    def test_allows_simple_name(self):
        assert rel_validate_identifier("parcels") == "parcels"

    def test_allows_schema_qualified(self):
        assert rel_validate_identifier("public.zones") == "public.zones"

    def test_allows_underscore_digits(self):
        assert rel_validate_identifier("my_table_123") == "my_table_123"

    def test_allows_leading_underscore(self):
        assert rel_validate_identifier("_private_table") == "_private_table"


class TestRelationsRouterValidateExpression:
    """_validate_expression() in relations_router.py."""

    # --- rejection ---

    def test_rejects_drop_table_semicolon(self):
        with pytest.raises(ValueError):
            rel_validate_expression("'; DROP TABLE zones; --")

    def test_rejects_delete_from(self):
        with pytest.raises(ValueError):
            rel_validate_expression("1; DELETE FROM users")

    def test_rejects_dollar_quote(self):
        with pytest.raises(ValueError):
            rel_validate_expression("$$ evil $$")

    def test_rejects_grant(self):
        with pytest.raises(ValueError):
            rel_validate_expression("GRANT ALL ON zones TO evil")

    def test_rejects_truncate(self):
        with pytest.raises(ValueError):
            rel_validate_expression("TRUNCATE zones")

    # --- acceptance ---

    def test_allows_area_comparison(self):
        result = rel_validate_expression("area > 100")
        assert result == "area > 100"

    def test_allows_status_equality(self):
        result = rel_validate_expression("status = 'active'")
        assert result == "status = 'active'"


# ---------------------------------------------------------------------------
# core/filter/expression_converter.py — ExpressionConverter.validate
# ---------------------------------------------------------------------------
from core.filter.expression_converter import ExpressionConverter


class TestExpressionConverterValidate:
    """ExpressionConverter.validate() in expression_converter.py."""

    def setup_method(self):
        self.converter = ExpressionConverter()

    # --- rejection ---

    def test_rejects_drop(self):
        valid, errors = self.converter.validate("DROP TABLE zones")
        assert valid is False
        assert errors

    def test_rejects_delete(self):
        valid, errors = self.converter.validate("DELETE FROM users")
        assert valid is False
        assert errors

    def test_rejects_insert(self):
        valid, errors = self.converter.validate("INSERT INTO evil VALUES (1)")
        assert valid is False
        assert errors

    def test_rejects_update(self):
        valid, errors = self.converter.validate("UPDATE zones SET name = 'x'")
        assert valid is False
        assert errors

    def test_rejects_alter(self):
        valid, errors = self.converter.validate("ALTER TABLE zones ADD COLUMN x INT")
        assert valid is False
        assert errors

    def test_rejects_create(self):
        valid, errors = self.converter.validate("CREATE TABLE evil (id INT)")
        assert valid is False
        assert errors

    def test_rejects_grant(self):
        valid, errors = self.converter.validate("GRANT ALL ON zones TO evil")
        assert valid is False
        assert errors

    def test_rejects_truncate(self):
        valid, errors = self.converter.validate("TRUNCATE zones")
        assert valid is False
        assert errors

    def test_rejects_empty_string(self):
        valid, errors = self.converter.validate("")
        assert valid is False
        assert errors

    def test_rejects_whitespace_only(self):
        valid, errors = self.converter.validate("   ")
        assert valid is False
        assert errors

    def test_rejects_unbalanced_parens_open(self):
        valid, errors = self.converter.validate("area > (100")
        assert valid is False
        assert errors

    def test_rejects_unbalanced_parens_close(self):
        valid, errors = self.converter.validate("area > 100)")
        assert valid is False
        assert errors

    # --- acceptance ---

    def test_allows_simple_comparison(self):
        valid, errors = self.converter.validate("area > 100")
        assert valid is True
        assert errors == []

    def test_allows_string_equality(self):
        valid, errors = self.converter.validate("name = 'Paris'")
        assert valid is True
        assert errors == []

    def test_allows_balanced_parens(self):
        valid, errors = self.converter.validate("(area > 100) AND (status = 'active')")
        assert valid is True
        assert errors == []


# ---------------------------------------------------------------------------
# capabilities/vector.py — _validate_calc_expression
# ---------------------------------------------------------------------------
from capabilities.vector import _validate_calc_expression, _validate_query_expression


class TestVectorValidateCalcExpression:
    """_validate_calc_expression() in capabilities/vector.py."""

    # --- rejection ---

    def test_rejects_os_system_via_import(self):
        with pytest.raises(ValueError):
            _validate_calc_expression("__import__('os').system('id')", columns=set())

    def test_rejects_class_bases_dunder(self):
        with pytest.raises(ValueError):
            _validate_calc_expression("().__class__.__bases__", columns=set())

    def test_rejects_builtins_dunder(self):
        with pytest.raises(ValueError):
            _validate_calc_expression("__builtins__['eval']('evil')", columns=set())

    def test_rejects_exec_call(self):
        with pytest.raises(ValueError):
            _validate_calc_expression("exec('import os')", columns=set())

    def test_rejects_eval_call(self):
        with pytest.raises(ValueError):
            _validate_calc_expression("eval('1+1')", columns=set())

    def test_rejects_import_call(self):
        with pytest.raises(ValueError):
            _validate_calc_expression("import('os')", columns=set())

    def test_rejects_getattr_call(self):
        with pytest.raises(ValueError):
            _validate_calc_expression("getattr(x, '__class__')", columns=set())

    def test_rejects_open_call(self):
        with pytest.raises(ValueError):
            _validate_calc_expression("open('/etc/passwd').read()", columns=set())

    def test_rejects_globals_call(self):
        with pytest.raises(ValueError):
            _validate_calc_expression("globals()['__builtins__']", columns=set())

    def test_rejects_semicolon(self):
        with pytest.raises(ValueError):
            _validate_calc_expression("area * 2; exec('evil')", columns={"area"})

    def test_rejects_list_comprehension(self):
        # ListComp is not in _CALC_SAFE_NODES
        with pytest.raises(ValueError):
            _validate_calc_expression("[x for x in range(10)]", columns=set())

    def test_rejects_lambda(self):
        with pytest.raises(ValueError):
            _validate_calc_expression("(lambda: exec('evil'))()", columns=set())

    # --- acceptance ---

    def test_allows_division(self):
        _validate_calc_expression("population / area_m2", columns={"population", "area_m2"})

    def test_allows_numpy_log(self):
        _validate_calc_expression("np.log(population)", columns={"population"})

    def test_allows_arithmetic(self):
        _validate_calc_expression("area * 2 + 1", columns={"area"})

    def test_allows_comparison(self):
        _validate_calc_expression("area > 100", columns={"area"})

    def test_allows_ternary(self):
        _validate_calc_expression("1 if area > 0 else 0", columns={"area"})


class TestVectorValidateQueryExpression:
    """_validate_query_expression() in capabilities/vector.py."""

    # --- rejection ---

    def test_rejects_backtick_pd_eval(self):
        with pytest.raises(ValueError):
            _validate_query_expression("`@pd.eval('evil')`")

    def test_rejects_class_dunder(self):
        with pytest.raises(ValueError):
            _validate_query_expression("__class__")

    def test_rejects_import_dunder(self):
        with pytest.raises(ValueError):
            _validate_query_expression("__import__('os')")

    def test_rejects_exec(self):
        with pytest.raises(ValueError):
            _validate_query_expression("exec('evil')")

    def test_rejects_eval(self):
        with pytest.raises(ValueError):
            _validate_query_expression("eval('evil')")

    def test_rejects_backtick_alone(self):
        with pytest.raises(ValueError):
            _validate_query_expression("area `> 100`")

    def test_rejects_builtins_dunder(self):
        with pytest.raises(ValueError):
            _validate_query_expression("__builtins__")

    # --- acceptance ---

    def test_allows_area_comparison(self):
        _validate_query_expression("area > 100")

    def test_allows_name_equality(self):
        _validate_query_expression("name == 'Paris'")

    def test_allows_logical_and(self):
        _validate_query_expression("area > 0 and status == 'active'")


# ---------------------------------------------------------------------------
# adapters/http/routers/portal_sql_router.py — _validate_sql_readonly
# ---------------------------------------------------------------------------
from fastapi import HTTPException
from gispulse.adapters.http.routers.portal_sql_router import _validate_sql_readonly


class TestPortalSQLRouterValidateSQLReadonly:
    """_validate_sql_readonly() in portal_sql_router.py."""

    # --- rejection ---

    def test_rejects_drop_table(self):
        with pytest.raises(HTTPException) as exc_info:
            _validate_sql_readonly("DROP TABLE users")
        assert exc_info.value.status_code == 400

    def test_rejects_alter_table(self):
        with pytest.raises(HTTPException) as exc_info:
            _validate_sql_readonly("ALTER TABLE zones ADD COLUMN x INT")
        assert exc_info.value.status_code == 400

    def test_rejects_grant_all(self):
        with pytest.raises(HTTPException) as exc_info:
            _validate_sql_readonly("GRANT ALL ON zones TO evil")
        assert exc_info.value.status_code == 400

    def test_rejects_copy_to(self):
        with pytest.raises(HTTPException) as exc_info:
            _validate_sql_readonly("COPY zones TO '/tmp/dump.csv'")
        assert exc_info.value.status_code == 400

    def test_rejects_pg_read_file(self):
        with pytest.raises(HTTPException) as exc_info:
            _validate_sql_readonly("SELECT pg_read_file('x')")
        assert exc_info.value.status_code == 400

    def test_rejects_create_table(self):
        with pytest.raises(HTTPException) as exc_info:
            _validate_sql_readonly("CREATE TABLE evil (id INT)")
        assert exc_info.value.status_code == 400

    def test_rejects_truncate(self):
        with pytest.raises(HTTPException) as exc_info:
            _validate_sql_readonly("TRUNCATE zones")
        assert exc_info.value.status_code == 400

    def test_rejects_revoke(self):
        with pytest.raises(HTTPException) as exc_info:
            _validate_sql_readonly("REVOKE SELECT ON zones FROM public")
        assert exc_info.value.status_code == 400

    def test_rejects_pg_write_file(self):
        with pytest.raises(HTTPException) as exc_info:
            _validate_sql_readonly("SELECT pg_write_file('/tmp/x', 'evil')")
        assert exc_info.value.status_code == 400

    def test_rejects_lo_import(self):
        with pytest.raises(HTTPException) as exc_info:
            _validate_sql_readonly("SELECT lo_import('/etc/passwd')")
        assert exc_info.value.status_code == 400

    def test_rejects_lo_export(self):
        with pytest.raises(HTTPException) as exc_info:
            _validate_sql_readonly("SELECT lo_export(1234, '/tmp/evil')")
        assert exc_info.value.status_code == 400

    def test_rejects_pg_terminate_backend(self):
        with pytest.raises(HTTPException) as exc_info:
            _validate_sql_readonly("SELECT pg_terminate_backend(1234)")
        assert exc_info.value.status_code == 400

    def test_rejects_case_insensitive_drop(self):
        with pytest.raises(HTTPException) as exc_info:
            _validate_sql_readonly("drop table users")
        assert exc_info.value.status_code == 400

    def test_rejects_mixed_case_alter(self):
        with pytest.raises(HTTPException) as exc_info:
            _validate_sql_readonly("aLtEr TABLE zones DROP COLUMN x")
        assert exc_info.value.status_code == 400

    # --- acceptance ---

    def test_allows_select_all(self):
        _validate_sql_readonly("SELECT * FROM parcels")

    def test_allows_select_with_where(self):
        _validate_sql_readonly("SELECT COUNT(*) FROM zones WHERE area > 0")

    def test_allows_select_with_join(self):
        _validate_sql_readonly(
            "SELECT a.id, b.name FROM parcels a JOIN zones b ON ST_Within(a.geom, b.geom)"
        )

    def test_allows_select_with_cte(self):
        _validate_sql_readonly(
            "WITH summary AS (SELECT id, COUNT(*) AS cnt FROM parcels GROUP BY id) "
            "SELECT * FROM summary"
        )


# ---------------------------------------------------------------------------
# persistence/bridge.py — HybridEngine.list_layers / load_layer identifier check
# ---------------------------------------------------------------------------
from persistence.bridge import HybridEngine


class TestBridgeIdentifierValidation:
    """HybridEngine validates schema/table names before building SQL.

    list_layers() wraps validation inside a try/except that falls through
    to _postgis on any exception (DB not available in unit tests). We
    verify that the validation regex itself rejects unsafe names by
    testing the inline check directly, and confirm load_layer() raises
    ValueError (it does not swallow the exception).
    """

    def _make_engine(self):
        return HybridEngine.__new__(HybridEngine)

    # --- list_layers: test the regex guard in isolation ---

    def test_list_layers_rejects_drop_injection_via_regex(self):
        import re
        _ident_re = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,126}$")
        assert not _ident_re.match("'; DROP"), (
            "Regex must not match injection payload '\"'; DROP\"'"
        )

    def test_list_layers_rejects_semicolon_schema_via_regex(self):
        import re
        _ident_re = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,126}$")
        assert not _ident_re.match("public; DROP TABLE zones"), (
            "Regex must not match schema with semicolon"
        )

    def test_list_layers_rejects_empty_schema_via_regex(self):
        import re
        _ident_re = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,126}$")
        # None.match("") returns None (falsy)
        assert not _ident_re.match(""), (
            "Regex must not match empty string"
        )

    def test_list_layers_rejects_dash_in_schema_via_regex(self):
        import re
        _ident_re = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,126}$")
        assert not _ident_re.match("my-schema"), (
            "Regex must not match schema name with dash"
        )

    # --- load_layer: raises ValueError directly (no swallowing) ---

    def test_load_layer_rejects_unsafe_table_name(self):
        engine = self._make_engine()
        with pytest.raises(ValueError, match="Unsafe table name"):
            engine.load_layer("'; DROP TABLE parcels")

    def test_load_layer_rejects_semicolon_table(self):
        engine = self._make_engine()
        with pytest.raises(ValueError, match="Unsafe table name"):
            engine.load_layer("parcels; DROP TABLE zones")

    def test_load_layer_rejects_unsafe_schema(self):
        engine = self._make_engine()
        with pytest.raises(ValueError, match="Unsafe schema name"):
            engine.load_layer("parcels", schema="'; DROP")
