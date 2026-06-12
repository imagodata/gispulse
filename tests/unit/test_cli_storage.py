from __future__ import annotations

import json
import os
from unittest.mock import patch

from typer.testing import CliRunner

from gispulse.cli import app


runner = CliRunner()


def test_storage_status_reports_auto_local_json() -> None:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GISPULSE_S3_")}
    with patch.dict(os.environ, env, clear=True):
        result = runner.invoke(app, ["storage", "status", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["requested_mode"] == "auto"
    assert payload["backend"] == "local"
    assert payload["storage_class"] == "LocalStorage"
    assert payload["fallback"] is False


def test_storage_status_s3_missing_endpoint_exits_1_json() -> None:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GISPULSE_S3_")}
    with patch.dict(os.environ, env, clear=True):
        result = runner.invoke(app, ["storage", "status", "--mode", "s3", "--json"])

    assert result.exit_code == 1, result.output
    payload = json.loads(result.output)
    assert payload["backend"] == "unconfigured"
    assert payload["error"]["code"] == "STORAGE_S3_NOT_CONFIGURED"
    assert "GISPULSE_S3_ENDPOINT" in payload["error"]["recovery"]
