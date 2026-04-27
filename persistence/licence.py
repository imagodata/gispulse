"""
Licence management — SQLite-backed licence storage.

Stores per-organisation licence state, synced with Stripe webhooks.
Provides CRUD + lookup by org_id and Stripe customer/subscription IDs.
"""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from persistence.sqlite_repository import DEFAULT_DB_PATH


@dataclass
class Licence:
    """Represents an organisation's subscription licence."""

    org_id: str
    tier: str = "community"  # community | pro | team | enterprise
    stripe_customer_id: str | None = None
    stripe_subscription_id: str | None = None
    status: str = "active"  # active | past_due | cancelled | trial
    trial_ends_at: datetime | None = None
    current_period_end: datetime | None = None
    cancel_at_period_end: bool = False
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class LicenceRepository:
    """SQLite-backed licence storage.

    Thread-safe via a threading.Lock.  Uses the same DB file as the main
    GISPulse repository.
    """

    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH) -> None:
        self._db_path = Path(db_path)
        self._lock = threading.Lock()
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_table()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_table(self) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS licences (
                        org_id TEXT PRIMARY KEY,
                        tier TEXT NOT NULL DEFAULT 'community',
                        stripe_customer_id TEXT,
                        stripe_subscription_id TEXT,
                        status TEXT NOT NULL DEFAULT 'active',
                        trial_ends_at TEXT,
                        current_period_end TEXT,
                        cancel_at_period_end INTEGER NOT NULL DEFAULT 0,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                """)
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_licences_stripe_customer
                    ON licences(stripe_customer_id)
                """)
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_licences_stripe_subscription
                    ON licences(stripe_subscription_id)
                """)
                conn.commit()
            finally:
                conn.close()

    # ------------------------------------------------------------------
    # Serialisation helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _dt_to_str(dt: datetime | None) -> str | None:
        if dt is None:
            return None
        return dt.isoformat()

    @staticmethod
    def _str_to_dt(s: str | None) -> datetime | None:
        if s is None:
            return None
        return datetime.fromisoformat(s)

    def _row_to_licence(self, row: sqlite3.Row) -> Licence:
        return Licence(
            org_id=row["org_id"],
            tier=row["tier"],
            stripe_customer_id=row["stripe_customer_id"],
            stripe_subscription_id=row["stripe_subscription_id"],
            status=row["status"],
            trial_ends_at=self._str_to_dt(row["trial_ends_at"]),
            current_period_end=self._str_to_dt(row["current_period_end"]),
            cancel_at_period_end=bool(row["cancel_at_period_end"]),
            created_at=self._str_to_dt(row["created_at"]) or datetime.now(timezone.utc),
            updated_at=self._str_to_dt(row["updated_at"]) or datetime.now(timezone.utc),
        )

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def get(self, org_id: str) -> Licence | None:
        """Return the licence for an org, or None."""
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT * FROM licences WHERE org_id = ?", (org_id,)
                ).fetchone()
                return self._row_to_licence(row) if row else None
            finally:
                conn.close()

    def get_by_stripe_customer(self, stripe_customer_id: str) -> Licence | None:
        """Look up a licence by Stripe customer ID."""
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT * FROM licences WHERE stripe_customer_id = ?",
                    (stripe_customer_id,),
                ).fetchone()
                return self._row_to_licence(row) if row else None
            finally:
                conn.close()

    def get_by_stripe_subscription(self, stripe_subscription_id: str) -> Licence | None:
        """Look up a licence by Stripe subscription ID."""
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT * FROM licences WHERE stripe_subscription_id = ?",
                    (stripe_subscription_id,),
                ).fetchone()
                return self._row_to_licence(row) if row else None
            finally:
                conn.close()

    def upsert(self, licence: Licence) -> Licence:
        """Insert or update a licence record."""
        now = datetime.now(timezone.utc)
        licence.updated_at = now

        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    INSERT INTO licences
                        (org_id, tier, stripe_customer_id, stripe_subscription_id,
                         status, trial_ends_at, current_period_end, cancel_at_period_end,
                         created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(org_id) DO UPDATE SET
                        tier = excluded.tier,
                        stripe_customer_id = excluded.stripe_customer_id,
                        stripe_subscription_id = excluded.stripe_subscription_id,
                        status = excluded.status,
                        trial_ends_at = excluded.trial_ends_at,
                        current_period_end = excluded.current_period_end,
                        cancel_at_period_end = excluded.cancel_at_period_end,
                        updated_at = excluded.updated_at
                    """,
                    (
                        licence.org_id,
                        licence.tier,
                        licence.stripe_customer_id,
                        licence.stripe_subscription_id,
                        licence.status,
                        self._dt_to_str(licence.trial_ends_at),
                        self._dt_to_str(licence.current_period_end),
                        int(licence.cancel_at_period_end),
                        self._dt_to_str(licence.created_at),
                        self._dt_to_str(licence.updated_at),
                    ),
                )
                conn.commit()
            finally:
                conn.close()

        return licence

    def delete(self, org_id: str) -> bool:
        """Delete a licence record. Returns True if a row was deleted."""
        with self._lock:
            conn = self._connect()
            try:
                cursor = conn.execute(
                    "DELETE FROM licences WHERE org_id = ?", (org_id,)
                )
                conn.commit()
                return cursor.rowcount > 0
            finally:
                conn.close()

    def list_active(self) -> list[Licence]:
        """Return all licences with status 'active' or 'trial'."""
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT * FROM licences WHERE status IN ('active', 'trial')"
                ).fetchall()
                return [self._row_to_licence(row) for row in rows]
            finally:
                conn.close()
