"""Tests for persistence.auth_repository — RBAC CRUD (users, orgs, API keys).

Security-critical: bugs here silently grant access (revoked key still
authenticates), break tenant isolation (org leak), or allow duplicate
emails (account takeover). Pin the contract tightly.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from gispulse.persistence.auth_models import ApiKey, Organisation, User
from gispulse.persistence.auth_repository import (
    AuthRepository,
    generate_api_key,
    hash_api_key,
)


@pytest.fixture
def repo(tmp_path) -> AuthRepository:
    return AuthRepository(db_path=tmp_path / "auth.db")


class TestKeyHelpers:
    def test_hash_is_deterministic(self):
        assert hash_api_key("abc") == hash_api_key("abc")
        assert hash_api_key("abc") != hash_api_key("abd")

    def test_hash_is_sha256_hex(self):
        # SHA-256 hex digest is always 64 chars
        assert len(hash_api_key("anything")) == 64
        assert all(c in "0123456789abcdef" for c in hash_api_key("x"))

    def test_generate_api_key_is_unique(self):
        keys = {generate_api_key() for _ in range(50)}
        assert len(keys) == 50

    def test_generate_api_key_has_gp_prefix(self):
        key = generate_api_key()
        assert key.startswith("gp_")
        assert len(key) > 50  # base64 of 48 bytes is ~64 chars


class TestRepositoryInit:
    def test_creates_db_file(self, tmp_path):
        db = tmp_path / "nested" / "auth.db"
        AuthRepository(db_path=db)
        assert db.exists()

    def test_init_is_idempotent(self, tmp_path):
        db = tmp_path / "auth.db"
        AuthRepository(db_path=db)
        AuthRepository(db_path=db)
        AuthRepository(db_path=db)


class TestOrganisationCRUD:
    def test_create_and_get(self, repo):
        org = Organisation(name="Acme", tier="pro")
        repo.create_org(org)
        fetched = repo.get_org(org.id)
        assert fetched is not None
        assert fetched.name == "Acme"
        assert fetched.tier == "pro"
        assert fetched.created_at.tzinfo is not None

    def test_get_unknown_returns_none(self, repo):
        assert repo.get_org("never-existed") is None

    def test_list_orgs(self, repo):
        repo.create_org(Organisation(name="A"))
        repo.create_org(Organisation(name="B"))
        repo.create_org(Organisation(name="C"))
        orgs = repo.list_orgs()
        assert {o.name for o in orgs} == {"A", "B", "C"}

    def test_list_orgs_empty(self, repo):
        assert repo.list_orgs() == []


class TestUserCRUD:
    def test_create_and_get(self, repo):
        user = User(email="alice@example.com", name="Alice", role="admin")
        repo.create_user(user)
        fetched = repo.get_user(user.id)
        assert fetched is not None
        assert fetched.email == "alice@example.com"
        assert fetched.role == "admin"
        assert fetched.is_active is True

    def test_get_by_email(self, repo):
        user = User(email="bob@example.com", name="Bob")
        repo.create_user(user)
        fetched = repo.get_user_by_email("bob@example.com")
        assert fetched is not None
        assert fetched.id == user.id

    def test_get_by_email_unknown(self, repo):
        assert repo.get_user_by_email("ghost@nowhere.com") is None

    def test_unique_email_constraint_rejects_duplicate(self, repo):
        """Two users with the same email must not be allowed — critical for
        account takeover prevention."""
        import sqlite3

        repo.create_user(User(email="clash@x.com", name="First"))
        with pytest.raises(sqlite3.IntegrityError):
            repo.create_user(User(email="clash@x.com", name="Second"))

    def test_list_users_sorted_by_created_at_desc(self, repo):
        import time

        repo.create_user(User(email="a@x.com", name="A"))
        time.sleep(0.01)
        repo.create_user(User(email="b@x.com", name="B"))
        time.sleep(0.01)
        repo.create_user(User(email="c@x.com", name="C"))
        users = repo.list_users()
        # Most recent first
        assert [u.email for u in users] == ["c@x.com", "b@x.com", "a@x.com"]

    def test_update_user(self, repo):
        user = User(email="x@x.com", name="X", role="viewer")
        repo.create_user(user)
        user.role = "admin"
        user.is_active = False
        repo.update_user(user)
        fetched = repo.get_user(user.id)
        assert fetched.role == "admin"
        assert fetched.is_active is False

    def test_user_count(self, repo):
        assert repo.user_count() == 0
        repo.create_user(User(email="a@x.com", name="A"))
        repo.create_user(User(email="b@x.com", name="B"))
        assert repo.user_count() == 2


class TestUserDeletionCascadesApiKeys:
    def test_delete_returns_false_for_missing(self, repo):
        assert repo.delete_user("never-existed") is False

    def test_delete_returns_true_and_removes_user(self, repo):
        user = User(email="x@x.com", name="X")
        repo.create_user(user)
        assert repo.delete_user(user.id) is True
        assert repo.get_user(user.id) is None

    def test_delete_cascades_api_keys(self, repo):
        """Deleting a user must also revoke (remove) their API keys."""
        user = User(email="owner@x.com", name="Owner")
        repo.create_user(user)
        _, raw1 = repo.create_api_key(user.id, name="key1")
        _, raw2 = repo.create_api_key(user.id, name="key2")

        assert len(repo.list_api_keys_for_user(user.id)) == 2
        repo.delete_user(user.id)

        # Keys must no longer authenticate
        assert repo.get_api_key_by_hash(hash_api_key(raw1)) is None
        assert repo.get_api_key_by_hash(hash_api_key(raw2)) is None


class TestApiKeyCRUD:
    def test_create_returns_tuple_with_raw_key(self, repo):
        user = User(email="x@x.com", name="X")
        repo.create_user(user)
        api_key, raw_key = repo.create_api_key(user.id, name="CI")
        assert isinstance(api_key, ApiKey)
        assert raw_key.startswith("gp_")
        assert api_key.key_hash == hash_api_key(raw_key)

    def test_raw_key_is_never_stored(self, repo):
        """Only the hash is persisted — raw key must not be retrievable."""
        user = User(email="x@x.com", name="X")
        repo.create_user(user)
        _, raw = repo.create_api_key(user.id)

        # Fetching by raw key directly would be a vulnerability
        # Only the hash path exists
        assert repo.get_api_key_by_hash(raw) is None  # raw ≠ hash
        assert repo.get_api_key_by_hash(hash_api_key(raw)) is not None

    def test_default_scopes(self, repo):
        user = User(email="x@x.com", name="X")
        repo.create_user(user)
        api_key, _ = repo.create_api_key(user.id)
        assert api_key.scopes == ["read"]

    def test_custom_scopes_roundtrip(self, repo):
        user = User(email="x@x.com", name="X")
        repo.create_user(user)
        api_key, raw = repo.create_api_key(
            user.id,
            scopes=["read", "rules:write", "jobs:run"],
        )
        fetched = repo.get_api_key_by_hash(hash_api_key(raw))
        assert sorted(fetched.scopes) == ["jobs:run", "read", "rules:write"]

    def test_expiry_roundtrip(self, repo):
        user = User(email="x@x.com", name="X")
        repo.create_user(user)
        expires = datetime.now(timezone.utc) + timedelta(days=90)
        _, raw = repo.create_api_key(user.id, expires_at=expires)
        fetched = repo.get_api_key_by_hash(hash_api_key(raw))
        assert fetched.expires_at is not None
        assert abs((fetched.expires_at - expires).total_seconds()) < 1


class TestApiKeyRevocation:
    def test_revoke_missing_returns_false(self, repo):
        assert repo.revoke_api_key("never-existed") is False

    def test_revoke_marks_inactive_and_removes_from_lookup(self, repo):
        """After revocation, get_api_key_by_hash must return None — the key
        can no longer authenticate. Previous data integrity bug would allow
        a revoked key to still pass auth."""
        user = User(email="x@x.com", name="X")
        repo.create_user(user)
        api_key, raw = repo.create_api_key(user.id)
        key_hash = hash_api_key(raw)

        # Before revocation
        assert repo.get_api_key_by_hash(key_hash) is not None

        # Revoke
        assert repo.revoke_api_key(api_key.id) is True

        # After revocation — lookup must fail (is_active = 0 filter)
        assert repo.get_api_key_by_hash(key_hash) is None

    def test_revoke_does_not_affect_other_keys(self, repo):
        user = User(email="x@x.com", name="X")
        repo.create_user(user)
        k1, raw1 = repo.create_api_key(user.id, name="k1")
        _, raw2 = repo.create_api_key(user.id, name="k2")

        repo.revoke_api_key(k1.id)

        assert repo.get_api_key_by_hash(hash_api_key(raw1)) is None
        assert repo.get_api_key_by_hash(hash_api_key(raw2)) is not None


class TestApiKeyListing:
    def test_list_for_user_empty(self, repo):
        user = User(email="x@x.com", name="X")
        repo.create_user(user)
        assert repo.list_api_keys_for_user(user.id) == []

    def test_list_for_user_includes_revoked(self, repo):
        """list_api_keys_for_user returns both active AND revoked keys so the
        admin UI can show history. get_api_key_by_hash applies the is_active
        filter separately."""
        user = User(email="x@x.com", name="X")
        repo.create_user(user)
        k1, _ = repo.create_api_key(user.id, name="k1")
        k2, _ = repo.create_api_key(user.id, name="k2")
        repo.revoke_api_key(k1.id)

        all_keys = repo.list_api_keys_for_user(user.id)
        assert len(all_keys) == 2
        ids = {k.id for k in all_keys}
        assert k1.id in ids and k2.id in ids

    def test_list_for_user_isolates_by_user_id(self, repo):
        """No cross-tenant leak: user A's keys must not appear in user B's list."""
        user_a = User(email="a@x.com", name="A")
        user_b = User(email="b@x.com", name="B")
        repo.create_user(user_a)
        repo.create_user(user_b)

        repo.create_api_key(user_a.id, name="ak1")
        repo.create_api_key(user_a.id, name="ak2")
        repo.create_api_key(user_b.id, name="bk1")

        a_keys = repo.list_api_keys_for_user(user_a.id)
        b_keys = repo.list_api_keys_for_user(user_b.id)
        assert len(a_keys) == 2
        assert len(b_keys) == 1
        assert all(k.user_id == user_a.id for k in a_keys)
        assert all(k.user_id == user_b.id for k in b_keys)
