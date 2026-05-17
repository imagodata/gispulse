"""
EXPERIMENTAL — Dead Letter Queue for distributed ESB deployments.

Not imported at startup. Becomes relevant with multi-worker Redis queue
or distributed pipeline execution. Use lazy import when needed.

DeadLetterQueue — file de messages échoués pour GISPulse ESB.

Les messages qui ont épuisé leurs tentatives ou causé des erreurs fatales
sont déplacés ici pour inspection manuelle et retry contrôlé.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from gispulse.adapters.esb.bus_message import BusMessage


@dataclass
class DLQEntry:
    """Entrée dans la Dead Letter Queue.

    Attributes:
        message:        Message ESB original.
        reason:         Motif du déplacement en DLQ (ex. "max_retries_reached").
        original_error: Représentation textuelle de l'erreur déclenchante.
        moved_at:       Timestamp UTC du déplacement en DLQ.
        retry_count:    Nombre de retentatives effectuées depuis la DLQ.
    """

    message: BusMessage
    reason: str
    original_error: str
    moved_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    retry_count: int = 0


class DeadLetterQueue:
    """File de messages échoués pour inspection et retry.

    Utilise un ``deque`` avec ``maxlen`` pour limiter la mémoire occupée :
    quand la DLQ est pleine, les entrées les plus anciennes sont éjectées
    automatiquement (comportement FIFO).

    Usage::

        dlq = DeadLetterQueue(max_size=1000, max_retries=3)
        dlq.push(message, reason="processing_error", error=str(exc))

        entry = dlq.pop()
        if dlq.retry(entry):
            # remettre en file
            pass

        stats = dlq.get_stats()
    """

    def __init__(self, max_size: int = 10000, max_retries: int = 3) -> None:
        """
        Args:
            max_size:    Capacité maximale (les plus anciens sont éjectés si dépassée).
            max_retries: Nombre maximum de tentatives autorisées depuis la DLQ.
        """
        self._entries: deque[DLQEntry] = deque(maxlen=max_size)
        self._max_retries = max_retries

    # ------------------------------------------------------------------
    # Mutations
    # ------------------------------------------------------------------

    def push(self, message: BusMessage, reason: str, error: str) -> None:
        """Ajoute un message à la DLQ.

        Si la DLQ est pleine (``max_size`` atteint), le message le plus
        ancien est automatiquement éjecté par le ``deque``.

        Args:
            message: Message ESB échoué.
            reason:  Motif court (ex. "max_retries_reached", "processing_error").
            error:   Texte de l'erreur originale.
        """
        entry = DLQEntry(
            message=message,
            reason=reason,
            original_error=error,
        )
        self._entries.append(entry)

    def pop(self) -> Optional[DLQEntry]:
        """Retire et retourne le message le plus ancien (FIFO).

        Returns:
            Entrée DLQ la plus ancienne, ou ``None`` si la file est vide.
        """
        if not self._entries:
            return None
        return self._entries.popleft()

    def retry(self, entry: DLQEntry) -> bool:
        """Incrémente le compteur de retry d'une entrée.

        Returns:
            ``True`` si le retry est autorisé (retry_count < max_retries).
            ``False`` si ``max_retries`` est atteint — l'appelant doit
            décider quoi faire de l'entrée (log, archive, discard).
        """
        if entry.retry_count >= self._max_retries:
            return False
        entry.retry_count += 1
        return True

    def purge(self, older_than: Optional[datetime] = None) -> int:
        """Supprime des entrées de la DLQ.

        Args:
            older_than: Si fourni, supprime uniquement les entrées dont
                        ``moved_at`` est strictement antérieur à cette date.
                        Si ``None``, supprime toutes les entrées.

        Returns:
            Nombre d'entrées supprimées.
        """
        if older_than is None:
            count = len(self._entries)
            self._entries.clear()
            return count

        to_keep = deque(
            entry for entry in self._entries if entry.moved_at >= older_than
        )
        removed = len(self._entries) - len(to_keep)
        self._entries = to_keep
        # Restaurer le maxlen en recréant le deque
        max_size = self._entries.maxlen
        if max_size is not None:
            self._entries = deque(to_keep, maxlen=max_size)
        return removed

    # ------------------------------------------------------------------
    # Readers
    # ------------------------------------------------------------------

    def peek(self, count: int = 10) -> list[DLQEntry]:
        """Retourne les N premiers messages sans les retirer.

        Args:
            count: Nombre maximum d'entrées à retourner.

        Returns:
            Liste d'entrées (les plus anciennes en premier).
        """
        entries = list(self._entries)
        return entries[:count]

    def get_stats(self) -> dict:
        """Retourne les statistiques de la DLQ.

        Returns:
            Dict avec les clés :
            ``count``   → nombre total d'entrées.
            ``oldest``  → ``moved_at`` de la plus ancienne entrée (ISO-8601 ou None).
            ``newest``  → ``moved_at`` de la plus récente entrée (ISO-8601 ou None).
            ``reasons`` → breakdown ``{reason: count}``.
        """
        entries = list(self._entries)
        if not entries:
            return {
                "count": 0,
                "oldest": None,
                "newest": None,
                "reasons": {},
            }

        reasons: dict[str, int] = {}
        for entry in entries:
            reasons[entry.reason] = reasons.get(entry.reason, 0) + 1

        return {
            "count": len(entries),
            "oldest": entries[0].moved_at.isoformat(),
            "newest": entries[-1].moved_at.isoformat(),
            "reasons": reasons,
        }

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def size(self) -> int:
        """Nombre d'entrées actuellement dans la DLQ."""
        return len(self._entries)

    @property
    def is_empty(self) -> bool:
        """Vrai si la DLQ est vide."""
        return len(self._entries) == 0
