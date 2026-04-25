"""
Enumerations for the GISPulse ESB.

Adapted from the Forge ESB reference (domain/enums.py) with
GISPulse-specific values and without Wyre-specific entries.
"""

from __future__ import annotations

from enum import Enum


class MessageStatus(str, Enum):
    """
    Unified state of a GISPulse ESB message.

    Pipeline::

        NEW -> IDENTIFYING -> IDENTIFIED -> PROCESSING
            -> DISPATCHING -> DISPATCHED -> COMPLETED

    Error states::

        * -> FAILED  (max retries reached)
        * -> DLQ     (Dead Letter Queue, manual intervention required)
    """
    # Entry point
    NEW = "NEW"

    # Phase 1: identification
    IDENTIFYING = "IDENTIFYING"
    IDENTIFIED = "IDENTIFIED"
    IDENTIFY_ERROR = "IDENTIFY_ERROR"

    # Phase 2: processing (rule/capability execution)
    PROCESSING = "PROCESSING"
    PROCESSED = "PROCESSED"
    PROCESSING_ERROR = "PROCESSING_ERROR"

    # Phase 3: dispatch (notifications)
    DISPATCHING = "DISPATCHING"
    DISPATCHED = "DISPATCHED"

    # Terminal states
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    DLQ = "DLQ"

    @classmethod
    def from_str(cls, value: str) -> "MessageStatus":
        """Create from a string (case-insensitive)."""
        return cls(value.upper())

    @property
    def is_terminal(self) -> bool:
        """Is this state terminal (success or failure)?"""
        return self in (
            MessageStatus.COMPLETED,
            MessageStatus.FAILED,
            MessageStatus.DLQ,
        )

    @property
    def is_error(self) -> bool:
        """Does this state represent an error condition?"""
        return self in (
            MessageStatus.IDENTIFY_ERROR,
            MessageStatus.PROCESSING_ERROR,
            MessageStatus.FAILED,
            MessageStatus.DLQ,
        )


class WorkerType(str, Enum):
    """Type of worker in the GISPulse ESB pipeline."""
    IDENTIFY = "IDENTIFY"
    PROCESSING = "PROCESSING"
    DISPATCH = "DISPATCH"

    @property
    def pool_size_key(self) -> str:
        """Environment variable key for pool size configuration."""
        return f"GISPULSE_ESB_{self.value}_WORKER_COUNT"


class CircuitBreakerState(str, Enum):
    """States of the Circuit Breaker pattern."""
    CLOSED = "CLOSED"       # Normal operation, calls pass through
    OPEN = "OPEN"           # Blocked after failures, fast-fail
    HALF_OPEN = "HALF_OPEN" # Recovery probe (one call allowed)
