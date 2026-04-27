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
from pydantic import BaseModel, ConfigDict, Field, field_validator

if TYPE_CHECKING:  # pragma: no cover
    from core.graph import ActionDef
    from core.models import Trigger


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
_SUPPORTED_DML_OPS: tuple[str, ...] = ("INSERT", "UPDATE", "DELETE")


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

    type: Literal["webhook", "set_field", "run_sql", "log_event", "notify"]
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


class TriggerConfigModel(BaseModel):
    """One trigger entry in the YAML config."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=120)
    table: str = Field(min_length=1, max_length=120)
    pk_col: str = Field(default="fid", min_length=1, max_length=64)
    when: list[Literal["INSERT", "UPDATE", "DELETE"]] = Field(
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

    @field_validator("when")
    @classmethod
    def _no_empty_when(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("when must list at least one of INSERT/UPDATE/DELETE")
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


class RuntimeConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    poll_interval_ms: int = Field(default=1000, ge=10, le=60_000)
    max_batch: int = Field(default=200, ge=1, le=10_000)


class GISPulseConfig(BaseModel):
    """Top-level config schema."""

    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    gpkg: str
    triggers: list[TriggerConfigModel] = Field(default_factory=list)
    security: SecurityConfigModel = Field(default_factory=SecurityConfigModel)
    runtime: RuntimeConfigModel = Field(default_factory=RuntimeConfigModel)


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
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {cfg_path}: {exc}") from exc

    if raw is None:
        raise ConfigError(f"empty config file: {cfg_path}")
    if not isinstance(raw, dict):
        raise ConfigError(
            f"config root must be a mapping, got {type(raw).__name__}"
        )

    # Resolve the GPKG path (override wins). We do this *before*
    # pydantic validation so the schema sees an absolute path string.
    gpkg_raw = gpkg_override if gpkg_override is not None else raw.get("gpkg")
    if not gpkg_raw:
        raise ConfigError("missing 'gpkg' key (no --gpkg override either)")
    gpkg_resolved = _resolve_safe(gpkg_raw, anchors=anchors, must_exist=True)
    raw["gpkg"] = str(gpkg_resolved)

    # Pydantic validation (strict, extra=forbid).
    try:
        config = GISPulseConfig.model_validate(raw)
    except Exception as exc:  # ValidationError or others
        raise ConfigError(f"invalid config schema: {exc}") from exc

    return config


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
    from core.graph import ActionDef, ActionType
    from core.enums import TriggerEvent, TriggerType, TriggerCategory
    from core.models import Trigger
    from gispulse.runtime.predicate_dsl import parse_predicate

    type_map = {
        "webhook": ActionType.WEBHOOK,
        "set_field": ActionType.SET_FIELD,
        "run_sql": ActionType.RUN_SQL,
        "log_event": ActionType.LOG_EVENT,
        "notify": ActionType.NOTIFY,
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
            # log_event takes no required config

            actions.append(
                ActionDef(action_type=type_map[ac.type], config=cfg),
            )

        # Stash the user-friendly metadata on the trigger so log lines
        # and validate output can reference the YAML name.
        conditions: dict[str, Any] = {
            "yaml_name": entry.name,
            "table": entry.table,
            "pk_col": entry.pk_col,
            "when": list(entry.when),
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
    from persistence.gpkg_engine import GeoPackageEngine

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
            if entry.table not in layers:
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
    "RuntimeConfigModel",
    "SecurityConfigModel",
    "TriggerConfigModel",
    "load_config",
    "to_triggers",
    "validate_against_gpkg",
]
