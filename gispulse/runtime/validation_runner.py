"""Validation runner — evaluates ``validate:`` rules against change events.

Closes the runtime half of the v1.6.0 ``validate:`` story. The schema
side (``ValidateRuleConfigModel``) shipped in #129; this module is the
component that:

1. Compiles each rule to safe DuckDB SQL once at boot using
   :func:`gispulse.dsl.compile_expression` in ``boolean`` mode.
2. For every INSERT / UPDATE_GEOM / UPDATE_ATTR change-log row, runs
   each rule against the underlying row via an injected ``sql_evaluator``
   callable. Returning ``False`` (or ``NULL``) means the rule failed.
3. Broadcasts ``validation.failed`` on the event hub for every failure
   so portal / QGIS clients can render the status.

Out of scope (tracked separately):
- ``mode: tag`` dispatch to a ``tag_field`` action — for now we log the
  failure and broadcast it, then fall back to ``warn`` semantics. The
  wiring to :class:`ActionDispatcher` lands when the trigger pipeline
  exposes ``TriggerContext`` to non-trigger callers (TODO: track in a
  follow-up issue).
- Cross-source layers (``geom_within(layer='communes')`` against a
  separate file) — handled by the cross-source ATTACH plumbing in
  the v1.6.x line. Rules referencing only ``layer='self'`` work today.

Architecture:
    ValidationRunner is engine-agnostic. The caller injects a
    ``sql_evaluator(sql: str, params: list) -> Any`` callable so the
    runner stays unit-testable; production wiring uses a thin DuckDB
    session that ATTACHes the GPKG and routes ``ST_*`` calls through
    the spatial extension. See ``gispulse.runtime.duckdb_engine``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from gispulse.dsl import CompilationContext, DSLValidationError, compile_expression

logger = logging.getLogger(__name__)


class _EventHubProtocol(Protocol):
    def broadcast(
        self, event_type: str, data: dict[str, Any] | None = None
    ) -> None: ...  # pragma: no cover


# ---------------------------------------------------------------------------
# Compiled rule
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CompiledValidateRule:
    """A YAML ``validate:`` entry compiled to safe DuckDB SQL.

    ``rule_sql`` is the boolean expression as emitted by
    :func:`gispulse.dsl.compile_expression`. The runner wraps it in a
    ``SELECT NOT (<rule_sql>) AS failed FROM "<table>" WHERE "<pk>" = ?``
    so a missing row evaluates to ``NULL`` (treated as a non-failure).
    """

    id: str
    table: str
    pk_col: str
    rule_sql: str
    mode: str  # "warn" | "tag"
    tag_field: str | None
    message: str | None
    enabled: bool = True


@dataclass(frozen=True, slots=True)
class ValidationFailure:
    """One rule failure, ready for log / WS broadcast / tag dispatch."""

    rule_id: str
    table: str
    row_id: Any
    mode: str
    message: str | None = None
    tag_field: str | None = None


@dataclass(frozen=True, slots=True)
class CompileError:
    """A rule that could not be compiled at boot.

    Surfaced separately so the caller can decide whether to abort the
    boot (strict mode) or skip the bad rule (lenient mode).
    """

    rule_id: str
    error: str


@dataclass
class CompileResult:
    """Outcome of :func:`compile_validate_rules`."""

    rules: list[CompiledValidateRule] = field(default_factory=list)
    errors: list[CompileError] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Compilation
# ---------------------------------------------------------------------------


def compile_validate_rules(
    rules: list[Any],
    *,
    table: str,
    source_epsg: str | None,
    pk_col: str = "id",
    geom_column: str = "geom",
) -> CompileResult:
    """Compile a list of :class:`ValidateRuleConfigModel` to runner-ready form.

    Parameters
    ----------
    rules:
        Iterable of ``ValidateRuleConfigModel`` instances (typically
        ``GISPulseConfig.validate_rules``). The runner only reads
        ``id``, ``rule``, ``mode``, ``tag_field``, ``message``,
        ``enabled``.
    table:
        Default table the rules are scoped to. Rules are evaluated
        against a single table at a time (one runner per ``(dataset,
        table)`` pair). Cross-table validation is out of scope.
    source_epsg:
        Source CRS of the dataset's geometry column. Forwarded to
        :class:`CompilationContext` so CRS-aware fcts (``geom_area_m2``)
        compile cleanly.
    pk_col:
        Primary-key column used for ``geom_overlaps_any(exclude_self=True)``
        guards. Defaults to ``"id"``.
    geom_column:
        Geometry column name. Defaults to ``"geom"``.
    """
    ctx = CompilationContext(
        geom_column=geom_column,
        source_epsg=source_epsg,
        current_table=table,
        pk_col=pk_col,
    )
    out: list[CompiledValidateRule] = []
    errors: list[CompileError] = []
    for rule in rules:
        if not getattr(rule, "enabled", True):
            continue
        try:
            sql = compile_expression(rule.rule, ctx, mode="boolean")
        except DSLValidationError as exc:
            errors.append(CompileError(rule_id=rule.id, error=str(exc)))
            continue
        out.append(
            CompiledValidateRule(
                id=rule.id,
                table=table,
                pk_col=pk_col,
                rule_sql=sql,
                mode=rule.mode,
                tag_field=rule.tag_field,
                message=rule.message,
                enabled=True,
            )
        )
    return CompileResult(rules=out, errors=errors)


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


class ValidationRunner:
    """Evaluates compiled validate rules against per-row events.

    The runner is *not* coupled to the change-log watcher; the caller
    wires it (typically inside the watcher's ``process_row`` after the
    trigger evaluator) and decides which DML ops trigger evaluation.
    INSERT and UPDATE_* are the canonical set; DELETE is not evaluated
    because the row no longer exists.

    Parameters
    ----------
    rules:
        Compiled rules from :func:`compile_validate_rules`.
    sql_evaluator:
        ``(sql: str, params: list) -> Any``. Should return a single-row
        result (or a list with one row) where the first column is the
        boolean ``failed`` flag. ``None`` rows are treated as non-failures
        — the row may have been deleted between the change-log capture
        and the rule evaluation.
    hub:
        Optional :class:`_EventHubProtocol`. When set, every failure is
        broadcast on ``validation.failed`` so QGIS / portal subscribers
        can react.
    dataset_id:
        Stable handle of the dataset, included verbatim in WS payloads
        so multi-tenant consumers can disambiguate failures across
        datasets that share table names.
    """

    def __init__(
        self,
        rules: list[CompiledValidateRule],
        sql_evaluator: Callable[[str, list[Any]], Any],
        *,
        hub: _EventHubProtocol | None = None,
        dataset_id: str = "",
        action_dispatcher: Any | None = None,
    ) -> None:
        self._rules = rules
        self._sql_evaluator = sql_evaluator
        self._hub = hub
        self._dataset_id = dataset_id
        # v1.6.0 — when set, ``mode: tag`` failures dispatch a synthetic
        # ``ActionType.TAG_FIELD`` action through the dispatcher so the
        # row receives ``failed:<rule.id>`` on the configured column.
        # Without a dispatcher, tag failures degrade to warn (log + WS
        # broadcast) per the dsl-validation.md docs.
        self._action_dispatcher = action_dispatcher

    @property
    def rule_count(self) -> int:
        return len(self._rules)

    def evaluate(self, table: str, row_id: Any) -> list[ValidationFailure]:
        """Run every compiled rule for ``table`` against the given row.

        Failures are returned in declaration order. Each failure is also
        broadcast on the event hub when one is configured.
        """
        failures: list[ValidationFailure] = []
        for rule in self._rules:
            if rule.table != table:
                continue
            try:
                failed = self._evaluate_rule(rule, row_id)
            except Exception as exc:  # noqa: BLE001 — driver-specific
                logger.warning(
                    "validation_rule_eval_failed rule=%s table=%s row=%s: %s",
                    rule.id,
                    rule.table,
                    row_id,
                    exc,
                )
                continue
            if not failed:
                continue
            failure = ValidationFailure(
                rule_id=rule.id,
                table=rule.table,
                row_id=row_id,
                mode=rule.mode,
                message=rule.message,
                tag_field=rule.tag_field,
            )
            failures.append(failure)
            self._broadcast(failure)
            if rule.mode == "tag":
                self._dispatch_tag(rule, row_id)
        return failures

    def _dispatch_tag(
        self, rule: CompiledValidateRule, row_id: Any
    ) -> None:
        """Emit a synthetic ``TAG_FIELD`` action for a ``mode: tag`` failure.

        Builds a one-off :class:`Trigger` + :class:`TriggerContext` so the
        existing :class:`ActionDispatcher._tag_field` handler — already
        battle-tested via #123 — can write the row without a special
        code path. The synthetic trigger's ``id`` is logged as
        ``trigger:<uuid>`` by the dispatcher for the origin-tagging
        guard, so the AFTER UPDATE refire skip works exactly like a
        regular tag_field action.

        No-op when ``action_dispatcher`` is unset (degraded mode warn,
        cf dsl-validation.md) or when ``rule.tag_field`` is missing
        (the schema validator already enforces this; the runtime guard
        is defence-in-depth).
        """
        if self._action_dispatcher is None or not rule.tag_field:
            return
        try:
            from core.graph import ActionDef, ActionType, EvalResult
            from core.models import Trigger
            from gispulse.core.dispatcher import TriggerContext

            synthetic = Trigger(
                name=f"validate:{rule.id}",
                description=f"Synthetic trigger for validate rule {rule.id!r}",
            )
            action = ActionDef(
                action_type=ActionType.TAG_FIELD,
                config={
                    "column": rule.tag_field,
                    "value": f"failed:{rule.id}",
                    "message": rule.message,
                },
            )
            ctx = TriggerContext(
                trigger=synthetic,
                eval_result=EvalResult(matched=True),
                table=rule.table,
                row_id=str(row_id),
                operation="VALIDATE",
            )
            self._action_dispatcher.dispatch(action, ctx)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "validation_tag_dispatch_failed rule=%s row=%s err=%s",
                rule.id,
                row_id,
                exc,
            )

    def _evaluate_rule(self, rule: CompiledValidateRule, row_id: Any) -> bool:
        """Return True when the rule failed for ``row_id``.

        Wraps the rule SQL in ``SELECT NOT (<rule>) AS failed`` so the
        runner gets a single boolean per call. ``NULL`` (deleted row,
        non-applicable rule) is treated as a non-failure.
        """
        sql = (
            f'SELECT NOT ({rule.rule_sql}) AS failed '
            f'FROM "{rule.table}" WHERE "{rule.pk_col}" = ?'
        )
        rows = self._sql_evaluator(sql, [row_id])
        if not rows:
            return False
        first = rows[0]
        if isinstance(first, dict):
            value = first.get("failed")
        else:
            try:
                value = first[0]
            except (IndexError, TypeError):
                value = None
        if value is None:
            return False
        return bool(value)

    def _broadcast(self, failure: ValidationFailure) -> None:
        if self._hub is None:
            return
        try:
            self._hub.broadcast(
                "validation.failed",
                {
                    "dataset_id": self._dataset_id,
                    "rule_id": failure.rule_id,
                    "table": failure.table,
                    "row_id": failure.row_id,
                    "mode": failure.mode,
                    "message": failure.message,
                    "tag_field": failure.tag_field,
                },
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "validation_broadcast_failed rule=%s err=%s",
                failure.rule_id,
                exc,
            )


# ---------------------------------------------------------------------------
# DuckDB sql_evaluator factory
# ---------------------------------------------------------------------------


def make_gpkg_sql_evaluator(
    gpkg_path: str,
    *,
    alias: str = "gpkg",
) -> Callable[[str, list[Any]], list[Any]]:
    """Return a sql_evaluator that runs SQL against ``gpkg_path`` via DuckDB.

    Opens a DuckDB connection with the spatial extension lazy-loaded
    (cf :mod:`gispulse.runtime.duckdb_engine`) and ATTACHes the GPKG
    as a SQLite catalog under ``alias``. Subsequent ``USE alias`` makes
    bare-table references in the rule SQL resolve naturally — the
    same ``"parcels"`` identifier compiled by
    :func:`gispulse.dsl.compile_expression` works against the attached
    GeoPackage without rewriting.

    The connection is held by the closure for the evaluator's lifetime;
    callers should treat the returned callable as owning the resource
    and discard it (letting the conn close) when the runner stops.

    Caveats:
    - ATTACH on a SQLite database is read-only by default in DuckDB —
      this is fine for validation. Concurrent SQLite writers (the GPKG
      AFTER triggers populating ``_gispulse_change_log``) coexist with
      this read connection because SQLite serialises around the WAL.
    - Cross-source layers (``geom_within(layer='communes')`` referencing
      a separate GPKG) require an additional ``ATTACH`` per layer —
      handled by the cross-source plumbing in #122 follow-ups.
    """
    from gispulse.runtime.duckdb_engine import get_spatial_connection

    if not gpkg_path or not isinstance(gpkg_path, str):
        raise ValueError(f"gpkg_path must be a non-empty string, got {gpkg_path!r}")

    conn = get_spatial_connection()
    # Use parameter substitution where possible; the ATTACH form below
    # has no SQL-parameter slot in DuckDB so we splice the path. We
    # validate the alias as an identifier and reject paths containing
    # single quotes that would break out of the literal.
    if "'" in gpkg_path or "\x00" in gpkg_path:
        raise ValueError(f"gpkg_path contains illegal characters: {gpkg_path!r}")
    import re as _re

    if not _re.match(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$", alias):
        raise ValueError(f"invalid alias {alias!r}")
    conn.execute(f"ATTACH '{gpkg_path}' AS {alias} (TYPE SQLITE)")
    conn.execute(f"USE {alias}")

    def evaluator(sql: str, params: list[Any]) -> list[Any]:
        return conn.execute(sql, params).fetchall()

    return evaluator
