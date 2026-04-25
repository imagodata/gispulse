"""
PostGIS SQL capability for GISPulse.

Exécute des requêtes SQL spatiales directement sur un PostGIS configuré.
Utilisé pour les opérations qui bénéficient du moteur spatial PostGIS :
  - topologie, ST_MakeValid, ST_IsValid
  - 3D (ST_3DDistance, ST_Extrude)
  - simplification massive avec index R-Tree
  - nearest neighbour KNN (geom <-> geom)
  - extensions: pgRouting, PostGIS Raster
"""

from __future__ import annotations

from string import Formatter
from typing import Any

import geopandas as gpd

from capabilities.base import Capability
from capabilities.registry import register


@register
class PostGISSQLCapability(Capability):
    """Exécute un template SQL spatial sur PostGIS et retourne un GeoDataFrame."""

    name = "postgis_sql"
    description = "Executes a parameterized SQL query on PostGIS and returns the result layer."

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        dsn: str = "",
        sql: str = "",
        params: dict[str, Any] | None = None,
        geom_col: str = "geom",
        input_table: str | None = None,
        input_schema: str = "public",
        **_,
    ) -> gpd.GeoDataFrame:
        """
        Args:
            gdf:           GeoDataFrame d'entrée — si `input_table` est None,
                           il est chargé en table temporaire `_gispulse_input`.
            dsn:           SQLAlchemy DSN PostGIS, ex: 'postgresql://user:pw@host/db'.
            sql:           Requête SQL. Peut contenir des placeholders {param}.
                           La table d'entrée est accessible via {input_table}.
            params:        Dict de valeurs pour les placeholders dans `sql`.
            geom_col:      Nom de la colonne géométrie dans le résultat.
            input_table:   Si fourni, utilise cette table comme source au lieu du GDF.
            input_schema:  Schéma de `input_table`.
        """
        if not dsn:
            raise ValueError("PostGISSQLCapability requires a 'dsn' parameter.")
        if not sql:
            raise ValueError("PostGISSQLCapability requires a 'sql' parameter.")

        try:
            from sqlalchemy import create_engine, text
        except ImportError as exc:
            raise ImportError("PostGISSQLCapability requires 'sqlalchemy'.") from exc

        # Normalise le DSN
        if dsn.startswith("postgresql://") and "+psycopg2" not in dsn:
            dsn = dsn.replace("postgresql://", "postgresql+psycopg2://", 1)

        engine = create_engine(dsn, future=True)
        params = params or {}

        try:
            if input_table is None:
                # Charge le GDF en table temporaire
                gdf.to_postgis(
                    name="_gispulse_input",
                    con=engine,
                    schema=input_schema,
                    if_exists="replace",
                    index=False,
                    temporary=True,
                )
                params.setdefault("input_table", f"{input_schema}._gispulse_input")
            else:
                params.setdefault("input_table", f"{input_schema}.{input_table}")

            # SECURITY: _safe_render only validates {key} parameter values.
            # The SQL template body itself (rule.config["sql"]) is user-provided
            # and can contain arbitrary SQL. This capability trusts the rule author.
            #
            # RBAC enforcement is MANDATORY:
            #   - Only users with "rules:write" scope (admin role) may create
            #     or modify rules using the SQLCapability.
            #   - The HTTP layer (rules_router) must enforce this before
            #     persisting any rule with capability="postgis_sql".
            #   - All rule creation/modification with this capability MUST be
            #     logged to the audit trail for review.
            rendered_sql = _safe_render(sql, params)

            result = gpd.read_postgis(rendered_sql, con=engine, geom_col=geom_col)
        finally:
            engine.dispose()

        return result.reset_index(drop=True)

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "dsn": {"type": "string", "description": "SQLAlchemy PostGIS DSN."},
                "sql": {"type": "string", "description": "SQL query with optional {placeholders}."},
                "params": {"type": "object", "description": "Values for SQL placeholders."},
                "geom_col": {"type": "string", "default": "geom"},
                "input_table": {"type": ["string", "null"]},
                "input_schema": {"type": "string", "default": "public"},
            },
            "required": ["dsn", "sql"],
        }


from core.sql_safety import validate_identifier as _validate_identifier


def _safe_render(sql: str, params: dict[str, Any]) -> str:
    """Substitute {key} placeholders in SQL for allowed scalar types only.

    String values are validated as safe SQL identifiers (alphanumeric, underscores,
    dots only). Numeric values are passed through directly. Non-scalar values
    are rejected.
    """
    safe: dict[str, Any] = {}
    for key, value in params.items():
        if isinstance(value, str):
            safe[key] = _validate_identifier(value)
        elif isinstance(value, (int, float)):
            safe[key] = value

    # Vérifie que tous les champs requis par le SQL sont présents
    required = {fname for _, fname, _, _ in Formatter().parse(sql) if fname}
    missing = required - set(safe.keys())
    if missing:
        raise ValueError(f"SQL template missing parameters: {missing}")

    return sql.format(**safe)
