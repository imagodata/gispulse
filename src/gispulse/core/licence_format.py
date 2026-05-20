"""Unified licence payload format (Mode A poste + Mode B tenant SaaS).

This module defines the **single** Ed25519 payload schema used by:

* the per-machine licence key (``GISPULSE_LICENSE_KEY``) — Mode A;
* the future SaaS tenant licence — Mode B;
* the data-pack signature (story G1a, future).

Doctrine (issue #266 / decision D2 of the v2.0.0 plan):

* one schema, versioned via ``schema_version`` so the format can evolve;
* additive fields only — an OSS verifier reading a newer payload must ignore
  unknown fields, never crash;
* signature on a *canonicalised* JSON serialisation so the same payload always
  produces the same bytes to sign and verify;
* signing tooling (private key) lives in ``gispulse-enterprise`` — this module
  only contains verification + encoding helpers usable from tests.

The class ``LicencePayload`` is intentionally permissive: the OSS verifier
extracts the few fields it acts on (``schema_version``, ``tier``, ``exp``,
``org``, ``tenant_id``) and keeps the rest in ``extra`` so a future enterprise
build can read them without a new release of the OSS verifier.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

__all__ = [
    "SCHEMA_VERSION_CURRENT",
    "KNOWN_FIELDS",
    "LicencePayload",
    "LicenceVerificationError",
    "canonicalise",
    "encode_payload",
    "decode_payload",
]


SCHEMA_VERSION_CURRENT: int = 1
"""Latest payload schema version this module knows how to interpret natively."""

KNOWN_FIELDS: frozenset[str] = frozenset(
    {
        # core (both profiles)
        "schema_version",
        "org",
        "tier",
        "exp",
        # tenant profile (additive, optional)
        "tenant_id",
        "quotas",
        "stripe_customer_id",
        "stripe_subscription_id",
    }
)
"""Field names this version understands. Any other key is preserved in ``extra``."""


class LicenceVerificationError(Exception):
    """Raised when a licence string fails to decode or its signature is invalid."""


@dataclass(frozen=True)
class LicencePayload:
    """Decoded licence payload — covers both Mode A (poste) and Mode B (tenant).

    All fields beyond ``schema_version`` are optional so the same dataclass can
    represent both profiles. Unknown fields (forward-compat) land in ``extra``.
    """

    schema_version: int = SCHEMA_VERSION_CURRENT
    org: str | None = None
    tier: str | None = None
    exp: str | None = None
    # tenant profile (Mode B) — all optional
    tenant_id: str | None = None
    quotas: dict[str, Any] | None = None
    stripe_customer_id: str | None = None
    stripe_subscription_id: str | None = None
    # forward-compat: any unknown key is kept here, never silently dropped
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dict suitable for canonicalisation.

        Drops ``None`` values and merges back ``extra`` (which never overrides
        a known field — that would be a contract violation).
        """
        out: dict[str, Any] = {"schema_version": self.schema_version}
        for key in (
            "org",
            "tier",
            "exp",
            "tenant_id",
            "quotas",
            "stripe_customer_id",
            "stripe_subscription_id",
        ):
            value = getattr(self, key)
            if value is not None:
                out[key] = value
        for key, value in self.extra.items():
            if key in out:
                # Defensive: never let extra shadow a known field.
                continue
            out[key] = value
        return out

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> LicencePayload:
        """Build a payload from a decoded JSON dict, tolerant of unknown keys.

        Unknown keys go into ``extra`` (no crash, no warning — forward-compat is
        the explicit contract).
        """
        if not isinstance(raw, dict):
            raise LicenceVerificationError(
                "licence payload must be a JSON object, got "
                f"{type(raw).__name__}"
            )
        sv = raw.get("schema_version", SCHEMA_VERSION_CURRENT)
        if not isinstance(sv, int):
            raise LicenceVerificationError(
                f"schema_version must be an int, got {type(sv).__name__}"
            )
        extra: dict[str, Any] = {
            k: v for k, v in raw.items() if k not in KNOWN_FIELDS
        }
        return cls(
            schema_version=sv,
            org=raw.get("org"),
            tier=raw.get("tier"),
            exp=raw.get("exp"),
            tenant_id=raw.get("tenant_id"),
            quotas=raw.get("quotas"),
            stripe_customer_id=raw.get("stripe_customer_id"),
            stripe_subscription_id=raw.get("stripe_subscription_id"),
            extra=extra,
        )

    def is_expired(self, *, now: datetime | None = None) -> bool:
        """True if ``exp`` is set and in the past.

        Absent or unparseable ``exp`` is treated as perpetual (False) — matches
        the legacy ``persistence.tier`` behaviour to avoid bricking poste
        licences without an expiry.
        """
        if not self.exp:
            return False
        # ``Z`` is only accepted by ``datetime.fromisoformat`` from
        # Python 3.11. Normalise to keep 3.10 tolerant.
        normalised = self.exp.replace("Z", "+00:00")
        try:
            exp_dt = datetime.fromisoformat(normalised)
        except (ValueError, TypeError):
            return False
        if exp_dt.tzinfo is None:
            exp_dt = exp_dt.replace(tzinfo=timezone.utc)
        ref = now if now is not None else datetime.now(timezone.utc)
        return ref > exp_dt


def canonicalise(payload: dict[str, Any]) -> bytes:
    """Return the byte string used both for signing and for verification.

    JSON, UTF-8, sorted keys, compact separators, no whitespace. Stable across
    runs and Python versions — this is what the Ed25519 signature covers.
    """
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(text: str) -> bytes:
    # urlsafe_b64decode is strict about padding; restore it.
    padded = text + "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(padded)


def encode_payload(payload: LicencePayload, private_key: Any) -> str:
    """Serialise + sign a payload. Returns ``"<b64-payload>.<b64-signature>"``.

    ``private_key`` must be a ``cryptography`` ``Ed25519PrivateKey``. This entry
    point is here so OSS tests can round-trip; the *production* signing key
    lives only in ``gispulse-enterprise`` — the OSS distribution must never
    embed it.
    """
    body = canonicalise(payload.to_dict())
    signature = private_key.sign(body)
    return f"{_b64url_encode(body)}.{_b64url_encode(signature)}"


def decode_payload(
    key_string: str, public_key: Any | None = None
) -> LicencePayload:
    """Parse a licence string, optionally verifying the Ed25519 signature.

    Args:
        key_string: ``"<b64-payload>.<b64-signature>"``.
        public_key: a ``cryptography`` ``Ed25519PublicKey``. When ``None`` the
            signature is **not** checked — useful in dev/test paths, never in
            production gating.

    Raises:
        LicenceVerificationError: malformed string, bad base64, non-JSON
            payload, unknown payload shape, or — when ``public_key`` is set —
            an invalid signature.

    Returns:
        The decoded ``LicencePayload``. The caller is responsible for tier and
        expiry policy (this module only owns the format).
    """
    if not isinstance(key_string, str):
        raise LicenceVerificationError(
            f"licence key must be a string, got {type(key_string).__name__}"
        )
    parts = key_string.strip().split(".")
    if len(parts) != 2 or not all(parts):
        raise LicenceVerificationError(
            "invalid licence format — expected '<payload>.<signature>'"
        )
    payload_b64, signature_b64 = parts
    try:
        body = _b64url_decode(payload_b64)
        signature = _b64url_decode(signature_b64)
    except Exception as exc:  # pragma: no cover - base64 raises many subtypes
        raise LicenceVerificationError(
            "invalid licence encoding (base64 decode failed)"
        ) from exc

    if public_key is not None:
        try:
            public_key.verify(signature, body)
        except Exception as exc:
            raise LicenceVerificationError(
                "invalid licence signature"
            ) from exc

    try:
        raw = json.loads(body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise LicenceVerificationError(
            "invalid licence payload (not valid JSON)"
        ) from exc

    return LicencePayload.from_dict(raw)
