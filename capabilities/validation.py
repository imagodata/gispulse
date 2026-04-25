"""
Data validation capabilities for GISPulse.

These capabilities are available on the Community tier (free) to drive adoption.
They cover geometry topology, duplicate detection, attribute schema validation,
and data completeness checks.

Dependencies: geopandas, shapely (already mandatory in the stack).
Optional: scipy (for fuzzy duplicate detection via spatial index).
"""

from __future__ import annotations

import re
from typing import Any

import geopandas as gpd
from shapely.geometry import MultiPolygon, Polygon
from shapely.validation import explain_validity

from capabilities.base import Capability
from capabilities.registry import register


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _geometry_type_label(geom) -> str:
    """Returns a human-readable geometry type label."""
    if geom is None:
        return "null"
    return geom.geom_type


# ---------------------------------------------------------------------------
# TopologyCheckCapability
# ---------------------------------------------------------------------------


@register
class TopologyCheckCapability(Capability):
    """Vérifie la topologie d'une couche vecteur: overlaps, self-intersections, géométries invalides."""

    name = "topology_check"
    description = (
        "Checks vector layer topology: invalid geometries, self-intersections, "
        "and overlapping polygons. Returns one row per issue found."
    )

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        check_overlaps: bool = True,
        check_self_intersections: bool = True,
        check_validity: bool = True,
        id_col: str | None = None,
        **_,
    ) -> gpd.GeoDataFrame:
        """
        Args:
            gdf:                      Couche vecteur à contrôler.
            check_overlaps:           Si True, détecte les polygones qui se chevauchent.
            check_self_intersections: Si True, détecte les géométries auto-intersectantes.
            check_validity:           Si True, détecte les géométries invalides (Shapely).
            id_col:                   Colonne identifiant les features (défaut: index).

        Returns:
            GeoDataFrame des problèmes détectés. Colonnes :
            - ``feature_id``  : identifiant de la feature concernée.
            - ``issue_type``  : type d'anomalie (invalid_geometry, self_intersection, overlap).
            - ``description`` : description détaillée.
            - ``geometry``    : géométrie de l'intersection/anomalie si disponible.

            Un GeoDataFrame vide (aucune colonne) signifie qu'aucun problème n'a été trouvé.
        """
        issues: list[dict[str, Any]] = []

        def _fid(idx: int) -> Any:
            if id_col and id_col in gdf.columns:
                return gdf.iloc[idx][id_col]
            return gdf.index[idx]

        # 1. Validité et auto-intersections
        if check_validity or check_self_intersections:
            for i, geom in enumerate(gdf.geometry):
                if geom is None:
                    issues.append(
                        {
                            "feature_id": _fid(i),
                            "issue_type": "null_geometry",
                            "description": "Geometry is null.",
                            "geometry": None,
                        }
                    )
                    continue
                if check_validity and not geom.is_valid:
                    reason = explain_validity(geom)
                    issues.append(
                        {
                            "feature_id": _fid(i),
                            "issue_type": "invalid_geometry",
                            "description": f"Invalid geometry: {reason}",
                            "geometry": geom,
                        }
                    )
                if check_self_intersections and not geom.is_simple:
                    issues.append(
                        {
                            "feature_id": _fid(i),
                            "issue_type": "self_intersection",
                            "description": "Geometry is not simple (self-intersection detected).",
                            "geometry": geom,
                        }
                    )

        # 2. Overlaps entre polygones
        if check_overlaps:
            poly_mask = gdf.geometry.apply(
                lambda g: g is not None and isinstance(g, (Polygon, MultiPolygon))
            )
            poly_gdf = gdf[poly_mask].reset_index(drop=False)

            if len(poly_gdf) > 1:
                sindex = poly_gdf.sindex
                for i, row_i in poly_gdf.iterrows():
                    geom_i = row_i.geometry
                    if geom_i is None or geom_i.is_empty:
                        continue
                    candidates = list(sindex.intersection(geom_i.bounds))
                    for j in candidates:
                        if j <= i:
                            continue
                        geom_j = poly_gdf.iloc[j].geometry
                        if geom_j is None or geom_j.is_empty:
                            continue
                        if geom_i.overlaps(geom_j):
                            inter = geom_i.intersection(geom_j)
                            fid_i = _fid(poly_gdf.iloc[i].name if "index" not in poly_gdf.columns else poly_gdf.iloc[i]["index"])
                            fid_j = _fid(poly_gdf.iloc[j].name if "index" not in poly_gdf.columns else poly_gdf.iloc[j]["index"])
                            issues.append(
                                {
                                    "feature_id": f"{fid_i}+{fid_j}",
                                    "issue_type": "overlap",
                                    "description": (
                                        f"Features {fid_i} and {fid_j} overlap "
                                        f"(area={inter.area:.6f})."
                                    ),
                                    "geometry": inter,
                                }
                            )

        if not issues:
            return gpd.GeoDataFrame(
                columns=["feature_id", "issue_type", "description", "geometry"],
                geometry="geometry",
                crs=gdf.crs,
            )

        return gpd.GeoDataFrame(issues, geometry="geometry", crs=gdf.crs).reset_index(
            drop=True
        )

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "check_overlaps": {"type": "boolean", "default": True},
                "check_self_intersections": {"type": "boolean", "default": True},
                "check_validity": {"type": "boolean", "default": True},
                "id_col": {
                    "type": ["string", "null"],
                    "description": "Column to use as feature identifier.",
                },
            },
        }


# ---------------------------------------------------------------------------
# DuplicateGeometryCapability
# ---------------------------------------------------------------------------


@register
class DuplicateGeometryCapability(Capability):
    """Détecte les géométries dupliquées (exact ou fuzzy avec tolérance)."""

    name = "duplicate_geometry"
    description = (
        "Detects duplicate geometries (exact match or fuzzy within a tolerance)."
    )

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        tolerance: float = 0.0,
        id_col: str | None = None,
        **_,
    ) -> gpd.GeoDataFrame:
        """
        Args:
            gdf:       Couche vecteur à analyser.
            tolerance: Tolérance en unités du CRS.
                       - 0.0 : comparaison exacte WKT (rapide).
                       - > 0 : compare les géométries simplifiées (buffer de tolérance).
            id_col:    Colonne identifiant les features (défaut: index).

        Returns:
            GeoDataFrame des doublons. Colonnes :
            - ``feature_id``    : identifiant de la feature dupliquée.
            - ``duplicate_of``  : identifiant de la première occurrence.
            - ``geometry``      : géométrie dupliquée.

            Un GeoDataFrame vide indique qu'aucun doublon n'a été trouvé.
        """
        if gdf.empty:
            return gpd.GeoDataFrame(
                columns=["feature_id", "duplicate_of", "geometry"],
                geometry="geometry",
                crs=gdf.crs,
            )

        def _fid(idx: int) -> Any:
            if id_col and id_col in gdf.columns:
                return gdf.iloc[idx][id_col]
            return gdf.index[idx]

        duplicates: list[dict[str, Any]] = []

        if tolerance == 0.0:
            # Comparaison exacte par WKT
            seen: dict[str, int] = {}
            for i, geom in enumerate(gdf.geometry):
                if geom is None:
                    continue
                wkt = geom.wkt
                if wkt in seen:
                    duplicates.append(
                        {
                            "feature_id": _fid(i),
                            "duplicate_of": _fid(seen[wkt]),
                            "geometry": geom,
                        }
                    )
                else:
                    seen[wkt] = i
        else:
            # Comparaison fuzzy : deux géométries sont "dupliquées" si leur
            # intersection symétrique est inférieure à tolerance^2 en surface,
            # ou si la distance entre elles est < tolerance.
            n = len(gdf)
            sindex = gdf.sindex
            marked: set[int] = set()

            for i in range(n):
                if i in marked:
                    continue
                geom_i = gdf.geometry.iloc[i]
                if geom_i is None or geom_i.is_empty:
                    continue
                # Expand bounds by tolerance for candidate search
                bounds = (
                    geom_i.bounds[0] - tolerance,
                    geom_i.bounds[1] - tolerance,
                    geom_i.bounds[2] + tolerance,
                    geom_i.bounds[3] + tolerance,
                )
                candidates = list(sindex.intersection(bounds))
                for j in candidates:
                    if j <= i or j in marked:
                        continue
                    geom_j = gdf.geometry.iloc[j]
                    if geom_j is None or geom_j.is_empty:
                        continue
                    if geom_i.distance(geom_j) <= tolerance:
                        duplicates.append(
                            {
                                "feature_id": _fid(j),
                                "duplicate_of": _fid(i),
                                "geometry": geom_j,
                            }
                        )
                        marked.add(j)

        if not duplicates:
            return gpd.GeoDataFrame(
                columns=["feature_id", "duplicate_of", "geometry"],
                geometry="geometry",
                crs=gdf.crs,
            )

        return gpd.GeoDataFrame(
            duplicates, geometry="geometry", crs=gdf.crs
        ).reset_index(drop=True)

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "tolerance": {
                    "type": "number",
                    "default": 0.0,
                    "description": (
                        "Comparison tolerance in CRS units. "
                        "0 = exact WKT match, >0 = fuzzy distance-based match."
                    ),
                },
                "id_col": {
                    "type": ["string", "null"],
                    "description": "Column to use as feature identifier.",
                },
            },
        }


# ---------------------------------------------------------------------------
# AttributeValidationCapability
# ---------------------------------------------------------------------------


@register
class AttributeValidationCapability(Capability):
    """Valide les attributs d'un layer contre un schéma (types, nullability, range, regex)."""

    name = "attribute_validation"
    description = (
        "Validates layer attributes against a schema defining types, "
        "nullability, value ranges, and regex patterns."
    )

    # Mapping Python type names -> sets of acceptable JSON/Python types
    _TYPE_MAP: dict[str, tuple[type, ...]] = {
        "str": (str,),
        "string": (str,),
        "int": (int,),
        "integer": (int,),
        "float": (float, int),
        "number": (float, int),
        "bool": (bool,),
        "boolean": (bool,),
    }

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        schema: dict[str, dict[str, Any]] | None = None,
        id_col: str | None = None,
        **_,
    ) -> gpd.GeoDataFrame:
        """
        Args:
            gdf:     Couche vecteur à valider.
            schema:  Schéma de validation. Dictionnaire ``{colonne: règles}``.
                     Règles supportées par colonne :

                     - ``type``     (str)  : type attendu parmi str/int/float/bool.
                     - ``nullable`` (bool) : si False, la valeur ne peut pas être null/None.
                     - ``min``      (num)  : valeur minimale (numérique ou longueur de chaîne).
                     - ``max``      (num)  : valeur maximale (numérique ou longueur de chaîne).
                     - ``pattern``  (str)  : regex que la valeur (str) doit respecter.
                     - ``allowed``  (list) : liste de valeurs autorisées (enum).

            id_col:  Colonne identifiant les features (défaut: index).

        Returns:
            GeoDataFrame des violations détectées. Colonnes :
            - ``feature_id`` : identifiant de la feature.
            - ``column``     : nom de la colonne en erreur.
            - ``rule``       : règle violée (type/nullable/min/max/pattern/allowed).
            - ``value``      : valeur incorrecte (str).
            - ``description``: message d'erreur détaillé.
            - ``geometry``   : géométrie de la feature concernée.

            Un GeoDataFrame vide signifie que toutes les données sont valides.
        """
        schema = schema or {}
        if not schema:
            return gpd.GeoDataFrame(
                columns=["feature_id", "column", "rule", "value", "description", "geometry"],
                geometry="geometry",
                crs=gdf.crs,
            )

        violations: list[dict[str, Any]] = []

        def _fid(idx: int) -> Any:
            if id_col and id_col in gdf.columns:
                return gdf.iloc[idx][id_col]
            return gdf.index[idx]

        for col, rules in schema.items():
            if col not in gdf.columns:
                # Colonne manquante = toutes les features en violation
                for i in range(len(gdf)):
                    violations.append(
                        {
                            "feature_id": _fid(i),
                            "column": col,
                            "rule": "missing_column",
                            "value": "N/A",
                            "description": f"Column '{col}' does not exist in the layer.",
                            "geometry": gdf.geometry.iloc[i],
                        }
                    )
                continue

            expected_type_name: str | None = rules.get("type")
            nullable: bool = rules.get("nullable", True)
            min_val = rules.get("min")
            max_val = rules.get("max")
            pattern: str | None = rules.get("pattern")
            allowed: list | None = rules.get("allowed")
            compiled_pattern = re.compile(pattern) if pattern else None

            for i, val in enumerate(gdf[col]):
                is_null = val is None or (
                    not isinstance(val, bool)
                    and hasattr(val, "__class__")
                    and val != val  # NaN check
                )
                try:
                    import pandas as pd
                    is_null = pd.isna(val)
                except Exception:
                    pass

                feature_geom = gdf.geometry.iloc[i]

                def _add(rule: str, desc: str) -> None:
                    violations.append(
                        {
                            "feature_id": _fid(i),
                            "column": col,
                            "rule": rule,
                            "value": str(val),
                            "description": desc,
                            "geometry": feature_geom,
                        }
                    )

                # Nullability
                if is_null:
                    if not nullable:
                        _add("nullable", f"Column '{col}' does not allow null values.")
                    continue  # Skip further checks for null values

                # Type check
                if expected_type_name:
                    accepted_types = self._TYPE_MAP.get(expected_type_name.lower())
                    if accepted_types and not isinstance(val, accepted_types):
                        _add(
                            "type",
                            f"Column '{col}': expected {expected_type_name}, "
                            f"got {type(val).__name__} (value={val!r}).",
                        )
                        continue  # Skip range/pattern checks if type is wrong

                # Min / max
                if min_val is not None:
                    if isinstance(val, str):
                        if len(val) < min_val:
                            _add(
                                "min",
                                f"Column '{col}': string length {len(val)} < min {min_val}.",
                            )
                    elif val < min_val:
                        _add("min", f"Column '{col}': value {val} < min {min_val}.")

                if max_val is not None:
                    if isinstance(val, str):
                        if len(val) > max_val:
                            _add(
                                "max",
                                f"Column '{col}': string length {len(val)} > max {max_val}.",
                            )
                    elif val > max_val:
                        _add("max", f"Column '{col}': value {val} > max {max_val}.")

                # Pattern
                if compiled_pattern and isinstance(val, str):
                    if not compiled_pattern.fullmatch(val):
                        _add(
                            "pattern",
                            f"Column '{col}': value {val!r} does not match pattern {pattern!r}.",
                        )

                # Allowed values
                if allowed is not None and val not in allowed:
                    _add(
                        "allowed",
                        f"Column '{col}': value {val!r} not in allowed list {allowed}.",
                    )

        if not violations:
            return gpd.GeoDataFrame(
                columns=["feature_id", "column", "rule", "value", "description", "geometry"],
                geometry="geometry",
                crs=gdf.crs,
            )

        return gpd.GeoDataFrame(
            violations, geometry="geometry", crs=gdf.crs
        ).reset_index(drop=True)

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "schema": {
                    "type": "object",
                    "description": (
                        "Validation schema: {column: {type, nullable, min, max, pattern, allowed}}."
                    ),
                    "additionalProperties": {
                        "type": "object",
                        "properties": {
                            "type": {
                                "type": "string",
                                "enum": ["str", "string", "int", "integer", "float", "number", "bool", "boolean"],
                            },
                            "nullable": {"type": "boolean", "default": True},
                            "min": {"type": "number"},
                            "max": {"type": "number"},
                            "pattern": {"type": "string"},
                            "allowed": {"type": "array"},
                        },
                    },
                },
                "id_col": {
                    "type": ["string", "null"],
                    "description": "Column to use as feature identifier.",
                },
            },
            "required": ["schema"],
        }


# ---------------------------------------------------------------------------
# CompletenessCheckCapability
# ---------------------------------------------------------------------------


@register
class CompletenessCheckCapability(Capability):
    """Vérifie la complétude des données: % null par champ et couverture spatiale."""

    name = "completeness_check"
    description = (
        "Reports completeness metrics: null ratio per column and spatial coverage "
        "relative to an optional reference extent."
    )

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        columns: list[str] | None = None,
        reference_gdf: gpd.GeoDataFrame | None = None,
        null_threshold: float = 0.0,
        **_,
    ) -> gpd.GeoDataFrame:
        """
        Args:
            gdf:            Couche vecteur à analyser.
            columns:        Liste de colonnes à contrôler. Si None, toutes les colonnes
                            non-géométriques sont analysées.
            reference_gdf:  Couche de référence pour calculer la couverture spatiale.
                            Si fournie, la couverture = aire(gdf) / aire(reference_gdf).
            null_threshold: Seuil d'alerte pour le taux de null (0.0 à 1.0).
                            Les colonnes dépassant ce seuil sont marquées comme incomplètes.

        Returns:
            GeoDataFrame avec une ligne par colonne analysée + une ligne de synthèse
            spatiale si reference_gdf est fourni. Colonnes :
            - ``column``         : nom de la colonne (ou "geometry" pour la couverture spatiale).
            - ``total``          : nombre total de features.
            - ``null_count``     : nombre de valeurs nulles.
            - ``null_ratio``     : taux de null (0.0 à 1.0).
            - ``is_complete``    : True si null_ratio <= null_threshold.
            - ``coverage_ratio`` : couverture spatiale (null si non applicable).
            - ``geometry``       : None (pas de géométrie pour les stats).
        """
        import pandas as pd

        if columns is None:
            columns = [
                col
                for col in gdf.columns
                if col != gdf.geometry.name
            ]

        total = len(gdf)
        rows: list[dict[str, Any]] = []

        for col in columns:
            if col not in gdf.columns:
                rows.append(
                    {
                        "column": col,
                        "total": total,
                        "null_count": total,
                        "null_ratio": 1.0,
                        "is_complete": False,
                        "coverage_ratio": None,
                        "geometry": None,
                    }
                )
                continue

            null_count = int(gdf[col].isna().sum())
            null_ratio = null_count / total if total > 0 else 0.0
            rows.append(
                {
                    "column": col,
                    "total": total,
                    "null_count": null_count,
                    "null_ratio": round(null_ratio, 6),
                    "is_complete": null_ratio <= null_threshold,
                    "coverage_ratio": None,
                    "geometry": None,
                }
            )

        # Couverture spatiale
        if reference_gdf is not None and not gdf.empty and not reference_gdf.empty:
            try:
                ref_gdf = reference_gdf
                if gdf.crs is not None and ref_gdf.crs is not None:
                    if gdf.crs != ref_gdf.crs:
                        ref_gdf = ref_gdf.to_crs(gdf.crs)

                from shapely.ops import unary_union

                data_union = unary_union(gdf.geometry.dropna())
                ref_union = unary_union(ref_gdf.geometry.dropna())

                data_area = data_union.area
                ref_area = ref_union.area
                coverage = data_area / ref_area if ref_area > 0 else None

                rows.append(
                    {
                        "column": "_spatial_coverage",
                        "total": total,
                        "null_count": 0,
                        "null_ratio": 0.0,
                        "is_complete": coverage is not None and coverage >= (1.0 - null_threshold),
                        "coverage_ratio": round(coverage, 6) if coverage is not None else None,
                        "geometry": None,
                    }
                )
            except Exception:
                pass  # Spatial coverage is best-effort

        # P1 (beta-test 2026-04-24): a GeoDataFrame with only the geometry
        # column (and no reference_gdf) leaves ``rows`` empty. Constructing
        # ``GeoDataFrame([], geometry="geometry")`` raises
        # ``ValueError: Unknown column geometry`` because the underlying
        # DataFrame has no columns at all. Build an empty frame with the
        # full expected schema so callers can still introspect the contract.
        if not rows:
            return gpd.GeoDataFrame(
                {
                    "column": pd.Series([], dtype="object"),
                    "total": pd.Series([], dtype="int64"),
                    "null_count": pd.Series([], dtype="int64"),
                    "null_ratio": pd.Series([], dtype="float64"),
                    "is_complete": pd.Series([], dtype="bool"),
                    "coverage_ratio": pd.Series([], dtype="float64"),
                    "geometry": gpd.GeoSeries([], crs=gdf.crs),
                },
                geometry="geometry",
                crs=gdf.crs,
            )

        result = gpd.GeoDataFrame(rows, geometry="geometry", crs=gdf.crs)
        return result.reset_index(drop=True)

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "columns": {
                    "type": ["array", "null"],
                    "items": {"type": "string"},
                    "description": "Columns to analyse. Null = all non-geometry columns.",
                },
                "reference_gdf": {
                    "type": ["object", "null"],
                    "description": "Reference GeoDataFrame for spatial coverage calculation.",
                },
                "null_threshold": {
                    "type": "number",
                    "default": 0.0,
                    "minimum": 0.0,
                    "maximum": 1.0,
                    "description": "Null ratio threshold above which a column is flagged incomplete.",
                },
            },
        }
