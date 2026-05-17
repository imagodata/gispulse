"""Tests for the v1.8.0 application façade — :class:`gispulse.GISPulseApp`.

Covers Chantier B of the "Foundations" refonte: the single in-process
entry point and the lazy pip façade re-exported from :mod:`gispulse`.
"""
from __future__ import annotations

import geopandas as gpd
import pytest
from shapely.geometry import Point

import gispulse


def _point_gdf() -> gpd.GeoDataFrame:
    """A one-row GeoDataFrame with a single point at the origin."""
    return gpd.GeoDataFrame({"id": [1]}, geometry=[Point(0.0, 0.0)], crs="EPSG:4326")


class TestPipFacade:
    """The lazy pip façade exposed from ``gispulse.__init__``."""

    def test_lazy_exports_are_reachable(self) -> None:
        from gispulse import GISPulseApp, apply, get_app, run

        assert callable(apply)
        assert callable(run)
        assert callable(get_app)
        assert isinstance(GISPulseApp(), GISPulseApp)

    def test_unknown_attribute_raises(self) -> None:
        with pytest.raises(AttributeError):
            _ = gispulse.does_not_exist  # type: ignore[attr-defined]

    def test_dir_lists_facade(self) -> None:
        exported = dir(gispulse)
        for name in ("GISPulseApp", "apply", "run", "get_app"):
            assert name in exported

    def test_get_app_is_a_singleton(self) -> None:
        from gispulse import get_app

        assert get_app() is get_app()


class TestCapabilities:
    def test_list_capabilities_includes_buffer(self) -> None:
        caps = gispulse.GISPulseApp().list_capabilities()
        assert caps, "expected at least one registered capability"
        names = {c["name"] for c in caps}
        assert "buffer" in names

    def test_apply_capability_runs(self) -> None:
        out = gispulse.GISPulseApp().apply_capability(
            "buffer", _point_gdf(), distance=5.0
        )
        assert len(out) == 1
        # A buffered point becomes a polygon with non-zero area.
        assert out.geometry.iloc[0].area > 0

    def test_apply_module_shortcut(self) -> None:
        out = gispulse.apply("buffer", _point_gdf(), distance=5.0)
        assert out.geometry.iloc[0].area > 0

    def test_unknown_capability_raises_keyerror(self) -> None:
        with pytest.raises(KeyError):
            gispulse.GISPulseApp().apply_capability("__nope__", _point_gdf())

    def test_unknown_parameter_is_rejected(self) -> None:
        from gispulse.capabilities.base import UnknownParameterError

        with pytest.raises(UnknownParameterError):
            gispulse.GISPulseApp().apply_capability(
                "buffer", _point_gdf(), distanZe=5.0
            )


class TestPipelines:
    def test_run_pipeline_from_dict(self) -> None:
        spec = {
            "version": 2,
            "name": "facade-smoke",
            "steps": [
                {
                    "id": "s1",
                    "type": "capability",
                    "capability": "buffer",
                    "params": {"distance": 5.0},
                }
            ],
        }
        result = gispulse.run(spec, {"input": _point_gdf()})
        assert "s1" in result
        assert result["s1"].geometry.iloc[0].area > 0


class TestCatalog:
    def test_browse_catalog_returns_list(self) -> None:
        entries = gispulse.GISPulseApp().browse_catalog(limit=5)
        assert isinstance(entries, list)


class TestTemplates:
    def test_list_templates_finds_builtins(self) -> None:
        templates = gispulse.GISPulseApp().list_templates()
        assert templates, "expected built-in templates at <repo>/templates"
        first = templates[0]
        assert {"name", "title", "description"} <= set(first)

    def test_get_template_unknown_raises(self) -> None:
        with pytest.raises(FileNotFoundError):
            gispulse.GISPulseApp().get_template("__no_such_template__")

    def test_instantiate_template_parses_a_spec(self) -> None:
        from gispulse.core.pipeline import PipelineSpec

        app = gispulse.GISPulseApp()
        name = app.list_templates()[0]["name"]
        spec = app.instantiate_template(name)
        assert isinstance(spec, PipelineSpec)


class TestPlugins:
    def test_list_plugins_returns_list(self) -> None:
        plugins = gispulse.GISPulseApp().list_plugins()
        assert isinstance(plugins, list)
