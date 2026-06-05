"""Safe DuckDB relation builders for Parquet/R2 and metric spatial joins."""

from __future__ import annotations

import asyncio
import hashlib
import math
import tempfile
import threading
from pathlib import Path
from urllib.parse import unquote, urlparse

from gispulse.core.fetchers.http_file import HttpFileFetcher
from gispulse.core.plugin_model import AccessProtocol, AccessSpec, FetchMode
from gispulse.persistence.storage import StorageError, create_storage

_PARQUET_SCAN_PREFIX = "read_parquet("
_SAFE_RELATION_CHARS = set(
    "abcdefghijklmnopqrstuvwxyz"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "0123456789"
    "_ ."
)
_SAFE_IDENTIFIER_CHARS = set(
    "abcdefghijklmnopqrstuvwxyz"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "0123456789"
    "_ "
)


def parquet_scan(uri: str | Path, *, union_by_name: bool = True) -> str:
    """Return a safe DuckDB ``read_parquet`` relation expression.

    The expression is only built, never executed. ``uri`` may be a local path
    or an ``s3://`` R2/S3 URI; credentials remain owned by ``DuckDBSession``.
    """
    return (
        f"read_parquet({_sql_literal(str(uri))}, "
        f"union_by_name={'true' if union_by_name else 'false'})"
    )


def materialize_remote_gpkg(
    uri: str | Path,
    *,
    layer: str | None = None,
    cache_dir: str | Path | None = None,
) -> Path:
    """Materialize a remote GeoPackage to local cache and return its path.

    Local paths are returned as-is after existence and optional layer checks.
    ``http(s)://`` downloads reuse ``HttpFileFetcher``. ``s3://`` downloads use
    the configured storage backend, so S3/R2 credentials stay centralized.
    """
    uri_text = str(uri)
    parsed = urlparse(uri_text)

    if parsed.scheme in ("", "file"):
        path = Path(unquote(parsed.path) if parsed.scheme == "file" else uri_text)
        if not path.exists():
            raise FileNotFoundError(f"GPKG file not found: {path}")
        _validate_gpkg_layer(path, layer)
        return path

    if parsed.scheme not in ("http", "https", "s3"):
        raise ValueError(f"Unsupported GPKG URI scheme: {parsed.scheme!r}")

    dest = _cached_gpkg_path(uri_text, cache_dir)
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        if parsed.scheme in ("http", "https"):
            access = AccessSpec(
                protocol=AccessProtocol.DOWNLOAD,
                endpoint=uri_text,
                params={"local_path": str(dest), "layer": layer} if layer else {"local_path": str(dest)},
            )
            HttpFileFetcher().fetch(access, mode=FetchMode.MATERIALIZE)
        else:
            _download_s3_uri(parsed, dest)

    _validate_gpkg_layer(dest, layer)
    return dest


def st_dwithin_join_sql(
    left: str,
    right: str,
    *,
    distance_m: float,
    left_geom: str = "geom",
    right_geom: str = "geom",
    select: str = "*",
) -> str:
    """Build a metric ``ST_DWithin`` join SQL statement for projected CRS data."""
    distance = float(distance_m)
    if not math.isfinite(distance) or distance < 0:
        raise ValueError("distance_m must be a finite non-negative number")

    left_relation = _relation_sql(left)
    right_relation = _relation_sql(right)
    left_geom_sql = _qualified_identifier("l", left_geom)
    right_geom_sql = _qualified_identifier("r", right_geom)
    select_sql = _select_sql(select)

    return (
        f"SELECT {select_sql} "
        f"FROM {left_relation} AS l "
        f"JOIN {right_relation} AS r "
        f"ON ST_DWithin({left_geom_sql}, {right_geom_sql}, {distance})"
    )


def _sql_literal(value: str) -> str:
    if "\x00" in value:
        raise ValueError("SQL literals cannot contain null bytes")
    return "'" + value.replace("'", "''") + "'"


def _quote_identifier(identifier: str) -> str:
    if not identifier or "\x00" in identifier:
        raise ValueError("Unsafe identifier")
    if any(char not in _SAFE_IDENTIFIER_CHARS for char in identifier):
        raise ValueError(f"Unsafe identifier: {identifier!r}")
    return '"' + identifier.replace('"', '""') + '"'


def _qualified_identifier(alias: str, identifier: str) -> str:
    return f"{alias}.{_quote_identifier(identifier)}"


def _relation_sql(relation: str) -> str:
    if _is_parquet_scan_expression(relation):
        return relation
    parts = relation.split(".")
    if not parts or any(not part for part in parts):
        raise ValueError(f"Unsafe relation: {relation!r}")
    if any(char not in _SAFE_RELATION_CHARS for char in relation):
        raise ValueError(f"Unsafe relation: {relation!r}")
    return ".".join(_quote_identifier(part) for part in parts)


def _is_parquet_scan_expression(value: str) -> bool:
    if not value.startswith(_PARQUET_SCAN_PREFIX) or not value.endswith(")"):
        return False
    return value == parquet_scan(_extract_parquet_scan_uri(value), union_by_name=True) or value == parquet_scan(
        _extract_parquet_scan_uri(value),
        union_by_name=False,
    )


def _extract_parquet_scan_uri(scan: str) -> str:
    true_suffix = ", union_by_name=true)"
    false_suffix = ", union_by_name=false)"
    if scan.endswith(true_suffix):
        literal = scan[len(_PARQUET_SCAN_PREFIX) : -len(true_suffix)]
    elif scan.endswith(false_suffix):
        literal = scan[len(_PARQUET_SCAN_PREFIX) : -len(false_suffix)]
    else:
        raise ValueError("Unsafe relation")
    return _parse_sql_literal(literal)


def _parse_sql_literal(literal: str) -> str:
    if len(literal) < 2 or literal[0] != "'" or literal[-1] != "'":
        raise ValueError("Unsafe relation")
    value_chars: list[str] = []
    i = 1
    while i < len(literal) - 1:
        char = literal[i]
        if char == "'":
            if i + 1 < len(literal) - 1 and literal[i + 1] == "'":
                value_chars.append("'")
                i += 2
                continue
            raise ValueError("Unsafe relation")
        value_chars.append(char)
        i += 1
    return "".join(value_chars)


def _select_sql(select: str) -> str:
    if select.strip() == "*":
        return "*"
    columns = [part.strip() for part in select.split(",")]
    if not columns or any(not part for part in columns):
        raise ValueError("Unsafe select")

    rendered: list[str] = []
    for column in columns:
        if column in ("l.*", "r.*"):
            rendered.append(column)
            continue
        if column == "*" or "*" in column:
            raise ValueError(f"Unsafe select: {select!r}")
        parts = column.split(".")
        try:
            if len(parts) == 1:
                rendered.append(_quote_identifier(parts[0]))
            elif len(parts) == 2 and parts[0] in ("l", "r"):
                rendered.append(_qualified_identifier(parts[0], parts[1]))
            else:
                raise ValueError
        except ValueError:
            raise ValueError(f"Unsafe select: {select!r}")
    return ", ".join(rendered)


def _cached_gpkg_path(uri: str, cache_dir: str | Path | None) -> Path:
    parsed = urlparse(uri)
    base = (
        Path(cache_dir)
        if cache_dir is not None
        else Path(tempfile.gettempdir()) / "gispulse-duckdb-gpkg-cache"
    )
    name = Path(unquote(parsed.path)).name or "remote.gpkg"
    if not name.lower().endswith(".gpkg"):
        name += ".gpkg"
    digest = hashlib.sha256(uri.encode("utf-8")).hexdigest()[:16]
    return base / f"{digest}-{name}"


def _download_s3_uri(parsed, dest: Path) -> None:  # noqa: ANN001
    bucket = parsed.netloc
    key = unquote(parsed.path.lstrip("/"))
    if not bucket or not key:
        raise ValueError("s3:// GPKG URI must include bucket and key")

    storage = create_storage()
    if not hasattr(storage, "_bucket"):
        raise StorageError("S3/R2 storage is not configured")
    configured_bucket = getattr(storage, "_bucket")
    if configured_bucket != bucket:
        raise StorageError(
            "Configured S3/R2 bucket does not match requested s3:// URI bucket"
        )
    data = _run_async(storage.download(key))
    dest.write_bytes(data)


def _run_async(coro):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    result: list[object] = []
    error: list[BaseException] = []

    def runner() -> None:
        try:
            result.append(asyncio.run(coro))
        except BaseException as exc:  # noqa: BLE001
            error.append(exc)

    thread = threading.Thread(target=runner)
    thread.start()
    thread.join()
    if error:
        raise error[0]
    return result[0]


def _validate_gpkg_layer(path: Path, layer: str | None) -> None:
    if layer is None:
        return
    from gispulse.persistence.gpkg import list_layers

    layers = list_layers(str(path))
    if layer not in layers:
        raise ValueError(f"Layer {layer!r} not found in {path}")
