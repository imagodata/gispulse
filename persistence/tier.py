"""Tier gating — enforce Community vs Pro feature access.

Environment variables:
    GISPULSE_TIER          "community" (default), "pro", or "enterprise"
    GISPULSE_LICENSE_KEY   Required for "pro" and "enterprise" tiers.
                           Format: ``<base64-payload>.<base64-signature>``
                           Payload JSON: ``{"org": "...", "tier": "...", "exp": "ISO-date"}``
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
from datetime import datetime, timezone

from core.config import settings

logger = logging.getLogger(__name__)

VALID_TIERS = ("community", "pro", "team", "enterprise")
TIER_HIERARCHY: dict[str, int] = {
    "community": 0,
    "pro": 1,
    "team": 2,
    "enterprise": 3,
}


class TierError(Exception):
    """Raised when a feature requires a higher tier than the current one."""


def get_current_tier() -> str:
    """Return the active tier from configuration (default: community)."""
    return settings.engine.tier


# Ed25519 public key for licence signature verification.
# The private key is held by the GISPulse licence server only.
_LICENCE_PUBLIC_KEY_B64 = settings.engine.licence_public_key


def make_test_license_key(tier: str = "pro", exp: str = "2030-01-01T00:00:00Z") -> str:
    """Generate a test licence key (valid format, unsigned).

    Only for testing — the signature is fake and won't pass cryptographic
    verification in production.
    """
    payload = json.dumps({"org": "test", "tier": tier, "exp": exp}).encode()
    payload_b64 = base64.urlsafe_b64encode(payload).rstrip(b"=").decode()
    fake_sig = base64.urlsafe_b64encode(b"\x00" * 64).rstrip(b"=").decode()
    return f"{payload_b64}.{fake_sig}"


def _validate_license(tier: str) -> None:
    """Validate the licence key for paid tiers using Ed25519 signatures.

    Licence format: ``<base64-payload>.<base64-signature>``
    Payload JSON: ``{"org": "...", "tier": "pro|enterprise", "exp": "2027-01-01T00:00:00Z"}``

    Falls back to community tier on invalid/expired keys.
    """
    if tier not in ("pro", "team", "enterprise"):
        return

    key = settings.engine.license_key.strip()
    if not key:
        raise TierError(
            f"GISPULSE_TIER={tier} requires a license key. "
            "Set GISPULSE_LICENSE_KEY to activate your plan."
        )

    # Parse key format: payload.signature
    parts = key.split(".")
    if len(parts) != 2:
        raise TierError(
            "Invalid license key format. Expected: <payload>.<signature>. "
            "Get a valid key at https://gispulse.com/pricing"
        )

    payload_b64, signature_b64 = parts

    try:
        payload_bytes = base64.urlsafe_b64decode(payload_b64 + "==")
        signature_bytes = base64.urlsafe_b64decode(signature_b64 + "==")
    except Exception:
        raise TierError("Invalid license key encoding (not valid base64).")

    # Verify Ed25519 signature (skip in test/dev with GISPULSE_LICENCE_SKIP_VERIFY)
    skip_verify = settings.engine.licence_skip_verify
    if skip_verify:
        logger.warning("licence_signature_skipped — GISPULSE_LICENCE_SKIP_VERIFY is set")
    else:
        try:
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
            from cryptography.hazmat.primitives.serialization import load_der_public_key

            pub_key_bytes = base64.b64decode(_LICENCE_PUBLIC_KEY_B64)
            pub_key = load_der_public_key(pub_key_bytes)
            if not isinstance(pub_key, Ed25519PublicKey):
                raise TierError("Licence public key is not Ed25519.")
            pub_key.verify(signature_bytes, payload_bytes)
        except ImportError:
            # cryptography not installed — accept key if format is valid (dev mode)
            logger.warning(
                "licence_crypto_unavailable — cryptography not installed, "
                "signature not verified (dev mode)"
            )
        except TierError:
            raise
        except Exception as exc:
            raise TierError(
                "Invalid license key signature. "
                "Get a valid key at https://gispulse.com/pricing"
            ) from exc

    # Parse and validate payload
    try:
        payload = json.loads(payload_bytes.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise TierError("Invalid license key payload (not valid JSON).")

    # Check tier matches
    key_tier = payload.get("tier", "community")
    if TIER_HIERARCHY.get(key_tier, 0) < TIER_HIERARCHY.get(tier, 0):
        raise TierError(
            f"License is for {key_tier} tier but {tier} is required. "
            "Upgrade at https://gispulse.com/pricing"
        )

    # Check expiry
    exp_str = payload.get("exp", "")
    if exp_str:
        try:
            exp_dt = datetime.fromisoformat(exp_str)
            if exp_dt.tzinfo is None:
                exp_dt = exp_dt.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) > exp_dt:
                raise TierError(
                    f"License expired on {exp_str}. "
                    "Renew at https://gispulse.com/pricing"
                )
        except (ValueError, TypeError):
            pass  # No valid expiry — treat as perpetual

    logger.info("licence_valid", extra={"tier": key_tier, "org": payload.get("org", "")})


def _demo_token_valid() -> bool:
    """Constant-time match of the runtime demo token against its SHA-256 digest.

    The digest (``GISPULSE_DEMO_TOKEN_SHA256``) is a non-secret constant checked
    into deploy configuration; the raw token (``GISPULSE_DEMO_TOKEN``) is
    provisioned only from GitHub Actions secrets at deploy time and never
    committed. Without both values, ``GISPULSE_DEMO_MODE=true`` alone cannot
    unlock Pro features on a copy of the public image.
    """
    token = settings.engine.demo_token
    expected = settings.engine.demo_token_sha256.strip().lower()
    if not token or not expected:
        logger.warning("demo_mode_rejected_missing_token_or_digest")
        return False
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    if not hmac.compare_digest(digest, expected):
        logger.warning("demo_mode_rejected_token_mismatch")
        return False
    return True


def check_tier(required_tier: str) -> bool:
    """Verify the current tier meets or exceeds *required_tier*.

    Args:
        required_tier: Minimum tier needed (``"community"``, ``"pro"``, or ``"enterprise"``).

    Returns:
        ``True`` if access is granted.

    Raises:
        TierError: If the current tier is insufficient or the license key is missing.
        ValueError: If *required_tier* is not a recognised tier name.
    """
    if required_tier not in VALID_TIERS:
        raise ValueError(f"Unknown tier: {required_tier!r}. Valid tiers: {VALID_TIERS}")

    if settings.engine.demo_mode and _demo_token_valid():
        logger.warning("tier_check_bypassed_demo_mode required=%s", required_tier)
        return True

    current = get_current_tier()
    _validate_license(current)

    if TIER_HIERARCHY[current] < TIER_HIERARCHY[required_tier]:
        raise TierError(
            f"This feature requires GISPulse {required_tier.title()}. "
            f"Current tier: {current}. "
            f"Set GISPULSE_TIER={required_tier} with a valid license key to unlock."
        )

    logger.debug("Tier check passed: current=%s, required=%s", current, required_tier)
    return True


def enforce_engine_tier(backend: str) -> None:
    """Gate engine backends by tier.

    Community tier only allows ``duckdb``.  PostGIS and Hybrid require Pro or above.

    Raises:
        TierError: If the current tier cannot use *backend*.
    """
    if backend in ("postgis", "hybrid"):
        try:
            check_tier("pro")
        except TierError as exc:
            current = get_current_tier()
            if TIER_HIERARCHY[current] < TIER_HIERARCHY["pro"]:
                raise TierError(
                    f"{backend.title()} engine requires GISPulse Pro. "
                    "Set GISPULSE_TIER=pro with a valid license, or use engine=duckdb (free)."
                ) from None
            # Tier is sufficient but license key is missing — propagate original error
            raise exc
