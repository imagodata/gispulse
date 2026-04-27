"""
SQLite-backed repository for GISPulse RBAC (users, organisations, API keys).

Uses the same database as the main SQLiteRepository, with dedicated tables
for auth concerns.  Thread-safe via a threading.Lock.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

from persistence.auth_models import ApiKey, Organisation, User
from persistence.sqlite_repository import DEFAULT_DB_PATH


def hash_api_key(raw_key: str) -> str:
    """Return the SHA-256 hex digest of a raw API key."""
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def generate_api_key() -> str:
    """Generate a cryptographically secure API key (48 URL-safe bytes)."""
    return f"gp_{secrets.token_urlsafe(48)}"


class AuthRepository:
    """SQLite-backed store for Users, Organisations, and API keys.

    All tables are created lazily via ``_ensure_tables()``.
    """

    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH) -> None:
        self._db_path = Path(db_path)
        self._lock = threading.Lock()
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_tables()

    # ------------------------------------------------------------------
    # Connection / low-level
    # ------------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _execute(
        self,
        sql: str,
        params: tuple = (),
        *,
        many: bool = False,
    ) -> list[sqlite3.Row]:
        with self._lock:
            conn = self._connect()
            try:
                cur = conn.execute(sql, params)
                rows = cur.fetchall()
                conn.commit()
                return rows
            finally:
                conn.close()

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def _ensure_tables(self) -> None:
        stmts = [
            """
            CREATE TABLE IF NOT EXISTS organisations (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                tier TEXT DEFAULT 'community',
                created_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                email TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'viewer',
                org_id TEXT,
                created_at TEXT NOT NULL,
                is_active INTEGER DEFAULT 1,
                FOREIGN KEY (org_id) REFERENCES organisations(id)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS api_keys (
                id TEXT PRIMARY KEY,
                key_hash TEXT NOT NULL UNIQUE,
                user_id TEXT NOT NULL,
                name TEXT DEFAULT '',
                scopes TEXT DEFAULT '["read"]',
                created_at TEXT NOT NULL,
                expires_at TEXT,
                is_active INTEGER DEFAULT 1,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
            """,
        ]
        with self._lock:
            conn = self._connect()
            try:
                for sql in stmts:
                    conn.execute(sql)
                conn.commit()
            finally:
                conn.close()

    # ------------------------------------------------------------------
    # Organisation CRUD
    # ------------------------------------------------------------------

    def create_org(self, org: Organisation) -> Organisation:
        self._execute(
            "INSERT INTO organisations (id, name, tier, created_at) VALUES (?, ?, ?, ?)",
            (org.id, org.name, org.tier, org.created_at.isoformat()),
        )
        return org

    def get_org(self, org_id: str) -> Organisation | None:
        rows = self._execute("SELECT * FROM organisations WHERE id = ?", (org_id,))
        if not rows:
            return None
        return self._row_to_org(dict(rows[0]))

    def list_orgs(self) -> list[Organisation]:
        rows = self._execute("SELECT * FROM organisations")
        return [self._row_to_org(dict(r)) for r in rows]

    # ------------------------------------------------------------------
    # User CRUD
    # ------------------------------------------------------------------

    def create_user(self, user: User) -> User:
        self._execute(
            """INSERT INTO users (id, email, name, role, org_id, created_at, is_active)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                user.id,
                user.email,
                user.name,
                user.role,
                user.org_id,
                user.created_at.isoformat(),
                int(user.is_active),
            ),
        )
        return user

    def get_user(self, user_id: str) -> User | None:
        rows = self._execute("SELECT * FROM users WHERE id = ?", (user_id,))
        if not rows:
            return None
        return self._row_to_user(dict(rows[0]))

    def get_user_by_email(self, email: str) -> User | None:
        rows = self._execute("SELECT * FROM users WHERE email = ?", (email,))
        if not rows:
            return None
        return self._row_to_user(dict(rows[0]))

    def list_users(self) -> list[User]:
        rows = self._execute("SELECT * FROM users ORDER BY created_at DESC")
        return [self._row_to_user(dict(r)) for r in rows]

    def update_user(self, user: User) -> User:
        self._execute(
            """UPDATE users SET email=?, name=?, role=?, org_id=?, is_active=?
               WHERE id=?""",
            (user.email, user.name, user.role, user.org_id, int(user.is_active), user.id),
        )
        return user

    def delete_user(self, user_id: str) -> bool:
        """Delete a user and their API keys in a single transaction."""
        with self._lock:
            conn = self._connect()
            try:
                cur = conn.execute("SELECT COUNT(*) as c FROM users WHERE id=?", (user_id,))
                row = cur.fetchone()
                if not row or row["c"] == 0:
                    return False
                conn.execute("DELETE FROM api_keys WHERE user_id=?", (user_id,))
                conn.execute("DELETE FROM users WHERE id=?", (user_id,))
                conn.commit()
                return True
            finally:
                conn.close()

    def user_count(self) -> int:
        rows = self._execute("SELECT COUNT(*) as c FROM users")
        return rows[0]["c"] if rows else 0

    # ------------------------------------------------------------------
    # API Key CRUD
    # ------------------------------------------------------------------

    def create_api_key(
        self,
        user_id: str,
        name: str = "",
        scopes: list[str] | None = None,
        expires_at: datetime | None = None,
    ) -> tuple[ApiKey, str]:
        """Create a new API key for *user_id*.

        Returns:
            Tuple of (ApiKey, raw_key).  The raw key is returned **once** and
            never stored.
        """
        raw_key = generate_api_key()
        key_hash = hash_api_key(raw_key)
        if scopes is None:
            scopes = ["read"]

        api_key = ApiKey(
            key_hash=key_hash,
            user_id=user_id,
            name=name,
            scopes=scopes,
            expires_at=expires_at,
        )
        self._execute(
            """INSERT INTO api_keys
               (id, key_hash, user_id, name, scopes, created_at, expires_at, is_active)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                api_key.id,
                api_key.key_hash,
                api_key.user_id,
                api_key.name,
                json.dumps(api_key.scopes),
                api_key.created_at.isoformat(),
                api_key.expires_at.isoformat() if api_key.expires_at else None,
                int(api_key.is_active),
            ),
        )
        return api_key, raw_key

    def get_api_key_by_hash(self, key_hash: str) -> ApiKey | None:
        rows = self._execute(
            "SELECT * FROM api_keys WHERE key_hash = ? AND is_active = 1",
            (key_hash,),
        )
        if not rows:
            return None
        return self._row_to_api_key(dict(rows[0]))

    def revoke_api_key(self, key_id: str) -> bool:
        rows = self._execute(
            "SELECT COUNT(*) as c FROM api_keys WHERE id=?", (key_id,)
        )
        if not rows or rows[0]["c"] == 0:
            return False
        self._execute("UPDATE api_keys SET is_active=0 WHERE id=?", (key_id,))
        return True

    def list_api_keys_for_user(self, user_id: str) -> list[ApiKey]:
        rows = self._execute(
            "SELECT * FROM api_keys WHERE user_id=? ORDER BY created_at DESC",
            (user_id,),
        )
        return [self._row_to_api_key(dict(r)) for r in rows]

    # ------------------------------------------------------------------
    # Row -> model helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_org(row: dict) -> Organisation:
        return Organisation(
            id=row["id"],
            name=row["name"],
            tier=row.get("tier", "community"),
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    @staticmethod
    def _row_to_user(row: dict) -> User:
        return User(
            id=row["id"],
            email=row["email"],
            name=row["name"],
            role=row["role"],
            org_id=row.get("org_id"),
            created_at=datetime.fromisoformat(row["created_at"]),
            is_active=bool(row.get("is_active", 1)),
        )

    @staticmethod
    def _row_to_api_key(row: dict) -> ApiKey:
        scopes_raw = row.get("scopes", '["read"]')
        scopes = json.loads(scopes_raw) if isinstance(scopes_raw, str) else scopes_raw

        expires = row.get("expires_at")
        expires_dt = datetime.fromisoformat(expires) if expires else None

        return ApiKey(
            id=row["id"],
            key_hash=row["key_hash"],
            user_id=row["user_id"],
            name=row.get("name", ""),
            scopes=scopes,
            created_at=datetime.fromisoformat(row["created_at"]),
            expires_at=expires_dt,
            is_active=bool(row.get("is_active", 1)),
        )
