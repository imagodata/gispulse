"""Headless trigger runtime for GISPulse Mode 1 (CLI/SDK).

This module is the FastAPI-free counterpart of the lifespan-bound
trigger wiring at ``gispulse/adapters/http/app.py:309-391``. The HTTP
server reproduces the same plumbing inside an ``@asynccontextmanager``
because it also needs uvicorn and the WebSocket fan-out; here we only
need a single tick (``run_once``) or a daemon-friendly start/stop pair.

Architecture (Mode 1, GPKG):

    GPKG SQLite triggers ──▶ _gispulse_change_log
                                    │
                                    │  (poll)
                                    ▼
                          ChangeLogWatcher._tick()
                                    │
                                    ├──▶ NullEventHub.broadcast(...)  (no-op)
                                    │
                                    ├──▶ TriggerEvaluator.evaluate(...)
                                    │
                                    └──▶ ActionDispatcher.dispatch_all(
                                            WEBHOOK / SET_FIELD / RUN_SQL / ...
                                        )

Thread safety
-------------
``ChangeLogWatcher`` runs the polling loop on a daemon thread.
:meth:`HeadlessRuntime.run_once` does **not** start that thread — it
manually drives a single ``_tick()`` for ``--once`` mode so the CLI can
exit cleanly. :meth:`HeadlessRuntime.start` / :meth:`stop` are exposed
for future ``--watch`` mode (S5) but stay unused this PR.

AGPL note
---------
Imports are restricted to the public OSS tree (``gispulse.*``,
``persistence.*``, ``rules.*``, ``core.*``, ``capabilities.*``). The
runtime never reaches into ``gispulse-enterprise``.
"""

from __future__ import annotations

import sqlite3
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Iterable, Sequence

from core.logging import get_logger

if TYPE_CHECKING:  # pragma: no cover - typing only
    from core.models import Trigger
    from gispulse.adapters.esb.action_dispatcher import ActionDispatcher
    from gispulse.runtime.sqlite_retry import RetryingSqlExecutor
    from persistence.change_log_watcher import ChangeLogWatcher
    from persistence.gpkg_engine import GeoPackageEngine

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# NullEventHub — no-op stand-in for adapters/http/event_hub.EventHub
# ---------------------------------------------------------------------------


class NullEventHub:
    """Drop-in :class:`EventHub` replacement that swallows every broadcast.

    The watcher and the dispatcher both call ``hub.broadcast(event_type,
    data)``. In Mode 1 we have no WebSocket fan-out — those events are
    not observed, so we accept and discard them.

    The HTTP ``EventHub`` also exposes ``bind_loop`` and a coroutine
    ``subscribe`` API; we deliberately do **not** implement those here
    because the headless path is single-threaded from the dispatcher's
    point of view (the watcher thread broadcasts, the hub no-ops).
    """

    def broadcast(  # pragma: no cover - trivial stub
        self,
        event_type: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        """No-op: discard the event payload."""
        return None


# ---------------------------------------------------------------------------
# Runtime handle
# ---------------------------------------------------------------------------


@dataclass
class HeadlessRuntime:
    """Bundle of objects returned by :func:`build_runtime`.

    Lifecycle:
        - ``run_once()``  — drive a single watcher tick, then return
                            (used by ``gispulse triggers run --once``).
        - ``start()`` / ``stop()`` — start the polling daemon thread
                            (reserved for ``--watch``, not exposed yet).
        - ``close()``     — close the underlying GPKG engine.
    """

    engine: "GeoPackageEngine"
    event_hub: NullEventHub
    action_dispatcher: "ActionDispatcher"
    watcher: "ChangeLogWatcher"
    triggers: list["Trigger"] = field(default_factory=list)
    gpkg_path: Path = field(default_factory=Path)
    # When the caller did not inject a custom ``sql_executor``, this
    # holds the :class:`RetryingSqlExecutor` wrapper installed around
    # ``engine.execute`` (S5). The CLI ``--watch`` mode reads
    # ``.snapshot_retries()`` per tick to populate the JSON log.
    # ``None`` when no executor is configured at all.
    retrying_sql: "RetryingSqlExecutor | None" = None

    def run_once(self) -> int:
        """Run one polling tick. Returns the number of change-log rows
        processed.

        Notes:
            - The watcher's daemon thread is **not** started; we drive
              ``_tick()`` directly so the CLI can exit cleanly without
              relying on ``stop()`` join semantics.
            - ``_tick()`` swallows broadcast / dispatch failures
              internally (per-row try/except), so a single bad webhook
              does not abort the batch.
        """
        # ``_tick`` is "private" but documented as the unit of work and
        # is safe to call directly: the public ``start()`` / ``stop()``
        # only adds the daemon thread + sleep loop on top.
        return self.watcher._tick()  # noqa: SLF001 - intentional

    def start(self) -> None:
        """Start the polling daemon thread (reserved for ``--watch``)."""
        self.watcher.start()

    def stop(self) -> None:
        """Stop the polling daemon thread (reserved for ``--watch``)."""
        self.watcher.stop()

    def close(self) -> None:
        """Release the underlying GPKG engine."""
        try:
            self.engine.close()
        except Exception as exc:  # pragma: no cover - defensive
            log.warning("headless_runtime_close_failed", error=str(exc))

    # Context manager sugar so callers can ``with build_runtime(...) as rt:``
    def __enter__(self) -> "HeadlessRuntime":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: Any,
    ) -> None:
        self.close()


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def _check_pragmas(engine: "GeoPackageEngine") -> None:
    """Verify the GPKG opened in WAL mode with a sensible busy timeout.

    The engine sets ``journal_mode=WAL`` + ``busy_timeout=5000`` in
    ``_open_conn``. We re-check at runtime startup so an externally
    re-created GPKG (or one opened in DELETE mode by a rogue tool) is
    surfaced loudly instead of silently bottlenecking the watcher.
    """
    conn = engine._get_conn()  # noqa: SLF001 - documented internal accessor

    journal_mode = ""
    busy_timeout = 0
    try:
        cur = conn.execute("PRAGMA journal_mode")
        row = cur.fetchone()
        if row is not None:
            journal_mode = str(row[0]).lower()
        cur = conn.execute("PRAGMA busy_timeout")
        row = cur.fetchone()
        if row is not None:
            busy_timeout = int(row[0])
    except sqlite3.Error as exc:  # pragma: no cover - defensive
        log.warning("pragma_check_failed", error=str(exc))
        return

    if journal_mode != "wal":
        warnings.warn(
            f"GPKG journal_mode is {journal_mode!r}, not 'wal'. Concurrent "
            "writers may block the trigger watcher.",
            RuntimeWarning,
            stacklevel=2,
        )
    if busy_timeout < 5000:
        warnings.warn(
            f"GPKG busy_timeout is {busy_timeout} ms (< 5000 ms). The watcher "
            "may raise SQLITE_BUSY under load.",
            RuntimeWarning,
            stacklevel=2,
        )


def build_runtime(
    gpkg_path: str | Path,
    triggers: Sequence["Trigger"],
    *,
    webhook_allowlist: Iterable[str] | None = None,
    poll_interval: float = 1.0,
    batch_limit: int = 200,
    dataset_id: str = "__cli__",
    sql_executor: Callable[..., Any] | None = None,
    webhook_client: Callable[[str, dict[str, Any]], None] | None = None,
) -> HeadlessRuntime:
    """Wire a headless trigger runtime over a single GPKG file.

    This reproduces the FastAPI lifespan wiring at
    ``adapters/http/app.py:309-391`` minus the HTTP server, the job
    worker, and the scheduler.

    Args:
        gpkg_path:         Path to the project GPKG. Must already be a
                           valid GeoPackage (the engine bootstraps the
                           internal tables on open).
        triggers:          List of :class:`Trigger` instances to evaluate.
                           Stored at runtime build time (snapshot).
                           ``--watch`` will re-poll this list each tick
                           via the embedded ``triggers_provider``.
        webhook_allowlist: When provided, restrict outbound webhook URLs
                           to hosts in this set. Empty/None disables the
                           filter (default ``HttpWebhookClient`` SSRF
                           policy still applies).
        poll_interval:     Seconds between two ``_tick`` calls when the
                           daemon thread is started (``--watch``). The
                           ``run_once`` path ignores this value.
        batch_limit:       Max change-log rows pulled per tick.
        dataset_id:        Synthetic id stamped onto every broadcast
                           payload. Mandatory non-empty per the watcher
                           contract.
        sql_executor:      Optional ``(sql, params) -> Any`` callable
                           injected into the dispatcher. Defaults to
                           ``engine.execute`` if available.
        webhook_client:    Optional ``(url, payload) -> None`` callable
                           injected into the dispatcher. Defaults to
                           :class:`HttpWebhookClient`.

    Returns:
        A :class:`HeadlessRuntime` ready for ``run_once()`` or daemon
        ``start()`` / ``stop()``.

    Raises:
        ValueError:    Empty ``dataset_id`` or non-positive intervals.
        FileNotFoundError: ``gpkg_path`` does not exist.
    """
    # Lazy imports keep CLI startup snappy: we only need rules / esb
    # when the user actually runs ``triggers``.
    from gispulse.adapters.esb.action_dispatcher import ActionDispatcher
    from gispulse.adapters.webhooks import HttpWebhookClient
    from gispulse.runtime.sqlite_retry import RetryingSqlExecutor
    from persistence.change_log_watcher import ChangeLogWatcher
    from persistence.gpkg_engine import GeoPackageEngine

    if poll_interval <= 0:
        raise ValueError("poll_interval must be > 0")
    if batch_limit <= 0:
        raise ValueError("batch_limit must be > 0")
    if not isinstance(dataset_id, str) or not dataset_id:
        raise ValueError("dataset_id must be a non-empty string")

    gpkg = Path(gpkg_path).expanduser()
    if not gpkg.exists():
        raise FileNotFoundError(f"GPKG not found: {gpkg}")

    # ---- Engine -------------------------------------------------------
    engine = GeoPackageEngine(path=gpkg)
    engine.open()
    _check_pragmas(engine)

    # ---- EventHub (no-op) --------------------------------------------
    hub = NullEventHub()

    # ---- ActionDispatcher --------------------------------------------
    # Match app.py wiring: sql_executor falls back to engine.execute,
    # webhook_client to HttpWebhookClient.post (SSRF-safe by default).
    #
    # S5: wrap whatever executor we end up with in a
    # :class:`RetryingSqlExecutor` so transient ``SQLITE_BUSY`` errors
    # (concurrent QGIS save, peer GISPulse tick) get up to 5 backoff
    # retries instead of failing the action on first lock contention.
    # Permanent errors (no such table, syntax) bypass the retry and
    # surface immediately. The wrapper exposes ``snapshot_retries`` so
    # the CLI ``--watch`` daemon can include the count in its tick log.
    if sql_executor is None:
        sql_executor = getattr(engine, "execute", None)
    retrying_sql: RetryingSqlExecutor | None = None
    if sql_executor is not None:
        retrying_sql = RetryingSqlExecutor(sql_executor)
        sql_executor = retrying_sql

    # Build the allowlist once (used both for logging and as the
    # default-client wrapper). When the caller injects an explicit
    # ``webhook_client`` we still keep the allowlist around for
    # observability without enforcing it on the injected callable.
    allowlist: set[str] | None = (
        {host.strip().lower() for host in webhook_allowlist if host.strip()}
        if webhook_allowlist
        else None
    )

    if webhook_client is None:
        # Optional allowlist: when set, wrap the SSRF-safe client with
        # an extra host check so operators can pin outbound traffic to
        # specific destinations even on local fixtures (where the SSRF
        # blocklist alone would refuse RFC1918).
        from urllib.parse import urlparse

        base_client = HttpWebhookClient()

        def _post_with_allowlist(url: str, payload: dict[str, Any]) -> None:
            if allowlist:
                host = (urlparse(url).hostname or "").lower()
                if host not in allowlist:
                    raise PermissionError(
                        f"Webhook host {host!r} not in allowlist "
                        f"{sorted(allowlist)!r}"
                    )
            base_client.post(url, payload)

        webhook_client = _post_with_allowlist

    dispatcher = ActionDispatcher(
        event_hub=hub,
        sql_executor=sql_executor,
        webhook_client=webhook_client,
    )

    # ---- ChangeLogWatcher --------------------------------------------
    trigger_list = list(triggers)

    def _triggers_provider() -> list["Trigger"]:
        # Snapshot reference: callers can mutate the original list and
        # pick up changes on the next tick (matches the API behaviour).
        return [t for t in trigger_list if getattr(t, "enabled", True)]

    watcher = ChangeLogWatcher(
        engine=engine,
        event_hub=hub,
        dataset_id=dataset_id,
        poll_interval=poll_interval,
        batch_limit=batch_limit,
        triggers_provider=_triggers_provider,
        action_dispatcher=dispatcher,
    )

    log.info(
        "headless_runtime_built",
        gpkg=str(gpkg),
        triggers=len(trigger_list),
        webhook_allowlist=sorted(allowlist) if allowlist else None,
    )

    return HeadlessRuntime(
        engine=engine,
        event_hub=hub,
        action_dispatcher=dispatcher,
        watcher=watcher,
        triggers=trigger_list,
        gpkg_path=gpkg,
        retrying_sql=retrying_sql,
    )


__all__ = ["HeadlessRuntime", "NullEventHub", "build_runtime"]
