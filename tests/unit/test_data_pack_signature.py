"""Tests for the Ed25519 data-pack signature (story G1a, issue #271).

Two layers:

* ``data_pack_signature`` module — pure verifier round-trip;
* ExtensionHub integration — the discovery gate that drops a tampered or
  unverifiable EXTERNAL manifest before it ever lands in the inventory.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    PublicFormat,
)

from gispulse.core import plugin_hub
from gispulse.core.data_pack_signature import (
    DataPackSignatureError,
    canonical_manifest_bytes,
    sign_manifest_dict,
    verify_manifest_dict,
)
from gispulse.core.plugin_model import (
    DataPackManifest,
    Origin,
    PluginKind,
)


@pytest.fixture
def keypair() -> tuple[Ed25519PrivateKey, Ed25519PublicKey]:
    priv = Ed25519PrivateKey.generate()
    return priv, priv.public_key()


@pytest.fixture
def public_key_b64(keypair) -> str:
    _, pub = keypair
    der = pub.public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    return base64.b64encode(der).decode("ascii")


# ---------------------------------------------------------------------------
# Verifier round-trip
# ---------------------------------------------------------------------------


def test_canonical_drops_signature_field() -> None:
    """A pre-existing ``signature`` key must NOT influence canonicalisation."""
    base = {"name": "x", "content": "source-catalog", "tier": "pro"}
    a = canonical_manifest_bytes(base)
    b = canonical_manifest_bytes({**base, "signature": "irrelevant"})
    assert a == b


def test_sign_then_verify_roundtrip(keypair) -> None:
    priv, pub = keypair
    manifest = {
        "name": "fr-zoning",
        "content": "source-catalog",
        "version": "1.0.0",
        "tier": "pro",
        "entries": [{"id": "gpu-zone-urba"}],
    }
    sig = sign_manifest_dict(manifest, priv)
    assert verify_manifest_dict(manifest, sig, pub) is True


def test_verify_ignores_signature_field_in_dict(keypair) -> None:
    """Verifier accepts the same dict whether or not it carries the field."""
    priv, pub = keypair
    manifest = {"name": "x", "content": "source-catalog", "tier": "pro"}
    sig = sign_manifest_dict(manifest, priv)
    enriched = {**manifest, "signature": sig}  # what a real packed manifest looks like
    assert verify_manifest_dict(enriched, sig, pub) is True


def test_tampered_manifest_rejected(keypair) -> None:
    priv, pub = keypair
    manifest = {"name": "x", "content": "source-catalog", "tier": "pro"}
    sig = sign_manifest_dict(manifest, priv)
    tampered = {**manifest, "tier": "enterprise"}  # silently upgrade tier
    with pytest.raises(DataPackSignatureError, match="does not match"):
        verify_manifest_dict(tampered, sig, pub)


def test_foreign_key_rejected() -> None:
    priv_a = Ed25519PrivateKey.generate()
    priv_b = Ed25519PrivateKey.generate()
    manifest = {"name": "x", "content": "source-catalog", "tier": "pro"}
    sig = sign_manifest_dict(manifest, priv_a)
    with pytest.raises(DataPackSignatureError, match="does not match"):
        verify_manifest_dict(manifest, sig, priv_b.public_key())


def test_empty_signature_rejected(keypair) -> None:
    _, pub = keypair
    with pytest.raises(DataPackSignatureError, match="non-empty"):
        verify_manifest_dict({"name": "x"}, "   ", pub)


# ---------------------------------------------------------------------------
# DataPackManifest carries the signature field
# ---------------------------------------------------------------------------


def test_manifest_from_dict_carries_signature(keypair) -> None:
    priv, _ = keypair
    raw = {"name": "x", "content": "source-catalog", "tier": "pro"}
    raw["signature"] = sign_manifest_dict(raw, priv)
    m = DataPackManifest.from_dict(raw)
    assert m.signature == raw["signature"]


def test_manifest_from_dict_signature_must_be_string() -> None:
    raw = {
        "name": "x",
        "content": "source-catalog",
        "tier": "pro",
        "signature": 42,  # not a string
    }
    with pytest.raises(ValueError, match="signature"):
        DataPackManifest.from_dict(raw)


# ---------------------------------------------------------------------------
# Hub integration — the discovery gate
# ---------------------------------------------------------------------------


def _write_manifest(
    dirpath: Path, name: str, *, signature: str | None = None
) -> Path:
    payload = {
        "name": name,
        "content": "source-catalog",
        "version": "1.0.0",
        "display_name": name.title(),
        "description": "fixture",
        "tier": "community",
        "entries": [],
    }
    if signature is not None:
        payload["signature"] = signature
    p = dirpath / f"{name}.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    return p


@pytest.fixture(autouse=True)
def _clear_pubkey_cache():
    plugin_hub._DATA_PACK_PUBLIC_KEY_CACHE.clear()


def test_unsigned_external_pack_admitted_by_default(
    tmp_path, monkeypatch
) -> None:
    """Default rollout-friendly policy: unsigned EXTERNAL packs are admitted."""
    monkeypatch.setenv(plugin_hub._DATA_PACKS_DIR_ENV, str(tmp_path))
    monkeypatch.delenv(plugin_hub._DATA_PACK_PUBLIC_KEY_ENV, raising=False)
    monkeypatch.delenv(plugin_hub._DATA_PACK_REQUIRE_SIGNATURE_ENV, raising=False)
    _write_manifest(tmp_path, "open_pack")

    hub = plugin_hub.ExtensionHub()
    hub._discover_data_packs()
    names = {r.name for r in hub.records_by_kind(PluginKind.DATA_PACK)}
    assert "open_pack" in names


def test_unsigned_external_pack_dropped_when_required(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv(plugin_hub._DATA_PACKS_DIR_ENV, str(tmp_path))
    monkeypatch.setenv(plugin_hub._DATA_PACK_REQUIRE_SIGNATURE_ENV, "true")
    _write_manifest(tmp_path, "open_pack")

    hub = plugin_hub.ExtensionHub()
    hub._discover_data_packs()
    names = {r.name for r in hub.records_by_kind(PluginKind.DATA_PACK)}
    assert "open_pack" not in names


def _write_signed_manifest(
    dirpath: Path, name: str, priv: Ed25519PrivateKey
) -> Path:
    """Write a manifest and sign exactly what's on disk."""
    payload = {
        "name": name,
        "content": "source-catalog",
        "version": "1.0.0",
        "display_name": name.title(),
        "description": "fixture",
        "tier": "community",
        "entries": [],
    }
    payload["signature"] = sign_manifest_dict(payload, priv)
    p = dirpath / f"{name}.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    return p


def test_signed_external_pack_admitted(
    tmp_path, monkeypatch, keypair, public_key_b64
) -> None:
    priv, _ = keypair
    monkeypatch.setenv(plugin_hub._DATA_PACKS_DIR_ENV, str(tmp_path))
    monkeypatch.setenv(plugin_hub._DATA_PACK_PUBLIC_KEY_ENV, public_key_b64)

    _write_signed_manifest(tmp_path, "signed_pack", priv)

    hub = plugin_hub.ExtensionHub()
    hub._discover_data_packs()
    names = {r.name for r in hub.records_by_kind(PluginKind.DATA_PACK)}
    assert "signed_pack" in names


def test_tampered_signed_pack_dropped(
    tmp_path, monkeypatch, keypair, public_key_b64
) -> None:
    """A pack whose manifest was edited after signing must NOT register."""
    priv, _ = keypair
    monkeypatch.setenv(plugin_hub._DATA_PACKS_DIR_ENV, str(tmp_path))
    monkeypatch.setenv(plugin_hub._DATA_PACK_PUBLIC_KEY_ENV, public_key_b64)

    # Sign legitimately, then tamper after the signature is computed.
    p = _write_signed_manifest(tmp_path, "shady", priv)
    raw = json.loads(p.read_text(encoding="utf-8"))
    raw["tier"] = "enterprise"  # silent tier upgrade
    p.write_text(json.dumps(raw), encoding="utf-8")

    hub = plugin_hub.ExtensionHub()
    hub._discover_data_packs()
    names = {r.name for r in hub.records_by_kind(PluginKind.DATA_PACK)}
    assert "shady" not in names


def test_signed_pack_dropped_when_no_public_key_configured(
    tmp_path, monkeypatch, keypair
) -> None:
    """If a manifest claims a signature but no key is configured, refuse it."""
    priv, _ = keypair
    monkeypatch.setenv(plugin_hub._DATA_PACKS_DIR_ENV, str(tmp_path))
    monkeypatch.delenv(plugin_hub._DATA_PACK_PUBLIC_KEY_ENV, raising=False)
    _write_signed_manifest(tmp_path, "claims_signature", priv)

    hub = plugin_hub.ExtensionHub()
    hub._discover_data_packs()
    names = {r.name for r in hub.records_by_kind(PluginKind.DATA_PACK)}
    assert "claims_signature" not in names


def test_bundled_internal_pack_exempt_from_signature_gate(
    tmp_path, monkeypatch
) -> None:
    """INTERNAL (bundled OSS) manifests are not gated — they're the source of truth."""
    monkeypatch.delenv(plugin_hub._DATA_PACKS_DIR_ENV, raising=False)
    monkeypatch.setenv(plugin_hub._DATA_PACK_REQUIRE_SIGNATURE_ENV, "true")

    hub = plugin_hub.ExtensionHub()
    hub._discover_data_packs()
    # The bundled templates manifest produces at least one DATA_PACK record
    # even though it carries no signature and signature is "required".
    internals = [
        r
        for r in hub.records_by_kind(PluginKind.DATA_PACK)
        if r.origin is Origin.INTERNAL
    ]
    assert internals, "bundled INTERNAL data pack should always register"
