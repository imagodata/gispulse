"""Tests for the curated plugin-author import surface."""

from __future__ import annotations


def test_plugin_api_reexports_curated_runtime_primitives() -> None:
    from capabilities.base import Capability
    from capabilities.registry import register
    from catalog.models import CatalogEntry, FluxEntry
    from core.crs import is_angular, suggest_metric_crs
    from core.models import OGCSourceConfig
    from core.pipeline import PipelineSpec, StepSpec
    from core.plugin_contracts import PluginHostContext
    from gispulse.adapters.ogc.wfs_client import fetch_wfs
    from gispulse.plugins import api
    from orchestration.pipeline_executor import PipelineExecutor

    assert api.Capability is Capability
    assert api.register_capability is register
    assert api.PluginHostContext is PluginHostContext
    assert api.CatalogEntry is CatalogEntry
    assert api.PipelineSpec is PipelineSpec
    assert api.StepSpec is StepSpec
    assert api.PipelineExecutor is PipelineExecutor
    assert api.FluxEntry is FluxEntry
    assert api.OGCSourceConfig is OGCSourceConfig
    assert api.fetch_wfs is fetch_wfs
    assert api.is_angular is is_angular
    assert api.suggest_metric_crs is suggest_metric_crs


def test_plugin_submodules_reexport_curated_runtime_primitives() -> None:
    from catalog.models import CatalogEntry, FluxEntry
    from core.crs import is_angular, suggest_metric_crs
    from core.models import OGCSourceConfig
    from core.pipeline import PipelineSpec, StepSpec
    from gispulse.adapters.ogc.wfs_client import fetch_wfs
    from gispulse.plugins import pipeline, sources, spatial
    from orchestration.pipeline_executor import PipelineExecutor

    assert pipeline.PipelineSpec is PipelineSpec
    assert pipeline.StepSpec is StepSpec
    assert pipeline.PipelineExecutor is PipelineExecutor
    assert sources.FluxEntry is FluxEntry
    assert sources.CatalogEntry is CatalogEntry
    assert sources.OGCSourceConfig is OGCSourceConfig
    assert sources.fetch_wfs is fetch_wfs
    assert spatial.is_angular is is_angular
    assert spatial.suggest_metric_crs is suggest_metric_crs


def test_get_catalog_entry_delegates_to_catalog_registry(monkeypatch) -> None:
    from catalog.models import CatalogDomain, CatalogEntry
    from gispulse.plugins import sources

    expected = CatalogEntry(
        id="flux:example:roads",
        domain=CatalogDomain.FLUX,
        provider="example",
        name="Roads",
    )
    calls: list[str] = []

    def fake_get_entry(entry_id: str) -> CatalogEntry:
        calls.append(entry_id)
        return expected

    monkeypatch.setattr(sources.registry, "get_entry", fake_get_entry)

    assert sources.get_catalog_entry("flux:example:roads") is expected
    assert calls == ["flux:example:roads"]


def test_get_flux_entry_returns_only_flux_entries(monkeypatch) -> None:
    from catalog.models import CatalogDomain, CatalogEntry, FluxEntry, FluxProtocol
    from gispulse.plugins import sources

    flux = FluxEntry(
        id="flux:example:roads",
        domain=CatalogDomain.FLUX,
        provider="example",
        name="Roads",
        protocol=FluxProtocol.WFS,
    )
    projection = CatalogEntry(
        id="projection:example:lambert",
        domain=CatalogDomain.PROJECTION,
        provider="example",
        name="Lambert",
    )
    entries = {
        flux.id: flux,
        projection.id: projection,
    }

    monkeypatch.setattr(sources.registry, "get_entry", entries.get)

    assert sources.get_flux_entry("flux:example:roads") is flux
    assert sources.get_flux_entry("projection:example:lambert") is None
    assert sources.get_flux_entry("missing") is None
