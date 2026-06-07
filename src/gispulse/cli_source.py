"""Unified source-management CLI."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import typer

source_app = typer.Typer(
    name="source",
    help="Manage source definitions and staged source artifacts.",
    add_completion=False,
)


@dataclass(frozen=True)
class SourcePluginBootstrap:
    """Source registrations captured with their plugin provenance."""

    plugins: list[dict[str, str]]
    sources_by_plugin: dict[tuple[str, str], Any]
    plugins_by_source: dict[str, list[str]]


@dataclass(frozen=True)
class SourceSelection:
    plugin: str
    source_name: str
    source: Any


def bootstrap_source_plugins() -> SourcePluginBootstrap:
    """Invoke active data-source plugin register hooks and capture provenance."""
    from gispulse.core.plugin_hub import ExtensionHub
    from gispulse.core.plugin_model import PluginKind, PluginState
    from gispulse.core.sources import SOURCES

    results: list[dict[str, str]] = []
    sources_by_plugin: dict[tuple[str, str], Any] = {}
    plugins_by_source: dict[str, list[str]] = {}
    for rec in ExtensionHub.get().records_by_kind(PluginKind.SOURCE):
        module = getattr(rec.entry_point, "value", rec.name)
        if rec.state is not PluginState.ACTIVE:
            results.append(
                {
                    "name": rec.name,
                    "module": module,
                    "status": f"error: {rec.detail or 'plugin not active'}",
                }
            )
            continue
        try:
            before = {name: SOURCES.get(name) for name in SOURCES.names()}
            rec.obj()
            after = {name: SOURCES.get(name) for name in SOURCES.names()}
            for source_name, source in after.items():
                if before.get(source_name) is source:
                    continue
                sources_by_plugin[(rec.name, source_name)] = source
                plugins_by_source.setdefault(source_name, []).append(rec.name)
            results.append({"name": rec.name, "module": module, "status": "ok"})
        except Exception as exc:  # noqa: BLE001 - isolate bad source plugins
            results.append({"name": rec.name, "module": module, "status": f"error: {exc}"})
    for source_name, plugins in list(plugins_by_source.items()):
        plugins_by_source[source_name] = sorted(dict.fromkeys(plugins))
    return SourcePluginBootstrap(
        plugins=results,
        sources_by_plugin=sources_by_plugin,
        plugins_by_source=plugins_by_source,
    )


def _enum_value(value: Any) -> Any:
    return getattr(value, "value", value)


def _catalog_entry(source: Any, entry: Any) -> dict[str, Any]:
    return {
        "id": entry.id,
        "name": entry.name,
        "protocol": entry.access.protocol.value,
        "revision": entry.revision_token,
        "payload": _enum_value(entry.payload or getattr(source, "payload", None)),
        "domain": _enum_value(entry.domain or getattr(source, "domain", None)),
        "jurisdiction": entry.jurisdiction or getattr(source, "jurisdiction", None),
        "format": entry.access.format,
        "metadata": dict(entry.metadata),
    }


def _split_plugin_source(value: str | None) -> tuple[str | None, str | None]:
    if value is None:
        return None, None
    if ":" not in value:
        return None, value
    plugin, source = value.split(":", 1)
    plugin = plugin.strip()
    source = source.strip()
    if not plugin or not source:
        raise ValueError(f"Invalid plugin-qualified source {value!r}; expected plugin:source")
    return plugin, source


def _source_filter_parts(
    *,
    plugin_filter: str | None,
    source_filter: str | None,
) -> tuple[str | None, str | None]:
    source_plugin, source_name = _split_plugin_source(source_filter)
    if source_plugin is not None:
        if plugin_filter is not None and plugin_filter != source_plugin:
            raise ValueError(
                f"Conflicting plugin filters: --plugin {plugin_filter!r} "
                f"and source {source_filter!r}"
            )
        plugin_filter = source_plugin
    return plugin_filter, source_name


def _source_plugins_message(source_name: str, plugins: list[str]) -> str:
    candidates = ", ".join(f"{plugin}:{source_name}" for plugin in plugins)
    return (
        f"ambiguous source {source_name!r}; use --plugin or a plugin-qualified "
        f"source ({candidates})"
    )


def _resolve_source_selection(
    bootstrap: SourcePluginBootstrap,
    *,
    source_selector: str,
    plugin_filter: str | None = None,
) -> SourceSelection:
    plugin_name, source_name = _source_filter_parts(
        plugin_filter=plugin_filter,
        source_filter=source_selector,
    )
    if source_name is None:
        raise ValueError("source name is required")
    if plugin_name is not None:
        try:
            source = bootstrap.sources_by_plugin[(plugin_name, source_name)]
        except KeyError:
            available = ", ".join(
                f"{plugin}:{source}"
                for plugin, source in sorted(bootstrap.sources_by_plugin)
            )
            raise KeyError(
                f"no data source named {plugin_name}:{source_name!r} is registered "
                f"(available: {available or 'none'})"
            ) from None
        return SourceSelection(plugin=plugin_name, source_name=source_name, source=source)

    plugins = bootstrap.plugins_by_source.get(source_name, [])
    if not plugins:
        available = ", ".join(sorted(bootstrap.plugins_by_source)) or "none"
        raise KeyError(
            f"no data source named {source_name!r} is registered "
            f"(available: {available})"
        )
    if len(plugins) > 1:
        raise ValueError(_source_plugins_message(source_name, plugins))
    plugin_name = plugins[0]
    return SourceSelection(
        plugin=plugin_name,
        source_name=source_name,
        source=bootstrap.sources_by_plugin[(plugin_name, source_name)],
    )


def _catalog_collisions(
    bootstrap: SourcePluginBootstrap,
) -> tuple[list[dict[str, Any]], list[str]]:
    collisions: list[dict[str, Any]] = []
    warnings: list[str] = []
    for source_name, plugins in sorted(bootstrap.plugins_by_source.items()):
        if len(plugins) < 2:
            continue
        collisions.append({"source": source_name, "plugins": plugins})
        warnings.append(
            f"source {source_name!r} is provided by multiple plugins: "
            f"{', '.join(plugins)}"
        )
    return collisions, warnings


def _catalog_payload(
    *,
    plugin_filter: str | None = None,
    source_filter: str | None = None,
    entry_filter: str | None = None,
    protocol_filter: str | None = None,
) -> dict[str, Any]:
    bootstrap = bootstrap_source_plugins()
    plugin_filter, source_filter = _source_filter_parts(
        plugin_filter=plugin_filter,
        source_filter=source_filter,
    )
    plugin_items: list[dict[str, Any]] = []
    flat_sources: list[dict[str, Any]] = []
    for plugin in bootstrap.plugins:
        plugin_name = plugin["name"]
        if plugin_filter is not None and plugin_name != plugin_filter:
            continue
        plugin_sources: list[dict[str, Any]] = []
        source_pairs = sorted(
            (source_name, source)
            for (provider, source_name), source in bootstrap.sources_by_plugin.items()
            if provider == plugin_name
        )
        for source_name, source in source_pairs:
            if source_filter is not None and source_name != source_filter:
                continue
            entries = []
            for entry in sorted(source.catalog(), key=lambda item: item.id):
                if entry_filter is not None and entry.id != entry_filter:
                    continue
                if protocol_filter is not None and entry.access.protocol.value != protocol_filter:
                    continue
                entries.append(_catalog_entry(source, entry))
            if entries:
                source_item = {
                    "name": source_name,
                    "plugin": plugin_name,
                    "entries": entries,
                }
                plugin_sources.append(source_item)
                flat_sources.append(source_item)
        if plugin_sources or plugin["status"] != "ok":
            plugin_items.append({**plugin, "sources": plugin_sources})
    collisions, warnings = _catalog_collisions(bootstrap)
    if plugin_filter is not None:
        collisions = [
            item for item in collisions if plugin_filter in item["plugins"]
        ]
        warnings = [
            warning for warning in warnings if plugin_filter in warning
        ]
    if source_filter is not None:
        collisions = [
            item for item in collisions if item["source"] == source_filter
        ]
        warnings = [
            warning for warning in warnings if f"{source_filter!r}" in warning
        ]
    return {
        "plugins": plugin_items,
        "sources": flat_sources,
        "collisions": collisions,
        "warnings": warnings,
    }


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


def _prefix_segments(prefix: str | None) -> list[str]:
    if prefix is None:
        return []
    cleaned = prefix.strip().replace("\\", "/").strip("/")
    if not cleaned:
        return []
    return [_clean_segment(part, label="prefix") for part in cleaned.split("/")]


def _parse_scope(scope_items: list[str] | None) -> dict[str, str]:
    scope: dict[str, str] = {}
    for item in scope_items or []:
        if "=" not in item:
            raise ValueError(f"Invalid --scope {item!r}; expected k=v")
        key, value = item.split("=", 1)
        clean_key = _clean_segment(key, label="scope key")
        clean_value = _clean_segment(value, label=f"scope {clean_key}")
        scope[clean_key] = clean_value
    return _normalize_scope(scope)


def _normalize_scope(scope: dict[str, str]) -> dict[str, str]:
    from gispulse.core.bulk_ingest import normalize_bulk_department

    normalized = dict(scope)
    if "department" in normalized and "departement" not in normalized:
        normalized["departement"] = normalized.pop("department")
    if "departement" in normalized:
        normalized["departement"] = normalize_bulk_department(normalized["departement"])
    return normalized


def _scope_segments(scope: dict[str, str]) -> list[str]:
    if not scope:
        return ["national"]
    ordered: list[tuple[str, str]] = []
    for priority_key in ("departement", "partition"):
        if priority_key in scope:
            ordered.append((priority_key, scope[priority_key]))
    ordered.extend(
        (key, scope[key]) for key in sorted(scope) if key not in {"departement", "partition"}
    )
    return [
        f"{_clean_segment(key, label='scope key')}={_clean_segment(value, label=f'scope {key}')}"
        for key, value in ordered
    ]


def _scope_value(scope: dict[str, str]) -> str:
    return "/".join(_scope_segments(scope))


def _artifact_key(
    *,
    prefix: str | None,
    kind: str,
    plugin: str,
    source: str,
    entry: str,
    revision: str,
    scope: dict[str, str],
    filename: str,
) -> str:
    if kind not in {"raw", "stage"}:
        raise ValueError(f"Invalid artifact kind: {kind!r}")
    return "/".join(
        [
            *_prefix_segments(prefix),
            kind,
            _clean_segment(plugin, label="plugin"),
            _clean_segment(source, label="source"),
            _clean_segment(entry, label="entry"),
            f"millesime={_clean_segment(revision, label='revision')}",
            *_scope_segments(scope),
            _clean_segment(filename, label="filename"),
        ]
    )


def _manifest_key(
    *,
    prefix: str | None,
    plugin: str,
    source: str,
    entry: str,
    revision: str,
    scope: dict[str, str],
) -> str:
    return "/".join(
        [
            *_prefix_segments(prefix),
            "manifest",
            "source-artifacts",
            _clean_segment(plugin, label="plugin"),
            _clean_segment(source, label="source"),
            _clean_segment(entry, label="entry"),
            f"millesime={_clean_segment(revision, label='revision')}",
            *_scope_segments(scope),
            "manifest.json",
        ]
    )


def _entry_for(source: Any, entry_id: str) -> Any:
    for entry in source.catalog():
        if entry.id == entry_id:
            return entry
    source_name = getattr(source, "name", type(source).__name__)
    raise KeyError(f"{source_name}: unknown entry {entry_id!r}")


def _resolve_revision(source: Any, entry: Any, revision: str) -> str:
    if revision != "auto":
        return _clean_segment(revision, label="revision")
    token = source.revision(entry.id) or entry.revision_token or "auto"
    return _clean_segment(token, label="revision")


def _runtime_access(entry: Any, scope: dict[str, str]) -> Any:
    params = dict(entry.access.params)
    params.update(scope)
    return replace(entry.access, params=params)


def _source_registry(source: Any) -> Any:
    from gispulse.core.sources import PROTOCOLS

    return getattr(source, "_registry", PROTOCOLS)


class _PluginQualifiedSource:
    def __init__(self, source: Any, *, name: str) -> None:
        self._source = source
        self.name = name

    def __getattr__(self, attr: str) -> Any:
        return getattr(self._source, attr)


def _artifact_filename(entry: Any) -> str:
    base = str(entry.metadata.get("base_key") or entry.id)
    ext = _extension_from_entry(entry)
    return f"{_safe_stem(base)}.{_safe_stem(ext)}"


def _extension_from_entry(entry: Any) -> str:
    access = entry.access
    fmt = (access.format or "").lower()
    if "geo+json" in fmt or "geojson" in fmt:
        return "geojson"
    if "json" in fmt:
        return "json"
    if "csv" in fmt:
        return "csv"
    if "parquet" in fmt:
        return "parquet"
    endpoint_suffix = Path(access.endpoint.split("?", 1)[0]).suffix.lower().lstrip(".")
    if endpoint_suffix:
        return endpoint_suffix
    payload = _enum_value(entry.payload)
    if payload == "table":
        return "json"
    return "bin"


def _safe_stem(value: object) -> str:
    raw = str(value).strip()
    chars = [ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in raw]
    stem = "".join(chars).strip("._-")
    return _clean_segment(stem or "data", label="filename")


def _result_bytes(result: Any) -> bytes:
    data = result.data
    if isinstance(data, bytes):
        return data
    if isinstance(data, bytearray | memoryview):
        return bytes(data)
    if isinstance(data, Path):
        return data.read_bytes()
    if isinstance(data, str):
        path = Path(data)
        if path.exists() and path.is_file():
            return path.read_bytes()
        return data.encode("utf-8")
    if hasattr(data, "to_json"):
        return data.to_json().encode("utf-8")
    if data is None and result.reference:
        return result.reference.encode("utf-8")
    return json.dumps(data, sort_keys=True).encode("utf-8")


def _row_count(result: Any) -> int | None:
    row_count = result.metadata.get("row_count")
    if isinstance(row_count, int):
        return row_count
    data = result.data
    if isinstance(data, bytes | bytearray | memoryview | str) or data is None:
        return None
    try:
        return len(data)
    except TypeError:
        return None


def _storage_for_dest(dest: str) -> Any:
    if dest == "local":
        from gispulse.core.config import settings
        from gispulse.persistence.storage import LocalStorage

        return LocalStorage(base_path=settings.storage.data_dir)
    if dest == "s3":
        from gispulse.core.config import settings
        from gispulse.persistence.storage import create_storage

        if not settings.s3.endpoint:
            raise ValueError("dest=s3 requires GISPULSE_S3_ENDPOINT")
        return create_storage()
    raise ValueError(f"Invalid --dest {dest!r}; expected local or s3")


def _uri_for_key(dest: str, key: str) -> str:
    if dest == "s3":
        from gispulse.core.bulk_ingest import bulk_s3_uri

        return bulk_s3_uri(key=key)
    return f"local://{key}"


def _await(coro: Any) -> Any:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    raise RuntimeError("source CLI storage operations cannot run inside an active event loop")


def _persist_manifest(storage: Any, record: dict[str, Any]) -> dict[str, Any]:
    data = json.dumps(record, sort_keys=True).encode("utf-8")
    _await(storage.upload(record["manifest_key"], data, content_type="application/json"))
    return record


def _s3_key_from_uri(uri: object) -> str:
    value = str(uri)
    if not value.startswith("s3://"):
        raise ValueError(f"Expected s3:// URI, got {value!r}")
    parts = value.split("/", 3)
    if len(parts) != 4 or not parts[3]:
        raise ValueError(f"Invalid s3:// URI: {value!r}")
    return parts[3]


def _uses_bulk_runner(dest: str, entry: Any) -> bool:
    from gispulse.core.plugin_model import AccessProtocol

    return dest == "s3" and entry.access.protocol in {
        AccessProtocol.TABLE_FILE,
        AccessProtocol.DOWNLOAD,
    }


def _ingest_bulk(
    *,
    source: Any,
    plugin_name: str,
    source_name: str,
    entry: Any,
    scope: dict[str, str],
    revision: str,
    prefix: str | None,
) -> dict[str, Any]:
    from gispulse.core.bulk_runner import BulkIngestRunner

    runner = BulkIngestRunner(key_prefix=prefix)
    runner_source = _PluginQualifiedSource(
        source,
        name=f"{plugin_name}:{source_name}",
    )
    result = runner.run_entry(
        runner_source,
        entry.id,
        departement=scope.get("departement"),
        partition=scope.get("partition"),
        revision=revision,
        params=scope or None,
    )
    manifest = dict(result.manifest)
    raw_uri = manifest.get("raw_s3_uri")
    stage_uri = manifest.get("stage_s3_uri")
    if stage_uri is None:
        stage_uris = manifest.get("stage_s3_uris") or []
        if stage_uris:
            stage_uri = stage_uris[0]
    if raw_uri is None or stage_uri is None:
        raise ValueError("BulkIngestRunner manifest must include raw and stage S3 URIs")
    raw_key = _s3_key_from_uri(raw_uri)
    stage_key = _s3_key_from_uri(stage_uri)
    manifest_key = _manifest_key(
        prefix=prefix,
        plugin=plugin_name,
        source=source_name,
        entry=entry.id,
        revision=revision,
        scope=scope,
    )
    record: dict[str, Any] = {
        "plugin": plugin_name,
        "source": source_name,
        "entry": entry.id,
        "scope": str(manifest.get("scope") or _scope_value(scope)),
        "scope_params": dict(scope),
        "revision": str(manifest.get("revision") or revision),
        "dest": "s3",
        "raw_uri": str(raw_uri),
        "stage_uri": str(stage_uri),
        "raw_key": raw_key,
        "stage_key": stage_key,
        "row_count": manifest.get("row_count"),
        "status": str(manifest.get("status") or "success"),
        "manifest_key": manifest_key,
    }
    if "stage_s3_uris" in manifest:
        record["stage_uris"] = manifest["stage_s3_uris"]
    return _persist_manifest(_storage_for_dest("s3"), record)


def _manifest_storage() -> Any:
    from gispulse.persistence.storage import create_storage

    return create_storage()


def _manifest_prefix(prefix: str | None) -> str:
    return "/".join([*_prefix_segments(prefix), "manifest", "source-artifacts"])


def _load_manifest_records(storage: Any, prefix: str | None) -> list[dict[str, Any]]:
    manifest_prefix = _manifest_prefix(prefix)
    keys = _await(storage.list_keys(manifest_prefix))
    records: list[dict[str, Any]] = []
    for key in keys:
        if not key.endswith(".json"):
            continue
        raw = _await(storage.download(key))
        record = json.loads(raw.decode("utf-8"))
        if isinstance(record, dict):
            record.setdefault("manifest_key", key)
            records.append(record)
    return records


def _record_matches(
    record: dict[str, Any],
    *,
    plugin_filter: str | None,
    source_filter: str | None,
    entry_filter: str | None,
    scope_filter: dict[str, str],
) -> bool:
    if plugin_filter is not None and record.get("plugin") != plugin_filter:
        return False
    if source_filter is not None and record.get("source") != source_filter:
        return False
    if entry_filter is not None and record.get("entry") != entry_filter:
        return False
    if scope_filter:
        params = record.get("scope_params") or {}
        if not isinstance(params, dict):
            return False
        for key, value in scope_filter.items():
            if str(params.get(key)) != value:
                return False
    return True


def _ensure_unambiguous_artifact_source(
    records: list[dict[str, Any]],
    *,
    plugin_filter: str | None,
    source_filter: str | None,
) -> None:
    if plugin_filter is not None or source_filter is None:
        return
    plugins = sorted(
        {
            str(record["plugin"])
            for record in records
            if record.get("source") == source_filter and record.get("plugin")
        }
    )
    if len(plugins) > 1:
        raise ValueError(_source_plugins_message(source_filter, plugins))


def _validate_kind(kind: str) -> str:
    clean = _clean_segment(kind, label="kind")
    if clean not in {"raw", "stage"}:
        raise ValueError(f"Invalid --kind {kind!r}; expected raw or stage")
    return clean


def _artifact_item(record: dict[str, Any], *, kind: str, exists: bool) -> dict[str, Any]:
    selected_key = record.get(f"{kind}_key")
    selected_uri = record.get(f"{kind}_uri") or record.get(f"{kind}_s3_uri")
    return {
        **record,
        "kind": kind,
        "selected_key": selected_key,
        "selected_uri": selected_uri,
        "exists": exists,
    }


def _list_artifacts(
    *,
    plugin_filter: str | None,
    source_filter: str | None,
    entry_filter: str | None,
    scope_filter: dict[str, str],
    kind: str,
    prefix: str | None,
) -> dict[str, Any]:
    storage = _manifest_storage()
    clean_kind = _validate_kind(kind)
    plugin_filter, source_filter = _source_filter_parts(
        plugin_filter=plugin_filter,
        source_filter=source_filter,
    )
    records = _load_manifest_records(storage, prefix)
    _ensure_unambiguous_artifact_source(
        records,
        plugin_filter=plugin_filter,
        source_filter=source_filter,
    )
    artifacts: list[dict[str, Any]] = []
    for record in records:
        if not _record_matches(
            record,
            plugin_filter=plugin_filter,
            source_filter=source_filter,
            entry_filter=entry_filter,
            scope_filter=scope_filter,
        ):
            continue
        selected_key = record.get(f"{clean_kind}_key")
        exists = bool(selected_key and _await(storage.exists(str(selected_key))))
        artifacts.append(_artifact_item(record, kind=clean_kind, exists=exists))
    artifacts.sort(
        key=lambda item: (
            str(item.get("plugin", "")),
            str(item.get("source", "")),
            str(item.get("entry", "")),
            str(item.get("scope", "")),
            str(item.get("revision", "")),
        )
    )
    return {"count": len(artifacts), "artifacts": artifacts}


def _delete_artifacts(
    *,
    plugin_filter: str | None,
    source_filter: str | None,
    entry_filter: str | None,
    scope_filter: dict[str, str],
    kind: str,
    prefix: str | None,
    yes: bool,
) -> dict[str, Any]:
    storage = _manifest_storage()
    clean_kind = _validate_kind(kind)
    matched = _list_artifacts(
        plugin_filter=plugin_filter,
        source_filter=source_filter,
        entry_filter=entry_filter,
        scope_filter=scope_filter,
        kind=clean_kind,
        prefix=prefix,
    )["artifacts"]
    dry_run = not yes
    deleted: list[str] = []
    deleted_manifests: list[str] = []
    if not dry_run:
        for item in matched:
            selected_key = item.get("selected_key")
            if selected_key:
                _await(storage.delete(str(selected_key)))
                deleted.append(str(selected_key))
            manifest_key = item.get("manifest_key")
            if manifest_key:
                _await(storage.delete(str(manifest_key)))
                deleted_manifests.append(str(manifest_key))
    return {
        "dry_run": dry_run,
        "kind": clean_kind,
        "matched": matched,
        "deleted": deleted,
        "deleted_manifests": deleted_manifests,
    }


def _ingest_generic(
    *,
    source_selector: str,
    plugin_filter: str | None,
    entry_id: str,
    scope: dict[str, str],
    revision: str,
    dest: str,
    prefix: str | None,
) -> dict[str, Any]:
    from gispulse.core.plugin_model import FetchMode
    from gispulse.core.sources import PROTOCOLS

    bootstrap = bootstrap_source_plugins()
    selection = _resolve_source_selection(
        bootstrap,
        source_selector=source_selector,
        plugin_filter=plugin_filter,
    )
    source = selection.source
    entry = _entry_for(source, entry_id)
    revision_token = _resolve_revision(source, entry, revision)
    if _uses_bulk_runner(dest, entry):
        return _ingest_bulk(
            source=source,
            plugin_name=selection.plugin,
            source_name=selection.source_name,
            entry=entry,
            scope=scope,
            revision=revision_token,
            prefix=prefix,
        )

    access = _runtime_access(entry, scope)
    registry = _source_registry(source)
    if registry is PROTOCOLS:
        from gispulse.core.fetchers import register_core_fetchers

        register_core_fetchers()
    result = registry.dispatch_fetch(access, mode=FetchMode.MATERIALIZE)
    payload = _result_bytes(result)
    filename = _artifact_filename(entry)
    raw_key = _artifact_key(
        prefix=prefix,
        kind="raw",
        plugin=selection.plugin,
        source=selection.source_name,
        entry=entry_id,
        revision=revision_token,
        scope=scope,
        filename=filename,
    )
    stage_key = _artifact_key(
        prefix=prefix,
        kind="stage",
        plugin=selection.plugin,
        source=selection.source_name,
        entry=entry_id,
        revision=revision_token,
        scope=scope,
        filename=filename,
    )
    manifest_key = _manifest_key(
        prefix=prefix,
        plugin=selection.plugin,
        source=selection.source_name,
        entry=entry_id,
        revision=revision_token,
        scope=scope,
    )
    storage = _storage_for_dest(dest)
    content_type = entry.access.format or "application/octet-stream"
    _await(storage.upload(raw_key, payload, content_type=content_type))
    _await(storage.upload(stage_key, payload, content_type=content_type))
    record: dict[str, Any] = {
        "plugin": selection.plugin,
        "source": selection.source_name,
        "entry": entry_id,
        "scope": _scope_value(scope),
        "scope_params": dict(scope),
        "revision": revision_token,
        "dest": dest,
        "raw_uri": _uri_for_key(dest, raw_key),
        "stage_uri": _uri_for_key(dest, stage_key),
        "raw_key": raw_key,
        "stage_key": stage_key,
        "row_count": _row_count(result),
        "status": "success",
        "manifest_key": manifest_key,
    }
    return _persist_manifest(storage, record)


@source_app.command("catalog")
def source_catalog(
    plugin: str | None = typer.Option(None, "--plugin", help="Filter by source plugin."),
    source: str | None = typer.Option(None, "--source", help="Filter by source name."),
    entry: str | None = typer.Option(None, "--entry", help="Filter by source entry id."),
    protocol: str | None = typer.Option(None, "--protocol", help="Filter by access protocol."),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON output."),
) -> None:
    """List dynamically discovered source definitions."""
    try:
        payload = _catalog_payload(
            plugin_filter=plugin,
            source_filter=source,
            entry_filter=entry,
            protocol_filter=protocol,
        )
    except Exception as exc:  # noqa: BLE001 - CLI boundary
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc
    for warning in payload["warnings"]:
        typer.echo(f"Warning: {warning}", err=True)
    if json_output:
        typer.echo(json.dumps(payload, sort_keys=True))
        return

    if not payload["plugins"]:
        typer.echo("No source definitions found.")
        return
    for plugin_item in payload["plugins"]:
        typer.echo(f"{plugin_item['name']} ({plugin_item['status']})")
        for source_item in plugin_item["sources"]:
            typer.echo(f"  {source_item['name']}")
            for entry_item in source_item["entries"]:
                revision = entry_item["revision"] or "-"
                typer.echo(
                    f"    - {entry_item['id']} [{entry_item['protocol']}] "
                    f"{entry_item['name']} revision={revision}"
                )


@source_app.command("ingest")
def source_ingest(
    source: str = typer.Argument(..., help="Source name or plugin:source."),
    entry: str = typer.Argument(..., help="Source entry id."),
    plugin: str | None = typer.Option(None, "--plugin", help="Source plugin provider."),
    scope: list[str] | None = typer.Option(
        None,
        "--scope",
        help="Scope parameter as k=v. May be passed multiple times.",
    ),
    revision: str = typer.Option("auto", "--revision", help="Revision token or 'auto'."),
    dest: str = typer.Option("s3", "--dest", help="Artifact destination: s3 or local."),
    prefix: str | None = typer.Option(None, "--prefix", help="Storage key prefix."),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON output."),
) -> None:
    """Ingest one source entry and persist its artifact manifest."""
    try:
        record = _ingest_generic(
            source_selector=source,
            plugin_filter=plugin,
            entry_id=entry,
            scope=_parse_scope(scope),
            revision=revision,
            dest=dest,
            prefix=prefix,
        )
    except Exception as exc:  # noqa: BLE001 - CLI boundary
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc

    if json_output:
        typer.echo(json.dumps(record, sort_keys=True))
        return
    typer.echo(
        f"{record['status']}: {record['plugin']}:{record['source']}:{record['entry']} "
        f"{record['scope']} -> {record['stage_uri']}"
    )


@source_app.command("list")
def source_list(
    plugin: str | None = typer.Option(None, "--plugin", help="Filter by source plugin."),
    source: str | None = typer.Option(
        None,
        "--source",
        help="Filter by source name or plugin:source.",
    ),
    entry: str | None = typer.Option(None, "--entry", help="Filter by source entry id."),
    scope: list[str] | None = typer.Option(
        None,
        "--scope",
        help="Scope filter as k=v. May be passed multiple times.",
    ),
    kind: str = typer.Option("stage", "--kind", help="Artifact kind: raw or stage."),
    prefix: str | None = typer.Option(None, "--prefix", help="Storage key prefix."),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON output."),
) -> None:
    """List staged source artifacts from the persistent manifest."""
    try:
        payload = _list_artifacts(
            plugin_filter=plugin,
            source_filter=source,
            entry_filter=entry,
            scope_filter=_parse_scope(scope),
            kind=kind,
            prefix=prefix,
        )
    except Exception as exc:  # noqa: BLE001 - CLI boundary
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc

    if json_output:
        typer.echo(json.dumps(payload, sort_keys=True))
        return
    if not payload["artifacts"]:
        typer.echo("No source artifacts found.")
        return
    for item in payload["artifacts"]:
        marker = "exists" if item["exists"] else "missing"
        typer.echo(
            f"{item.get('plugin', '-')}:"
            f"{item['source']}:{item['entry']} {item['scope']} "
            f"{item['kind']} {marker} {item['selected_uri']}"
        )


@source_app.command("delete")
def source_delete(
    plugin: str | None = typer.Option(None, "--plugin", help="Filter by source plugin."),
    source: str | None = typer.Option(
        None,
        "--source",
        help="Filter by source name or plugin:source.",
    ),
    entry: str | None = typer.Option(None, "--entry", help="Filter by source entry id."),
    scope: list[str] | None = typer.Option(
        None,
        "--scope",
        help="Scope filter as k=v. May be passed multiple times.",
    ),
    kind: str = typer.Option("stage", "--kind", help="Artifact kind: raw or stage."),
    prefix: str | None = typer.Option(None, "--prefix", help="Storage key prefix."),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Preview matching artifacts, even when --yes is also passed.",
    ),
    yes: bool = typer.Option(False, "--yes", help="Actually delete matched artifacts."),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON output."),
) -> None:
    """Delete source artifacts selected from the persistent manifest."""
    try:
        payload = _delete_artifacts(
            plugin_filter=plugin,
            source_filter=source,
            entry_filter=entry,
            scope_filter=_parse_scope(scope),
            kind=kind,
            prefix=prefix,
            yes=yes and not dry_run,
        )
    except Exception as exc:  # noqa: BLE001 - CLI boundary
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc

    if json_output:
        typer.echo(json.dumps(payload, sort_keys=True))
        return
    action = "Would delete" if payload["dry_run"] else "Deleted"
    typer.echo(f"{action} {len(payload['matched'])} {payload['kind']} artifact(s).")
