"""Watcher dashboard router — GET /watchers + GET /watchers/{dataset_id}.

Closes P0-3 of the CLI ↔ Portal parity audit (issue #95 / EPIC #90)
**post-errata 2026-05-03** : the watcher already auto-starts when
``POST /datasets/{id}/enable_tracking`` registers a dataset, so what
this router exposes is the **observability dashboard** — operators can
see which datasets are being watched, how many ticks / fires / errors
each one has accumulated, and whether the polling thread is alive.

Mutations remain on ``/datasets/{id}/{enable,disable}_tracking`` —
duplicating the verbs here would create two paths to the same registry
state.

Endpoints
---------
* ``GET /watchers``                    — list every registered watcher.
* ``GET /watchers/{dataset_id}``       — single watcher detail.

Auth
----
``viewer`` role is sufficient for both. The dashboard does not expose
field values or geometry — only counters, paths, and layer names. In
portal-mode local (``gispulse portal``) where auth is disabled, the
``require_role`` dependency is a no-op and both endpoints are reachable.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from gispulse.adapters.http.auth import require_role

router = APIRouter(prefix="/watchers", tags=["watchers"])


class WatcherStats(BaseModel):
    """Snapshot of a single watcher's runtime counters."""

    dataset_id: str
    running: bool
    started_at: float | None
    tick_count: int
    rows_processed: int
    fire_count: int
    error_count: int
    last_tick_at: float | None
    last_fire_at: float | None
    last_error_at: float | None
    last_error_msg: str | None
    poll_interval: float
    batch_limit: int
    bulk_threshold: int
    bulk_eval: str
    layers: list[str]
    gpkg_path: str | None


class WatchersList(BaseModel):
    """Response shape for ``GET /watchers``.

    The ``count`` field mirrors ``len(items)`` and is provided for
    clients that want to display "N watchers active" without parsing
    the array.
    """

    count: int
    items: list[WatcherStats]


def _registry_or_503(request: Request) -> Any:
    """Return the registry from app state, or 503 if missing.

    The registry is created in the ASGI lifespan on startup; a 503
    here means the lifespan failed to install it (configuration bug
    or shutdown in progress). 503 is the right code — this is a
    server-side prerequisite, not a client error.
    """
    registry = getattr(request.app.state, "watcher_registry", None)
    if registry is None:
        raise HTTPException(
            status_code=503,
            detail="watcher_registry not initialised on app.state",
        )
    return registry


@router.get("", response_model=WatchersList)
def list_watchers(
    request: Request,
    _user=Depends(require_role("viewer")),
) -> WatchersList:
    """List every registered watcher with its current counters.

    Response is a snapshot — counters reflect the moment the registry
    was iterated. Subsequent ticks may move the numbers between two
    successive calls.
    """
    registry = _registry_or_503(request)
    items = registry.list_with_stats()
    return WatchersList(count=len(items), items=[WatcherStats(**it) for it in items])


@router.get("/{dataset_id}", response_model=WatcherStats)
def get_watcher(
    dataset_id: str,
    request: Request,
    _user=Depends(require_role("viewer")),
) -> WatcherStats:
    """Return the detail snapshot for a single watcher.

    Returns 404 when no watcher is registered for *dataset_id*. The
    intent is "404 = no watcher, ergo no tracking" — operators can
    treat this as "tracking disabled" without a second call to
    ``GET /datasets/{id}/tracking_status``.
    """
    registry = _registry_or_503(request)
    stats = registry.get_stats(dataset_id)
    if stats is None:
        raise HTTPException(
            status_code=404,
            detail=f"No watcher registered for dataset_id={dataset_id!r}. "
            "Call POST /datasets/{id}/enable_tracking first.",
        )
    return WatcherStats(**stats)
