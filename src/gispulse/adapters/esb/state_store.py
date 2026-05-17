"""State Store for spatial transition tracking (ENTER/EXIT/DWELL).

Maintains per-(object, predicate) state so the system can detect
transitions rather than re-firing on every DML.

Two backends:
- **InMemoryStateStore**: dict-based, suitable for DuckDB/portable mode.
- **PostgresStateStore**: table ``trigger_states``, suitable for persistent mode.
"""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from gispulse.core.logging import get_logger
from gispulse.core.models import ObjectState, SpatialState, Transition

log = get_logger(__name__)


class StateStore(ABC):
    """Abstract state store interface."""

    @abstractmethod
    def get_state(self, object_id: UUID, predicate_id: UUID) -> ObjectState:
        ...

    @abstractmethod
    def update_state(
        self,
        object_id: UUID,
        predicate_id: UUID,
        new_spatial: SpatialState,
        zone_id: UUID | None = None,
    ) -> Transition | None:
        ...

    @abstractmethod
    def cleanup_predicate(self, predicate_id: UUID) -> int:
        ...

    # ------------------------------------------------------------------
    # Shared transition logic
    # ------------------------------------------------------------------

    @staticmethod
    def compute_transition(
        previous: SpatialState,
        current_match: bool,
    ) -> tuple[SpatialState, Transition | None]:
        """Determine new state and optional transition."""
        new_state = SpatialState.INSIDE if current_match else SpatialState.OUTSIDE

        if previous == SpatialState.UNKNOWN:
            transition = Transition.ENTER if current_match else None
        elif previous == SpatialState.OUTSIDE and current_match:
            transition = Transition.ENTER
        elif previous == SpatialState.INSIDE and not current_match:
            transition = Transition.EXIT
        else:
            transition = None  # no state change

        return new_state, transition


# ---------------------------------------------------------------------------
# In-memory backend
# ---------------------------------------------------------------------------


class InMemoryStateStore(StateStore):
    """Dict-backed state store for portable / DuckDB mode."""

    def __init__(self) -> None:
        self._store: dict[tuple[UUID, UUID], ObjectState] = {}

    def get_state(self, object_id: UUID, predicate_id: UUID) -> ObjectState:
        key = (object_id, predicate_id)
        if key not in self._store:
            self._store[key] = ObjectState(
                object_id=object_id, predicate_id=predicate_id
            )
        return self._store[key]

    def update_state(
        self,
        object_id: UUID,
        predicate_id: UUID,
        new_spatial: SpatialState,
        zone_id: UUID | None = None,
    ) -> Transition | None:
        obj = self.get_state(object_id, predicate_id)
        _, transition = self.compute_transition(obj.state, new_spatial == SpatialState.INSIDE)

        obj.state = new_spatial
        obj.zone_id = zone_id
        obj.last_evaluated = datetime.now(timezone.utc)
        if transition == Transition.ENTER:
            obj.entered_at = datetime.now(timezone.utc)
        elif transition == Transition.EXIT:
            obj.entered_at = None

        if transition:
            log.info(
                "state_transition",
                object_id=str(object_id),
                predicate_id=str(predicate_id),
                transition=transition.value,
            )
        return transition

    def cleanup_predicate(self, predicate_id: UUID) -> int:
        keys = [k for k in self._store if k[1] == predicate_id]
        for k in keys:
            del self._store[k]
        return len(keys)

    @property
    def size(self) -> int:
        return len(self._store)


# ---------------------------------------------------------------------------
# PostgreSQL backend
# ---------------------------------------------------------------------------


class PostgresStateStore(StateStore):
    """PostgreSQL-backed state store using table ``trigger_states``.

    Requires a SpatialEngine with ``execute_sql`` capability.
    """

    DDL = """
    CREATE TABLE IF NOT EXISTS trigger_states (
        object_id    UUID NOT NULL,
        predicate_id UUID NOT NULL,
        zone_id      UUID,
        state        TEXT NOT NULL DEFAULT 'UNKNOWN',
        entered_at   TIMESTAMPTZ,
        last_evaluated TIMESTAMPTZ,
        last_geom_hash BIGINT,
        PRIMARY KEY (object_id, predicate_id)
    );
    CREATE INDEX IF NOT EXISTS idx_trigger_states_pred_state
        ON trigger_states (predicate_id, state);
    """

    def __init__(self, engine: Any) -> None:
        self._engine = engine
        self._ensure_table()

    def _ensure_table(self) -> None:
        self._engine.execute_sql(self.DDL)

    def get_state(self, object_id: UUID, predicate_id: UUID) -> ObjectState:
        rows = self._engine.execute_sql(
            "SELECT state, zone_id, entered_at, last_evaluated, last_geom_hash "
            "FROM trigger_states WHERE object_id = %s AND predicate_id = %s",
            [str(object_id), str(predicate_id)],
        )
        if rows:
            row = rows[0]
            return ObjectState(
                object_id=object_id,
                predicate_id=predicate_id,
                state=SpatialState(row[0]),
                zone_id=UUID(row[1]) if row[1] else None,
                entered_at=row[2],
                last_evaluated=row[3],
                last_geom_hash=row[4],
            )
        return ObjectState(object_id=object_id, predicate_id=predicate_id)

    def update_state(
        self,
        object_id: UUID,
        predicate_id: UUID,
        new_spatial: SpatialState,
        zone_id: UUID | None = None,
    ) -> Transition | None:
        current = self.get_state(object_id, predicate_id)
        _, transition = self.compute_transition(
            current.state, new_spatial == SpatialState.INSIDE
        )
        now = datetime.now(timezone.utc)
        entered = now if transition == Transition.ENTER else (
            None if transition == Transition.EXIT else current.entered_at
        )

        self._engine.execute_sql(
            """
            INSERT INTO trigger_states
                (object_id, predicate_id, zone_id, state, entered_at, last_evaluated)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (object_id, predicate_id) DO UPDATE SET
                zone_id = EXCLUDED.zone_id,
                state = EXCLUDED.state,
                entered_at = EXCLUDED.entered_at,
                last_evaluated = EXCLUDED.last_evaluated
            """,
            [str(object_id), str(predicate_id),
             str(zone_id) if zone_id else None,
             new_spatial.value, entered, now],
        )

        if transition:
            log.info(
                "state_transition",
                object_id=str(object_id),
                predicate_id=str(predicate_id),
                transition=transition.value,
            )
        return transition

    def cleanup_predicate(self, predicate_id: UUID) -> int:
        result = self._engine.execute_sql(
            "DELETE FROM trigger_states WHERE predicate_id = %s RETURNING 1",
            [str(predicate_id)],
        )
        return len(result) if result else 0


# ---------------------------------------------------------------------------
# Geometry hashing utility
# ---------------------------------------------------------------------------


def geom_hash(wkb: bytes | None) -> int | None:
    """Fast hash for deduplicating geometry evaluations.

    Uses BLAKE2b — collision-resistant and faster than SHA-256. MD5 was
    used historically but its collision weakness lets an attacker craft
    two distinct geometries hashing equal, which would mask a mutation
    in any change-detection path that compares fingerprints.
    """
    if wkb is None:
        return None
    return int(hashlib.blake2b(wkb, digest_size=8).hexdigest(), 16)
