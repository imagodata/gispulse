from __future__ import annotations

from typing import Any

import geopandas as gpd
import pandas as pd

from gispulse.capabilities.base import Capability
from gispulse.capabilities.registry import register




@register
class SymmetricDifferenceCapability(Capability):
    """Symmetric difference (XOR) of each feature against the reference."""

    name = "symmetric_difference"
    description = (
        "Returns the symmetric difference (A XOR B) of each feature against "
        "the reference layer union — areas unique to each side."
    )

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        ref_gdf: gpd.GeoDataFrame | None = None,
        **_,
    ) -> gpd.GeoDataFrame:
        """
        Args:
            gdf:     Input GeoDataFrame (side A).
            ref_gdf: Reference GeoDataFrame (side B), injected via ref_layer.
        """
        if ref_gdf is None or ref_gdf.empty:
            raise ValueError("symmetric_difference requires a reference layer.")
        if gdf.empty:
            return gdf.copy()

        if gdf.crs != ref_gdf.crs:
            ref_gdf = ref_gdf.to_crs(gdf.crs)
        ref_union = ref_gdf.geometry.union_all()

        result = gdf.copy()
        result["geometry"] = [
            g.symmetric_difference(ref_union)
            if g is not None and not g.is_empty
            else g
            for g in gdf.geometry
        ]
        return result

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "ref_layer": {
                    "type": "string",
                    "description": "Reference layer for XOR.",
                },
            },
            # ``ref_layer`` is pipeline plumbing (stripped by
            # rules.validation._PLUMBING_KEYS before validation) so it cannot
            # appear in ``required`` — the runtime raises a clear ValueError
            # when ``ref_gdf`` is None, which preserves the contract.
        }


@register
class VectorDiffCapability(Capability):
    """Computes differences between two vector layers — added / removed / modified."""

    name = "vector_diff"
    description = (
        "Compares two vector layers by feature id and returns a layer with "
        "a 'diff_status' column: 'added', 'removed', 'modified', 'unchanged'."
    )

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        ref_gdf: gpd.GeoDataFrame | None = None,
        id_field: str = "id",
        tolerance: float = 0.001,
        check_attrs: bool = True,
        attr_fields: list[str] | None = None,
        **_,
    ) -> gpd.GeoDataFrame:
        """
        Args:
            gdf:         New/current version of the layer.
            ref_gdf:     Old/previous version (injected via ref_layer).
            id_field:    Primary key column, present on both layers.
            tolerance:   Hausdorff distance threshold above which two
                         features with the same id are considered modified
                         geometrically.
            check_attrs: Also compare attributes (excluding geometry and
                         id_field).
            attr_fields: When set, restrict attribute comparison to this list.

        Returns:
            Concatenated GeoDataFrame of:
              - all features of *gdf*    → 'added' or 'modified' or 'unchanged'
              - features only in ref_gdf → 'removed' (with ref geometry)
            Plus columns diff_status and (for modified) attr_changed (bool),
            geom_changed (bool).
        """
        if ref_gdf is None:
            raise ValueError("vector_diff requires a reference layer.")
        if id_field not in gdf.columns:
            raise ValueError(f"id_field '{id_field}' missing from new layer.")
        if id_field not in ref_gdf.columns:
            raise ValueError(f"id_field '{id_field}' missing from reference layer.")

        if gdf.crs != ref_gdf.crs and ref_gdf.crs is not None:
            ref_gdf = ref_gdf.to_crs(gdf.crs)

        new_by_id = {row[id_field]: row for _, row in gdf.iterrows()}
        ref_by_id = {row[id_field]: row for _, row in ref_gdf.iterrows()}

        all_ids = set(new_by_id) | set(ref_by_id)

        compare_fields: list[str] = []
        if check_attrs:
            if attr_fields is not None:
                compare_fields = list(attr_fields)
            else:
                compare_fields = [
                    c for c in gdf.columns
                    if c != id_field and c != gdf.geometry.name
                ]

        rows_out: list[dict[str, Any]] = []
        for fid in sorted(all_ids, key=str):
            in_new = fid in new_by_id
            in_ref = fid in ref_by_id

            if in_new and not in_ref:
                base = new_by_id[fid].to_dict()
                base["diff_status"] = "added"
                base["attr_changed"] = False
                base["geom_changed"] = True
                rows_out.append(base)
                continue

            if in_ref and not in_new:
                base = ref_by_id[fid].to_dict()
                base["diff_status"] = "removed"
                base["attr_changed"] = False
                base["geom_changed"] = True
                rows_out.append(base)
                continue

            # Both sides present — compare
            new_row = new_by_id[fid]
            ref_row = ref_by_id[fid]
            new_geom = new_row.geometry
            ref_geom = ref_row.geometry

            geom_changed = True
            if new_geom is not None and ref_geom is not None:
                try:
                    geom_changed = new_geom.hausdorff_distance(ref_geom) > tolerance
                except Exception:
                    geom_changed = not new_geom.equals(ref_geom)

            attr_changed = False
            if compare_fields:
                for f in compare_fields:
                    if f not in new_row or f not in ref_row:
                        continue
                    if new_row[f] != ref_row[f]:
                        # Handle NaN equality quirk
                        try:
                            if pd.isna(new_row[f]) and pd.isna(ref_row[f]):
                                continue
                        except (TypeError, ValueError):
                            pass
                        attr_changed = True
                        break

            base = new_row.to_dict()
            if geom_changed or attr_changed:
                base["diff_status"] = "modified"
            else:
                base["diff_status"] = "unchanged"
            base["attr_changed"] = attr_changed
            base["geom_changed"] = geom_changed
            rows_out.append(base)

        if not rows_out:
            return gpd.GeoDataFrame(geometry=[], crs=gdf.crs)
        return gpd.GeoDataFrame(rows_out, crs=gdf.crs)

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "ref_layer": {
                    "type": "string",
                    "description": "Previous version of the layer.",
                },
                "id_field": {"type": "string", "default": "id"},
                "tolerance": {
                    "type": "number",
                    "minimum": 0,
                    "default": 0.001,
                    "description": "Hausdorff distance threshold for geometry diff.",
                },
                "check_attrs": {"type": "boolean", "default": True},
                "attr_fields": {
                    "type": ["array", "null"],
                    "items": {"type": "string"},
                },
            },
            # ``ref_layer`` is pipeline plumbing (stripped by
            # rules.validation._PLUMBING_KEYS before validation) so it cannot
            # appear in ``required`` — the runtime raises a clear ValueError
            # when ``ref_gdf`` is None, which preserves the contract.
        }


# ---------------------------------------------------------------------------
# ELT Lot 3 (#246) — DuckDB / PostGIS SQL push-down strategy
# ---------------------------------------------------------------------------
# Only symmetric_difference pushes down — it is a clean per-feature XOR
# against the unioned reference. vector_diff stays on Python: its
# row-by-row added/removed/modified diff with Hausdorff tolerance is not
# a SQL-pure operation.

from gispulse.capabilities import _geometry_sql as _gsql  # noqa: E402
from gispulse.capabilities.sql_pushdown import attach_sql_pushdown  # noqa: E402

attach_sql_pushdown(
    SymmetricDifferenceCapability,
    _gsql.build_symmetric_difference,
    gate=lambda p: p.get("ref_gdf") is not None,
    extra_inputs={"ref": "ref_gdf"},
)


