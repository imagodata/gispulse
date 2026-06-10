"""Generic readiness probe engine for staged data sources.

Checks that data artefacts are present before a costly build (fast-fail at the
boundary). Supports three guard modes: ``strict`` (block on any missing source),
``warn`` (continue but emit warnings), and ``off`` (no-op, always ok).

Probe kinds are **extensible**: the application registers its own kinds via
:func:`register_probe_kind`; the core ships three generic kinds out of the box:
``parquet_template``, ``stage_status``, and ``static``.

Extension example::

    from gispulse.core.readiness import (
        ProbeContext,
        ProbeResult,
        ReadinessSpec,
        register_probe_kind,
    )

    def _probe_my_kind(
        spec: ReadinessSpec,
        scope: str,
        context: ProbeContext,
    ) -> ProbeResult:
        path = f"/data/{spec.name}-{scope}.parquet"
        if Path(path).exists():
            return ProbeResult(ready=True, status="present", message=path)
        return ProbeResult(ready=False, status="missing", message=f"not found: {path}")

    register_probe_kind("my_kind", _probe_my_kind)
"""

from __future__ import annotations

import glob
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal, Mapping, Sequence

import structlog

log = structlog.get_logger(__name__)

GuardMode = Literal["strict", "warn", "off"]

# ---------------------------------------------------------------------------
# Probe-kind registry
# ---------------------------------------------------------------------------

_PROBE_REGISTRY: dict[str, Callable[["ReadinessSpec", str, "ProbeContext"], "ProbeResult"]] = {}


def register_probe_kind(
    name: str,
    fn: Callable[["ReadinessSpec", str, "ProbeContext"], "ProbeResult"],
    *,
    replace: bool = False,
) -> None:
    """Register a probe kind handler.

    Args:
        name: Unique identifier for the kind (e.g. ``"parquet_template"``).
        fn: Callable ``(spec, scope, context) -> ProbeResult``.
        replace: When ``False`` (default) a second registration for the same
            name raises :exc:`ValueError`. Set to ``True`` to override.

    Raises:
        ValueError: If *name* is already registered and *replace* is ``False``.
    """
    if name in _PROBE_REGISTRY and not replace:
        raise ValueError(
            f"probe kind {name!r} is already registered; "
            "pass replace=True to override"
        )
    _PROBE_REGISTRY[name] = fn


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProbeSpec:
    """Machine-readable description of how source presence is checked.

    *metadata* is an open mapping for probe-kind-specific parameters.  The
    built-in kinds consume keys documented below; application kinds may add
    whatever they need.

    Built-in keys consumed by ``parquet_template``:
        - ``env_names`` (list[str]): env-var names tried in order.
        - ``default_template`` (str | None): fallback path template.

    Built-in keys consumed by ``stage_status``:
        - ``required_entries`` (list[str] | None): entry names that must be
          present and green in *context.stage_statuses*.

    Built-in keys consumed by ``static``:
        - ``ready`` (bool): fixed readiness value.
        - ``status`` (str): fixed status string.
        - ``message`` (str): fixed message.
    """

    kind: str
    description: str = ""
    env_names: tuple[str, ...] = ()
    default_template: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ReadinessSpec:
    """Generic description of a source to probe.

    Unlike the applicative ``SourceSpec``, this carries no product-level
    wiring or plugin metadata — those belong to the application layer.
    *metadata* is an open slot for anything the application needs.
    """

    name: str
    probe: ProbeSpec
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProbeResult:
    """Outcome of a single probe execution.

    Renamed ``ingested`` → ``ready`` to avoid foncier-specific vocabulary.
    """

    ready: bool
    status: str
    version: str | None = None
    observed_at: str | None = None
    message: str = ""
    details: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class ProbeContext:
    """Runtime context passed to every probe call.

    Attributes:
        env: Optional environment mapping; falls back to :data:`os.environ`.
        templates: Explicit template overrides keyed by env-var name.
        stage_statuses: Staging manifest entries (any mapping structure —
            the ``stage_status`` kind reads them with duck-typing, matching
            the reference implementation).
        probe_overrides: Hard-wired probe results keyed by
            ``(spec.name, scope)``; bypasses the probe entirely.
    """

    env: Mapping[str, str] | None = None
    templates: Mapping[str, str] | None = None
    stage_statuses: Mapping[str, Any] | None = None
    probe_overrides: Mapping[tuple[str, str], ProbeResult] | None = None

    @property
    def environ(self) -> Mapping[str, str]:
        """Resolved environment: explicit *env* or :data:`os.environ`."""
        return self.env if self.env is not None else os.environ


# ---------------------------------------------------------------------------
# ReadinessReport  (generalised SourceGuardResult)
# ---------------------------------------------------------------------------


def _coerce_json(value: object) -> object:
    """Recursively coerce non-JSON-serialisable values to strings.

    Handles ``Path``, any non-primitive scalar, and nested dicts/lists.
    Keeps ``None``, ``bool``, ``int``, ``float``, ``str`` as-is.
    """
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        return {str(k): _coerce_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_coerce_json(item) for item in value]
    return str(value)


@dataclass(frozen=True)
class _ResultRow:
    """Internal row: one (source, scope) probe result."""

    source: str
    scope: str
    ready: bool
    status: str
    version: str | None
    observed_at: str | None
    message: str
    details: dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        d = asdict(self)
        # Coerce details values so the dict is always JSON-safe
        d["details"] = _coerce_json(d.get("details", {}))
        return d


@dataclass(frozen=True)
class ReadinessReport:
    """Aggregated report for a set of sources × scopes.

    Conceptually equivalent to ``SourceGuardResult``; generalised to avoid
    foncier-specific fields (``profile``, ``granularity``, ``partial_sources``,
    ``blocked``).

    Attributes:
        ok: ``True`` when no error is present (mode off → always True).
        mode: The guard mode that produced this report.
        errors: Error messages (strict mode: missing required sources).
        warnings: Warning messages (warn mode: missing optional sources).
        results: Individual ``(source, scope)`` probe rows.
    """

    ok: bool
    mode: GuardMode
    errors: list[str]
    warnings: list[str]
    results: list[_ResultRow]

    def to_manifest(self) -> dict[str, object]:
        """Return a JSON-serialisable manifest dict.

        Shape mirrors ``SourceGuardResult.to_manifest()`` conceptually:
        ``status`` summarises the outcome (``"off"``, ``"failed"``,
        ``"warn"``, ``"ok"``).
        """
        if self.mode == "off":
            status = "off"
        elif not self.ok:
            status = "failed"
        elif self.warnings:
            status = "warn"
        else:
            status = "ok"
        return {
            "status": status,
            "mode": self.mode,
            "ok": self.ok,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "sources": [row.to_dict() for row in self.results],
        }


# ---------------------------------------------------------------------------
# Core probe dispatch
# ---------------------------------------------------------------------------


def probe_source(
    spec: ReadinessSpec,
    scope: str,
    context: ProbeContext,
) -> ProbeResult:
    """Probe a single source for a given scope.

    Resolution order:

    1. If ``context.probe_overrides`` contains ``(spec.name, scope)``, return it
       directly (no I/O).
    2. Dispatch to the registered handler for ``spec.probe.kind``.
    3. If the kind is unknown, return a ``probe_error`` result (never raises — a
       bad kind must not abort a guard in ``warn`` mode).
    """
    override = (context.probe_overrides or {}).get((spec.name, scope))
    if override is not None:
        return override

    handler = _PROBE_REGISTRY.get(spec.probe.kind)
    if handler is None:
        return ProbeResult(
            ready=False,
            status="probe_error",
            message=f"unknown probe kind {spec.probe.kind!r}",
        )
    try:
        return handler(spec, scope, context)
    except Exception as exc:  # noqa: BLE001 — probe errors must never crash guard
        log.warning(
            "probe_kind_raised",
            kind=spec.probe.kind,
            source=spec.name,
            scope=scope,
            error=str(exc),
        )
        return ProbeResult(ready=False, status="probe_error", message=str(exc))


# ---------------------------------------------------------------------------
# resolve_status
# ---------------------------------------------------------------------------


def resolve_status(
    specs: Sequence[ReadinessSpec],
    scopes: Sequence[str],
    context: ProbeContext | None = None,
    mode: GuardMode = "strict",
) -> ReadinessReport:
    """Check readiness for all *specs* across all *scopes*.

    Args:
        specs: Ordered sequence of :class:`ReadinessSpec` to probe.
        scopes: Scope identifiers (e.g. department codes, region IDs, or any
            opaque string meaningful to the probe kind).
        context: Runtime context. Defaults to an empty :class:`ProbeContext`.
        mode: Guard mode — ``"strict"``, ``"warn"``, or ``"off"``.

    Returns:
        A :class:`ReadinessReport` aggregating all results.

    When *mode* is ``"off"`` no probing is performed and the report is always
    ``ok=True`` with empty errors/warnings (mirrors the reference).
    """
    if mode not in {"strict", "warn", "off"}:
        raise ValueError(f"mode must be one of strict, warn, off; got {mode!r}")

    if mode == "off":
        return ReadinessReport(
            ok=True,
            mode="off",
            errors=[],
            warnings=[],
            results=[],
        )

    ctx = context or ProbeContext()
    rows: list[_ResultRow] = []
    errors: list[str] = []
    warnings: list[str] = []

    for spec in specs:
        for scope in scopes:
            result = probe_source(spec, scope, ctx)
            rows.append(
                _ResultRow(
                    source=spec.name,
                    scope=scope,
                    ready=result.ready,
                    status=result.status,
                    version=result.version,
                    observed_at=result.observed_at,
                    message=result.message,
                    details=dict(result.details),
                )
            )
            if result.ready:
                continue

            missing_msg = (
                f"missing expected source {spec.name!r} for scope={scope!r}: "
                f"{result.message or result.status}"
            )
            if mode == "strict":
                errors.append(missing_msg)
            elif mode == "warn":
                warnings.append(missing_msg)

    ok = len(errors) == 0
    return ReadinessReport(
        ok=ok,
        mode=mode,
        errors=errors,
        warnings=warnings,
        results=rows,
    )


# ---------------------------------------------------------------------------
# Built-in probe kinds
# ---------------------------------------------------------------------------

# --- helpers shared by built-in kinds ---

_PLACEHOLDER_TOKEN = "/gispulse-missing/"


def _is_placeholder(path: str) -> bool:
    return _PLACEHOLDER_TOKEN in path.replace("\\", "/")


def _is_glob_template(template: str) -> bool:
    """Return True when the template string (before scope substitution) contains glob chars."""
    return any(ch in template for ch in "*?[")


def _local_matches(path: str) -> list[Path]:
    """Resolve a local path or glob pattern; returns empty list for s3://."""
    if path.startswith("s3://"):
        return []
    if any(ch in path for ch in "*?["):
        return [Path(m) for m in glob.glob(path)]
    candidate = Path(path)
    return [candidate] if candidate.exists() else []


def _latest_mtime_iso(paths: list[Path]) -> str | None:
    mtimes = [p.stat().st_mtime for p in paths if p.exists()]
    if not mtimes:
        return None
    return (
        datetime.fromtimestamp(max(mtimes), tz=timezone.utc)
        .replace(microsecond=0)
        .isoformat()
    )


def _local_matches_exact(path: str) -> list[Path]:
    """Strict existence check — never glob, even if path contains glob chars."""
    if path.startswith("s3://"):
        return []
    candidate = Path(path)
    return [candidate] if candidate.exists() else []


def _probe_paths(
    paths: tuple[str, ...],
    *,
    version: str | None,
    source_name: str,
    is_glob_template: bool = False,
) -> ProbeResult:
    """Check that each path exists (or matches if glob template).

    Args:
        paths: Resolved path strings (after scope substitution).
        version: Embedded version token extracted from the template, if any.
        source_name: Source name for error messages.
        is_glob_template: When ``True``, the *template* contained glob chars and
            the paths are resolved via :func:`glob.glob`.  When ``False``, each
            path is checked with strict :meth:`~pathlib.Path.exists` — glob
            characters introduced by the scope value are not expanded.
    """
    missing: list[str] = []
    matched: list[Path] = []
    for path in paths:
        if _is_placeholder(path):
            missing.append(path)
            continue
        hits = _local_matches(path) if is_glob_template else _local_matches_exact(path)
        if hits:
            matched.extend(hits)
        else:
            missing.append(path)

    if missing:
        return ProbeResult(
            ready=False,
            status="missing",
            version=version,
            message=f"missing {source_name} artifact(s): {', '.join(missing)}",
            details={"paths": paths},
        )
    observed_at = _latest_mtime_iso(matched)
    return ProbeResult(
        ready=True,
        status="present",
        version=version,
        observed_at=observed_at,
        message=f"{len(matched)} artifact(s) found",
        details={"paths": [str(p) for p in matched]},
    )


def _resolve_template(
    spec: ReadinessSpec,
    context: ProbeContext,
) -> str | None:
    """Resolve the template string for a *parquet_template* kind probe.

    Priority (mirrors reference ``_template_value``):
    1. ``context.templates[env_name]`` for each env_name in order.
    2. ``context.environ[env_name]`` for each env_name in order.
    3. ``spec.probe.default_template`` (if any env_name is listed).

    Returns the first non-empty value found, or ``None``.
    """
    templates_map = context.templates or {}
    for env_name in spec.probe.env_names:
        val = templates_map.get(env_name)
        if val:
            return str(val)
    for env_name in spec.probe.env_names:
        val = context.environ.get(env_name)
        if val:
            return str(val)
    if spec.probe.env_names and spec.probe.default_template:
        return spec.probe.default_template
    return None


def _format_scope_template(template: str, scope: str) -> str:
    """Expand scope into template placeholders.

    Supports ``{scope}`` (generic) and ``{dept}`` / ``{departement}`` (compat
    with foncier reference templates).

    When the *template* itself contains glob characters (``*``, ``?``, ``[``),
    the scope value is passed through :func:`glob.escape` so that a scope like
    ``"*"`` or ``"[0-9]"`` cannot accidentally match arbitrary files.  The glob
    characters that belong to the *template* are preserved and evaluated
    normally by the filesystem.
    """
    # Determine glob-ness on the raw template, before any substitution.
    scope_value = glob.escape(scope) if _is_glob_template(template) else scope
    value = os.path.expanduser(template)
    value = value.replace("{scope}", scope_value)
    value = value.replace("{dept}", scope_value)
    value = value.replace("{departement}", scope_value)
    return value


def _template_version(template: str) -> str | None:
    """Extract an embedded version token from the template string."""
    for token in ("millesime=", "revision=", "version="):
        if token not in template:
            continue
        tail = template.split(token, 1)[1]
        return tail.split("/", 1)[0].split("}", 1)[0] or None
    return None


# --- parquet_template ---

_GREEN_STAGE_STATUSES = {"success", "skipped"}


def _probe_parquet_template(
    spec: ReadinessSpec,
    scope: str,
    context: ProbeContext,
) -> ProbeResult:
    """Check that a parquet file derived from a template exists on disk."""
    template = _resolve_template(spec, context)
    if not template:
        env_desc = ", ".join(spec.probe.env_names) if spec.probe.env_names else "(none)"
        return ProbeResult(
            ready=False,
            status="missing",
            message=f"none of {env_desc} is set and no default_template configured",
        )
    is_glob = _is_glob_template(template)
    path = _format_scope_template(template, scope)
    return _probe_paths(
        (path,),
        version=_template_version(template),
        source_name=spec.name,
        is_glob_template=is_glob,
    )


register_probe_kind("parquet_template", _probe_parquet_template)


# --- stage_status ---


def _probe_stage_status(
    spec: ReadinessSpec,
    scope: str,
    context: ProbeContext,
) -> ProbeResult:
    """Check stage-manifest entries for the given source/scope pair.

    Mirrors the reference ``_probe_stage_statuses`` without the cadastre-specific
    logic. Reads duck-typed objects or plain dicts.

    Probe metadata keys (set via ``ProbeSpec.metadata``):
        ``required_entries`` (list[str]): Entry names that must all be present
        and green in the stage manifest.
    """
    stage_statuses = context.stage_statuses or {}
    if not stage_statuses:
        return ProbeResult(
            ready=False,
            status="missing",
            message="no stage_statuses in context",
        )

    # Match entries by (source, scope/departement) using duck-typing
    def _get(obj: Any, *attrs: str, default: Any = None) -> Any:
        for attr in attrs:
            try:
                val = getattr(obj, attr, _SENTINEL)
                if val is not _SENTINEL:
                    return val
            except Exception:  # noqa: BLE001
                pass
            if isinstance(obj, dict):
                try:
                    return obj[attr]
                except KeyError:
                    pass
        return default

    _SENTINEL = object()

    matching = [
        status
        for status in stage_statuses.values()
        for item in [_get(status, "item", default=status)]
        if _get(item, "source") == spec.name
        and _get(item, "departement", "scope") == scope
    ]

    if not matching:
        return ProbeResult(
            ready=False,
            status="missing",
            message=f"no stage entries found for source={spec.name!r} scope={scope!r}",
        )

    required_entries: list[str] = list(spec.probe.metadata.get("required_entries", []))
    if required_entries:
        seen_entries = set()
        for status in matching:
            item = _get(status, "item", default=status)
            entry = _get(item, "entry")
            if entry is not None:
                seen_entries.add(str(entry))
        missing_entries = sorted(set(required_entries) - seen_entries)
        if missing_entries:
            return ProbeResult(
                ready=False,
                status="missing",
                message=f"stage manifest missing entries: {', '.join(missing_entries)}",
            )

    failed = [
        status
        for status in matching
        if _get(status, "status") not in _GREEN_STAGE_STATUSES
    ]

    first_item = _get(matching[0], "item", default=matching[0])
    version = (
        str(
            _get(first_item, "source_revision_token")
            or _get(first_item, "revision")
            or ""
        )
        or None
    )

    if failed:
        return ProbeResult(
            ready=False,
            status="missing",
            version=version,
            message="stage manifest contains failed source item(s)",
        )

    return ProbeResult(
        ready=True,
        status="present",
        version=version,
        message="stage manifest green",
    )


register_probe_kind("stage_status", _probe_stage_status)


# --- static ---


def _probe_static(
    spec: ReadinessSpec,
    scope: str,  # noqa: ARG001 — ignored, result is fixed
    context: ProbeContext,  # noqa: ARG001 — ignored, result is fixed
) -> ProbeResult:
    """Return a fixed result specified in ``spec.probe.metadata``.

    Useful for tests and for sources that are always available.

    Probe metadata keys (set via ``ProbeSpec.metadata``):
        ``ready`` (bool, default ``True``): readiness value.
        ``status`` (str, default ``"static"``): status string.
        ``message`` (str, default ``""``): message.
    """
    meta = spec.probe.metadata
    return ProbeResult(
        ready=bool(meta.get("ready", True)),
        status=str(meta.get("status", "static")),
        message=str(meta.get("message", "")),
    )


register_probe_kind("static", _probe_static)
