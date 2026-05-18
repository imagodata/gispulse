"""
Auth domain models for GISPulse RBAC.

Defines User, Organisation, and ApiKey dataclasses used by the auth
repository and middleware.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from uuid import uuid4


class UserRole(str, Enum):
    """Roles ordered by privilege level (viewer < editor < admin)."""

    VIEWER = "viewer"
    EDITOR = "editor"
    ADMIN = "admin"


class OrgTier(str, Enum):
    COMMUNITY = "community"
    PRO = "pro"
    TEAM = "team"
    ENTERPRISE = "enterprise"


# Privilege ordering for role comparison
_ROLE_LEVEL: dict[str, int] = {
    UserRole.VIEWER.value: 0,
    UserRole.EDITOR.value: 1,
    UserRole.ADMIN.value: 2,
}


def role_gte(user_role: str, min_role: str) -> bool:
    """Return True if *user_role* is at least as privileged as *min_role*."""
    return _ROLE_LEVEL.get(user_role, -1) >= _ROLE_LEVEL.get(min_role, 999)


# All valid scopes
VALID_SCOPES = frozenset({
    "read",
    "write",
    "admin",
    "rules:write",
    "jobs:run",
})


@dataclass
class User:
    id: str = field(default_factory=lambda: str(uuid4()))
    email: str = ""
    name: str = ""
    role: str = "viewer"
    org_id: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    is_active: bool = True


@dataclass
class Organisation:
    id: str = field(default_factory=lambda: str(uuid4()))
    name: str = ""
    tier: str = "community"
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class ApiKey:
    id: str = field(default_factory=lambda: str(uuid4()))
    key_hash: str = ""  # SHA-256 hex digest; never store raw key
    user_id: str = ""
    name: str = ""
    scopes: list[str] = field(default_factory=lambda: ["read"])
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime | None = None
    is_active: bool = True
