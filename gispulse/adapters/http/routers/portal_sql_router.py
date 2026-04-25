"""
Portal SQL router — execute and export PostGIS SQL queries.
"""

from __future__ import annotations

import hmac
import json
import re
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel

from gispulse.adapters.http.rate_limit import limiter
from core.config import settings as cfg
from core.logging import get_logger

log = get_logger(__name__)

# SQL keywords forbidden in user SQL to prevent DDL/DCL via the SQL endpoint
_SQL_DDL_BLOCKLIST = re.compile(
    r"\b(DROP|ALTER|CREATE|TRUNCATE|GRANT|REVOKE|COPY|"
    r"pg_read_file|pg_write_file|lo_import|lo_export|"
    r"pg_terminate_backend|pg_cancel_backend|"
    r"set\s+role|reset\s+role)\b",
    re.IGNORECASE,
)


def _validate_sql_readonly(sql: str) -> None:
    """Reject SQL containing DDL/DCL keywords.

    Raises:
        HTTPException: If SQL contains forbidden keywords.
    """
    if _SQL_DDL_BLOCKLIST.search(sql):
        raise HTTPException(
            status_code=400,
            detail="SQL contains forbidden DDL/DCL keywords. Only SELECT queries are allowed.",
        )

router = APIRouter()


def _check_admin_key(x_admin_key: str | None) -> None:
    """Raise 403 if admin key is missing or does not match.

    When GISPULSE_SQL_ADMIN_KEY is not set, SQL endpoints are blocked
    entirely to prevent unauthenticated arbitrary SQL execution.
    """
    required = cfg.api.sql_admin_key
    if not required:
        raise HTTPException(
            status_code=403,
            detail="SQL endpoints are disabled. Set GISPULSE_SQL_ADMIN_KEY to enable.",
        )
    if not hmac.compare_digest(x_admin_key or "", required):
        raise HTTPException(status_code=403, detail="Forbidden: invalid or missing X-Admin-Key header.")


class SQLExecuteRequest(BaseModel):
    sql: str
    params: dict[str, str] = {}
    limit: int = 1000
    offset: int = 0


class SQLExecuteResponse(BaseModel):
    columns: list[str] = []
    rows: list[dict[str, Any]] = []
    total: int = 0
    error: str | None = None


class SQLExportRequest(BaseModel):
    sql: str
    params: dict[str, str] = {}
    format: str = "geojson"
    filename: str | None = None


def _render_and_clean(sql: str, params: dict[str, str]) -> str:
    from capabilities.postgis_sql import _safe_render
    return _safe_render(sql, params)


@router.post("/sql/execute", response_model=SQLExecuteResponse)
@limiter.limit("30/minute")
def sql_execute(
    request: Request,
    body: SQLExecuteRequest,
    x_admin_key: str | None = Header(default=None),
) -> SQLExecuteResponse:
    """Execute a SQL query with paginated results against PostGIS."""
    _check_admin_key(x_admin_key)

    dsn = cfg.database.postgis_dsn
    if not dsn:
        return SQLExecuteResponse(
            error="No PostGIS DSN configured. Set GISPULSE_POSTGIS_DSN.",
        )
    if not body.sql.strip():
        return SQLExecuteResponse(error="SQL query is empty.")

    try:
        _validate_sql_readonly(body.sql)

        from gispulse.adapters.http.dependencies import get_postgis_sqlalchemy_engine
        from sqlalchemy import text

        engine = get_postgis_sqlalchemy_engine(request)
        if engine is None:
            return SQLExecuteResponse(
                error="No PostGIS DSN configured. Set GISPULSE_POSTGIS_DSN.",
            )

        rendered = _render_and_clean(body.sql, body.params)

        count_sql = f"SELECT COUNT(*) FROM ({rendered}) AS _count_q"
        page_sql = f"SELECT * FROM ({rendered}) AS _page_q LIMIT {body.limit} OFFSET {body.offset}"

        with engine.connect() as conn:
            total_result = conn.execute(text(count_sql))
            total = total_result.scalar() or 0
            result = conn.execute(text(page_sql))
            columns = list(result.keys())
            raw_rows = result.fetchall()

        clean_rows: list[dict[str, Any]] = []
        for row in raw_rows:
            clean: dict[str, Any] = {}
            for k, v in zip(columns, row):
                if v is None:
                    clean[k] = None
                elif isinstance(v, (int, float, bool)):
                    clean[k] = v
                elif isinstance(v, bytes):
                    clean[k] = f"<binary {len(v)} bytes>"
                else:
                    clean[k] = str(v)
            clean_rows.append(clean)

        return SQLExecuteResponse(columns=columns, rows=clean_rows, total=total)

    except ImportError as exc:
        return SQLExecuteResponse(error=f"Missing dependency: {exc}")
    except ValueError as exc:
        return SQLExecuteResponse(error=f"Parameter error: {exc}")
    except Exception as exc:
        log.warning("SQL execute failed: %s", exc, exc_info=True)
        return SQLExecuteResponse(error=str(exc))


@router.post("/sql/export")
@limiter.limit("10/minute")
def sql_export(
    request: Request,
    body: SQLExportRequest,
    x_admin_key: str | None = Header(default=None),
):
    """Execute a SQL query and return results as a downloadable file."""
    import io

    from fastapi.responses import Response

    _check_admin_key(x_admin_key)

    dsn = cfg.database.postgis_dsn
    if not dsn:
        raise HTTPException(503, "No PostGIS DSN configured.")
    if not body.sql.strip():
        raise HTTPException(400, "SQL query is empty.")

    _validate_sql_readonly(body.sql)

    fmt = body.format.lower()
    if fmt not in {"geojson", "csv", "gpkg"}:
        raise HTTPException(400, f"Unsupported format '{fmt}'. Use: geojson, csv, gpkg")

    try:
        from gispulse.adapters.http.dependencies import get_postgis_sqlalchemy_engine
        from sqlalchemy import text

        engine = get_postgis_sqlalchemy_engine(request)
        if engine is None:
            raise HTTPException(503, "No PostGIS DSN configured.")

        rendered = _render_and_clean(body.sql, body.params)

        with engine.connect() as conn:
            result = conn.execute(text(rendered))
            columns = list(result.keys())
            raw_rows = result.fetchall()

    except Exception as exc:
        raise HTTPException(500, f"SQL execution failed: {exc}") from exc

    filename = body.filename or f"export_{uuid.uuid4().hex[:8]}"

    if fmt == "csv":
        import csv

        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(columns)
        for row in raw_rows:
            writer.writerow([str(v) if v is not None else "" for v in row])
        content = buf.getvalue().encode("utf-8")
        return Response(
            content=content,
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{filename}.csv"'},
        )

    elif fmt == "geojson":
        rows_as_dicts = [dict(zip(columns, row)) for row in raw_rows]
        geom_col = next(
            (c for c in columns if c.lower() in {"geom", "geometry", "the_geom", "wkb_geometry"}),
            None,
        )
        if geom_col:
            import geopandas as gpd_sql
            import pandas as pd
            from shapely import wkb, wkt

            def _parse_geom(g: Any):
                if g is None:
                    return None
                if isinstance(g, bytes):
                    try:
                        return wkb.loads(g)
                    except Exception:
                        return None
                try:
                    return wkt.loads(str(g))
                except Exception:
                    return None

            df = pd.DataFrame(rows_as_dicts)
            df[geom_col] = df[geom_col].apply(_parse_geom)
            gdf_out = gpd_sql.GeoDataFrame(df, geometry=geom_col, crs="EPSG:4326")
            from gispulse.adapters.http.layer_utils import sanitize_datetime_columns
            gdf_out = sanitize_datetime_columns(gdf_out)
            content = gdf_out.to_json().encode("utf-8")
        else:
            features = [{"type": "Feature", "geometry": None, "properties": d} for d in rows_as_dicts]
            fc = {"type": "FeatureCollection", "features": features}
            content = json.dumps(fc).encode("utf-8")

        return Response(
            content=content,
            media_type="application/geo+json",
            headers={"Content-Disposition": f'attachment; filename="{filename}.geojson"'},
        )

    else:  # gpkg
        import tempfile as _tempfile

        rows_as_dicts = [dict(zip(columns, row)) for row in raw_rows]
        geom_col = next(
            (c for c in columns if c.lower() in {"geom", "geometry", "the_geom", "wkb_geometry"}),
            None,
        )

        with _tempfile.NamedTemporaryFile(suffix=".gpkg", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            import pandas as pd

            df = pd.DataFrame(rows_as_dicts)
            if geom_col:
                import geopandas as gpd_sql
                from shapely import wkb, wkt

                def _parse_geom2(g: Any):
                    if g is None:
                        return None
                    if isinstance(g, bytes):
                        try:
                            return wkb.loads(g)
                        except Exception:
                            return None
                    try:
                        return wkt.loads(str(g))
                    except Exception:
                        return None

                df[geom_col] = df[geom_col].apply(_parse_geom2)
                gdf_out = gpd_sql.GeoDataFrame(df, geometry=geom_col, crs="EPSG:4326")
                gdf_out.to_file(tmp_path, layer="result", driver="GPKG")
            else:
                df.to_csv(tmp_path.replace(".gpkg", ".csv"), index=False)

            with open(tmp_path, "rb") as f:
                content = f.read()
        finally:
            Path(tmp_path).unlink(missing_ok=True)

        return Response(
            content=content,
            media_type="application/geopackage+sqlite3",
            headers={"Content-Disposition": f'attachment; filename="{filename}.gpkg"'},
        )
