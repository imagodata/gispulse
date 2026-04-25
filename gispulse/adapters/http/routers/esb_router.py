"""
ESB status router (issue #259).

Endpoint:
    GET /esb/status  -- snapshot of ESB operational state
                        (workers, circuit breakers, DLQ, pg_notify)
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/esb", tags=["esb"])


@router.get("/status", summary="ESB operational status")
def esb_status() -> dict:
    """Return a snapshot of the ESB operational state.

    Includes:
    - ``workers``: pool status from WorkerPool singleton (if started)
    - ``circuit_breakers``: state of all registered circuit breakers
    - ``dlq_size``: number of messages in the dead-letter queue
    - ``pg_notify_connected``: whether the pg_notify listener is connected
    """
    result: dict = {
        "workers": None,
        "circuit_breakers": {},
        "dlq_size": 0,
        "pg_notify_connected": False,
    }

    # -- Circuit breakers ---------------------------------------------------
    try:
        from gispulse.adapters.esb.circuit_breaker import _circuit_breakers

        result["circuit_breakers"] = {
            name: cb.state.value
            for name, cb in _circuit_breakers.items()
        }
    except Exception:
        pass

    # -- DLQ size -----------------------------------------------------------
    try:
        from gispulse.adapters.esb.dlq import DeadLetterQueue

        # The DLQ is typically instantiated per-use; we report 0 if no
        # singleton is accessible.
        dlq = getattr(DeadLetterQueue, "_instance", None)
        if dlq is not None:
            result["dlq_size"] = dlq.size
    except Exception:
        pass

    # -- Worker pool --------------------------------------------------------
    try:
        from gispulse.adapters.esb.pool import WorkerPool

        pool = getattr(WorkerPool, "_instance", None)
        if pool is not None:
            result["workers"] = pool.get_pool_status()
    except Exception:
        pass

    # -- pg_notify ----------------------------------------------------------
    try:
        from gispulse.adapters.esb.pg_notify import PgNotifyListener

        listener = getattr(PgNotifyListener, "_instance", None)
        if listener is not None:
            result["pg_notify_connected"] = getattr(listener, "connected", False)
    except Exception:
        pass

    return result
