"""
BusMessage — GISPulse ESB message model.

Represents a single message flowing through the GISPulse event bus.
The ``payload`` field carries spatial event data: dataset_id, layer_id,
operation, geometry changes, etc.

Adapted from the Forge ESB reference (domain/bus_message.py) with
GISPulse-specific payload conventions.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID

from gispulse.adapters.esb.enums import MessageStatus


@dataclass
class BusMessage:
    """
    Représentation d'un message dans le bus ESB de GISPulse.

    Le champ ``payload`` est un dict JSON avec la structure spatiale::

        {
            "dataset_id":    "<uuid>",
            "layer_id":      "<uuid>",
            "operation":     "INSERT" | "UPDATE" | "DELETE" | "PROCESS",
            "data_category": "vector" | "raster" | ...,
            "new_data":      {...},
            "old_data":      {...},
            "processing_mode": "SYNC" | "ASYNC",
            "trigger_timestamp": "ISO-8601",
        }

    Attributes:
        id:               Identifiant unique du message.
        channel_id:       Canal/source ayant émis le message.
        payload:          Données spatiales de l'événement (JSONB en DB).
        message_status:   État courant dans le pipeline.
        message_priority: Priorité de traitement (1=haute, 10=basse). Défaut: 5.
        retry_count:      Nombre de tentatives effectuées.
    """

    id: UUID
    channel_id: UUID
    payload: dict[str, Any]
    message_status: MessageStatus

    type_message_id: Optional[UUID] = None
    worker_id: Optional[UUID] = None
    message_priority: int = 5
    retry_count: int = 0

    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

    dispatched: bool = False
    dispatched_at: Optional[datetime] = None

    # Internal result slots (not persisted directly)
    _worker_result: Optional[dict[str, Any]] = field(default=None, repr=False)

    # ------------------------------------------------------------------
    # Payload accessors
    # ------------------------------------------------------------------

    @property
    def dataset_id(self) -> Optional[UUID]:
        """UUID du Dataset concerné par cet événement."""
        raw = self.payload.get("dataset_id")
        if raw is None:
            return None
        return UUID(str(raw)) if not isinstance(raw, UUID) else raw

    @property
    def layer_id(self) -> Optional[UUID]:
        """UUID du Layer concerné."""
        raw = self.payload.get("layer_id")
        if raw is None:
            return None
        return UUID(str(raw)) if not isinstance(raw, UUID) else raw

    @property
    def operation(self) -> Optional[str]:
        """Opération déclenchante: INSERT, UPDATE, DELETE, PROCESS."""
        return self.payload.get("operation")

    @property
    def data_category(self) -> str:
        """Catégorie de données spatiales (vector, raster, …)."""
        return self.payload.get("data_category", "vector")

    @property
    def processing_mode(self) -> str:
        """Mode de traitement: SYNC ou ASYNC."""
        return self.payload.get("processing_mode", "SYNC")

    @property
    def new_data(self) -> Optional[dict[str, Any]]:
        """Données après modification (INSERT/UPDATE)."""
        return self.payload.get("new_data")

    @property
    def old_data(self) -> Optional[dict[str, Any]]:
        """Données avant modification (UPDATE/DELETE)."""
        return self.payload.get("old_data")

    @property
    def trigger_timestamp(self) -> Optional[datetime]:
        """Timestamp de déclenchement de l'événement source."""
        ts = self.payload.get("trigger_timestamp")
        if ts is None:
            return None
        if isinstance(ts, str):
            return datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return ts

    @property
    def affected_layers(self) -> list[str]:
        """Layers affectés par le traitement (rempli par le worker)."""
        if self._worker_result:
            return self._worker_result.get("affected_layers", [])
        return []

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def is_insert(self) -> bool:
        return self.operation == "INSERT"

    def is_update(self) -> bool:
        return self.operation == "UPDATE"

    def is_delete(self) -> bool:
        return self.operation == "DELETE"

    def is_process(self) -> bool:
        return self.operation == "PROCESS"

    def age_seconds(self) -> float:
        """Age du message en secondes depuis sa création."""
        return (datetime.now(timezone.utc) - self.created_at).total_seconds()

    def processing_duration_seconds(self) -> Optional[float]:
        """Durée effective de traitement (None si pas encore terminé)."""
        if self.started_at and self.completed_at:
            return (self.completed_at - self.started_at).total_seconds()
        return None

    def set_worker_result(self, result: dict[str, Any]) -> None:
        """Stocke le résultat du worker pour le dispatch aval."""
        self._worker_result = result
        self.payload["_worker_result"] = result

    def to_dict(self) -> dict[str, Any]:
        """Sérialise le message pour logs/métriques."""
        return {
            "id": str(self.id),
            "channel_id": str(self.channel_id),
            "message_status": self.message_status.value,
            "operation": self.operation,
            "dataset_id": str(self.dataset_id) if self.dataset_id else None,
            "layer_id": str(self.layer_id) if self.layer_id else None,
            "data_category": self.data_category,
            "priority": self.message_priority,
            "retry_count": self.retry_count,
            "created_at": self.created_at.isoformat(),
            "dispatched": self.dispatched,
        }

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def from_db_row(cls, row: dict[str, Any]) -> "BusMessage":
        """Crée une instance depuis une ligne asyncpg (dict-like)."""
        raw_payload = row.get("payload", {})
        if isinstance(raw_payload, str):
            raw_payload = json.loads(raw_payload)

        return cls(
            id=row["id"],
            channel_id=row["channel_id"],
            payload=raw_payload,
            message_status=MessageStatus(row["message_status"]),
            type_message_id=row.get("type_message_id"),
            worker_id=row.get("worker_id"),
            message_priority=row.get("message_priority", 5),
            retry_count=row.get("retry_count", 0),
            created_at=row.get("created_at", datetime.now(timezone.utc)),
            started_at=row.get("started_at"),
            completed_at=row.get("completed_at"),
            dispatched=row.get("dispatched", False),
            dispatched_at=row.get("dispatched_at"),
        )
