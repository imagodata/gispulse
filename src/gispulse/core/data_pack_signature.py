"""Story G1a (#271) — Ed25519 signature of data-pack manifests.

This module sits between the unified licence format (story L0, #266) and
the third-party PyPI discovery channel (story T5, #269). It defines the
*shape* of a signed data-pack manifest and provides the verifier that
ExtensionHub calls during discovery.

The signing side (private key, key rotation, packaging) lives in
``gispulse-enterprise`` and in the regulatory data-pack repo
(``gispulse-data-regulatory``, story G1b). The OSS engine only owns the
verifier — that's what gating "Pro/Enterprise-only data-packs via
signature" means in practice.

Canonicalisation note
---------------------

We reuse :func:`gispulse.core.licence_format.canonicalise` so the bytes
hashed for signing are identical to the licence-payload format. The
``signature`` field is removed from the dict before canonicalisation —
otherwise the signature would have to commit to itself.
"""

from __future__ import annotations

import base64
from typing import Any, Mapping

from gispulse.core.licence_format import canonicalise
from gispulse.core.logging import get_logger

log = get_logger(__name__)

__all__ = [
    "DataPackSignatureError",
    "canonical_manifest_bytes",
    "sign_manifest_dict",
    "verify_manifest_dict",
]


class DataPackSignatureError(Exception):
    """Raised when a data-pack signature cannot be verified."""


def canonical_manifest_bytes(manifest_dict: Mapping[str, Any]) -> bytes:
    """Return the byte string to sign / verify for a manifest.

    The ``signature`` key — if present — is dropped before canonicalisation
    so the verifier can recompute the same bytes the signer originally fed
    to the private key. Everything else is sorted-key, compact JSON, UTF-8.
    """
    payload = {k: v for k, v in manifest_dict.items() if k != "signature"}
    return canonicalise(payload)


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(text: str) -> bytes:
    padded = text + "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(padded)


def sign_manifest_dict(
    manifest_dict: Mapping[str, Any], private_key: Any
) -> str:
    """Compute the urlsafe-base64 Ed25519 signature of ``manifest_dict``.

    The dict is canonicalised first (``signature`` key stripped). The
    private key must be a ``cryptography`` ``Ed25519PrivateKey``.

    This helper exists in the OSS module purely so tests can round-trip a
    real signature. In production the signing happens upstream
    (``gispulse-enterprise`` / data-pack release pipeline) and the OSS
    engine only verifies.
    """
    body = canonical_manifest_bytes(manifest_dict)
    return _b64url_encode(private_key.sign(body))


def verify_manifest_dict(
    manifest_dict: Mapping[str, Any], signature: str, public_key: Any
) -> bool:
    """Verify that ``signature`` matches ``manifest_dict`` under ``public_key``.

    Args:
        manifest_dict: the raw manifest mapping as read from disk (the
            ``signature`` field may or may not be present; it is removed
            internally before verification).
        signature: urlsafe-base64 Ed25519 signature.
        public_key: a ``cryptography`` ``Ed25519PublicKey``.

    Raises:
        DataPackSignatureError: any failure — bad base64, public-key
            mismatch, tampered manifest. The original exception is
            preserved as ``__cause__``.

    Returns:
        ``True`` on success. The boolean return keeps the call site
        flexible (``if verify_manifest_dict(...)`` works alongside the
        explicit exception path).
    """
    if not isinstance(signature, str) or not signature.strip():
        raise DataPackSignatureError("signature must be a non-empty string")
    try:
        sig_bytes = _b64url_decode(signature)
    except Exception as exc:  # pragma: no cover - base64 raises many subtypes
        raise DataPackSignatureError(
            "invalid data-pack signature encoding (base64 decode failed)"
        ) from exc
    body = canonical_manifest_bytes(manifest_dict)
    try:
        public_key.verify(sig_bytes, body)
    except Exception as exc:
        raise DataPackSignatureError(
            "data-pack signature does not match the manifest"
        ) from exc
    return True


def load_public_key_b64(b64: str) -> Any:
    """Load an Ed25519 public key from a base64-encoded DER blob.

    Mirrors how ``persistence.tier`` reads ``GISPULSE_LICENCE_PUBLIC_KEY``,
    so the same operational tooling can configure both fields.
    """
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PublicKey,
    )
    from cryptography.hazmat.primitives.serialization import load_der_public_key

    raw = base64.b64decode(b64)
    key = load_der_public_key(raw)
    if not isinstance(key, Ed25519PublicKey):
        raise DataPackSignatureError(
            "configured data-pack public key is not Ed25519"
        )
    return key
