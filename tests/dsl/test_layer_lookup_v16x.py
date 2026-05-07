"""Tests for v1.6.x #124 — layer_lookup(layer, match, take) DSL fct.

Coverage:
- Registry: present, ``is_subquery=True``, ``return_type='scalar'``,
  accepted kwargs (``layer``, ``match``, ``take``, ``layer_geom``).
- ``layer_lookup(layer='communes', match='spatial_within', take='code_insee')``
  → emits scalar ``(SELECT _L."code_insee" FROM "communes" AS _L
  WHERE ST_Within("geom", _L."geom") LIMIT 1)``.
- ``match='spatial_intersects'`` swaps the predicate.
- ``match='code_insee'`` (column identifier) emits attribute equality.
- ``layer='self'`` resolves to ``current_table``.
- ``layer_geom`` overrides the default geometry column on the lookup layer.
- Invalid identifiers / missing required kwargs raise ``DSLValidationError``.

Cross-source ATTACH plumbing (#122 push-down) lives in the LayerRegistry
and is exercised by ``tests/runtime/test_layer_registry_v16x.py``.
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


class TestLayerLookupRegistered:
    def test_present(self) -> None:
        spec = GEOM_FUNCTIONS["layer_lookup"]
        assert spec.return_type == "scalar"
        assert spec.is_subquery is True
        assert spec.crs_aware is False
        assert set(spec.accepted_kwargs) == {"layer", "match", "take", "layer_geom"}


# ---------------------------------------------------------------------------
# Compilation
# ---------------------------------------------------------------------------


class TestLayerLookupCompilation:
    def test_spatial_within_default(self) -> None:
        sql = compile_expression(
            "layer_lookup(layer='communes', take='code_insee')"
        )
        # ``match`` defaults to ``spatial_within`` per #124.
        assert 'SELECT _L."code_insee"' in sql
        assert 'FROM "communes" AS _L' in sql
        assert 'ST_Within("geom", _L."geom")' in sql
        assert "LIMIT 1" in sql

    def test_spatial_within_explicit(self) -> None:
        sql = compile_expression(
            "layer_lookup(layer='communes', match='spatial_within', "
            "take='code_insee')"
        )
        assert 'ST_Within("geom", _L."geom")' in sql

    def test_spatial_intersects(self) -> None:
        sql = compile_expression(
            "layer_lookup(layer='zonage', match='spatial_intersects', "
            "take='zone_label')"
        )
        assert 'ST_Intersects("geom", _L."geom")' in sql

    def test_attribute_match_shorthand(self) -> None:
        sql = compile_expression(
            "layer_lookup(layer='communes', match='code_insee', "
            "take='region_name')"
        )
        # Column identifier match → self.col = layer.col
        assert '"code_insee" = _L."code_insee"' in sql
        assert 'SELECT _L."region_name"' in sql

    def test_layer_geom_override(self) -> None:
        sql = compile_expression(
            "layer_lookup(layer='communes', take='code_insee', "
            "layer_geom='geom_3d')"
        )
        assert '_L."geom_3d"' in sql

    def test_layer_self_resolves_to_current_table(self) -> None:
        ctx = CompilationContext(current_table="parcels")
        sql = compile_expression(
            "layer_lookup(layer='self', match='spatial_intersects', "
            "take='id')",
            ctx,
        )
        assert 'FROM "parcels" AS _L' in sql

    def test_set_field_scalar_mode(self) -> None:
        # Realistic ``set_field:`` usage — scalar mode (the default).
        sql = compile_expression(
            "layer_lookup(layer='communes', take='code_insee')",
            mode="scalar",
        )
        assert sql.startswith("(SELECT _L.")

    def test_layer_required(self) -> None:
        with pytest.raises(DSLValidationError):
            compile_expression("layer_lookup(take='x')")

    def test_take_required(self) -> None:
        with pytest.raises(DSLValidationError) as exc:
            compile_expression("layer_lookup(layer='communes')")
        assert "take" in str(exc.value).lower()

    def test_take_must_be_identifier(self) -> None:
        with pytest.raises(DSLValidationError):
            compile_expression(
                "layer_lookup(layer='communes', take='code; DROP')"
            )

    def test_invalid_layer_rejected(self) -> None:
        with pytest.raises(DSLValidationError):
            compile_expression(
                "layer_lookup(layer='communes; DROP', take='code_insee')"
            )

    def test_unknown_match_rejected(self) -> None:
        with pytest.raises(DSLValidationError) as exc:
            compile_expression(
                "layer_lookup(layer='communes', match='bogus value', "
                "take='code_insee')"
            )
        assert "match" in str(exc.value).lower()

    def test_layer_self_without_current_table_raises(self) -> None:
        with pytest.raises(DSLValidationError) as exc:
            compile_expression(
                "layer_lookup(layer='self', take='code_insee')"
            )
        assert "current_table" in str(exc.value)

    def test_no_positional_args(self) -> None:
        with pytest.raises(DSLValidationError):
            compile_expression("layer_lookup('communes', 'code_insee')")
