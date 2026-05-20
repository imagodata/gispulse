"""Tests for the unified licence payload format (issue #266, story L0)."""

from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from gispulse.core.licence_format import (
    KNOWN_FIELDS,
    SCHEMA_VERSION_CURRENT,
    LicencePayload,
    LicenceVerificationError,
    canonicalise,
    decode_payload,
    encode_payload,
)


@pytest.fixture
def keypair() -> tuple[Ed25519PrivateKey, Ed25519PublicKey]:
    priv = Ed25519PrivateKey.generate()
    return priv, priv.public_key()


# ---------------------------------------------------------------------------
# Round-trip — Mode A (poste) and Mode B (tenant SaaS)
# ---------------------------------------------------------------------------


def test_roundtrip_poste_profile(keypair: tuple[Ed25519PrivateKey, Ed25519PublicKey]) -> None:
    """Given a poste-profile payload, when signed and verified, then round-trip OK."""
    priv, pub = keypair
    payload = LicencePayload(
        schema_version=SCHEMA_VERSION_CURRENT,
        org="ACME",
        tier="pro",
        exp="2030-01-01T00:00:00Z",
    )
    key = encode_payload(payload, priv)
    out = decode_payload(key, pub)
    assert out == payload


def test_roundtrip_tenant_profile(keypair: tuple[Ed25519PrivateKey, Ed25519PublicKey]) -> None:
    """Tenant profile (SaaS) round-trips with the same module — no separate path."""
    priv, pub = keypair
    payload = LicencePayload(
        schema_version=SCHEMA_VERSION_CURRENT,
        org="ACME",
        tier="pro",
        exp="2030-01-01T00:00:00Z",
        tenant_id="tnt_42",
        quotas={"requests_per_day": 10_000, "datasets": 50},
        stripe_customer_id="cus_abc",
        stripe_subscription_id="sub_xyz",
    )
    key = encode_payload(payload, priv)
    out = decode_payload(key, pub)
    assert out == payload
    assert out.tenant_id == "tnt_42"
    assert out.quotas == {"requests_per_day": 10_000, "datasets": 50}


# ---------------------------------------------------------------------------
# Forward compat — unknown fields must NOT crash the OSS verifier
# ---------------------------------------------------------------------------


def test_forward_compat_unknown_fields_preserved(
    keypair: tuple[Ed25519PrivateKey, Ed25519PublicKey],
) -> None:
    """A payload from a *future* schema_version with unknown fields decodes cleanly."""
    priv, pub = keypair
    raw = {
        "schema_version": 99,  # future
        "org": "Future Corp",
        "tier": "enterprise",
        "exp": "2099-12-31T23:59:59Z",
        "shiny_new_field": {"k": "v"},  # unknown to this verifier
        "another_one": [1, 2, 3],
    }
    body = canonicalise(raw)
    signature = priv.sign(body)
    key = (
        base64.urlsafe_b64encode(body).rstrip(b"=").decode("ascii")
        + "."
        + base64.urlsafe_b64encode(signature).rstrip(b"=").decode("ascii")
    )
    out = decode_payload(key, pub)
    assert out.schema_version == 99
    assert out.org == "Future Corp"
    assert out.extra == {
        "shiny_new_field": {"k": "v"},
        "another_one": [1, 2, 3],
    }
    # the well-known fields are still where we expect
    for k in ("shiny_new_field", "another_one"):
        assert k not in KNOWN_FIELDS


def test_extra_cannot_shadow_known_field() -> None:
    """``LicencePayload.to_dict`` never lets ``extra`` overwrite a known field."""
    p = LicencePayload(
        org="ACME",
        tier="pro",
        extra={"tier": "enterprise", "totally_new": True},  # 'tier' shadow attempt
    )
    d = p.to_dict()
    assert d["tier"] == "pro"  # known wins
    assert d["totally_new"] is True


# ---------------------------------------------------------------------------
# Signature failure cases
# ---------------------------------------------------------------------------


def test_invalid_signature_rejected(
    keypair: tuple[Ed25519PrivateKey, Ed25519PublicKey],
) -> None:
    """Given a tampered signature, when verified, then rejected explicitly."""
    priv, pub = keypair
    payload = LicencePayload(org="ACME", tier="pro")
    key = encode_payload(payload, priv)
    # flip the last char of the signature half
    head, sig = key.rsplit(".", 1)
    tampered = head + "." + ("A" if sig[-1] != "A" else "B") + sig[1:]
    with pytest.raises(LicenceVerificationError, match="invalid licence signature"):
        decode_payload(tampered, pub)


def test_signature_from_other_key_rejected() -> None:
    """A payload signed with key A must not verify against key B."""
    priv_a = Ed25519PrivateKey.generate()
    priv_b = Ed25519PrivateKey.generate()
    payload = LicencePayload(org="ACME", tier="pro")
    key = encode_payload(payload, priv_a)
    with pytest.raises(LicenceVerificationError, match="invalid licence signature"):
        decode_payload(key, priv_b.public_key())


def test_skip_verification_when_public_key_none(
    keypair: tuple[Ed25519PrivateKey, Ed25519PublicKey],
) -> None:
    """Passing public_key=None skips verification (dev path)."""
    priv, _ = keypair
    payload = LicencePayload(org="dev", tier="pro")
    key = encode_payload(payload, priv)
    out = decode_payload(key, public_key=None)
    assert out == payload


# ---------------------------------------------------------------------------
# Format / encoding failure cases
# ---------------------------------------------------------------------------


def test_malformed_string_rejected() -> None:
    with pytest.raises(LicenceVerificationError, match="invalid licence format"):
        decode_payload("no-dot-here")
    with pytest.raises(LicenceVerificationError, match="invalid licence format"):
        decode_payload(".only-sig")
    with pytest.raises(LicenceVerificationError, match="invalid licence format"):
        decode_payload("only-payload.")


def test_non_string_key_rejected() -> None:
    with pytest.raises(LicenceVerificationError, match="must be a string"):
        decode_payload(123)  # type: ignore[arg-type]


def test_non_json_payload_rejected(
    keypair: tuple[Ed25519PrivateKey, Ed25519PublicKey],
) -> None:
    priv, pub = keypair
    body = b"not-json-at-all"
    sig = priv.sign(body)
    key = (
        base64.urlsafe_b64encode(body).rstrip(b"=").decode("ascii")
        + "."
        + base64.urlsafe_b64encode(sig).rstrip(b"=").decode("ascii")
    )
    with pytest.raises(LicenceVerificationError, match="not valid JSON"):
        decode_payload(key, pub)


def test_non_object_payload_rejected(
    keypair: tuple[Ed25519PrivateKey, Ed25519PublicKey],
) -> None:
    """JSON arrays/scalars at the root must not be accepted as a payload."""
    priv, pub = keypair
    body = json.dumps([1, 2, 3]).encode("utf-8")
    sig = priv.sign(body)
    key = (
        base64.urlsafe_b64encode(body).rstrip(b"=").decode("ascii")
        + "."
        + base64.urlsafe_b64encode(sig).rstrip(b"=").decode("ascii")
    )
    with pytest.raises(LicenceVerificationError, match="must be a JSON object"):
        decode_payload(key, pub)


def test_non_int_schema_version_rejected(
    keypair: tuple[Ed25519PrivateKey, Ed25519PublicKey],
) -> None:
    priv, pub = keypair
    body = canonicalise({"schema_version": "v1", "org": "x"})
    sig = priv.sign(body)
    key = (
        base64.urlsafe_b64encode(body).rstrip(b"=").decode("ascii")
        + "."
        + base64.urlsafe_b64encode(sig).rstrip(b"=").decode("ascii")
    )
    with pytest.raises(LicenceVerificationError, match="schema_version"):
        decode_payload(key, pub)


# ---------------------------------------------------------------------------
# Canonicalisation determinism
# ---------------------------------------------------------------------------


def test_canonicalisation_is_stable() -> None:
    """Two equivalent dicts produce the same canonical bytes — required for signing."""
    a = canonicalise({"b": 2, "a": 1, "c": [3, 1, 2]})
    b = canonicalise({"c": [3, 1, 2], "a": 1, "b": 2})
    assert a == b
    assert a == b'{"a":1,"b":2,"c":[3,1,2]}'


# ---------------------------------------------------------------------------
# Expiry helper — kept narrow on purpose (tier policy lives elsewhere)
# ---------------------------------------------------------------------------


def test_is_expired_perpetual_when_no_exp() -> None:
    assert LicencePayload(org="x", tier="pro").is_expired() is False


def test_is_expired_perpetual_when_garbage_exp() -> None:
    assert LicencePayload(org="x", tier="pro", exp="not-a-date").is_expired() is False


def test_is_expired_true_when_past() -> None:
    payload = LicencePayload(org="x", tier="pro", exp="2000-01-01T00:00:00Z")
    assert payload.is_expired() is True


def test_is_expired_false_when_future() -> None:
    future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    payload = LicencePayload(org="x", tier="pro", exp=future)
    assert payload.is_expired() is False
