"""Regression tests for the local Garage object-store Compose wiring."""

from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]


def _compose() -> dict:
    return yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))


def test_garage_service_is_opt_in_and_uses_env_secrets() -> None:
    compose = _compose()
    garage = compose["services"]["garage"]

    assert "garage" in garage.get("profiles", [])
    env = garage["environment"]
    assert env["GARAGE_RPC_SECRET"] == "${GARAGE_RPC_SECRET:-}"
    assert env["GARAGE_ADMIN_TOKEN"] == "${GARAGE_ADMIN_TOKEN:-}"


def test_garage_toml_does_not_commit_secret_placeholders() -> None:
    text = (ROOT / "docker" / "garage.toml").read_text(encoding="utf-8")

    assert "REPLACE_ME" not in text
    assert "rpc_secret =" not in text
    assert "admin_token =" not in text


def test_gispulse_api_receives_s3_env_interpolation() -> None:
    compose = _compose()
    env = compose["services"]["gispulse-api"]["environment"]

    expected = {
        "GISPULSE_TIER": "${GISPULSE_TIER:-community}",
        "GISPULSE_LICENSE_KEY": "${GISPULSE_LICENSE_KEY:-}",
        "GISPULSE_LICENCE_SKIP_VERIFY": "${GISPULSE_LICENCE_SKIP_VERIFY:-false}",
        "GISPULSE_S3_ENDPOINT": "${GISPULSE_S3_ENDPOINT:-}",
        "GISPULSE_S3_BUCKET": "${GISPULSE_S3_BUCKET:-gispulse}",
        "GISPULSE_S3_ACCESS_KEY": "${GISPULSE_S3_ACCESS_KEY:-}",
        "GISPULSE_S3_SECRET_KEY": "${GISPULSE_S3_SECRET_KEY:-}",
        "GISPULSE_S3_REGION": "${GISPULSE_S3_REGION:-garage}",
    }
    for key, value in expected.items():
        assert env[key] == value


def test_env_example_documents_garage_and_gispulse_secret_split() -> None:
    text = (ROOT / ".env.example").read_text(encoding="utf-8")

    assert "GARAGE_RPC_SECRET=" in text
    assert "GARAGE_ADMIN_TOKEN=" in text
    assert "GISPULSE_S3_ACCESS_KEY=" in text
    assert "GISPULSE_S3_SECRET_KEY=" in text
    assert "uv run --env-file .env" in text
