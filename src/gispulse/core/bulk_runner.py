"""N3 declarative bulk ingest runner.

This module is intentionally narrow: it wires already-declared bulk source
entries to the Garage/S3 raw + stage layout from :mod:`gispulse.core.bulk_ingest`.
It does not teach dbt/foncier to consume those objects; that is the next N3
step.
"""

from __future__ import annotations

import asyncio
import gzip
import json
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Iterator
from zipfile import ZipFile

from gispulse.core.bulk_ingest import (
    BULK_RAW_PREFIX,
    BULK_STAGE_PREFIX,
    bulk_ingest_manifest_record,
    bulk_s3_key,
    bulk_s3_uri,
    normalize_bulk_department,
)
from gispulse.core.config import settings
from gispulse.core.fetchers import register_core_fetchers
from gispulse.core.plugin_model import (
    AccessProtocol,
    AccessSpec,
    FetchMode,
    Payload,
    SourceResult,
    resolve_access_endpoint,
)
from gispulse.core.sources import DataSource, ProtocolRegistry, SourceEntryRef
from gispulse.core.ssrf import guard_outbound_url
from gispulse.persistence.storage import DatasetStorage, S3Storage

__all__ = ["BulkIngestResult", "BulkIngestRunner"]

_VECTOR_SUFFIXES = (".gpkg", ".shp", ".geojson", ".json", ".fgb", ".gml")
_SPATIAL_GZIP_ARCHIVE_FORMATS = {"geojson.gz", "json.gz"}
_GEOJSON_READ_CHUNK_BYTES = 1024 * 1024
_GEOJSON_BATCH_SIZE = 10_000
_PARQUET_CONTENT_TYPE = "application/vnd.apache.parquet"


@dataclass(frozen=True)
class BulkIngestResult:
    """Result returned by :class:`BulkIngestRunner` for one bulk entry."""

    source: str
    entry: str
    access: AccessSpec
    fetch_result: SourceResult
    manifest: dict[str, object]


class BulkIngestRunner:
    """Orchestrate one declarative bulk source entry into raw/stage S3."""

    def __init__(
        self,
        *,
        registry: ProtocolRegistry | None = None,
        storage: DatasetStorage | None = None,
        bucket: str | None = None,
        key_prefix: str | None = None,
        write_table_raw: bool = False,
        temp_dir: str | Path | None = None,
    ) -> None:
        if registry is None:
            registry = ProtocolRegistry()
            register_core_fetchers(registry)
        self._registry = registry
        self._storage = storage
        self._bucket = bucket or settings.s3.bucket
        self._key_prefix = key_prefix
        self._write_table_raw = write_table_raw
        self._temp_dir = Path(temp_dir) if temp_dir is not None else None

    def run_registered(
        self,
        source_name: str,
        entry_id: str,
        *,
        departement: object | None = None,
        partition: object | None = None,
        revision: object | None = None,
        params: dict[str, Any] | None = None,
    ) -> BulkIngestResult:
        """Run an entry from the process-wide source registry."""
        from gispulse.core.sources import SOURCES

        source = SOURCES.get(source_name)
        return self.run_entry(
            source,
            entry_id,
            departement=departement,
            partition=partition,
            revision=revision,
            params=params,
        )

    def run_entry(
        self,
        source: DataSource,
        entry_id: str,
        *,
        departement: object | None = None,
        partition: object | None = None,
        revision: object | None = None,
        params: dict[str, Any] | None = None,
    ) -> BulkIngestResult:
        """Materialize one declarative source entry to the N3 S3 layout."""
        entry = _entry_from_source(source, entry_id)
        source_name = _source_name(source)
        revision_token = self._revision_token(source, entry, revision)
        access = self._runtime_access(
            entry,
            departement=departement,
            partition=partition,
            params=params,
        )
        scope_departement = _scope_departement(entry, access, departement)

        if access.protocol is AccessProtocol.TABLE_FILE:
            return self._run_table_file(
                source_name=source_name,
                entry=entry,
                access=access,
                departement=scope_departement,
                partition=partition,
                revision=revision_token,
            )
        if access.protocol is AccessProtocol.DOWNLOAD:
            return self._run_download_archive(
                source_name=source_name,
                entry=entry,
                access=access,
                departement=scope_departement,
                partition=partition,
                revision=revision_token,
                source_schema=_source_schema(source, entry.id),
            )
        raise NotImplementedError(
            "N3 bulk runner currently supports TABLE_FILE and DOWNLOAD entries; "
            f"got {access.protocol.value!r} for {source_name}:{entry_id}"
        )

    def _revision_token(
        self,
        source: DataSource,
        entry: SourceEntryRef,
        revision: object | None,
    ) -> str:
        if revision is not None:
            return _clean_segment(revision, label="revision")
        token = source.revision(entry.id)
        if token is None:
            token = entry.revision_token or entry.metadata.get("millesime")
        if token is None:
            raise ValueError(
                f"{_source_name(source)}:{entry.id} has no revision token; "
                "pass revision=... for a versioned bulk run"
            )
        return _safe_stem(token)

    def _runtime_access(
        self,
        entry: SourceEntryRef,
        *,
        departement: object | None,
        partition: object | None,
        params: dict[str, Any] | None,
    ) -> AccessSpec:
        runtime_params = dict(entry.access.params)
        endpoint = entry.access.endpoint
        dept = None
        if departement is not None:
            dept = normalize_bulk_department(departement)
            if "departement" in runtime_params or "{departement}" in endpoint:
                runtime_params["departement"] = dept
            department_param = entry.metadata.get("department_param")
            if isinstance(department_param, str) and (
                department_param in runtime_params
                or f"{{{department_param}}}" in endpoint
            ):
                runtime_params[department_param] = dept
            zone_format = entry.metadata.get("zone_format")
            if isinstance(zone_format, str) and zone_format:
                runtime_params["zone"] = zone_format.format(code_departement=dept)
        if partition is not None:
            runtime_params["partition"] = str(partition)
        if params:
            runtime_params.update(params)
        return replace(entry.access, params=runtime_params)

    def _run_table_file(
        self,
        *,
        source_name: str,
        entry: SourceEntryRef,
        access: AccessSpec,
        departement: object | None,
        partition: object | None,
        revision: str,
    ) -> BulkIngestResult:
        stage_filename = _stage_filename(entry)
        raw_filename = _raw_filename(entry, access)
        stage_key = bulk_s3_key(
            key_prefix=self._key_prefix,
            kind=BULK_STAGE_PREFIX,
            source=source_name,
            entry=entry.id,
            departement=departement,
            partition=partition,
            revision=revision,
            filename=stage_filename,
        )
        raw_key = bulk_s3_key(
            key_prefix=self._key_prefix,
            kind=BULK_RAW_PREFIX,
            source=source_name,
            entry=entry.id,
            departement=departement,
            partition=partition,
            revision=revision,
            filename=raw_filename,
        )
        stage_uri = bulk_s3_uri(key=stage_key, bucket=self._bucket)
        resolved_access = resolve_access_endpoint(access)
        guard_outbound_url(resolved_access.endpoint)

        copy_sql: str
        fetch_metadata: dict[str, Any]
        with tempfile.TemporaryDirectory(dir=self._temp_dir) as tmp:
            local_params = dict(resolved_access.params)
            local_params.pop("s3_uri", None)
            local_params.pop("s3_key", None)
            local_params.pop("s3_bucket", None)
            local_path = Path(tmp) / raw_filename
            local_params["local_path"] = str(local_path)
            fetch_access = replace(resolved_access, params=local_params)
            fetched = self._registry.dispatch_fetch(
                fetch_access,
                mode=FetchMode.MATERIALIZE,
            )
            table_path = _table_file_path(
                fetched,
                tmp_dir=Path(tmp),
                filename=raw_filename,
            )

            if self._write_table_raw:
                storage = self._storage or _create_s3_storage(self._bucket)
                _await_storage(
                    storage.upload(
                        raw_key,
                        table_path.read_bytes(),
                        content_type=_content_type(entry, resolved_access),
                    )
                )

            from gispulse.persistence.duckdb_engine import DuckDBSession

            copy_sql = _copy_table_to_s3_sql(table_path, resolved_access, stage_uri)
            with DuckDBSession() as session:
                session.conn.execute(copy_sql)
            fetch_metadata = dict(fetched.metadata)

        params = dict(resolved_access.params)
        params["s3_key"] = stage_key
        params["s3_bucket"] = self._bucket
        stage_access = replace(resolved_access, params=params)
        result = SourceResult(
            payload=entry.payload or Payload.TABLE,
            mode=FetchMode.MATERIALIZE,
            data=stage_uri,
            reference=stage_uri,
            metadata={
                **fetch_metadata,
                "copy_sql": copy_sql,
                "s3_uri": stage_uri,
            },
        )
        manifest = bulk_ingest_manifest_record(
            key_prefix=self._key_prefix,
            bucket=self._bucket,
            source=source_name,
            entry=entry.id,
            departement=departement,
            partition=partition,
            revision=revision,
            raw_filename=raw_filename,
            stage_filename=stage_filename,
            status="success",
        )
        return BulkIngestResult(
            source=source_name,
            entry=entry.id,
            access=stage_access,
            fetch_result=result,
            manifest=manifest,
        )

    def _run_download_archive(
        self,
        *,
        source_name: str,
        entry: SourceEntryRef,
        access: AccessSpec,
        departement: object | None,
        partition: object | None,
        revision: str,
        source_schema: dict[str, Any] | None,
    ) -> BulkIngestResult:
        resolved_access = resolve_access_endpoint(access)
        guard_outbound_url(resolved_access.endpoint)

        raw_filename = _raw_filename(entry, resolved_access)
        raw_key = bulk_s3_key(
            key_prefix=self._key_prefix,
            kind=BULK_RAW_PREFIX,
            source=source_name,
            entry=entry.id,
            departement=departement,
            partition=partition,
            revision=revision,
            filename=raw_filename,
        )
        storage = self._storage or _create_s3_storage(self._bucket)

        copy_sqls: list[str] = []
        stage_uris: list[str] = []
        stage_rows: int | None = None
        with tempfile.TemporaryDirectory(dir=self._temp_dir) as tmp:
            tmp_dir = Path(tmp)
            archive_path = tmp_dir / raw_filename
            _download_to_path(resolved_access.endpoint, archive_path)
            with archive_path.open("rb") as raw_file:
                _await_storage(
                    storage.upload(
                        raw_key,
                        raw_file,
                        content_type=_content_type(entry, resolved_access),
                    )
                )

            if _is_spatial_gzip_download(entry, resolved_access):
                stage_filename = _stage_filename(entry)
                stage_key = bulk_s3_key(
                    key_prefix=self._key_prefix,
                    kind=BULK_STAGE_PREFIX,
                    source=source_name,
                    entry=entry.id,
                    departement=departement,
                    partition=partition,
                    revision=revision,
                    filename=stage_filename,
                )
                stage_uri = bulk_s3_uri(key=stage_key, bucket=self._bucket)
                geojson_path = tmp_dir / _decompressed_geojson_filename(raw_filename)
                stage_path = tmp_dir / stage_filename
                _decompress_gzip_to_path(archive_path, geojson_path)
                stage_rows = _geojson_to_parquet_batches(
                    geojson_path,
                    stage_path,
                    schema_hint=source_schema,
                    batch_size=_geojson_batch_size(resolved_access),
                )
                with stage_path.open("rb") as stage_file:
                    _await_storage(
                        storage.upload(
                            stage_key,
                            stage_file,
                            content_type=_PARQUET_CONTENT_TYPE,
                        )
                    )
                copy_sqls.append(
                    f"stream_geojson_gzip_to_parquet({_sql_literal(str(archive_path))})"
                )
                stage_uris.append(stage_uri)
            else:
                from gispulse.persistence.duckdb_engine import DuckDBSession

                with DuckDBSession() as session:
                    extract_dir = tmp_dir / "extract"
                    extract_dir.mkdir()
                    extracted = _extract_archive(
                        archive_path,
                        extract_dir,
                        archive_format=_archive_format(entry, resolved_access),
                    )
                    vector_paths = _vector_members(extract_dir, extracted)
                    for vector_path in vector_paths:
                        stage_filename = _stage_filename(
                            entry,
                            vector_path=vector_path,
                            force_vector_stem=len(vector_paths) > 1,
                        )
                        stage_key = bulk_s3_key(
                            key_prefix=self._key_prefix,
                            kind=BULK_STAGE_PREFIX,
                            source=source_name,
                            entry=entry.id,
                            departement=departement,
                            partition=partition,
                            revision=revision,
                            filename=stage_filename,
                        )
                        stage_uri = bulk_s3_uri(key=stage_key, bucket=self._bucket)
                        copy_sql = _copy_vector_to_s3_sql(
                            vector_path,
                            resolved_access,
                            stage_uri,
                        )
                        session.conn.execute(copy_sql)
                        copy_sqls.append(copy_sql)
                        stage_uris.append(stage_uri)

        first_stage_filename = _filename_from_s3_uri(stage_uris[0])
        manifest = bulk_ingest_manifest_record(
            key_prefix=self._key_prefix,
            bucket=self._bucket,
            source=source_name,
            entry=entry.id,
            departement=departement,
            partition=partition,
            revision=revision,
            raw_filename=raw_filename,
            stage_filename=first_stage_filename,
            row_count=stage_rows,
            status="success",
        )
        manifest["stage_s3_uris"] = stage_uris
        result = SourceResult(
            payload=entry.payload or Payload.VECTOR,
            mode=FetchMode.MATERIALIZE,
            data=stage_uris[0],
            reference=stage_uris[0],
            metadata={
                "raw_s3_uri": manifest["raw_s3_uri"],
                "stage_s3_uris": stage_uris,
                "copy_sql": copy_sqls[0] if len(copy_sqls) == 1 else copy_sqls,
                **({"stage_rows": stage_rows} if stage_rows is not None else {}),
            },
        )
        return BulkIngestResult(
            source=source_name,
            entry=entry.id,
            access=resolved_access,
            fetch_result=result,
            manifest=manifest,
        )


def _entry_from_source(source: DataSource, entry_id: str) -> SourceEntryRef:
    for entry in source.catalog():
        if getattr(entry, "id", None) == entry_id:
            return entry
    raise KeyError(f"{_source_name(source)}: unknown entry {entry_id!r}")


def _source_name(source: DataSource) -> str:
    name = getattr(source, "name", None)
    if not name:
        raise ValueError("bulk source must expose a non-empty name")
    return _clean_segment(name, label="source")


def _source_schema(source: DataSource, entry_id: str) -> dict[str, Any] | None:
    schema_fn = getattr(source, "schema", None)
    if not callable(schema_fn):
        return None
    schema = schema_fn(entry_id)
    return dict(schema) if isinstance(schema, dict) and schema else None


def _scope_departement(
    entry: SourceEntryRef,
    access: AccessSpec,
    departement: object | None,
) -> object | None:
    if departement is not None:
        return departement
    value = access.params.get("departement")
    if value:
        return value
    department_param = entry.metadata.get("department_param")
    if isinstance(department_param, str):
        value = access.params.get(department_param)
        if value:
            return value
    return None


def _create_s3_storage(bucket: str) -> DatasetStorage:
    endpoint = settings.s3.endpoint
    if not endpoint:
        raise RuntimeError(
            "DOWNLOAD raw archive upload requires S3/Garage storage; pass "
            "storage=... for tests or set GISPULSE_S3_ENDPOINT"
        )
    return S3Storage(
        endpoint_url=endpoint,
        bucket=bucket,
        access_key=settings.s3.access_key,
        secret_key=settings.s3.secret_key,
        region=settings.s3.region,
    )


def _clean_segment(value: object, *, label: str) -> str:
    segment = str(value).strip()
    if (
        not segment
        or segment in {".", ".."}
        or "/" in segment
        or "\\" in segment
        or "//" in segment
        or ".." in segment
    ):
        raise ValueError(f"Invalid {label} path segment: {value!r}")
    return segment


def _safe_stem(value: object) -> str:
    raw = str(value).strip()
    chars = [ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in raw]
    stem = "".join(chars).strip("._-")
    return _clean_segment(stem or "data", label="filename")


def _metadata_str(entry: SourceEntryRef, key: str) -> str | None:
    value = entry.metadata.get(key)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _archive_format(entry: SourceEntryRef, access: AccessSpec) -> str:
    endpoint_path = access.endpoint.split("?", 1)[0].lower()
    for suffix in (".geojson.gz", ".json.gz"):
        if endpoint_path.endswith(suffix):
            return suffix.lstrip(".")
    raw = (
        access.params.get("archive_format")
        or entry.metadata.get("archive_format")
        or _endpoint_suffix(access.endpoint).lstrip(".")
    )
    return str(raw).lower().lstrip(".")


def _stage_filename(
    entry: SourceEntryRef,
    *,
    vector_path: Path | None = None,
    force_vector_stem: bool = False,
) -> str:
    if vector_path is not None and force_vector_stem:
        stem = vector_path.stem
    else:
        stem = _metadata_str(entry, "base_key") or (
            vector_path.stem if vector_path is not None else entry.id
        )
    return f"{_safe_stem(stem)}.parquet"


def _raw_filename(entry: SourceEntryRef, access: AccessSpec) -> str:
    if access.protocol is AccessProtocol.DOWNLOAD:
        if _is_spatial_gzip_download(entry, access):
            filename = _endpoint_filename(access.endpoint)
            if filename is not None:
                return filename
        archive_format = _archive_format(entry, access)
        return f"archive.{_safe_stem(archive_format)}"

    base = _metadata_str(entry, "base_key") or entry.id
    archive_format = access.params.get("archive_format") or entry.metadata.get(
        "archive_format"
    )
    if archive_format:
        ext = str(archive_format).lower().lstrip(".")
    else:
        ext = (
            str(
                access.params.get("table_format")
                or entry.metadata.get("table_format")
                or entry.metadata.get("data_format")
                or _endpoint_suffix(access.endpoint).lstrip(".")
                or "dat"
            )
            .lower()
            .lstrip(".")
        )
    return f"{_safe_stem(base)}.{_safe_stem(ext)}"


def _endpoint_suffix(endpoint: str) -> str:
    path = endpoint.split("?", 1)[0].rstrip("/")
    suffix = Path(path).suffix
    return suffix if suffix else ""


def _content_type(entry: SourceEntryRef, access: AccessSpec) -> str:
    if access.format:
        return access.format
    archive_format = _archive_format(entry, access)
    if archive_format == "zip":
        return "application/zip"
    if archive_format == "7z":
        return "application/x-7z-compressed"
    if archive_format in _SPATIAL_GZIP_ARCHIVE_FORMATS:
        return "application/geo+json+gzip"
    return ""


def _endpoint_filename(endpoint: str) -> str | None:
    path = endpoint.split("?", 1)[0].rstrip("/")
    filename = Path(path).name
    if not filename:
        return None
    return _clean_segment(filename, label="filename")


def _is_spatial_gzip_download(entry: SourceEntryRef, access: AccessSpec) -> bool:
    if access.protocol is not AccessProtocol.DOWNLOAD:
        return False
    return _archive_format(entry, access) in _SPATIAL_GZIP_ARCHIVE_FORMATS


def _download_to_path(endpoint: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if endpoint.startswith(("http://", "https://")):
        import httpx

        with httpx.stream("GET", endpoint, follow_redirects=True) as resp:
            resp.raise_for_status()
            with dest.open("wb") as out:
                for chunk in resp.iter_bytes():
                    if chunk:
                        out.write(chunk)
        return
    shutil.copyfile(Path(endpoint), dest)


def _decompressed_geojson_filename(raw_filename: str) -> str:
    lower = raw_filename.lower()
    if lower.endswith(".geojson.gz"):
        return raw_filename[:-3]
    if lower.endswith(".json.gz"):
        return raw_filename[:-3]
    return f"{raw_filename}.json"


def _decompress_gzip_to_path(src: Path, dest: Path) -> None:
    with gzip.open(src, "rb") as compressed, dest.open("wb") as out:
        shutil.copyfileobj(compressed, out, length=1024 * 1024)


def _geojson_batch_size(access: AccessSpec) -> int:
    raw = access.params.get("geojson_batch_size")
    if raw is None:
        return _GEOJSON_BATCH_SIZE
    try:
        size = int(raw)
    except (TypeError, ValueError):
        raise ValueError(f"Invalid geojson_batch_size: {raw!r}") from None
    if size <= 0:
        raise ValueError(f"Invalid geojson_batch_size: {raw!r}")
    return size


def _geojson_to_parquet_batches(
    geojson_path: Path,
    parquet_path: Path,
    *,
    schema_hint: dict[str, Any] | None,
    batch_size: int,
) -> int:
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError as exc:
        raise RuntimeError(
            "GeoJSON gzip DOWNLOAD staging requires pyarrow. "
            "Install the parquet extra for N3 cadastre bulk ingest."
        ) from exc

    writer: pq.ParquetWriter | None = None
    field_types = _arrow_field_types(schema_hint, pa)
    property_columns = [name for name in field_types if name != "geometry"]
    rows = 0
    batch: list[dict[str, Any]] = []
    try:
        for feature in _iter_geojson_features(geojson_path):
            batch.append(feature)
            if len(batch) >= batch_size:
                if writer is None and not property_columns:
                    property_columns = _infer_property_columns(batch)
                    field_types = _arrow_field_types(
                        {name: "str" for name in property_columns} | {"geometry": "geometry"},
                        pa,
                    )
                table = _features_to_arrow_table(batch, property_columns, field_types, pa)
                writer = _write_arrow_batch(writer, parquet_path, table, pq)
                rows += table.num_rows
                batch = []
        if batch:
            if writer is None and not property_columns:
                property_columns = _infer_property_columns(batch)
                field_types = _arrow_field_types(
                    {name: "str" for name in property_columns} | {"geometry": "geometry"},
                    pa,
                )
            table = _features_to_arrow_table(batch, property_columns, field_types, pa)
            writer = _write_arrow_batch(writer, parquet_path, table, pq)
            rows += table.num_rows
        if writer is None:
            table = _features_to_arrow_table([], property_columns, field_types, pa)
            writer = _write_arrow_batch(writer, parquet_path, table, pq)
    finally:
        if writer is not None:
            writer.close()
    return rows


def _write_arrow_batch(
    writer: Any,
    parquet_path: Path,
    table: Any,
    pq: Any,
) -> Any:
    if writer is None:
        parquet_path.parent.mkdir(parents=True, exist_ok=True)
        writer = pq.ParquetWriter(
            parquet_path,
            table.schema,
            compression="zstd",
            use_dictionary=True,
        )
    writer.write_table(table)
    return writer


def _arrow_field_types(schema_hint: dict[str, Any] | None, pa: Any) -> dict[str, Any]:
    field_types: dict[str, Any] = {}
    for name, kind in (schema_hint or {}).items():
        if str(kind) == "geometry":
            field_types[name] = pa.binary()
        elif str(kind) in {"int", "integer"}:
            field_types[name] = pa.int64()
        elif str(kind) in {"float", "double", "number"}:
            field_types[name] = pa.float64()
        elif str(kind) in {"bool", "boolean"}:
            field_types[name] = pa.bool_()
        elif str(kind) == "date":
            field_types[name] = pa.date32()
        else:
            field_types[name] = pa.string()
    field_types.setdefault("geometry", pa.binary())
    return field_types


def _infer_property_columns(batch: list[dict[str, Any]]) -> list[str]:
    columns: list[str] = []
    seen: set[str] = set()
    for feature in batch:
        props = feature.get("properties")
        if not isinstance(props, dict):
            continue
        for key in props:
            if key not in seen and key != "geometry":
                seen.add(key)
                columns.append(str(key))
    return columns


def _features_to_arrow_table(
    features: list[dict[str, Any]],
    property_columns: list[str],
    field_types: dict[str, Any],
    pa: Any,
) -> Any:
    arrays: dict[str, list[Any]] = {name: [] for name in property_columns}
    geometries: list[bytes | None] = []
    for feature in features:
        props = feature.get("properties")
        if not isinstance(props, dict):
            props = {}
        for name in property_columns:
            arrays[name].append(_coerce_arrow_value(props.get(name), field_types[name]))
        geometries.append(_feature_geometry_wkb(feature.get("geometry")))

    fields = [(name, arrays[name]) for name in property_columns]
    fields.append(("geometry", geometries))
    arrow_arrays = [
        pa.array(values, type=field_types.get(name, pa.string())) for name, values in fields
    ]
    schema = pa.schema(
        [pa.field(name, field_types.get(name, pa.string())) for name, _values in fields]
    )
    return pa.Table.from_arrays(arrow_arrays, schema=schema)


def _coerce_arrow_value(value: Any, arrow_type: Any) -> Any:
    if value is None:
        return None
    type_name = str(arrow_type)
    if value == "":
        return None
    if type_name == "int64":
        return int(value)
    if type_name == "double":
        return float(value)
    if type_name == "bool":
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "t", "yes", "y"}
        return bool(value)
    if type_name.startswith("date32"):
        if isinstance(value, date):
            return value
        if isinstance(value, str) and value:
            try:
                return date.fromisoformat(value[:10])
            except ValueError:
                return None
        return None
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return str(value)


def _feature_geometry_wkb(geometry: Any) -> bytes | None:
    if geometry is None:
        return None
    from shapely import from_geojson, to_wkb

    geom = from_geojson(json.dumps(geometry, separators=(",", ":")))
    return bytes(to_wkb(geom)) if geom is not None else None


def _iter_geojson_features(path: Path) -> Iterator[dict[str, Any]]:
    decoder = json.JSONDecoder()
    with path.open("r", encoding="utf-8") as fh:
        buffer = _read_until_features_array(fh)
        eof = False
        while True:
            while True:
                stripped = buffer.lstrip(" \t\r\n,")
                if len(stripped) != len(buffer):
                    buffer = stripped
                if buffer:
                    break
                chunk = fh.read(_GEOJSON_READ_CHUNK_BYTES)
                if not chunk:
                    return
                buffer += chunk

            if buffer[0] == "]":
                return

            try:
                feature, end = decoder.raw_decode(buffer)
            except json.JSONDecodeError:
                if eof:
                    raise
                chunk = fh.read(_GEOJSON_READ_CHUNK_BYTES)
                if chunk:
                    buffer += chunk
                    continue
                eof = True
                continue

            if not isinstance(feature, dict):
                raise ValueError("GeoJSON features array contains a non-object item")
            yield feature
            buffer = buffer[end:]


def _read_until_features_array(fh: Any) -> str:
    pattern = '"features"'
    buffer = ""
    while True:
        chunk = fh.read(_GEOJSON_READ_CHUNK_BYTES)
        if not chunk:
            raise ValueError("GeoJSON FeatureCollection has no features array")
        buffer += chunk
        idx = buffer.find(pattern)
        if idx < 0:
            if len(buffer) > len(pattern):
                buffer = buffer[-len(pattern) :]
            continue
        colon = buffer.find(":", idx + len(pattern))
        if colon < 0:
            continue
        pos = colon + 1
        while True:
            while pos < len(buffer) and buffer[pos].isspace():
                pos += 1
            if pos < len(buffer):
                break
            chunk = fh.read(_GEOJSON_READ_CHUNK_BYTES)
            if not chunk:
                raise ValueError("GeoJSON features key has no array value")
            buffer += chunk
        if buffer[pos] != "[":
            raise ValueError("GeoJSON features value is not an array")
        return buffer[pos + 1 :]


def _await_storage(coro: Any) -> Any:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    raise RuntimeError(
        "BulkIngestRunner.run_entry() cannot await storage upload inside a "
        "running event loop; use a synchronous worker context for N3 bulk ingest"
    )


def _extract_archive(
    archive_path: Path,
    extract_dir: Path,
    *,
    archive_format: str,
) -> list[Path]:
    if archive_format == "zip":
        return _extract_zip_archive(archive_path, extract_dir)
    if archive_format == "7z":
        _extract_7z_archive(archive_path, extract_dir)
        return []
    raise NotImplementedError(f"unsupported DOWNLOAD archive format: {archive_format!r}")


def _extract_zip_archive(archive_path: Path, extract_dir: Path) -> list[Path]:
    extracted: list[Path] = []
    root = extract_dir.resolve()
    with ZipFile(archive_path) as archive:
        for info in archive.infolist():
            dest = (extract_dir / info.filename).resolve()
            if not str(dest).startswith(str(root)):
                raise ValueError(f"unsafe archive member path: {info.filename!r}")
            if info.is_dir():
                dest.mkdir(parents=True, exist_ok=True)
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as src, open(dest, "wb") as out:
                shutil.copyfileobj(src, out)
            extracted.append(dest)
    return extracted


def _extract_7z_archive(archive_path: Path, extract_dir: Path) -> None:
    try:
        import py7zr  # type: ignore[import-not-found]
    except ImportError:
        py7zr = None
    if py7zr is not None:
        with py7zr.SevenZipFile(archive_path, mode="r") as archive:
            archive.extractall(path=extract_dir)
        return

    binary = next(
        (name for name in ("7zz", "7z", "7za") if shutil.which(name)),
        None,
    )
    if binary is None:
        raise RuntimeError(
            "7z DOWNLOAD archives require py7zr or a local 7zz/7z/7za binary"
        )
    subprocess.run(
        [binary, "x", str(archive_path), f"-o{extract_dir}", "-y"],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _vector_members(extract_dir: Path, extracted: Iterable[Path]) -> list[Path]:
    seen: set[Path] = set()
    members: list[Path] = []
    for path in extracted:
        if path.suffix.lower() in _VECTOR_SUFFIXES:
            resolved = path.resolve()
            seen.add(resolved)
            members.append(resolved)
    for path in extract_dir.rglob("*"):
        if path.is_file() and path.suffix.lower() in _VECTOR_SUFFIXES:
            resolved = path.resolve()
            if resolved not in seen:
                seen.add(resolved)
                members.append(resolved)
    if not members:
        raise RuntimeError(
            f"DOWNLOAD archive extracted under {extract_dir} has no vector member "
            f"with suffix in {_VECTOR_SUFFIXES!r}"
        )
    return members


def _copy_vector_to_s3_sql(
    vector_path: Path,
    access: AccessSpec,
    stage_uri: str,
) -> str:
    source = _st_read_scan(vector_path, access)
    dest = _sql_literal(stage_uri)
    return f"COPY (SELECT * FROM {source}) TO {dest} (FORMAT PARQUET)"


def _copy_gzip_vector_to_s3_sql(
    gzip_path: Path,
    access: AccessSpec,
    stage_uri: str,
) -> str:
    source = _st_read_scan(gzip_path, access, vsi_prefix="/vsigzip/")
    dest = _sql_literal(stage_uri)
    return f"COPY (SELECT * FROM {source}) TO {dest} (FORMAT PARQUET)"


def _copy_table_to_s3_sql(
    table_path: Path,
    access: AccessSpec,
    stage_uri: str,
) -> str:
    source = _table_file_scan(table_path, access)
    dest = _sql_literal(stage_uri)
    return f"COPY (SELECT * FROM {source}) TO {dest} (FORMAT PARQUET)"


def _table_file_scan(table_path: Path, access: AccessSpec) -> str:
    if not _is_table_csv(access):
        raise NotImplementedError(
            "TABLE_FILE N3 materialization currently supports CSV tables only"
        )
    uri = str(table_path)
    if _is_table_zip(access):
        member = str(access.params.get("archive_member", "*.csv")).lstrip("/")
        uri = f"/vsizip/{uri}/{member}"
    return f"read_csv_auto({_sql_literal(uri)})"


def _is_table_csv(access: AccessSpec) -> bool:
    endpoint = access.endpoint.split("?", 1)[0].lower()
    return endpoint.endswith(".csv") or access.params.get("table_format") == "csv"


def _is_table_zip(access: AccessSpec) -> bool:
    endpoint = access.endpoint.split("?", 1)[0].lower()
    return endpoint.endswith(".zip") or access.params.get("archive_format") == "zip"


def _table_file_path(
    result: SourceResult,
    *,
    tmp_dir: Path,
    filename: str,
) -> Path:
    data = result.data
    if isinstance(data, Path):
        return data
    if isinstance(data, str):
        return Path(data)
    if isinstance(data, (bytes, bytearray, memoryview)):
        path = tmp_dir / filename
        path.write_bytes(bytes(data))
        return path
    raise TypeError(
        "TABLE_FILE materialization must return a local path or bytes; "
        f"got {type(data).__name__}"
    )


def _st_read_scan(
    vector_path: Path,
    access: AccessSpec,
    *,
    vsi_prefix: str = "",
) -> str:
    uri = f"{vsi_prefix}{vector_path}" if vsi_prefix else str(vector_path)
    path_sql = _sql_literal(uri)
    st_read = f"ST_Read({path_sql}"
    layer = access.params.get("layer")
    if layer:
        st_read += f", layer={_sql_literal(layer)}"
    st_read += ")"
    return st_read


def _sql_literal(value: object) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _filename_from_s3_uri(uri: str) -> str:
    return uri.rstrip("/").rsplit("/", 1)[-1]
