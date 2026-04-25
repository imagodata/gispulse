"""Shared fixtures for SDK tests."""

from __future__ import annotations

import pytest
import respx

from gispulse_sdk import GISPulseClient


BASE_URL = "https://gispulse.test"
API_KEY = "test-key-123"


@pytest.fixture
def mock_api():
    """Activate respx mock router for the test."""
    with respx.mock(base_url=BASE_URL) as router:
        yield router


@pytest.fixture
def client(mock_api):
    """Return a GISPulseClient wired to the mock API."""
    c = GISPulseClient(BASE_URL, api_key=API_KEY)
    yield c
    c.close()
