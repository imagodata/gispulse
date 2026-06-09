"""GeoParquet to PMTiles batch writer backed by DuckDB spatial."""

from __future__ import annotations

import json
import math
import os
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from gispulse.core.fetchers import DUCKDB_SCAN_KEY
from gispulse.core.logging import get_logger
from gispulse.core.plugin_model import (
    AccessProtocol,
    Payload,
    SourceResult,
    WriteReport,
    WriteSpec,
)
from gispulse.core.sources import PROTOCOLS, ProtocolRegistry
from gispulse.persistence.duckdb_engine import DuckDBSession
from gispulse.persistence.duckdb_relations import parquet_scan

log = get_logger(__name__)

_WEB_MERCATOR_LIMIT = 85.0511287798066
_WEB_MERCATOR_HALF_WORLD = 20037508.3427892
_GEOM_NAMES = {"geom", "geometry", "wkb_geometry", "the_geom", "shape", "__wkb"}
_TEMP_FEATURES = "__gispulse_pmtiles_features"


@dataclass(frozen=True)
class _Column:
    name: str
    type_name: str


@dataclass(frozen=True)
class _PreparedSource:
    relation: str
    geometry_column: str
    source_crs: str | None


def write_pmtiles(
    source: str | Path | SourceResult,
    out_path: str | Path,
    *,
    layer: str,
    min_zoom: int,
    max_zoom: int,
    geometry_column: str | None = None,
    source_crs: str | None = None,
    simplify_tolerance: float | None = None,
    extent: int = 4096,
    buffer: int = 256,
    session: DuckDBSession | None = None,
) -> WriteReport:
    """Write static PMTiles from a GeoParquet source with DuckDB ``ST_AsMVT``.

    ``source`` may be a local GeoParquet path or a vector ``SourceResult``
    carrying either a materialized path or a DuckDB scan expression under
    ``metadata['duckdb_scan']``. Geometries are projected to EPSG:3857 for
    MVT generation; pass ``source_crs`` when the input relation does not
    carry CRS metadata.
    """
    _validate_options(
        layer=layer,
        min_zoom=min_zoom,
        max_zoom=max_zoom,
        simplify_tolerance=simplify_tolerance,
        extent=extent,
        buffer=buffer,
    )
    destination = Path(out_path)
    destination.parent.mkdir(parents=True, exist_ok=True)

    owns_session = session is None
    duck = session or DuckDBSession()
    if owns_session:
        duck.open()

    try:
        report = _write_with_session(
            duck,
            source,
            destination,
            layer=layer,
            min_zoom=min_zoom,
            max_zoom=max_zoom,
            geometry_column=geometry_column,
            source_crs=source_crs,
            simplify_tolerance=simplify_tolerance,
            extent=extent,
            buffer=buffer,
        )
    finally:
        if owns_session:
            duck.close()
    return report


class PmtilesWriter:
    """Protocol writer for ``AccessProtocol.PMTILES`` static tile artifacts."""

    protocol = AccessProtocol.PMTILES

    def write(self, result: SourceResult, spec: WriteSpec) -> WriteReport:
        if spec.protocol is not AccessProtocol.PMTILES:
            raise ValueError(f"PmtilesWriter cannot handle protocol {spec.protocol.value!r}")
        layer = spec.layer or _required_option(spec, "layer")
        min_zoom = int(_required_option(spec, "min_zoom"))
        max_zoom = int(_required_option(spec, "max_zoom"))
        return write_pmtiles(
            result,
            spec.destination,
            layer=layer,
            min_zoom=min_zoom,
            max_zoom=max_zoom,
            geometry_column=_optional_str(spec.options.get("geometry_column")),
            source_crs=_optional_str(spec.options.get("source_crs")),
            simplify_tolerance=_optional_float(
                spec.options.get("simplify_tolerance", spec.options.get("simplification"))
            ),
            extent=int(spec.options.get("extent", 4096)),
            buffer=int(spec.options.get("buffer", 256)),
        )


def register_pmtiles_writer(
    registry: ProtocolRegistry | None = None, *, override: bool = True
) -> int:
    """Register the PMTiles writer in a protocol registry."""
    target = registry if registry is not None else PROTOCOLS
    target.register(PmtilesWriter(), override=override)
    log.info("pmtiles_writer_registered")
    return 1


def _write_with_session(
    session: DuckDBSession,
    source: str | Path | SourceResult,
    out_path: Path,
    *,
    layer: str,
    min_zoom: int,
    max_zoom: int,
    geometry_column: str | None,
    source_crs: str | None,
    simplify_tolerance: float | None,
    extent: int,
    buffer: int,
) -> WriteReport:
    prepared = _prepare_source(
        session,
        source,
        geometry_column=geometry_column,
        source_crs=source_crs,
    )
    columns = _describe_relation(session, prepared.relation)
    geometry = _resolve_geometry_column(columns, prepared.geometry_column)
    feature_count = _materialize_features(session, prepared, columns, geometry)
    if feature_count == 0:
        raise ValueError("PMTiles source contains no geometries")

    bounds_4326 = _features_bounds_4326(session)
    field_types = _metadata_field_types(columns, geometry.name)
    tile_coords = _coverage_tiles(bounds_4326, min_zoom, max_zoom)
    tmp_path = out_path.with_name(f".{out_path.name}.tmp")
    if tmp_path.exists():
        tmp_path.unlink()

    tile_count = 0
    try:
        tile_count = _write_archive(
            session,
            tmp_path,
            tile_coords,
            layer=layer,
            bounds_4326=bounds_4326,
            field_types=field_types,
            simplify_tolerance=simplify_tolerance,
            extent=extent,
            buffer=buffer,
        )
        if tile_count == 0:
            raise ValueError("PMTiles source produced no non-empty MVT tiles")
        os.replace(tmp_path, out_path)
    finally:
        _cleanup_temp_relation(session)
        if tmp_path.exists():
            tmp_path.unlink()

    detail = json.dumps(
        {
            "features_read": feature_count,
            "tiles_written": tile_count,
            "min_zoom": min_zoom,
            "max_zoom": max_zoom,
            "layer": layer,
        },
        sort_keys=True,
    )
    return WriteReport(
        destination=str(out_path),
        rows_written=tile_count,
        created=True,
        detail=detail,
    )


def _validate_options(
    *,
    layer: str,
    min_zoom: int,
    max_zoom: int,
    simplify_tolerance: float | None,
    extent: int,
    buffer: int,
) -> None:
    if not layer:
        raise ValueError("layer is required")
    if not (0 <= min_zoom <= max_zoom <= 31):
        raise ValueError("zoom range must satisfy 0 <= min_zoom <= max_zoom <= 31")
    if extent <= 0:
        raise ValueError("extent must be positive")
    if buffer < 0:
        raise ValueError("buffer must be non-negative")
    if simplify_tolerance is not None and simplify_tolerance < 0:
        raise ValueError("simplify_tolerance must be non-negative")


def _prepare_source(
    session: DuckDBSession,
    source: str | Path | SourceResult,
    *,
    geometry_column: str | None,
    source_crs: str | None,
) -> _PreparedSource:
    if isinstance(source, SourceResult):
        if source.payload is not Payload.VECTOR:
            raise ValueError("PMTiles writer expects a vector SourceResult")
        return _prepare_source_result(
            session,
            source,
            geometry_column=geometry_column,
            source_crs=source_crs,
        )
    return _PreparedSource(
        relation=parquet_scan(Path(source)),
        geometry_column=geometry_column or "",
        source_crs=source_crs,
    )


def _prepare_source_result(
    session: DuckDBSession,
    result: SourceResult,
    *,
    geometry_column: str | None,
    source_crs: str | None,
) -> _PreparedSource:
    scan = result.metadata.get(DUCKDB_SCAN_KEY)
    result_crs = source_crs or result.crs
    if isinstance(scan, str) and scan:
        return _PreparedSource(
            relation=scan,
            geometry_column=geometry_column or "",
            source_crs=result_crs,
        )
    if isinstance(result.data, (str, Path)):
        return _PreparedSource(
            relation=parquet_scan(Path(result.data)),
            geometry_column=geometry_column or "",
            source_crs=result_crs,
        )
    if result.data is None and result.reference:
        return _PreparedSource(
            relation=parquet_scan(Path(result.reference)),
            geometry_column=geometry_column or "",
            source_crs=result_crs,
        )

    try:
        import geopandas as gpd
    except Exception as exc:  # pragma: no cover - geopandas is a base dependency
        raise TypeError("GeoDataFrame SourceResult data requires geopandas") from exc

    if isinstance(result.data, gpd.GeoDataFrame):
        table = "__gispulse_pmtiles_source_gdf"
        session.conn.execute(f"DROP VIEW IF EXISTS {_quote_identifier(table)}")
        session.conn.execute(f"DROP TABLE IF EXISTS {_quote_identifier(table)}")
        session.register_gdf(table, result.data)
        gdf_crs = str(result.data.crs) if result.data.crs else None
        return _PreparedSource(
            relation=_quote_identifier(table),
            geometry_column=geometry_column or "__wkb",
            source_crs=result_crs or gdf_crs,
        )
    raise TypeError(
        f"cannot write PMTiles from SourceResult.data of type {type(result.data).__name__}"
    )


def _describe_relation(session: DuckDBSession, relation: str) -> list[_Column]:
    rows = session.conn.execute(f"DESCRIBE SELECT * FROM {relation}").fetchall()
    return [_Column(str(row[0]), str(row[1]).upper()) for row in rows]


def _resolve_geometry_column(columns: Sequence[_Column], requested: str | None) -> _Column:
    if requested:
        for column in columns:
            if column.name == requested:
                return column
        raise ValueError(f"geometry column {requested!r} not found")
    for column in columns:
        if column.type_name.startswith("GEOMETRY"):
            return column
    for column in columns:
        if column.name.lower() in _GEOM_NAMES:
            return column
    raise ValueError("could not infer geometry column")


def _materialize_features(
    session: DuckDBSession,
    prepared: _PreparedSource,
    columns: Sequence[_Column],
    geometry: _Column,
) -> int:
    _cleanup_temp_relation(session)
    properties = [column for column in columns if column.name != geometry.name]
    select_properties = ", ".join(_property_select_expr(column) for column in properties)
    if select_properties:
        select_properties += ", "
    geom_expr = _geometry_expression(geometry)
    geom_3857 = _transform_expression(geom_expr, "EPSG:3857", prepared.source_crs)
    geom_4326 = _transform_expression(geom_expr, "EPSG:4326", prepared.source_crs)
    session.conn.execute(
        f"""
        CREATE TEMP TABLE {_quote_identifier(_TEMP_FEATURES)} AS
        SELECT
            {select_properties}
            {geom_3857} AS __geom_3857,
            {geom_4326} AS __geom_4326
        FROM {prepared.relation}
        WHERE {geom_expr} IS NOT NULL
        """
    )
    row = session.conn.execute(
        f"SELECT COUNT(*) FROM {_quote_identifier(_TEMP_FEATURES)}"
    ).fetchone()
    return int(row[0]) if row else 0


# Types de propriete encodables tels quels par ``ST_AsMVT``.
_MVT_DIRECT_TYPES = frozenset({"VARCHAR", "FLOAT", "DOUBLE", "INTEGER", "BIGINT", "BOOLEAN"})
_MVT_INTEGER_TYPES = frozenset(
    {"TINYINT", "SMALLINT", "UTINYINT", "USMALLINT", "UINTEGER", "UBIGINT", "HUGEINT", "UHUGEINT"}
)
_MVT_DOUBLE_TYPES = frozenset({"DECIMAL", "NUMERIC", "REAL"})


def _property_select_expr(column: _Column) -> str:
    """Expression SELECT d'une propriete, castee vers un type encodable MVT.

    ``ST_AsMVT`` n'accepte que VARCHAR / FLOAT / DOUBLE / INTEGER / BIGINT / BOOLEAN.
    Les autres types (DATE, TIMESTAMP, UUID, listes, …) sont caste : les entiers
    elargis vers BIGINT, les decimaux vers DOUBLE, le reste vers VARCHAR. Sans cela
    une colonne date/timestamp fait echouer l'encodage de la tuile.
    """
    identifier = _quote_identifier(column.name)
    base = column.type_name.upper().split("(", 1)[0].strip()
    if base in _MVT_DIRECT_TYPES:
        return identifier
    if base in _MVT_INTEGER_TYPES:
        return f"CAST({identifier} AS BIGINT) AS {identifier}"
    if base in _MVT_DOUBLE_TYPES:
        return f"CAST({identifier} AS DOUBLE) AS {identifier}"
    return f"CAST({identifier} AS VARCHAR) AS {identifier}"


def _cleanup_temp_relation(session: DuckDBSession) -> None:
    session.conn.execute(f"DROP TABLE IF EXISTS {_quote_identifier(_TEMP_FEATURES)}")


def _geometry_expression(geometry: _Column) -> str:
    identifier = _quote_identifier(geometry.name)
    if geometry.type_name.startswith("GEOMETRY"):
        return identifier
    if geometry.type_name == "BLOB" or geometry.name.lower() in {"__wkb", "wkb_geometry"}:
        return f"ST_GeomFromWKB({identifier})"
    raise ValueError(
        f"geometry column {geometry.name!r} has unsupported DuckDB type {geometry.type_name!r}"
    )


def _transform_expression(geom_expr: str, target_crs: str, source_crs: str | None) -> str:
    target = _sql_literal(target_crs)
    if source_crs:
        return f"ST_Transform({geom_expr}, {_sql_literal(source_crs)}, {target}, true)"
    return f"ST_Transform({geom_expr}, {target}, true)"


def _features_bounds_4326(session: DuckDBSession) -> tuple[float, float, float, float]:
    row = session.conn.execute(
        f"""
        SELECT
            ST_XMin(bounds) AS minx,
            ST_YMin(bounds) AS miny,
            ST_XMax(bounds) AS maxx,
            ST_YMax(bounds) AS maxy
        FROM (
            -- ST_Envelope_Agg agrege l'enveloppe de TOUTES les features
            -- (nom compatible DuckDB 1.0+ ; ST_Extent_Agg existe aussi en 1.5).
            -- ST_Extent est scalaire (bbox d'une seule geometrie) : dans un
            -- SELECT sans GROUP BY, .fetchone() ne lisait que la 1re feature,
            -- effondrant les bounds de couverture sur un point.
            SELECT ST_Envelope_Agg(__geom_4326) AS bounds
            FROM {_quote_identifier(_TEMP_FEATURES)}
        )
        """
    ).fetchone()
    if row is None or row[0] is None:
        raise ValueError("PMTiles source has no finite bounds")
    bounds = tuple(float(value) for value in row)
    minx, miny, maxx, maxy = bounds
    return (
        max(-180.0, min(180.0, minx)),
        max(-_WEB_MERCATOR_LIMIT, min(_WEB_MERCATOR_LIMIT, miny)),
        max(-180.0, min(180.0, maxx)),
        max(-_WEB_MERCATOR_LIMIT, min(_WEB_MERCATOR_LIMIT, maxy)),
    )


def _metadata_field_types(columns: Sequence[_Column], geometry_name: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for column in columns:
        if column.name == geometry_name:
            continue
        fields[column.name] = _tilejson_field_type(column.type_name)
    return fields


def _tilejson_field_type(type_name: str) -> str:
    if any(token in type_name for token in ("INT", "DOUBLE", "FLOAT", "DECIMAL", "REAL")):
        return "Number"
    if "BOOL" in type_name:
        return "Boolean"
    return "String"


def _coverage_tiles(
    bounds: tuple[float, float, float, float],
    min_zoom: int,
    max_zoom: int,
) -> list[tuple[int, int, int, int]]:
    from pmtiles.tile import zxy_to_tileid

    min_lon, min_lat, max_lon, max_lat = bounds
    tiles: list[tuple[int, int, int, int]] = []
    for z in range(min_zoom, max_zoom + 1):
        min_x, max_y = _lonlat_to_tile(min_lon, min_lat, z)
        max_x, min_y = _lonlat_to_tile(max_lon, max_lat, z)
        if max_x < min_x:
            min_x, max_x = max_x, min_x
        if max_y < min_y:
            min_y, max_y = max_y, min_y
        for x in range(min_x, max_x + 1):
            for y in range(min_y, max_y + 1):
                tiles.append((zxy_to_tileid(z, x, y), z, x, y))
    return sorted(tiles)


def _lonlat_to_tile(lon: float, lat: float, z: int) -> tuple[int, int]:
    lat = max(-_WEB_MERCATOR_LIMIT, min(_WEB_MERCATOR_LIMIT, lat))
    lon = max(-180.0, min(180.0, lon))
    n = 1 << z
    x = int((lon + 180.0) / 360.0 * n)
    lat_rad = math.radians(lat)
    y = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)
    return (max(0, min(n - 1, x)), max(0, min(n - 1, y)))


_TEMP_TILES = "__gispulse_pmtiles_tiles"


def _write_archive(
    session: DuckDBSession,
    out_path: Path,
    tile_coords: Sequence[tuple[int, int, int, int]],
    *,
    layer: str,
    bounds_4326: tuple[float, float, float, float],
    field_types: dict[str, str],
    simplify_tolerance: float | None,
    extent: int,
    buffer: int,
) -> int:
    from pmtiles.tile import Compression, TileType, zxy_to_tileid
    from pmtiles.writer import Writer

    # Encodage MVT en UNE seule requete groupee (jointure spatiale features x tuiles
    # -> ST_AsMVTGeom -> GROUP BY tuile -> ST_AsMVT) au lieu d'un appel ST_AsMVT par
    # tuile. La boucle par tuile (N appels successifs sur la meme connexion DuckDB)
    # corrompt la memoire de l'extension spatiale au-dela de quelques centaines de
    # features lignes (segfault / "tile width and height must be positive" non
    # deterministe). La forme groupee evite cette accumulation et est nettement plus
    # rapide.
    geom_expr = "features.__geom_3857"
    if simplify_tolerance is not None and simplify_tolerance > 0:
        geom_expr = f"ST_SimplifyPreserveTopology({geom_expr}, {float(simplify_tolerance)})"

    tiles_table = _quote_identifier(_TEMP_TILES)
    session.conn.execute(f"DROP TABLE IF EXISTS {tiles_table}")
    session.conn.execute(
        f"CREATE TEMP TABLE {tiles_table} "
        "(z INTEGER, x INTEGER, y INTEGER, "
        "xmin DOUBLE, ymin DOUBLE, xmax DOUBLE, ymax DOUBLE)"
    )
    tile_rows = []
    for _tile_id, z, x, y in tile_coords:
        xmin, ymin, xmax, ymax = _tile_bounds_3857(z, x, y)
        tile_rows.append((z, x, y, xmin, ymin, xmax, ymax))
    if not tile_rows:
        return 0
    session.conn.executemany(f"INSERT INTO {tiles_table} VALUES (?, ?, ?, ?, ?, ?, ?)", tile_rows)

    box_expr = "ST_MakeBox2D(ST_Point(tiles.xmin, tiles.ymin), ST_Point(tiles.xmax, tiles.ymax))"
    struct_fields = ", ".join(
        f"{_quote_identifier(name)}: {_quote_identifier(name)}" for name in field_types
    )
    feature_struct = "{" + (struct_fields + ", " if struct_fields else "") + "geom: geom}"

    sql = f"""
        WITH mvtgeom AS (
            SELECT
                tiles.z AS __z,
                tiles.x AS __x,
                tiles.y AS __y,
                ST_AsMVTGeom(
                    {geom_expr},
                    {box_expr},
                    {int(extent)},
                    {int(buffer)},
                    true
                ) AS geom,
                features.* EXCLUDE (__geom_3857, __geom_4326)
            FROM {tiles_table} AS tiles
            JOIN {_quote_identifier(_TEMP_FEATURES)} AS features
              ON ST_Intersects(features.__geom_3857, {box_expr})
        )
        SELECT
            __z, __x, __y,
            ST_AsMVT({feature_struct}, {_sql_literal(layer)}, {int(extent)}, 'geom') AS tile
        FROM mvtgeom
        WHERE geom IS NOT NULL
        GROUP BY __z, __x, __y
    """
    result = session.conn.execute(sql).fetchall()
    session.conn.execute(f"DROP TABLE IF EXISTS {tiles_table}")

    encoded: list[tuple[int, bytes]] = []
    for z, x, y, tile in result:
        if tile is None:
            continue
        data = bytes(tile)
        if data:
            encoded.append((zxy_to_tileid(int(z), int(x), int(y)), data))
    if not encoded:
        return 0
    encoded.sort(key=lambda item: item[0])

    with out_path.open("wb") as fh:
        writer = Writer(fh)
        for tile_id, data in encoded:
            writer.write_tile(tile_id, data)
        with _deterministic_gzip():
            writer.finalize(
                _pmtiles_header(bounds_4326, Compression.NONE, TileType.MVT),
                _pmtiles_metadata(layer, bounds_4326, field_types),
            )
    return len(encoded)


def _mvt_tile(
    session: DuckDBSession,
    z: int,
    x: int,
    y: int,
    *,
    layer: str,
    simplify_tolerance: float | None,
    extent: int,
    buffer: int,
) -> bytes | None:
    xmin, ymin, xmax, ymax = _tile_bounds_3857(z, x, y)
    geom_expr = "__geom_3857"
    if simplify_tolerance is not None and simplify_tolerance > 0:
        geom_expr = f"ST_SimplifyPreserveTopology({geom_expr}, {float(simplify_tolerance)})"
    sql = f"""
        WITH bounds AS (
            SELECT ST_MakeBox2D(
                ST_Point({xmin}, {ymin}),
                ST_Point({xmax}, {ymax})
            ) AS geom
        ),
        raw_mvtgeom AS (
            SELECT
                ST_AsMVTGeom(
                    {geom_expr},
                    bounds.geom,
                    {int(extent)},
                    {int(buffer)},
                    true
                ) AS geom,
                features.* EXCLUDE (__geom_3857, __geom_4326)
            FROM {_quote_identifier(_TEMP_FEATURES)} AS features, bounds
            WHERE ST_Intersects(features.__geom_3857, bounds.geom)
        )
        -- ST_AsMVT ignore nativement les lignes a geometrie NULL : pas besoin
        -- d'une CTE intermediaire de filtre. Le filtre `WHERE geom IS NOT NULL`
        -- declenchait un SIGBUS DuckDB 1.5.3 (ST_AsMVT sur MULTILINESTRING -> NULL
        -- a bas zoom + colonnes de proprietes jointes).
        SELECT ST_AsMVT(
            raw_mvtgeom,
            {_sql_literal(layer)},
            {int(extent)},
            'geom'
        ) AS tile
        FROM raw_mvtgeom
    """
    row = session.conn.execute(sql).fetchone()
    if row is None or row[0] is None:
        return None
    tile = bytes(row[0])
    return tile or None


def _tile_bounds_3857(z: int, x: int, y: int) -> tuple[float, float, float, float]:
    n = 2.0**z
    xmin = (x / n) * 2 * _WEB_MERCATOR_HALF_WORLD - _WEB_MERCATOR_HALF_WORLD
    xmax = ((x + 1) / n) * 2 * _WEB_MERCATOR_HALF_WORLD - _WEB_MERCATOR_HALF_WORLD
    ymax = _WEB_MERCATOR_HALF_WORLD - (y / n) * 2 * _WEB_MERCATOR_HALF_WORLD
    ymin = _WEB_MERCATOR_HALF_WORLD - ((y + 1) / n) * 2 * _WEB_MERCATOR_HALF_WORLD
    return (xmin, ymin, xmax, ymax)


def _pmtiles_header(
    bounds_4326: tuple[float, float, float, float],
    compression: Any,
    tile_type: Any,
) -> dict[str, Any]:
    min_lon, min_lat, max_lon, max_lat = bounds_4326
    center_lon = (min_lon + max_lon) / 2.0
    center_lat = (min_lat + max_lat) / 2.0
    return {
        "tile_compression": compression,
        "tile_type": tile_type,
        "min_lon_e7": round(min_lon * 10_000_000),
        "min_lat_e7": round(min_lat * 10_000_000),
        "max_lon_e7": round(max_lon * 10_000_000),
        "max_lat_e7": round(max_lat * 10_000_000),
        "center_lon_e7": round(center_lon * 10_000_000),
        "center_lat_e7": round(center_lat * 10_000_000),
    }


def _pmtiles_metadata(
    layer: str,
    bounds_4326: tuple[float, float, float, float],
    fields: dict[str, str],
) -> dict[str, Any]:
    min_lon, min_lat, max_lon, max_lat = bounds_4326
    center_lon = (min_lon + max_lon) / 2.0
    center_lat = (min_lat + max_lat) / 2.0
    return {
        "name": layer,
        "type": "overlay",
        "version": "1",
        "scheme": "xyz",
        "bounds": f"{min_lon},{min_lat},{max_lon},{max_lat}",
        "center": f"{center_lon},{center_lat},0",
        "vector_layers": [{"id": layer, "fields": fields}],
    }


@contextmanager
def _deterministic_gzip() -> Iterator[None]:
    import gzip

    original = gzip.compress

    def compress(data: bytes, compresslevel: int = 9, *, mtime: int | None = None) -> bytes:
        return original(data, compresslevel=compresslevel, mtime=0)

    gzip.compress = compress  # type: ignore[assignment]
    try:
        yield
    finally:
        gzip.compress = original  # type: ignore[assignment]


def _required_option(spec: WriteSpec, name: str) -> Any:
    try:
        value = spec.options[name]
    except KeyError:
        raise ValueError(f"WriteSpec.options[{name!r}] is required for PMTiles writes") from None
    if value is None or value == "":
        raise ValueError(f"WriteSpec.options[{name!r}] is required for PMTiles writes")
    return value


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text or None


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def _quote_identifier(identifier: str) -> str:
    if not identifier or "\x00" in identifier:
        raise ValueError("unsafe SQL identifier")
    return '"' + identifier.replace('"', '""') + '"'


def _sql_literal(value: str) -> str:
    if "\x00" in value:
        raise ValueError("SQL literals cannot contain null bytes")
    return "'" + value.replace("'", "''") + "'"
