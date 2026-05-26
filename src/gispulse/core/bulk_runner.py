"""N3 declarative bulk ingest runner.

This module is intentionally narrow: it wires already-declared bulk source
entries to the Garage/S3 raw + stage layout from :mod:`gispulse.core.bulk_ingest`.
It does not teach dbt/foncier to consume those objects; that is the next N3
step.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Iterable
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
        if self._write_table_raw:
            resolved_access = resolve_access_endpoint(access)
            guard_outbound_url(resolved_access.endpoint)
            raw_bytes = _download_bytes(resolved_access.endpoint)
            storage = self._storage or _create_s3_storage(self._bucket)
            _await_storage(
                storage.upload(
                    raw_key,
                    raw_bytes,
                    content_type=_content_type(entry, resolved_access),
                )
            )
        params = dict(access.params)
        params["s3_key"] = stage_key
        params["s3_bucket"] = self._bucket
        stage_access = replace(access, params=params)
        result = self._registry.dispatch_fetch(
            stage_access,
            mode=FetchMode.MATERIALIZE,
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
        raw_bytes = _download_bytes(resolved_access.endpoint)
        storage = self._storage or _create_s3_storage(self._bucket)
        _await_storage(
            storage.upload(
                raw_key,
                raw_bytes,
                content_type=_content_type(entry, resolved_access),
            )
        )

        copy_sqls: list[str] = []
        stage_uris: list[str] = []
        with tempfile.TemporaryDirectory(dir=self._temp_dir) as tmp:
            tmp_dir = Path(tmp)
            archive_path = tmp_dir / raw_filename
            archive_path.write_bytes(raw_bytes)
            extract_dir = tmp_dir / "extract"
            extract_dir.mkdir()
            extracted = _extract_archive(
                archive_path,
                extract_dir,
                archive_format=_archive_format(entry, resolved_access),
            )
            vector_paths = _vector_members(extract_dir, extracted)

            from gispulse.persistence.duckdb_engine import DuckDBSession

            with DuckDBSession() as session:
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
    return ""


def _download_bytes(endpoint: str) -> bytes:
    if endpoint.startswith(("http://", "https://")):
        import httpx

        chunks: list[bytes] = []
        with httpx.stream("GET", endpoint, follow_redirects=True) as resp:
            resp.raise_for_status()
            chunks.extend(resp.iter_bytes())
        return b"".join(chunks)
    return Path(endpoint).read_bytes()


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


def _st_read_scan(vector_path: Path, access: AccessSpec) -> str:
    path_sql = _sql_literal(vector_path)
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
