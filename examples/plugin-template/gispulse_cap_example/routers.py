"""Example host router factory for the current PluginHub contract."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter


class ExampleRouterFactory:
    """Factory loaded from the ``gispulse.routers`` entry-point group."""

    name = "example"

    def create(self, app: Any) -> APIRouter:
        router = APIRouter(prefix="/plugins/example", tags=["plugins"])

        @router.get("/health")
        def health() -> dict[str, str]:
            return {"plugin": "example", "status": "ok"}

        return router
