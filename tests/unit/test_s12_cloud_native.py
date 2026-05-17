"""
Sprint S12 — Cloud-native integration tests.

Covers:
- GeoParquet read/write roundtrip (core.io.geoparquet)
- STACClient search with mocked HTTP (catalog.providers.stac_client)
- Pipeline templates: valid JSON, parseable, expected structure
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import geopandas as gpd
import pytest
from shapely.geometry import Point

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "templates"


@pytest.fixture()
def sample_gdf() -> gpd.GeoDataFrame:
    """Small GeoDataFrame with 5 points in EPSG:4326."""
    return gpd.GeoDataFrame(
        {
            "id": [1, 2, 3, 4, 5],
            "name": ["paris", "lyon", "marseille", "bordeaux", "lille"],
            "population": [2_161_000, 515_000, 861_000, 254_000, 232_000],
            "geometry": [
                Point(2.3522, 48.8566),
                Point(4.8357, 45.7640),
                Point(5.3698, 43.2965),
                Point(-0.5800, 44.8378),
                Point(3.0573, 50.6292),
            ],
        },
        crs="EPSG:4326",
    )


# ---------------------------------------------------------------------------
# 1. GeoParquet read/write roundtrip
# ---------------------------------------------------------------------------


class TestGeoParquetRoundtrip:
    """read_geoparquet(write_geoparquet(gdf)) == gdf (geometries + attributes)."""

    def test_roundtrip_basic(self, sample_gdf: gpd.GeoDataFrame, tmp_path: Path) -> None:
        from gispulse.core.io.geoparquet import read_geoparquet, write_geoparquet

        out = str(tmp_path / "cities.parquet")
        write_geoparquet(sample_gdf, out)

        result = read_geoparquet(out)

        assert len(result) == len(sample_gdf)
        assert list(result.columns) == list(sample_gdf.columns) or "geometry" in result.columns
        assert result.crs is not None
        assert result.crs == sample_gdf.crs

    def test_roundtrip_preserves_attributes(
        self, sample_gdf: gpd.GeoDataFrame, tmp_path: Path
    ) -> None:
        from gispulse.core.io.geoparquet import read_geoparquet, write_geoparquet

        out = str(tmp_path / "attrs.parquet")
        write_geoparquet(sample_gdf, out)
        result = read_geoparquet(out)

        assert "name" in result.columns
        assert "population" in result.columns
        assert sorted(result["name"].tolist()) == sorted(sample_gdf["name"].tolist())

    def test_roundtrip_geometry_types(
        self, sample_gdf: gpd.GeoDataFrame, tmp_path: Path
    ) -> None:
        from gispulse.core.io.geoparquet import read_geoparquet, write_geoparquet

        out = str(tmp_path / "geom.parquet")
        write_geoparquet(sample_gdf, out)
        result = read_geoparquet(out)

        # All features must have valid Point geometries
        assert all(not g.is_empty for g in result.geometry)
        assert all(g.geom_type == "Point" for g in result.geometry)

    def test_write_compression_zstd(
        self, sample_gdf: gpd.GeoDataFrame, tmp_path: Path
    ) -> None:
        from gispulse.core.io.geoparquet import read_geoparquet, write_geoparquet

        out = str(tmp_path / "zstd.parquet")
        write_geoparquet(sample_gdf, out, compression="zstd")

        assert Path(out).exists()
        result = read_geoparquet(out)
        assert len(result) == len(sample_gdf)

    def test_force_geopandas_backend(
        self, sample_gdf: gpd.GeoDataFrame, tmp_path: Path
    ) -> None:
        from gispulse.core.io.geoparquet import read_geoparquet, write_geoparquet

        out = str(tmp_path / "gpd_backend.parquet")
        write_geoparquet(sample_gdf, out)

        result = read_geoparquet(out, use_duckdb=False)
        assert len(result) == len(sample_gdf)

    def test_write_no_geometry_raises(self, tmp_path: Path) -> None:
        from gispulse.core.io.geoparquet import write_geoparquet

        gdf = gpd.GeoDataFrame({"a": [1, 2]})
        with pytest.raises(ValueError, match="geometry"):
            write_geoparquet(gdf, str(tmp_path / "no_geom.parquet"))

    def test_read_missing_file_raises(self) -> None:
        from gispulse.core.io.geoparquet import read_geoparquet

        with pytest.raises(FileNotFoundError):
            read_geoparquet("/nonexistent/path/data.parquet")

    def test_auto_creates_parent_dir(
        self, sample_gdf: gpd.GeoDataFrame, tmp_path: Path
    ) -> None:
        from gispulse.core.io.geoparquet import read_geoparquet, write_geoparquet

        nested = str(tmp_path / "a" / "b" / "c" / "data.parquet")
        write_geoparquet(sample_gdf, nested)
        assert Path(nested).exists()
        result = read_geoparquet(nested)
        assert len(result) == len(sample_gdf)

    def test_duckdb_threshold_env_var(
        self, sample_gdf: gpd.GeoDataFrame, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Setting threshold to 0 forces DuckDB path (if available, else falls back)."""
        monkeypatch.setenv("GISPULSE_DUCKDB_THRESHOLD", "0")

        # Re-import after env var change to pick up new constant
        import importlib
        import gispulse.core.io.geoparquet as mod
        importlib.reload(mod)

        out = str(tmp_path / "threshold.parquet")
        mod.write_geoparquet(sample_gdf, out)
        result = mod.read_geoparquet(out)
        assert len(result) == len(sample_gdf)

        # Restore default
        monkeypatch.delenv("GISPULSE_DUCKDB_THRESHOLD", raising=False)
        importlib.reload(mod)


# ---------------------------------------------------------------------------
# 2. STACClient — mocked HTTP
# ---------------------------------------------------------------------------

_FAKE_ITEM: dict = {
    "type": "Feature",
    "stac_version": "1.0.0",
    "id": "S2A_20240601_T31UDQ",
    "geometry": {
        "type": "Polygon",
        "coordinates": [[[2.0, 48.0], [3.0, 48.0], [3.0, 49.0], [2.0, 49.0], [2.0, 48.0]]],
    },
    "properties": {
        "datetime": "2024-06-01T10:30:00Z",
        "eo:cloud_cover": 5.2,
        "platform": "sentinel-2a",
    },
    "assets": {
        "B04": {"href": "https://example.com/B04.tif", "type": "image/tiff"},
        "B08": {"href": "https://example.com/B08.tif", "type": "image/tiff"},
        "thumbnail": {"href": "https://example.com/thumb.jpg", "type": "image/jpeg"},
    },
    "links": [],
    "bbox": [2.0, 48.0, 3.0, 49.0],
}

_FAKE_COLLECTIONS: dict = {
    "collections": [
        {
            "id": "sentinel-2-l2a",
            "title": "Sentinel-2 Level-2A",
            "description": "Sentinel-2 MSI, Level-2A",
            "extent": {},
        },
        {
            "id": "landsat-c2-l2",
            "title": "Landsat Collection 2 Level-2",
            "description": "USGS Landsat Collection 2 Level-2",
            "extent": {},
        },
    ]
}


class TestSTACClientMocked:
    """Tests for STACClient using mocked urllib.request to avoid network calls."""

    @pytest.fixture()
    def client(self) -> "STACClient":  # noqa: F821
        from gispulse.catalog.providers.stac_client import STACClient

        return STACClient("https://fake-stac.example.com/api/stac/v1")

    def _mock_urlopen(self, response_data: dict):
        """Return a context manager mock that yields a fake HTTP response."""
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(response_data).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        return mock_resp

    def test_search_returns_items(self, client) -> None:
        """search() should return a list of item dicts."""
        fake_response = {"type": "FeatureCollection", "features": [_FAKE_ITEM]}

        with patch("urllib.request.urlopen", return_value=self._mock_urlopen(fake_response)):
            items = client.search(
                bbox=[2.0, 48.0, 3.0, 49.0],
                datetime="2024-06-01/2024-08-31",
                collections=["sentinel-2-l2a"],
                limit=5,
            )

        assert isinstance(items, list)
        assert len(items) == 1
        assert items[0]["id"] == "S2A_20240601_T31UDQ"

    def test_search_empty_result(self, client) -> None:
        """search() should return empty list when catalog has no matches."""
        fake_response = {"type": "FeatureCollection", "features": []}

        with patch("urllib.request.urlopen", return_value=self._mock_urlopen(fake_response)):
            items = client.search(
                bbox=[0.0, 0.0, 1.0, 1.0],
                datetime="2020-01-01",
                collections=["nonexistent-collection"],
                limit=10,
            )

        assert items == []

    def test_search_network_error_returns_empty(self, client) -> None:
        """A network failure during search should return [] and not raise."""
        with patch("urllib.request.urlopen", side_effect=OSError("connection refused")):
            items = client.search(
                bbox=[2.0, 48.0, 3.0, 49.0],
                datetime="2024-01-01",
                collections=["sentinel-2-l2a"],
            )

        assert items == []

    def test_list_collections(self, client) -> None:
        """list_collections() should return list of collection summaries."""
        with patch("urllib.request.urlopen", return_value=self._mock_urlopen(_FAKE_COLLECTIONS)):
            collections = client.list_collections()

        assert isinstance(collections, list)
        assert len(collections) == 2
        ids = [c["id"] for c in collections]
        assert "sentinel-2-l2a" in ids
        assert "landsat-c2-l2" in ids

    def test_list_collections_network_error(self, client) -> None:
        """list_collections() should return [] on network failure."""
        with patch("urllib.request.urlopen", side_effect=OSError("timeout")):
            result = client.list_collections()
        assert result == []

    def test_download_asset_missing_key_raises(self, client, tmp_path) -> None:
        """download_asset() should raise KeyError for unknown asset_key."""
        with pytest.raises(KeyError, match="B99"):
            client.download_asset(_FAKE_ITEM, "B99", str(tmp_path))

    def test_download_asset_skips_existing(self, client, tmp_path) -> None:
        """download_asset() skips download if file already exists and overwrite=False."""
        # Pre-create the destination file
        dest = tmp_path / "B04.tif"
        dest.write_bytes(b"fake-tiff-data")

        with patch("urllib.request.urlretrieve") as mock_retrieve:
            result = client.download_asset(_FAKE_ITEM, "B04", str(tmp_path), overwrite=False)

        mock_retrieve.assert_not_called()
        assert result == str(dest)

    def test_download_asset_calls_urlretrieve(self, client, tmp_path) -> None:
        """download_asset() calls urlretrieve when file is absent."""
        def fake_retrieve(url, dest):
            Path(dest).write_bytes(b"downloaded")

        with patch("urllib.request.urlretrieve", side_effect=fake_retrieve):
            result = client.download_asset(_FAKE_ITEM, "B04", str(tmp_path))

        assert Path(result).exists()
        assert Path(result).read_bytes() == b"downloaded"

    def test_known_catalogs_available(self) -> None:
        """KNOWN_CATALOGS should include Planetary Computer and Earth Search."""
        from gispulse.catalog.providers.stac_client import KNOWN_CATALOGS

        assert "planetary_computer" in KNOWN_CATALOGS
        assert "earth_search" in KNOWN_CATALOGS
        assert KNOWN_CATALOGS["planetary_computer"].startswith("https://")


# ---------------------------------------------------------------------------
# 3. Pipeline templates — JSON validity and structure
# ---------------------------------------------------------------------------


EXPECTED_TEMPLATES = [
    "validation_plu_cnig",
    "ftth_network_analysis",
    "environmental_monitoring",
]


class TestPipelineTemplates:
    """Templates must be valid JSON, parseable, and follow the rules schema."""

    @pytest.mark.parametrize("name", EXPECTED_TEMPLATES)
    def test_template_exists(self, name: str) -> None:
        tpl = TEMPLATES_DIR / f"{name}.json"
        assert tpl.exists(), f"Template file missing: {tpl}"

    @pytest.mark.parametrize("name", EXPECTED_TEMPLATES)
    def test_template_is_valid_json(self, name: str) -> None:
        tpl = TEMPLATES_DIR / f"{name}.json"
        content = tpl.read_text(encoding="utf-8")
        parsed = json.loads(content)  # raises json.JSONDecodeError if invalid
        assert parsed is not None

    @pytest.mark.parametrize("name", EXPECTED_TEMPLATES)
    def test_template_is_list_of_rules(self, name: str) -> None:
        """Each template must be a JSON array of rule objects."""
        tpl = TEMPLATES_DIR / f"{name}.json"
        rules = json.loads(tpl.read_text(encoding="utf-8"))

        assert isinstance(rules, list), f"{name}: expected list, got {type(rules)}"
        assert len(rules) > 0, f"{name}: template must have at least one rule"

    @pytest.mark.parametrize("name", EXPECTED_TEMPLATES)
    def test_each_rule_has_required_keys(self, name: str) -> None:
        """Each rule object must have 'name', 'capability', 'config', 'enabled'."""
        tpl = TEMPLATES_DIR / f"{name}.json"
        rules = json.loads(tpl.read_text(encoding="utf-8"))

        required_keys = {"name", "capability", "config", "enabled"}
        for i, rule in enumerate(rules):
            missing = required_keys - rule.keys()
            assert not missing, (
                f"{name}[{i}]: missing keys {missing} in rule '{rule.get('name', '?')}'"
            )

    @pytest.mark.parametrize("name", EXPECTED_TEMPLATES)
    def test_rule_names_are_unique(self, name: str) -> None:
        tpl = TEMPLATES_DIR / f"{name}.json"
        rules = json.loads(tpl.read_text(encoding="utf-8"))

        names = [r["name"] for r in rules]
        assert len(names) == len(set(names)), f"{name}: duplicate rule names: {names}"

    @pytest.mark.parametrize("name", EXPECTED_TEMPLATES)
    def test_capabilities_are_strings(self, name: str) -> None:
        tpl = TEMPLATES_DIR / f"{name}.json"
        rules = json.loads(tpl.read_text(encoding="utf-8"))

        for rule in rules:
            assert isinstance(rule["capability"], str), (
                f"{name}: rule '{rule['name']}' has non-string capability"
            )
            assert rule["capability"].strip() != "", (
                f"{name}: rule '{rule['name']}' has empty capability"
            )

    @pytest.mark.parametrize("name", EXPECTED_TEMPLATES)
    def test_config_order_sequential(self, name: str) -> None:
        """Rules with 'order' in config should have unique, non-negative integer orders."""
        tpl = TEMPLATES_DIR / f"{name}.json"
        rules = json.loads(tpl.read_text(encoding="utf-8"))

        orders = [r["config"].get("order") for r in rules if "order" in r.get("config", {})]
        if not orders:
            return  # no order keys — fine

        assert all(isinstance(o, int) and o >= 0 for o in orders), (
            f"{name}: orders must be non-negative integers, got {orders}"
        )
        assert len(orders) == len(set(orders)), (
            f"{name}: duplicate order values: {orders}"
        )

    def test_all_template_files_parseable(self) -> None:
        """Every .json file in templates/ must parse without error."""
        if not TEMPLATES_DIR.exists():
            pytest.skip("templates/ directory not found")

        for tpl in TEMPLATES_DIR.glob("*.json"):
            content = tpl.read_text(encoding="utf-8")
            parsed = json.loads(content)
            assert parsed is not None, f"Null result parsing {tpl}"
