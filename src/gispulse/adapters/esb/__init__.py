"""
GISPulse ESB (Event Service Bus) adapter.

Provides async message bus infrastructure backed by PostgreSQL LISTEN/NOTIFY.
Adapted from the Forge ESB reference architecture for GISPulse domain types.
"""

from gispulse.adapters.esb.bus_message import BusMessage
from gispulse.adapters.esb.pg_notify import PgNotifyChannel, PgNotifyListener
from gispulse.adapters.esb.pool import WorkerPool, WorkerPoolConfig, WorkerInfo

# CircuitBreaker and DLQ are EXPERIMENTAL — lazy import only.
# They become relevant with multi-worker/Redis deployments.
# Import them directly when needed:
#   from gispulse.adapters.esb.circuit_breaker import CircuitBreaker
#   from gispulse.adapters.esb.dlq import DeadLetterQueue

__all__ = [
    "BusMessage",
    "PgNotifyChannel",
    "PgNotifyListener",
    "WorkerPool",
    "WorkerPoolConfig",
    "WorkerInfo",
]
