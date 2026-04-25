"""End-to-end test for the GISPulse CLI pipeline."""

import json

import geopandas as gpd
import pytest
from shapely.geometry import Point
from typer.testing import CliRunner

from gispulse.cli import app

runner = CliRunner()


@pytest.fixture
def sample_gpkg(tmp_path):
    """Create a sample GPKG with test data."""
    gdf = gpd.GeoDataFrame(
        {"name": ["A", "B", "C", "D"], "area": [50, 150, 200, 10]},
        geometry=[Point(0, 0), Point(1, 1), Point(2, 2), Point(3, 3)],
        crs="EPSG:4326",
    )
    path = tmp_path / "input.gpkg"
    gdf.to_file(str(path), layer="data", driver="GPKG")
    return path


@pytest.fixture
def filter_rules(tmp_path):
    """Create rules that filter by area > 100."""
    rules = [
        {
            "name": "filter_large",
            "capability": "filter",
            "config": {"expression": "area > 100"},
        }
    ]
    path = tmp_path / "rules.json"
    path.write_text(json.dumps(rules), encoding="utf-8")
    return path


@pytest.fixture
def buffer_rules(tmp_path):
    """Create rules that apply a 100m buffer."""
    rules = [
        {
            "name": "buffer_100",
            "capability": "buffer",
            "config": {"distance": 100},
        }
    ]
    path = tmp_path / "rules.json"
    path.write_text(json.dumps(rules), encoding="utf-8")
    return path


class TestCLIRun:
    def test_filter_pipeline(self, sample_gpkg, filter_rules, tmp_path):
        output = tmp_path / "output.gpkg"
        result = runner.invoke(app, [
            "run", str(sample_gpkg),
            "--rules", str(filter_rules),
            "--output", str(output),
            "--layer", "data",
        ])
        assert result.exit_code == 0, result.output
        assert output.exists()

        # Verify filtered output
        gdf = gpd.read_file(str(output))
        assert len(gdf) == 2  # B (150) and C (200)
        assert set(gdf["name"]) == {"B", "C"}

    def test_buffer_pipeline(self, sample_gpkg, buffer_rules, tmp_path):
        output = tmp_path / "output.gpkg"
        result = runner.invoke(app, [
            "run", str(sample_gpkg),
            "--rules", str(buffer_rules),
            "--output", str(output),
            "--layer", "data",
        ])
        assert result.exit_code == 0, result.output
        assert output.exists()

        gdf = gpd.read_file(str(output))
        assert len(gdf) == 4
        # Buffered geometries should be polygons, not points
        assert all(g.geom_type == "Polygon" for g in gdf.geometry)

    def test_missing_input(self, tmp_path):
        result = runner.invoke(app, [
            "run", str(tmp_path / "missing.gpkg"),
            "--rules", str(tmp_path / "r.json"),
            "--output", str(tmp_path / "out.gpkg"),
        ])
        assert result.exit_code != 0

    def test_missing_rules(self, sample_gpkg, tmp_path):
        result = runner.invoke(app, [
            "run", str(sample_gpkg),
            "--rules", str(tmp_path / "missing.json"),
            "--output", str(tmp_path / "out.gpkg"),
        ])
        assert result.exit_code != 0


class TestCLIMultiFormat:
    """Test the CLI with various input/output format combinations."""

    @pytest.fixture
    def sample_geojson(self, tmp_path):
        gdf = gpd.GeoDataFrame(
            {"name": ["A", "B", "C", "D"], "area": [50, 150, 200, 10]},
            geometry=[Point(0, 0), Point(1, 1), Point(2, 2), Point(3, 3)],
            crs="EPSG:4326",
        )
        path = tmp_path / "input.geojson"
        gdf.to_file(str(path), driver="GeoJSON")
        return path

    @pytest.fixture
    def sample_shapefile(self, tmp_path):
        gdf = gpd.GeoDataFrame(
            {"name": ["A", "B", "C", "D"], "area": [50, 150, 200, 10]},
            geometry=[Point(0, 0), Point(1, 1), Point(2, 2), Point(3, 3)],
            crs="EPSG:4326",
        )
        path = tmp_path / "input.shp"
        gdf.to_file(str(path), driver="ESRI Shapefile")
        return path

    @pytest.fixture
    def sample_csv(self, tmp_path):
        path = tmp_path / "input.csv"
        path.write_text(
            "name,area,latitude,longitude\n"
            "A,50,0,0\n"
            "B,150,1,1\n"
            "C,200,2,2\n"
            "D,10,3,3\n",
            encoding="utf-8",
        )
        return path

    def test_geojson_to_gpkg(self, sample_geojson, filter_rules, tmp_path):
        output = tmp_path / "output.gpkg"
        result = runner.invoke(app, [
            "run", str(sample_geojson),
            "--rules", str(filter_rules),
            "--output", str(output),
        ])
        assert result.exit_code == 0, result.output
        assert output.exists()
        gdf = gpd.read_file(str(output))
        assert len(gdf) == 2

    def test_geojson_to_geojson(self, sample_geojson, filter_rules, tmp_path):
        output = tmp_path / "result.geojson"
        result = runner.invoke(app, [
            "run", str(sample_geojson),
            "--rules", str(filter_rules),
            "--output", str(output),
        ])
        assert result.exit_code == 0, result.output
        gdf = gpd.read_file(str(output))
        assert len(gdf) == 2

    def test_shapefile_to_flatgeobuf(self, sample_shapefile, filter_rules, tmp_path):
        output = tmp_path / "result.fgb"
        result = runner.invoke(app, [
            "run", str(sample_shapefile),
            "--rules", str(filter_rules),
            "--output", str(output),
        ])
        assert result.exit_code == 0, result.output
        gdf = gpd.read_file(str(output))
        assert len(gdf) == 2

    def test_gpkg_to_geojson(self, sample_gpkg, filter_rules, tmp_path):
        output = tmp_path / "result.geojson"
        result = runner.invoke(app, [
            "run", str(sample_gpkg),
            "--rules", str(filter_rules),
            "--output", str(output),
            "--layer", "data",
        ])
        assert result.exit_code == 0, result.output
        gdf = gpd.read_file(str(output))
        assert len(gdf) == 2

    def test_csv_to_gpkg(self, sample_csv, filter_rules, tmp_path):
        output = tmp_path / "result.gpkg"
        result = runner.invoke(app, [
            "run", str(sample_csv),
            "--rules", str(filter_rules),
            "--output", str(output),
        ])
        assert result.exit_code == 0, result.output
        gdf = gpd.read_file(str(output))
        assert len(gdf) == 2

    def test_unsupported_input_format(self, tmp_path, filter_rules):
        bad_input = tmp_path / "data.xyz"
        bad_input.write_text("dummy")
        result = runner.invoke(app, [
            "run", str(bad_input),
            "--rules", str(filter_rules),
            "--output", str(tmp_path / "out.gpkg"),
        ])
        assert result.exit_code != 0
        assert "unsupported" in result.output.lower()

    def test_unsupported_output_format(self, sample_gpkg, filter_rules, tmp_path):
        result = runner.invoke(app, [
            "run", str(sample_gpkg),
            "--rules", str(filter_rules),
            "--output", str(tmp_path / "out.dxf"),
            "--layer", "data",
        ])
        assert result.exit_code != 0
        assert "cannot write" in result.output.lower()


class TestCLILayers:
    def test_list_layers_gpkg(self, sample_gpkg):
        result = runner.invoke(app, ["layers", str(sample_gpkg)])
        assert result.exit_code == 0
        assert "data" in result.output

    def test_single_layer_format(self, tmp_path):
        gdf = gpd.GeoDataFrame(
            {"val": [1]},
            geometry=[Point(0, 0)],
            crs="EPSG:4326",
        )
        path = tmp_path / "test.geojson"
        gdf.to_file(str(path), driver="GeoJSON")
        result = runner.invoke(app, ["layers", str(path)])
        assert result.exit_code == 0
        assert "Single-layer" in result.output


class TestCLIFormats:
    def test_list_formats(self):
        result = runner.invoke(app, ["formats"])
        assert result.exit_code == 0
        assert ".gpkg" in result.output
        assert ".geojson" in result.output
        assert ".shp" in result.output
        assert ".csv" in result.output
        assert "Read" in result.output or "yes" in result.output


class TestCLICapabilities:
    def test_list_capabilities(self):
        result = runner.invoke(app, ["capabilities"])
        assert result.exit_code == 0
        assert "buffer" in result.output
        assert "filter" in result.output
        assert "intersects" in result.output
        assert "spatial_join" in result.output
        assert "centroid" in result.output
        assert "area_length" in result.output


class TestCLIValidate:
    def test_validate_valid_rules(self, filter_rules):
        result = runner.invoke(app, ["validate", str(filter_rules)])
        assert result.exit_code == 0
        assert "OK" in result.output
        assert "valid" in result.output.lower()

    def test_validate_invalid_rules(self, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text(json.dumps([
            {"name": "bad", "capability": "nonexistent", "config": {}}
        ]))
        result = runner.invoke(app, ["validate", str(bad)])
        assert result.exit_code != 0
        assert "FAIL" in result.output

    def test_validate_missing_file(self, tmp_path):
        result = runner.invoke(app, ["validate", str(tmp_path / "missing.json")])
        assert result.exit_code != 0


class TestCLIInit:
    def test_init_creates_structure(self, tmp_path):
        project_dir = tmp_path / "my_project"
        result = runner.invoke(app, ["init", str(project_dir), "--name", "TestProj"])
        assert result.exit_code == 0
        assert "Initialized" in result.output
        assert (project_dir / "rules" / "rules.json").exists()
        assert (project_dir / "data").is_dir()
        assert (project_dir / "output").is_dir()
        assert (project_dir / "Makefile").exists()

    def test_init_default_current_dir(self, tmp_path):
        result = runner.invoke(app, ["init", str(tmp_path)])
        assert result.exit_code == 0
        assert (tmp_path / "rules" / "rules.json").exists()

    def test_init_idempotent(self, tmp_path):
        """Running init twice should not overwrite existing files."""
        runner.invoke(app, ["init", str(tmp_path)])
        # Modify the rules file
        rules_file = tmp_path / "rules" / "rules.json"
        rules_file.write_text("[]")
        # Run init again
        runner.invoke(app, ["init", str(tmp_path)])
        # Rules file should NOT be overwritten
        assert rules_file.read_text() == "[]"


class TestCLIInfo:
    def test_info_gpkg(self, sample_gpkg):
        result = runner.invoke(app, ["info", str(sample_gpkg)])
        assert result.exit_code == 0
        assert "GPKG" in result.output
        assert "EPSG:4326" in result.output
        assert "data" in result.output
        assert "4 features" in result.output

    def test_info_missing_file(self, tmp_path):
        result = runner.invoke(app, ["info", str(tmp_path / "missing.gpkg")])
        assert result.exit_code != 0


class TestCLICrossLayer:
    """Test cross-layer operations via CLI."""

    @pytest.fixture
    def multi_layer_gpkg(self, tmp_path):
        from shapely.geometry import box
        parcels = gpd.GeoDataFrame(
            {"name": ["A", "B"], "zone": ["green", "industrial"]},
            geometry=[box(0, 0, 1, 1), box(2, 2, 3, 3)],
            crs="EPSG:4326",
        )
        zones = gpd.GeoDataFrame(
            {"risk": ["high"]},
            geometry=[box(0.5, 0.5, 1.5, 1.5)],
            crs="EPSG:4326",
        )
        path = tmp_path / "multi.gpkg"
        parcels.to_file(str(path), layer="parcels", driver="GPKG")
        zones.to_file(str(path), layer="zones", driver="GPKG", mode="a")
        return path

    @pytest.fixture
    def cross_rules(self, tmp_path):
        rules = [
            {
                "name": "intersect_zones",
                "capability": "intersects",
                "config": {"ref_layer": "zones"},
            }
        ]
        path = tmp_path / "cross.json"
        path.write_text(json.dumps(rules))
        return path

    def test_cross_layer_intersects(self, multi_layer_gpkg, cross_rules, tmp_path):
        output = tmp_path / "result.geojson"
        result = runner.invoke(app, [
            "run", str(multi_layer_gpkg),
            "--layer", "parcels",
            "--rules", str(cross_rules),
            "--output", str(output),
        ])
        assert result.exit_code == 0, result.output
        assert "ref layer: zones" in result.output.lower() or "ref: zones" in result.output
        gdf = gpd.read_file(str(output))
        assert len(gdf) == 1  # Only parcel A intersects the zone
