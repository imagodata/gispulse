"""Example host router factory for the typed PluginHostContext contract."""

from __future__ import annotations

from fastapi import APIRouter

from gispulse.plugins.api import PluginHostContext


class ExampleRouterFactory:
    """Factory loaded from the ``gispulse.routers`` entry-point group."""

    name = "example"

    def create(self, ctx: PluginHostContext) -> APIRouter:
        ctx.logger.debug("example_plugin_router_mounting")
        router = APIRouter(prefix="/plugins/example", tags=["plugins"])

        @router.get("/health")
        def health() -> dict[str, str]:
            return {"plugin": "example", "status": "ok"}

        return router
