"""Wire :class:`SourceWatcherRegistry` into the watch runtime (issue #197).

PR #189 delivered :class:`~persistence.source_watcher.SourceWatcherRegistry`
but nothing instantiated it — the external-source watcher was dead code.
This module is the missing bridge between three pieces:

- the ``source_changed`` triggers parsed from ``triggers.yaml`` (#195),
- the live :class:`~core.sources.DataSource` objects registered by the
  ``gispulse.data_sources`` plugins (resolved through
  :data:`core.sources.SOURCES`),
- the runtime :class:`~gispulse.adapters.esb.action_dispatcher.ActionDispatcher`
  that runs the trigger's actions.

Flow::

    SourceWatcherRegistry.poll()        # revision() differs
        └─▶ SourceChangeBridge.broadcast("source.changed", payload)
                └─▶ match SOURCE_CHANGED triggers by source URI
                        └─▶ ActionDispatcher.dispatch_all(trigger.actions)

:func:`build_source_watcher` is called once by ``run_watch_loop`` after
the runtime is built; the returned registry runs its own slow daemon
thread (hours-scale poll), independent of the fast DML tick.
"""

from __future__ import annotations

from typing import Any, Callable
from urllib.parse import urlparse

from core.enums import TriggerType
from core.logging import get_logger
from persistence.source_watcher import SourceWatcherRegistry

log = get_logger(__name__)

# A resolver maps a ``<source>://<entry>`` URI to ``(DataSource, entry_id)``.
SourceResolver = Callable[[str], "tuple[Any, str]"]


def _is_source_trigger(trigger: Any) -> bool:
    """True when ``trigger`` is a SOURCE_CHANGED trigger."""
    return trigger.trigger_type == TriggerType.SOURCE_CHANGED


def parse_source_uri(uri: str) -> tuple[str, str]:
    """Split ``<source>://<entry>`` into ``(source_name, entry_id)``.

    Example: ``cadastre://parcelles`` → ``("cadastre", "parcelles")``.

    Raises:
        ValueError: when the URI has no scheme or no entry component.
    """
    parsed = urlparse(uri)
    name = parsed.scheme
    entry = f"{parsed.netloc}{parsed.path}".strip("/")
    if not name or not entry:
        raise ValueError(
            f"invalid source URI {uri!r} — expected '<source>://<entry>'"
        )
    return name, entry


def resolve_via_registry(uri: str) -> tuple[Any, str]:
    """Default resolver — look the source up in :data:`core.sources.SOURCES`."""
    from core.sources import SOURCES

    name, entry = parse_source_uri(uri)
    return SOURCES.get(name), entry


def _make_context(trigger: Any, data: dict[str, Any]) -> Any:
    """Build the :class:`TriggerContext` an action handler expects.

    A source-changed event has no DML row, so ``table`` / ``row_id`` are
    empty and the source payload (source URI, new + previous revision)
    travels in ``new_attrs`` for handlers that want to reference it.
    """
    from gispulse.core.dispatcher import EvalResult, TriggerContext

    return TriggerContext(
        trigger=trigger,
        eval_result=EvalResult(matched=True),
        table="",
        operation="source_changed",
        new_attrs=dict(data),
    )


class SourceChangeBridge:
    """EventHub adapter turning a ``source.changed`` broadcast into dispatch.

    Implements the ``broadcast(event_type, data)`` contract that
    :class:`SourceWatcherRegistry` calls. On a ``source.changed`` event
    it selects the SOURCE_CHANGED triggers whose configured ``source``
    equals the event's source URI and dispatches their actions.

    The registry only broadcasts on an *actual* revision change (it
    diffs revisions internally), so matching here is a plain source-URI
    equality check — no revision comparison is repeated.
    """

    def __init__(self, triggers: list[Any], dispatcher: Any) -> None:
        self._triggers = [t for t in triggers if _is_source_trigger(t)]
        self._dispatcher = dispatcher
        self.fired = 0  # cumulative dispatched-trigger count (observability)

    def broadcast(
        self, event_type: str, data: dict[str, Any] | None = None
    ) -> None:
        if event_type != "source.changed" or not data:
            return
        event_source = data.get("source")
        for trig in self._triggers:
            if not trig.enabled:
                continue
            if trig.conditions.get("source") != event_source:
                continue
            try:
                self._dispatcher.dispatch_all(
                    trig.actions, _make_context(trig, data)
                )
            except Exception as exc:  # noqa: BLE001 — one trigger never blocks the rest
                log.warning(
                    "source_changed_dispatch_failed",
                    trigger=trig.name,
                    source=event_source,
                    error=str(exc),
                )
                continue
            self.fired += 1
            log.info(
                "source_changed_trigger_fired",
                trigger=trig.name,
                source=event_source,
                revision=data.get("revision"),
            )


def build_source_watcher(
    triggers: list[Any],
    dispatcher: Any,
    *,
    resolver: SourceResolver = resolve_via_registry,
) -> SourceWatcherRegistry | None:
    """Build a :class:`SourceWatcherRegistry` wired to dispatch triggers.

    Args:
        triggers:   The full trigger list (DML + source); only the
                    SOURCE_CHANGED ones are wired.
        dispatcher: The runtime ``ActionDispatcher`` that runs actions.
        resolver:   ``uri -> (DataSource, entry_id)``. Defaults to
                    :func:`resolve_via_registry`; tests inject a fake.

    Returns:
        A ready (not-yet-started) registry, or ``None`` when the config
        declares no source trigger. An unresolvable URI is logged and
        skipped — one bad source never blocks the others.
    """
    source_triggers = [t for t in triggers if _is_source_trigger(t)]
    if not source_triggers:
        return None

    bridge = SourceChangeBridge(triggers, dispatcher)
    registry = SourceWatcherRegistry(event_hub=bridge)

    for trig in source_triggers:
        uri = trig.conditions.get("source")
        if not uri:
            log.warning("source_watch_missing_uri", trigger=trig.name)
            continue
        try:
            source, entry_id = resolver(uri)
        except (KeyError, ValueError) as exc:
            log.warning("source_watch_unresolved", uri=uri, error=str(exc))
            continue
        try:
            registry.register(
                source,
                entry_id,
                frequency=trig.conditions.get("frequency"),
            )
        except Exception as exc:  # noqa: BLE001 — bad baseline revision, etc.
            log.warning(
                "source_watch_register_failed", uri=uri, error=str(exc)
            )
    return registry


__all__ = [
    "SourceChangeBridge",
    "SourceResolver",
    "build_source_watcher",
    "parse_source_uri",
    "resolve_via_registry",
]
