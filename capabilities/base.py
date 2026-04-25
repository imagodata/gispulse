"""
Base class for all GISPulse capabilities.

Each capability encapsulates a single, reusable spatial operation.
Concrete implementations live in capabilities/vector.py (and future
capabilities/raster.py, capabilities/network.py, etc.).
"""

from __future__ import annotations

import inspect
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

import geopandas as gpd

if TYPE_CHECKING:
    from capabilities.strategy import ExecutionContext, ExecutionStrategy

from core.logging import get_logger

log = get_logger(__name__)


class UnknownParameterError(TypeError):
    """Raised when a kwarg is passed that the capability does not declare.

    Inherits :class:`TypeError` so existing callers that catch ``TypeError``
    around an ``execute()`` call still see this. The dedicated subclass
    lets the dispatch layer (and pipeline UI) surface a precise message
    pointing at the typo'd parameter name and the list of accepted ones.

    Replaces the silent ``**_`` swallow that allowed
    ``AddFieldCapability(fild="...")`` to return the input unchanged with
    no warning (Beta finding P2-3, reclassed P1 in EPIC-1 v1.2.0).
    """


def _accepted_param_names(execute_fn) -> set[str]:
    """Introspect ``execute_fn`` and return the kwargs it explicitly names.

    Skips ``self``/``gdf`` (positional contract) and any ``VAR_KEYWORD``
    (``**_``) / ``VAR_POSITIONAL`` (``*args``) — those exist only to
    absorb the legacy soft-call protocol and never identify a real
    parameter the caller can target.
    """
    accepted: set[str] = set()
    sig = inspect.signature(execute_fn)
    for name, param in sig.parameters.items():
        if name in {"self", "gdf"}:
            continue
        if param.kind in (
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        ):
            accepted.add(name)
    return accepted


def safe_execute(cap, gdf: gpd.GeoDataFrame, **params) -> gpd.GeoDataFrame:
    """Validate ``params`` against ``cap.execute()``'s signature, then dispatch.

    Module-level helper used by every orchestration entry point
    (``pipeline_executor``, ``graph_executor``, ``rules.engine``,
    ``capability_executor``, ``pipelines_router``).

    Validation only applies to :class:`Capability` subclasses — production
    capabilities are all registered through ``@register`` which enforces
    that lineage, so this covers 100 % of the kwarg-swallow surface.
    Duck-typed test fixtures (``FakeCapability`` without inheritance) are
    intentionally permissive wrappers and pass through unchanged.

    Catches typos like ``fild=...`` instead of ``field=...`` which the
    ubiquitous ``**_`` placeholder in capability signatures would
    otherwise silently swallow (Beta finding P2-3 / EPIC-1 v1.2.0).
    """
    if not isinstance(cap, Capability):
        # Duck-typed plugin / test stub — preserve the legacy permissive
        # contract. Production code never lands here because ``@register``
        # requires inheriting from Capability.
        return cap.execute(gdf, **params)
    accepted = _accepted_param_names(cap.execute)
    unknown = sorted(set(params) - accepted)
    if unknown:
        cap_name = getattr(cap, "name", type(cap).__name__)
        raise UnknownParameterError(
            f"{cap_name}: unknown parameter(s) {unknown}; "
            f"accepted: {sorted(accepted)}"
        )
    return cap.execute(gdf, **params)


class Capability(ABC):
    """Abstract base for all GISPulse spatial capabilities.

    Subclasses must declare:
    - ``name``        : unique snake_case identifier used in the registry.
    - ``description`` : human-readable one-liner for Studio/API.

    And implement:
    - ``execute()``   : the spatial operation itself.
    - ``get_schema()`` (optional): JSON Schema of accepted **params.

    Optionally, subclasses can populate ``_strategies`` with
    :class:`ExecutionStrategy` instances to enable backend-aware
    dispatching via ``execute_with_context()``.
    """

    name: str
    description: str
    # Subclasses override with a list of strategies; empty tuple prevents
    # accidental mutation of the shared class-level default.
    _strategies: list[ExecutionStrategy] | tuple[()] = ()

    @abstractmethod
    def execute(self, gdf: gpd.GeoDataFrame, **params) -> gpd.GeoDataFrame:
        """Run the capability on a GeoDataFrame.

        Args:
            gdf:    Input GeoDataFrame (never mutated in-place).
            **params: Capability-specific keyword arguments.

        Returns:
            New GeoDataFrame with the operation applied.
        """
        ...

    def execute_safe(self, gdf: gpd.GeoDataFrame, **params) -> gpd.GeoDataFrame:
        """Validate ``params`` against ``execute()``'s signature, then dispatch.

        Thin wrapper around :func:`safe_execute` — see that function for
        the validation rules. Calling ``execute()`` directly still
        accepts arbitrary kwargs (legacy contract); ``execute_safe`` is
        the validated entry point used by all orchestration code.
        """
        return safe_execute(self, gdf, **params)

    def execute_with_context(
        self, gdf: gpd.GeoDataFrame, ctx: ExecutionContext,
    ) -> gpd.GeoDataFrame:
        """Run the capability using the best available execution strategy.

        Selects the highest-priority strategy that can execute in the
        given context.  Falls back to the plain ``execute()`` method
        (Python/GeoPandas) if no strategy is eligible or none are declared.

        Args:
            gdf: Input GeoDataFrame (never mutated in-place).
            ctx: Runtime execution context with engine info and params.

        Returns:
            New GeoDataFrame with the operation applied.
        """
        from capabilities.strategy import select_strategy

        if self._strategies:
            strategy = select_strategy(self._strategies, ctx)
            if strategy is not None:
                log.info(
                    "strategy_selected",
                    capability=self.name,
                    strategy=strategy.mode.value,
                    priority=strategy.priority,
                )
                return strategy.execute(gdf, ctx)
            log.debug(
                "no_eligible_strategy",
                capability=self.name,
                fallback="python",
            )

        # Fallback: validated execute() with params from context. Going
        # through safe_execute surfaces typo'd kwargs in pipeline configs
        # instead of silently dropping them via the ``**_`` placeholder.
        return safe_execute(self, gdf, **ctx.params)

    def get_schema(self) -> dict:
        """Return the JSON Schema for this capability's **params.

        Used by GISPulse Studio to build dynamic forms.
        Subclasses should override this to expose their parameters.

        Returns:
            JSON Schema dict (``{"type": "object", "properties": {...}}``)
            or an empty dict when no parameters are needed.
        """
        return {}
