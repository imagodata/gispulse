"""Authentication helpers for OGC service requests."""

from __future__ import annotations

import base64


def build_auth_headers(auth: dict[str, str] | None) -> dict[str, str]:
    """Build HTTP headers from an auth configuration dict.

    Supported schemes:

    - ``{"type": "apikey", "key": "<value>"}``
      -> ``{"Authorization": "Apikey <value>"}``
    - ``{"type": "apikey", "header": "X-Api-Key", "key": "<value>"}``
      -> ``{"X-Api-Key": "<value>"}``
    - ``{"type": "basic", "username": "<u>", "password": "<p>"}``
      -> ``{"Authorization": "Basic <b64>"}``
    - ``{"type": "bearer", "token": "<t>"}``
      -> ``{"Authorization": "Bearer <t>"}``

    Returns an empty dict when *auth* is ``None`` or empty.
    """
    if not auth:
        return {}

    scheme = auth.get("type", "").lower()

    if scheme == "apikey":
        header_name = auth.get("header", "Authorization")
        key = auth["key"]
        if header_name == "Authorization":
            return {"Authorization": f"Apikey {key}"}
        return {header_name: key}

    if scheme == "basic":
        credential = base64.b64encode(
            f"{auth['username']}:{auth['password']}".encode()
        ).decode()
        return {"Authorization": f"Basic {credential}"}

    if scheme == "bearer":
        return {"Authorization": f"Bearer {auth['token']}"}

    raise ValueError(f"Unsupported auth type: {scheme!r}")
