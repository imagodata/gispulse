"""Tests for v1.6.0 #122 — geom_within / geom_overlaps_any subquery fcts.

Coverage:
- Registry: both fcts present, ``is_subquery=True``, accepted kwargs.
- ``geom_within(layer='communes')`` → emits ``EXISTS ... ST_Within ...``.
- ``geom_within(layer='communes', match='code_insee')`` adds
  ``AND _L."code_insee" = "code_insee"``.
- ``geom_overlaps_any(layer='self', exclude_self=true)`` resolves
  ``self`` to ``current_table`` and emits the pk-guard clause.
- ``layer_geom='geom_3d'`` overrides the default geometry column.
- ``layer='self'`` without ``current_table`` raises ``DSLValidationError``.
- Invalid layer / match identifiers rejected.
- ``exclude_self`` rejected on ``geom_within`` (kwargs allowlist).

DuckDB E2E tests are deferred until the cross-source ATTACH plumbing
lands (the validation runner). The compiled SQL is asserted as a string
here so we lock the contract that downstream wiring will rely on.
"""

from __future__ import annotations

import pytest

from gispulse.dsl import (
    GEOM_FUNCTIONS,
    CompilationContext,
    DSLValidationError,
    compile_expression,
)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class TestSubqueryFunctionsRegistered:
    def test_geom_within_present(self) -> None:
        spec = GEOM_FUNCTIONS["geom_within"]
        assert spec.return_type == "boolean"
        assert spec.is_subquery is True
        assert spec.crs_aware is False
        assert "layer" in spec.accepted_kwargs
        assert "match" in spec.accepted_kwargs
        assert "layer_geom" in spec.accepted_kwargs

    def test_geom_overlaps_any_present(self) -> None:
        spec = GEOM_FUNCTIONS["geom_overlaps_any"]
        assert spec.return_type == "boolean"
        assert spec.is_subquery is True
        assert "layer" in spec.accepted_kwargs
        assert "exclude_self" in spec.accepted_kwargs
        assert "layer_geom" in spec.accepted_kwargs


# ---------------------------------------------------------------------------
# geom_within compilation
# ---------------------------------------------------------------------------


class TestGeomWithinCompilation:
    def test_simple_within(self) -> None:
        sql = compile_expression(
            "geom_within(layer='communes')", mode="boolean"
        )
        assert "EXISTS" in sql
        assert 'FROM "communes" AS _L' in sql
        assert 'ST_Within("geom", _L."geom")' in sql

    def test_with_match_attribute(self) -> None:
        sql = compile_expression(
            "geom_within(layer='communes', match='code_insee')",
            mode="boolean",
        )
        assert 'AND _L."code_insee" = "code_insee"' in sql

    def test_layer_geom_override(self) -> None:
        sql = compile_expression(
            "geom_within(layer='communes', layer_geom='geometry')",
            mode="boolean",
        )
        assert '_L."geometry"' in sql

    def test_layer_self_resolves_to_current_table(self) -> None:
        ctx = CompilationContext(current_table="parcels")
        sql = compile_expression(
            "geom_within(layer='self', match='code_insee')", ctx, mode="boolean"
        )
        assert 'FROM "parcels" AS _L' in sql

    def test_layer_self_without_current_table_raises(self) -> None:
        with pytest.raises(DSLValidationError) as exc:
            compile_expression(
                "geom_within(layer='self')", mode="boolean"
            )
        assert "current_table" in str(exc.value)

    def test_invalid_layer_identifier_rejected(self) -> None:
        with pytest.raises(DSLValidationError):
            compile_expression(
                "geom_within(layer='communes; DROP TABLE x')", mode="boolean"
            )

    def test_invalid_match_identifier_rejected(self) -> None:
        with pytest.raises(DSLValidationError):
            compile_expression(
                "geom_within(layer='communes', match='c; DROP')",
                mode="boolean",
            )

    def test_layer_required(self) -> None:
        with pytest.raises(DSLValidationError):
            compile_expression("geom_within()", mode="boolean")

    def test_exclude_self_rejected_on_within(self) -> None:
        with pytest.raises(DSLValidationError) as exc:
            compile_expression(
                "geom_within(layer='self', exclude_self=True)", mode="boolean"
            )
        assert "exclude_self" in str(exc.value)


# ---------------------------------------------------------------------------
# geom_overlaps_any compilation
# ---------------------------------------------------------------------------


class TestGeomOverlapsAnyCompilation:
    def test_overlaps_self_with_exclude(self) -> None:
        ctx = CompilationContext(current_table="parcels", pk_col="id")
        sql = compile_expression(
            "geom_overlaps_any(layer='self', exclude_self=True)",
            ctx,
            mode="boolean",
        )
        assert 'FROM "parcels" AS _L' in sql
        assert 'ST_Overlaps("geom", _L."geom")' in sql
        assert 'AND _L."id" <> "id"' in sql

    def test_overlaps_self_without_exclude(self) -> None:
        ctx = CompilationContext(current_table="parcels")
        sql = compile_expression(
            "geom_overlaps_any(layer='self', exclude_self=False)",
            ctx,
            mode="boolean",
        )
        # No exclude clause emitted
        assert "<>" not in sql

    def test_overlaps_other_layer(self) -> None:
        sql = compile_expression(
            "geom_overlaps_any(layer='zonage_pli')", mode="boolean"
        )
        assert 'FROM "zonage_pli" AS _L' in sql

    def test_pk_col_override_via_ctx(self) -> None:
        ctx = CompilationContext(current_table="parcels", pk_col="fid")
        sql = compile_expression(
            "geom_overlaps_any(layer='self', exclude_self=True)",
            ctx,
            mode="boolean",
        )
        assert 'AND _L."fid" <> "fid"' in sql

    def test_exclude_self_must_be_bool(self) -> None:
        with pytest.raises(DSLValidationError) as exc:
            compile_expression(
                "geom_overlaps_any(layer='self', exclude_self='yes')",
                CompilationContext(current_table="parcels"),
                mode="boolean",
            )
        assert "string literal" in str(exc.value).lower() or "bool" in str(exc.value).lower()

    def test_match_rejected_on_overlaps(self) -> None:
        with pytest.raises(DSLValidationError):
            compile_expression(
                "geom_overlaps_any(layer='communes', match='code_insee')",
                mode="boolean",
            )


# ---------------------------------------------------------------------------
# Combined with arithmetic / boolean ops
# ---------------------------------------------------------------------------


class TestSubquerysInBooleanExpressions:
    def test_negation(self) -> None:
        ctx = CompilationContext(current_table="parcels")
        sql = compile_expression(
            "not geom_overlaps_any(layer='self', exclude_self=True)",
            ctx,
            mode="boolean",
        )
        assert sql.startswith("(NOT")

    def test_combined_with_geom_is_valid(self) -> None:
        ctx = CompilationContext(current_table="parcels")
        sql = compile_expression(
            "geom_is_valid() and not geom_overlaps_any(layer='self', exclude_self=True)",
            ctx,
            mode="boolean",
        )
        assert "AND" in sql
        assert "NOT" in sql
        assert "EXISTS" in sql

    def test_match_self_reference_via_validate_rule(self) -> None:
        # End-to-end use case from docs-site/guide/dsl-validation.md
        sql = compile_expression(
            "geom_within(layer='communes', match='code_insee')",
            CompilationContext(current_table="parcels"),
            mode="boolean",
        )
        assert "EXISTS" in sql
        assert 'AND _L."code_insee" = "code_insee"' in sql


# ---------------------------------------------------------------------------
# CompilationContext extensions
# ---------------------------------------------------------------------------


class TestCompilationContextExtensions:
    def test_default_pk_is_id(self) -> None:
        assert CompilationContext().pk_col == "id"

    def test_default_layer_geom(self) -> None:
        assert CompilationContext().default_layer_geom == "geom"

    def test_invalid_current_table(self) -> None:
        with pytest.raises(DSLValidationError):
            CompilationContext(current_table="parcels; DROP")

    def test_invalid_pk_col(self) -> None:
        with pytest.raises(DSLValidationError):
            CompilationContext(pk_col="id; --")
