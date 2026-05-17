"""YAML config loader for ``gispulse triggers``.

Schema (version 1, strict)::

    version: 1
    gpkg: ./layer.gpkg          # surchargeable par --gpkg
    triggers:
      - name: enrich_parcels
        table: parcels
        pk_col: fid
        when: [INSERT, UPDATE]
        predicate: "status == 'pending'"   # AttrPredicate expression
        actions:
          - type: webhook
            url: https://example.com/hook
          - type: set_field
            field: enriched_at
            value: 2026-04-27
          - type: run_sql
            expression: "UPDATE parcels SET status='ok' WHERE fid=NEW.fid"
      # A source-watched trigger (#195) — fires when an external data
      # source publishes a new revision instead of on a local DML edit.
      # It declares ``on: {source_changed: <uri>}`` and no ``table``.
      - name: refresh_on_new_millesime
        on:
          source_changed: cadastre://parcelles
          frequency: mensuel
        actions:
          - type: log_event
    security:
      webhook_allowlist:
        - example.com
    runtime:
      poll_interval_ms: 1000
      max_batch: 200

Security guarantees
-------------------
- ``yaml.safe_load`` only — never ``yaml.load`` (CVE class).
- Config path is canonicalised with ``Path.resolve(strict=True)`` and
  rejected if it escapes the user's ``$HOME`` and the current working
  directory (path traversal guard, e.g. ``../../etc/passwd``).
- The referenced GPKG path is canonicalised the same way and is checked
  against the same anchor set.
- Trigger ``table`` is validated against the actual GPKG layer list at
  ``validate_against_gpkg`` time so a typo is reported by
  ``gispulse triggers validate`` before any tick runs.
- Strict pydantic v2 models (``extra="forbid"``) so unknown keys raise
  loudly instead of being silently ignored.

The loader does **not** evaluate predicates or open the GPKG by itself —
that's the runtime's job. ``validate_against_gpkg(config, engine)`` is
called explicitly by the CLI before ``build_runtime()`` so config errors
surface as exit-code 1 from ``triggers validate``.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable, Literal

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from gispulse.core.logging import get_logger

if TYPE_CHECKING:  # pragma: no cover
    from gispulse.core.graph import ActionDef
    from gispulse.core.models import Trigger


log = get_logger(__name__)

CONFIG_VERSION = 1

# Action types the loader recognises in YAML. Subset of the full
# ``ActionType`` enum: only the ones a Mode 1 user can actually
# meaningfully express in a flat YAML file. Other action types
# (RUN_JOB, RUN_GRAPH, ENQUEUE, ...) require infrastructure the
# headless runtime does not boot.
_SUPPORTED_ACTION_TYPES: tuple[str, ...] = (
    "webhook",
    "set_field",
    "run_sql",
    "log_event",
    "notify",
)

# DML operations the watcher emits.
_SUPPORTED_DML_OPS: tuple[str, ...] = (
    "INSERT",
    "UPDATE",
    "UPDATE_GEOM",
    "UPDATE_ATTR",
    "DELETE",
    "BULK",
)


def _expand_when_to_events(when: list[str]) -> list[str]:
    """Map the user-facing ``when`` list to the internal ``events`` set.

    The granular verbs (``UPDATE_GEOM``/``UPDATE_ATTR``) are passed through
    unchanged. The coarse ``UPDATE`` is expanded so a v1.5.x config keeps
    catching every UPDATE event the watcher resolves to a granular variant.
    Order is preserved and duplicates are removed; the watcher resolves a
    raw row's operation to ``UPDATE_GEOM`` or ``UPDATE_ATTR`` based on the
    ``geom_changed`` change-log column before evaluation.
    """
    out: list[str] = []
    seen: set[str] = set()
    for verb in when:
        if verb == "UPDATE":
            for v in ("UPDATE", "UPDATE_GEOM", "UPDATE_ATTR"):
                if v not in seen:
                    seen.add(v)
                    out.append(v)
        else:
            if verb not in seen:
                seen.add(verb)
                out.append(verb)
    return out


# ---------------------------------------------------------------------------
# Pydantic models (strict)
# ---------------------------------------------------------------------------


class ActionConfigModel(BaseModel):
    """One action under a trigger.

    The ``type`` field is closed (Literal) so unknown action types are
    rejected at parse time. Type-specific keys are validated by the
    field validator below.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    type: Literal[
        "webhook",
        "set_field",
        "run_sql",
        "log_event",
        "notify",
        "tag_field",
    ]
    # webhook
    url: str | None = None
    # set_field
    field: str | None = Field(default=None, alias="field")
    value: Any = None
    # run_sql
    expression: str | None = None
    # notify
    channel: str | None = None
    payload_template: dict[str, Any] | str | None = None
    # tag_field (#123) — write a validation status onto the row.
    # ``column`` / ``message_column`` are SQL identifiers, validated
    # by the engine when the action runs.
    column: str | None = None
    message_column: str | None = None
    message: str | None = None

    @field_validator("url")
    @classmethod
    def _validate_url(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if not (v.startswith("http://") or v.startswith("https://")):
            raise ValueError(
                f"webhook url must start with http:// or https://, got {v!r}"
            )
        return v


class OnConfigModel(BaseModel):
    """The ``on:`` discriminator for non-DML triggers (issue #195).

    Today it carries a single key — ``source_changed`` — naming the
    external source URI to watch (e.g. ``cadastre://parcelles``). A
    trigger declaring ``on:`` is a *source-watched* trigger: it fires
    when :class:`~persistence.source_watcher.SourceWatcherRegistry`
    observes a new ``revision()`` token, not on a local DML edit. Such
    a trigger therefore declares no ``table`` / ``when`` / ``predicate``.

    ``frequency`` is an optional catalog-style label (``quotidien``,
    ``mensuel``…) mapped to a poll interval by
    :func:`persistence.source_watcher.interval_from_frequency`.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    source_changed: str = Field(min_length=1, max_length=2048)
    frequency: str | None = Field(default=None, max_length=64)


class TriggerConfigModel(BaseModel):
    """One trigger entry in the YAML config.

    Two mutually-exclusive shapes:

    - **DML trigger** (historical default) — declares a ``table`` and
      fires on local change-log edits.
    - **Source trigger** (#195) — declares ``on: {source_changed: <uri>}``
      and fires on an external-source revision change. It declares no
      ``table``.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=120)
    # Required for DML triggers, forbidden for source triggers — enforced
    # by :meth:`_validate_trigger_mode`. Optional at the field level so a
    # source trigger can omit it entirely.
    table: str | None = Field(default=None, max_length=120)
    # ``on:`` discriminator — when set, this is a source-watched trigger.
    on: OnConfigModel | None = None
    # ESRI Attribute Rules vocabulary aliases (#125):
    # ``constraint`` ≡ ``validation`` (rule rejects/tags the row),
    # ``calculation`` ≡ ``trigger`` (rule writes derived attributes),
    # ``validation``  is the GISPulse-native name. ``trigger`` (default)
    # is kept as a no-op label so v1.5.x configs without a ``kind:`` line
    # keep loading. The runtime ignores the value today — it is exposed
    # solely to ease ESRI migration; full semantic wiring is tracked in
    # docs-site/guide/migration-from-esri.md.
    kind: Literal[
        "trigger",
        "validation",
        "constraint",
        "calculation",
    ] = "trigger"
    pk_col: str = Field(default="fid", min_length=1, max_length=64)
    when: list[
        Literal[
            "INSERT",
            "UPDATE",
            "UPDATE_GEOM",
            "UPDATE_ATTR",
            "DELETE",
            "BULK",
        ]
    ] = Field(
        # ``UPDATE`` is a backward-compatible alias kept for v1.5.x configs;
        # config_loader expands it to ``UPDATE_GEOM + UPDATE_ATTR`` when
        # building Trigger.conditions["events"] so dispatch stays granular
        # under the hood (see ``to_triggers`` below).
        # mypy can't widen ``list[str]`` into the Literal[...] union mode
        # the BaseModel field expects; the runtime values are perfectly
        # fine because pydantic re-validates them at construction time.
        default_factory=lambda: [  # type: ignore[arg-type]
            "INSERT",
            "UPDATE",
            "DELETE",
        ],
    )
    predicate: str | None = None
    actions: list[ActionConfigModel] = Field(default_factory=list)
    enabled: bool = True

    @model_validator(mode="after")
    def _validate_trigger_mode(self) -> "TriggerConfigModel":
        """Enforce the DML / source-trigger split (#195).

        A source-watched trigger (``on:`` set) watches an external
        source, not a GPKG layer — declaring a ``table`` or a
        ``predicate`` on it is a config error. Conversely a DML trigger
        must name the ``table`` it watches.
        """
        if self.on is not None:
            if self.table is not None:
                raise ValueError(
                    f"trigger {self.name!r}: a source_changed trigger must "
                    f"not declare a 'table' — it watches the external "
                    f"source {self.on.source_changed!r}, not a layer"
                )
            if self.predicate is not None:
                raise ValueError(
                    f"trigger {self.name!r}: 'predicate' is not supported on "
                    f"a source_changed trigger"
                )
        elif not self.table:
            raise ValueError(
                f"trigger {self.name!r}: a DML trigger requires a 'table' "
                f"(or declare 'on: {{source_changed: <uri>}}' for a "
                f"source-watched trigger)"
            )
        return self

    @field_validator("when")
    @classmethod
    def _no_empty_when(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError(
                "when must list at least one of INSERT/UPDATE/UPDATE_GEOM/"
                "UPDATE_ATTR/DELETE/BULK"
            )
        # Deduplicate while preserving order.
        seen: list[str] = []
        for op in v:
            if op not in seen:
                seen.append(op)
        return seen

    @field_validator("predicate")
    @classmethod
    def _validate_predicate_dsl(cls, v: str | None) -> str | None:
        """Compile the predicate string at config-load time.

        We only call ``parse_predicate`` for its side effect — the
        compiled AST is recomputed in :func:`to_triggers` so the
        pydantic model stays a pure data carrier (frozen-friendly,
        JSON-serialisable for ``triggers validate --json``). Surfacing
        DSL errors here means ``triggers validate`` can flag them as
        config errors instead of letting them blow up the first tick.
        """
        if v is None:
            return None
        # Local import keeps the module import-light; the DSL only
        # matters when there's a predicate to compile.
        from gispulse.runtime.predicate_dsl import (
            PredicateError,
            parse_predicate,
        )

        try:
            parse_predicate(v)
        except PredicateError as exc:
            raise ValueError(f"predicate parse failed: {exc}") from exc
        return v


class SecurityConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    webhook_allowlist: list[str] = Field(default_factory=list)


class ValidateRuleConfigModel(BaseModel):
    """One declarative validation rule under the ``validate:`` top-level key.

    Validation rules fire on every INSERT and UPDATE event in the dataset
    (granular ``when`` will be added later). ``mode: warn`` logs the
    failure and broadcasts ``validation.failed`` over the event hub;
    ``mode: tag`` writes the failure onto the row via a ``tag_field``
    action so external consumers (QGIS, portal map) can render the
    validation status.

    The ``rule`` is compiled the same way as a ``set_field`` expression
    (see :mod:`gispulse.dsl`) — the value at evaluation time must be a
    boolean (``geom_is_valid()``, ``geom_area_m2() >= 50``…).
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    id: str = Field(min_length=1, max_length=120)
    rule: str = Field(min_length=1, max_length=4096)
    mode: Literal["warn", "tag"] = "warn"
    tag_field: str | None = None
    message: str | None = None
    enabled: bool = True
    # v1.6.x — explicit per-rule table override. When unset, the runtime
    # falls back to ``GISPulseConfig.default_table`` and finally to the
    # single-table auto-detection (see :func:`build_runtime`).
    table: str | None = Field(default=None, min_length=1, max_length=120)

    @model_validator(mode="after")
    def _validate_tag_field_required_for_tag_mode(self) -> "ValidateRuleConfigModel":
        if self.mode == "tag" and not self.tag_field:
            raise ValueError(
                "validate rule with mode='tag' requires a tag_field column name"
            )
        return self

    @field_validator("rule")
    @classmethod
    def _validate_rule_syntax(cls, v: str) -> str:
        """Compile the rule at config-load time to surface syntax errors early.

        We use a placeholder ``EPSG:4326`` source so CRS-aware fcts compile
        cleanly without forcing operators to repeat the dataset CRS in every
        rule. The real CRS is injected at runtime from the engine's
        :class:`gispulse.dsl.CompilationContext`.
        """
        from gispulse.dsl import CompilationContext, compile_expression

        try:
            compile_expression(
                v, CompilationContext(source_epsg="EPSG:4326"), mode="boolean"
            )
        except Exception as exc:  # noqa: BLE001
            raise ValueError(f"validate rule failed to compile: {exc}") from exc
        return v


class RuntimeConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    poll_interval_ms: int = Field(default=1000, ge=10, le=60_000)
    max_batch: int = Field(default=200, ge=1, le=10_000)


class LayerSourceConfigModel(BaseModel):
    """One entry under the top-level ``layers:`` key.

    Declares a cross-source layer reference resolvable by the DSL fcts
    :func:`geom_within`, :func:`geom_overlaps_any` and
    :func:`layer_lookup`. The runtime translates each entry into a
    DuckDB view at session boot (cf
    :class:`gispulse.runtime.layer_registry.LayerRegistry`).
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=120)
    uri: str = Field(min_length=1, max_length=2048)
    table: str | None = Field(default=None, min_length=1, max_length=120)
    schema_: str = Field(default="public", alias="schema", min_length=1, max_length=120)


class GISPulseConfig(BaseModel):
    """Top-level config schema."""

    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    gpkg: str
    engine: str | None = Field(
        default=None,
        description=(
            "Optional explicit engine override. If unset, GISPulse infers "
            "the engine from the dataset URI (`*.gpkg` → gpkg, "
            "`postgresql://...` → postgis, etc). See "
            "docs-site/guide/engines.md for the full mapping."
        ),
    )
    # v1.6.x — fallback for ``validate:`` rules that do not pin a table.
    # Useful on multi-layer GPKGs when most rules apply to the same
    # canonical layer. Single-layer GPKGs auto-detect at build_runtime
    # time and don't need this knob.
    default_table: str | None = Field(default=None, min_length=1, max_length=120)
    triggers: list[TriggerConfigModel] = Field(default_factory=list)
    validate_rules: list[ValidateRuleConfigModel] = Field(
        default_factory=list,
        alias="validate",
        description=(
            "Declarative validation rules — see "
            "docs-site/guide/dsl-validation.md."
        ),
    )
    # v1.6.x — cross-source layer registry (#122). Each entry creates a
    # DuckDB view at runtime so DSL ``layer='communes'`` resolves
    # against external GPKG / Parquet / PostGIS sources without SQL
    # rewriting downstream.
    layers: list[LayerSourceConfigModel] = Field(
        default_factory=list,
        description=(
            "Cross-source layer declarations — see "
            "docs-site/guide/layers.md."
        ),
    )
    security: SecurityConfigModel = Field(default_factory=SecurityConfigModel)
    runtime: RuntimeConfigModel = Field(default_factory=RuntimeConfigModel)

    @model_validator(mode="after")
    def _validate_layer_names_unique(self) -> "GISPulseConfig":
        seen: set[str] = set()
        for layer in self.layers:
            if layer.name in seen:
                raise ValueError(
                    f"duplicate layer name {layer.name!r} in layers:"
                )
            seen.add(layer.name)
        return self

    def resolved_engine(self) -> str:
        """Return the engine that will actually run this config.

        Wraps :func:`gispulse.runtime.engine_inference.resolve_engine` so
        callers get a single source of truth. Raises
        :class:`gispulse.runtime.engine_inference.EngineInferenceError` on
        conflict between the URI and an explicit override.
        """
        from gispulse.runtime.engine_inference import resolve_engine

        return resolve_engine(self.gpkg, self.engine)


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


class ConfigError(ValueError):
    """Raised when the YAML config is invalid or violates the safety policy."""


def _safe_anchors() -> list[Path]:
    """Return the directories under which user-controlled paths are
    accepted. Anything resolved outside of these roots is rejected.

    We accept:
      * ``cwd``                — the canonical operator location.
      * ``$HOME``              — typical config locations under ``~``.
      * ``tempfile.gettempdir()`` — CI runners and ephemeral pipelines.

    Operators wanting a hardened deploy can ``cd`` into the project
    root before running the CLI, and the ``--gpkg`` override goes
    through the same check. The ``GISPULSE_CONFIG_ALLOW_ROOTS`` env
    var can extend the list at runtime (colon-separated).
    """
    import os as _os
    import tempfile

    anchors: list[Path] = []
    try:
        anchors.append(Path.cwd().resolve())
    except OSError:
        pass
    try:
        anchors.append(Path.home().resolve())
    except (OSError, RuntimeError):
        pass
    try:
        anchors.append(Path(tempfile.gettempdir()).resolve())
    except OSError:  # pragma: no cover - defensive
        pass

    extra = _os.environ.get("GISPULSE_CONFIG_ALLOW_ROOTS", "")
    for raw in extra.split(":"):
        raw = raw.strip()
        if not raw:
            continue
        try:
            anchors.append(Path(raw).expanduser().resolve())
        except OSError:  # pragma: no cover - defensive
            continue

    # Deduplicate while preserving order.
    seen: set[Path] = set()
    unique: list[Path] = []
    for a in anchors:
        if a not in seen:
            seen.add(a)
            unique.append(a)
    return unique


def _check_within_anchors(path: Path, anchors: Iterable[Path]) -> None:
    """Raise :class:`ConfigError` if ``path`` is not under any anchor."""
    for anchor in anchors:
        try:
            path.relative_to(anchor)
        except ValueError:
            continue
        return
    raise ConfigError(
        f"path {path!s} escapes the allowed roots ("
        + ", ".join(str(a) for a in anchors)
        + "). Refusing to load (path traversal guard)."
    )


def _resolve_safe(raw: str | os.PathLike[str], *, anchors: list[Path], must_exist: bool = True) -> Path:
    """Canonicalise ``raw`` and ensure it is under one of the anchors.

    Args:
        raw:        Path string from the YAML (or CLI flag).
        anchors:    List of allowed root directories.
        must_exist: When *True*, use ``resolve(strict=True)`` so a
                    missing target also fails.

    Raises:
        ConfigError: Missing path (when ``must_exist``) or escapes roots.
    """
    p = Path(raw).expanduser()
    if not p.is_absolute():
        # Anchor relative paths to cwd before resolution.
        p = (Path.cwd() / p)
    try:
        resolved = p.resolve(strict=must_exist)
    except FileNotFoundError as exc:
        raise ConfigError(f"path does not exist: {raw!s}") from exc
    except OSError as exc:  # pragma: no cover - defensive
        raise ConfigError(f"cannot resolve path {raw!s}: {exc}") from exc

    _check_within_anchors(resolved, anchors)
    return resolved


def load_config(
    config_path: str | os.PathLike[str],
    *,
    gpkg_override: str | os.PathLike[str] | None = None,
) -> GISPulseConfig:
    """Load + validate a YAML config file.

    Args:
        config_path:   Path to the ``triggers.yaml`` file.
        gpkg_override: Optional ``--gpkg`` value that wins over the
                       ``gpkg:`` key. Subject to the same path-traversal
                       guard.

    Returns:
        A validated :class:`GISPulseConfig` with absolute resolved paths.

    Raises:
        ConfigError:        Path traversal, missing file, or schema violation.
        pydantic.ValidationError: Invalid YAML schema (re-raised as-is so
                                  callers can format detailed messages).
    """
    anchors = _safe_anchors()
    cfg_path = _resolve_safe(config_path, anchors=anchors, must_exist=True)

    if not cfg_path.is_file():
        raise ConfigError(f"config path is not a file: {cfg_path}")

    # SECURITY: never use yaml.load — only safe_load. This is the single
    # most important guard in this module.
    text = cfg_path.read_text(encoding="utf-8")
    return parse_config_text(
        text,
        gpkg_override=gpkg_override,
        source=str(cfg_path),
    )


def _normalize_on_keys(raw: dict[str, Any]) -> None:
    """Restore the trigger ``on:`` key mangled into ``True`` by YAML 1.1.

    ``yaml.safe_load`` coerces the bare scalar ``on`` to the boolean
    ``True`` per the YAML 1.1 boolean grammar (the "Norway problem").
    The ``on:`` trigger discriminator (#195) therefore arrives as a
    boolean dict key, which pydantic rejects (keys must be strings). We
    move it back to the string ``"on"`` in place, before validation.

    A trigger that somehow carries *both* a boolean ``on`` key and an
    explicit string ``"on"`` key is a config error — we refuse rather
    than silently picking one.
    """
    triggers = raw.get("triggers")
    if not isinstance(triggers, list):
        return
    for entry in triggers:
        if isinstance(entry, dict) and True in entry:
            if "on" in entry:
                raise ConfigError(
                    "trigger declares the 'on:' key twice (once bare, once "
                    "quoted) — keep a single 'on:'"
                )
            entry["on"] = entry.pop(True)


def parse_config_text(
    text: str,
    *,
    gpkg_override: str | os.PathLike[str] | None = None,
    source: str = "<inline>",
    resolve_gpkg: bool = True,
) -> GISPulseConfig:
    """Parse + validate a YAML config from an in-memory string.

    Issue #94: the HTTP ``POST /triggers/import`` endpoint receives the
    YAML over the wire (multipart upload or raw body), so :func:`load_config`
    delegates here once the file has been read. This function performs no
    I/O on the YAML itself — only the optional GPKG path resolution
    (``resolve_gpkg=True``) which the importer can disable when it already
    binds the import to a tenant ``dataset_id``.

    Args:
        text:          The YAML document as a UTF-8 string.
        gpkg_override: Optional ``--gpkg`` value that wins over the
                       ``gpkg:`` key in *text*. Subject to the same
                       path-traversal guard as :func:`load_config`.
        source:        Human-readable origin used in error messages
                       (file path or ``"<inline>"`` / ``"<upload>"``).
        resolve_gpkg:  When ``True`` (default), the ``gpkg:`` path is
                       required and resolved through
                       :func:`_resolve_safe`. When ``False`` the field is
                       still parsed (kept verbatim) but path resolution
                       and on-disk existence checks are skipped — used
                       by the HTTP importer in dry-run mode where the
                       caller binds the YAML to an existing tenant
                       dataset rather than to a path on the server's
                       filesystem.

    Returns:
        A validated :class:`GISPulseConfig`.

    Raises:
        ConfigError:        Path traversal, missing ``gpkg`` key, or
                            schema violation.
    """
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {source}: {exc}") from exc

    if raw is None:
        raise ConfigError(f"empty config: {source}")
    if not isinstance(raw, dict):
        raise ConfigError(
            f"config root must be a mapping, got {type(raw).__name__}"
        )

    if resolve_gpkg:
        anchors = _safe_anchors()
        gpkg_raw = (
            gpkg_override if gpkg_override is not None else raw.get("gpkg")
        )
        if not gpkg_raw:
            raise ConfigError("missing 'gpkg' key (no --gpkg override either)")
        gpkg_resolved = _resolve_safe(gpkg_raw, anchors=anchors, must_exist=True)
        raw["gpkg"] = str(gpkg_resolved)
    else:
        # Importer mode: keep whatever's in the YAML so the preview can
        # report it, but tolerate missing / non-existent paths since the
        # caller is responsible for binding to a tenant dataset.
        if gpkg_override is not None:
            raw["gpkg"] = str(gpkg_override)
        elif "gpkg" not in raw or not raw.get("gpkg"):
            # ``GISPulseConfig`` requires a string here; fall back to a
            # placeholder so pydantic validates the rest of the doc.
            raw["gpkg"] = "<unbound>"

    # YAML 1.1 coerces the bare trigger key ``on`` to boolean True;
    # restore it to the string ``"on"`` so pydantic accepts it (#195).
    _normalize_on_keys(raw)

    try:
        config = GISPulseConfig.model_validate(raw)
    except Exception as exc:  # ValidationError or others
        raise ConfigError(f"invalid config schema: {exc}") from exc

    _warn_dialect_drift(config, source)
    return config


def _warn_dialect_drift(config: GISPulseConfig, source: str) -> None:
    """Emit a ``dialect_drift`` warning per PostGIS-only construct (#146).

    ADR 0001 makes DuckDB-spatial the contract dialect. A ``run_sql`` /
    ``predicate`` using a PostGIS-only construct loads fine but fails at
    the first tick — so we surface it here, at config-load time, unless
    the config pins ``engine: postgis`` (in which case the constructs
    are legitimate and :func:`scan_for_dialect_drift` returns nothing).
    """
    from gispulse.runtime.dialect_scanner import scan_for_dialect_drift

    for finding in scan_for_dialect_drift(config):
        log.warning(
            "dialect_drift",
            source=source,
            construct=finding.construct,
            location=finding.location,
            hint=finding.hint,
        )


# ---------------------------------------------------------------------------
# Mapping into domain dataclasses
# ---------------------------------------------------------------------------


def to_triggers(config: GISPulseConfig) -> list["Trigger"]:
    """Convert YAML trigger entries into domain :class:`Trigger` objects.

    Maps:
        - ``type: webhook`` ─▶ ``ActionType.WEBHOOK``
        - ``type: set_field`` ─▶ ``ActionType.SET_FIELD``
        - ``type: run_sql`` ─▶ ``ActionType.RUN_SQL``
        - ``type: log_event`` ─▶ ``ActionType.LOG_EVENT``
        - ``type: notify`` ─▶ ``ActionType.NOTIFY``

    Predicate handling (S4)
    -----------------------
    When ``predicate:`` is set on the YAML entry, the DSL string is
    parsed into a :class:`PredicateNode` AST and stored under
    ``Trigger.conditions["predicate_ast"]``. The verbatim source stays
    in ``conditions["predicate"]`` for round-trip / observability. The
    headless runtime evaluates the AST against the row payload before
    dispatching any action; trigger entries without a predicate keep
    the pre-S4 always-fire behaviour.
    """
    from gispulse.core.graph import ActionDef, ActionType
    from gispulse.core.enums import TriggerEvent, TriggerType, TriggerCategory
    from gispulse.core.models import Trigger
    from gispulse.runtime.predicate_dsl import parse_predicate

    type_map = {
        "webhook": ActionType.WEBHOOK,
        "set_field": ActionType.SET_FIELD,
        "run_sql": ActionType.RUN_SQL,
        "log_event": ActionType.LOG_EVENT,
        "notify": ActionType.NOTIFY,
        "tag_field": ActionType.TAG_FIELD,
    }

    triggers: list[Trigger] = []
    for entry in config.triggers:
        actions: list[ActionDef] = []
        for ac in entry.actions:
            cfg: dict[str, Any] = {}
            if ac.type == "webhook":
                cfg["url"] = ac.url or ""
            elif ac.type == "set_field":
                cfg["field"] = ac.field or ""
                cfg["value"] = ac.value
            elif ac.type == "run_sql":
                cfg["expression"] = ac.expression or ""
            elif ac.type == "notify":
                cfg["channel"] = ac.channel or "gispulse_events"
                if ac.payload_template is not None:
                    cfg["payload_template"] = ac.payload_template
            elif ac.type == "tag_field":
                cfg["column"] = ac.column or ""
                cfg["value"] = ac.value
                if ac.message_column:
                    cfg["message_column"] = ac.message_column
                if ac.message is not None:
                    cfg["message"] = ac.message
            # log_event takes no required config

            actions.append(
                ActionDef(action_type=type_map[ac.type], config=cfg),
            )

        # Source-watched trigger (#195) — fires on an external-source
        # revision change, not a local DML edit. It carries the watched
        # source URI (and optional poll frequency) in ``conditions``;
        # ``TriggerEvaluator._eval_source_changed`` reads ``source`` /
        # ``last_revision`` from there.
        if entry.on is not None:
            source_conditions: dict[str, Any] = {
                "yaml_name": entry.name,
                "source": entry.on.source_changed,
            }
            if entry.on.frequency:
                source_conditions["frequency"] = entry.on.frequency
            triggers.append(
                Trigger(
                    name=entry.name,
                    description=(
                        f"Source watcher (source={entry.on.source_changed})"
                    ),
                    event=TriggerEvent.DATA_CHANGED,
                    trigger_type=TriggerType.SOURCE_CHANGED,
                    category=TriggerCategory.INTEGRATION,
                    conditions=source_conditions,
                    actions=actions,
                    enabled=entry.enabled,
                )
            )
            continue

        # Stash the user-friendly metadata on the trigger so log lines
        # and validate output can reference the YAML name. ``events`` is
        # the expanded form consumed by ``DMLConditions.events`` (see #119)
        # so a config with ``when: [UPDATE]`` keeps matching the watcher's
        # granular ``UPDATE_GEOM`` / ``UPDATE_ATTR`` outputs.
        conditions: dict[str, Any] = {
            "yaml_name": entry.name,
            "table": entry.table,
            "pk_col": entry.pk_col,
            "when": list(entry.when),
            "events": _expand_when_to_events(list(entry.when)),
        }
        if entry.predicate:
            conditions["predicate"] = entry.predicate
            # Compile the DSL and stash the AST. The pydantic validator
            # already parsed it once for early error surfacing; we
            # re-parse here so the runtime path holds an AST object
            # rather than a string. This is a few µs and keeps the
            # config model JSON-serialisable.
            conditions["predicate_ast"] = parse_predicate(entry.predicate)

        trigger = Trigger(
            name=entry.name,
            description=f"Loaded from YAML config (table={entry.table})",
            event=TriggerEvent.DATA_CHANGED,
            trigger_type=TriggerType.DML,
            category=TriggerCategory.DATA,
            conditions=conditions,
            actions=actions,
            enabled=entry.enabled,
        )
        triggers.append(trigger)

    return triggers


# ---------------------------------------------------------------------------
# Validation against the GPKG
# ---------------------------------------------------------------------------


def validate_against_gpkg(config: GISPulseConfig) -> list[str]:
    """Open the GPKG and check every trigger references a real layer.

    This is a *separate* step from :func:`load_config` because opening
    the GPKG is comparatively expensive and the bare schema validation
    is enough for ``--validate`` shape checks. Callers who want full
    structural validation chain ``load_config`` then this function.

    Args:
        config: A validated :class:`GISPulseConfig`.

    Returns:
        List of human-readable error strings. Empty list = valid.
    """
    from gispulse.persistence.gpkg_engine import GeoPackageEngine

    errors: list[str] = []

    gpkg_path = Path(config.gpkg)
    if not gpkg_path.exists():
        return [f"gpkg file not found: {gpkg_path}"]

    engine = GeoPackageEngine(path=gpkg_path)
    try:
        engine.open()
    except Exception as exc:
        return [f"cannot open gpkg {gpkg_path}: {exc}"]

    try:
        try:
            layers = set(engine.list_layers() or [])
        except Exception as exc:
            errors.append(f"cannot list layers in {gpkg_path}: {exc}")
            return errors

        # Internal GPKG metadata tables are also valid trigger targets
        # (they show up in _gispulse_change_log too) but we don't
        # encourage that path. We just don't reject them.
        for entry in config.triggers:
            # Source-watched triggers (#195) reference an external source,
            # not a GPKG layer — there is no table to check.
            if entry.on is None and entry.table not in layers:
                errors.append(
                    f"trigger {entry.name!r}: table {entry.table!r} not "
                    f"found in {gpkg_path.name} (available: "
                    f"{', '.join(sorted(layers)) or 'none'})"
                )
            for ac in entry.actions:
                if ac.type == "webhook" and not ac.url:
                    errors.append(
                        f"trigger {entry.name!r}: webhook action requires url"
                    )
                if ac.type == "set_field" and not ac.field:
                    errors.append(
                        f"trigger {entry.name!r}: set_field action requires field"
                    )
                if ac.type == "run_sql" and not ac.expression:
                    errors.append(
                        f"trigger {entry.name!r}: run_sql action requires expression"
                    )

    finally:
        try:
            engine.close()
        except Exception:  # pragma: no cover - defensive
            pass

    return errors


__all__ = [
    "CONFIG_VERSION",
    "ActionConfigModel",
    "ConfigError",
    "GISPulseConfig",
    "OnConfigModel",
    "RuntimeConfigModel",
    "SecurityConfigModel",
    "TriggerConfigModel",
    "load_config",
    "to_triggers",
    "validate_against_gpkg",
]
