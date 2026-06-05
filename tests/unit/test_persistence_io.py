"""
Unit tests for persistence.io — format detection, read/write vector, dataset_from_file.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pytest
from pyproj import Transformer
from shapely.geometry import Point

from gispulse.persistence import io as io_mod
from gispulse.persistence.io import (
    dataset_from_file,
    detect_format,
    read_vector,
    supported_extensions,
    write_vector,
)


class TestFormatDetection:
    def test_detect_gpkg(self) -> None:
        assert detect_format("data/parcels.gpkg") == "GPKG"

    def test_detect_geojson(self) -> None:
        assert detect_format("data/points.geojson") == "GeoJSON"

    def test_detect_json_as_geojson(self) -> None:
        assert detect_format("data/features.json") == "GeoJSON"

    def test_detect_shapefile(self) -> None:
        assert detect_format("data/roads.shp") == "ESRI Shapefile"

    def test_detect_flatgeobuf(self) -> None:
        assert detect_format("data/zones.fgb") == "FlatGeobuf"

    def test_detect_parquet(self) -> None:
        assert detect_format("data/layers.parquet") == "Parquet"

    def test_detect_unknown_returns_none(self) -> None:
        result = detect_format("data/file.xyz")
        assert result is None

    def test_supported_extensions_includes_common(self) -> None:
        exts = supported_extensions()
        assert ".gpkg" in exts
        assert ".geojson" in exts
        assert ".shp" in exts

    def test_detect_kmz_as_kml(self) -> None:
        assert detect_format("data/sites.kmz") == "KML"
        assert ".kmz" in supported_extensions()


class TestReadWriteVector:
    @pytest.fixture()
    def sample_gdf(self) -> gpd.GeoDataFrame:
        return gpd.GeoDataFrame(
            {"name": ["A", "B", "C"], "value": [1, 2, 3]},
            geometry=[Point(0, 0), Point(1, 1), Point(2, 2)],
            crs="EPSG:4326",
        )

    def test_write_and_read_gpkg(self, sample_gdf: gpd.GeoDataFrame, tmp_path: Path) -> None:
        path = str(tmp_path / "test.gpkg")
        write_vector(sample_gdf, path)
        result = read_vector(path)
        assert len(result) == 3
        assert "name" in result.columns
        assert result.crs is not None

    def test_write_and_read_geojson(self, sample_gdf: gpd.GeoDataFrame, tmp_path: Path) -> None:
        path = str(tmp_path / "test.geojson")
        write_vector(sample_gdf, path)
        result = read_vector(path)
        assert len(result) == 3

    def test_write_and_read_fgb(self, sample_gdf: gpd.GeoDataFrame, tmp_path: Path) -> None:
        path = str(tmp_path / "test.fgb")
        write_vector(sample_gdf, path)
        result = read_vector(path)
        assert len(result) == 3

    def test_read_nonexistent_raises(self) -> None:
        with pytest.raises(Exception):
            read_vector("/nonexistent/file.gpkg")


class TestReadKmz:
    def test_read_kmz_doc_kml_as_epsg_4326(self, tmp_path: Path) -> None:
        path = tmp_path / "site.kmz"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr(
                "doc.kml",
                """<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <Placemark>
      <name>Orange site</name>
      <Point><coordinates>4.3517,50.8503,0</coordinates></Point>
    </Placemark>
  </Document>
</kml>
""",
            )

        result = read_vector(str(path))

        assert len(result) == 1
        assert result.crs.to_epsg() == 4326
        assert result.geometry.iloc[0].x == pytest.approx(4.3517)
        assert result.geometry.iloc[0].y == pytest.approx(50.8503)

    def test_read_kmz_uses_doc_kml_when_multiple_kml_files_exist(
        self,
        tmp_path: Path,
    ) -> None:
        path = tmp_path / "network.kmz"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr(
                "other.kml",
                """<kml xmlns="http://www.opengis.net/kml/2.2">
<Placemark><name>Other</name><Point><coordinates>5,51,0</coordinates></Point></Placemark>
</kml>""",
            )
            archive.writestr(
                "doc.kml",
                """<kml xmlns="http://www.opengis.net/kml/2.2">
<Placemark><name>Doc</name><Point><coordinates>4,50,0</coordinates></Point></Placemark>
</kml>""",
            )

        result = read_vector(str(path))

        assert result.geometry.iloc[0].x == pytest.approx(4)
        assert result.geometry.iloc[0].y == pytest.approx(50)

    def test_read_kmz_falls_back_to_first_kml_in_archive_order(
        self,
        tmp_path: Path,
    ) -> None:
        path = tmp_path / "network.kmz"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr(
                "z-first.kml",
                """<kml xmlns="http://www.opengis.net/kml/2.2">
<Placemark><name>Archive first</name><Point><coordinates>1,49,0</coordinates></Point></Placemark>
</kml>""",
            )
            archive.writestr(
                "a-second.kml",
                """<kml xmlns="http://www.opengis.net/kml/2.2">
<Placemark><name>Sorted first</name><Point><coordinates>2,50,0</coordinates></Point></Placemark>
</kml>""",
            )

        result = read_vector(str(path))

        assert result.geometry.iloc[0].x == pytest.approx(1)
        assert result.geometry.iloc[0].y == pytest.approx(49)

    def test_read_kmz_rejects_oversized_kml_member(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        path = tmp_path / "oversized.kmz"
        monkeypatch.setattr(io_mod, "KMZ_KML_MAX_BYTES", 64, raising=False)
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("doc.kml", "x" * 65)

        with pytest.raises(ValueError, match="KMZ KML member .* exceeds maximum size"):
            read_vector(str(path))

    def test_read_kmz_raises_clear_error_for_invalid_zip(self, tmp_path: Path) -> None:
        path = tmp_path / "broken.kmz"
        path.write_text("not a zip")

        with pytest.raises(ValueError, match="Invalid KMZ"):
            read_vector(str(path))

    def test_read_kmz_raises_clear_error_when_no_kml_exists(self, tmp_path: Path) -> None:
        path = tmp_path / "empty.kmz"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("readme.txt", "no kml here")

        with pytest.raises(ValueError, match="does not contain a KML"):
            read_vector(str(path))


class TestReadXlsxGeo:
    def test_read_xlsx_lambert_72_reprojects_to_epsg_4326(self, tmp_path: Path) -> None:
        path = tmp_path / "orange-sites.xlsx"
        lon, lat = 4.3517, 50.8503
        x, y = Transformer.from_crs("EPSG:4326", "EPSG:31370", always_xy=True).transform(lon, lat)
        pd.DataFrame({"name": ["Orange site"], "X": [x], "Y": [y]}).to_excel(path, index=False)

        result = read_vector(
            str(path),
            x_col="X",
            y_col="Y",
            source_crs="EPSG:31370",
        )

        assert len(result) == 1
        assert result.crs.to_epsg() == 4326
        assert result.geometry.iloc[0].x == pytest.approx(lon, abs=1e-6)
        assert result.geometry.iloc[0].y == pytest.approx(lat, abs=1e-6)

    def test_read_xlsx_rejects_projected_xy_without_source_crs(self, tmp_path: Path) -> None:
        path = tmp_path / "orange-sites.xlsx"
        pd.DataFrame({"name": ["Orange site"], "X": [149_000], "Y": [171_000]}).to_excel(
            path,
            index=False,
        )

        with pytest.raises(ValueError, match="source_crs"):
            read_vector(str(path))

    def test_read_xlsx_reports_missing_openpyxl_clearly(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        path = tmp_path / "orange-sites.xlsx"
        path.write_bytes(b"not used")

        def raise_missing_openpyxl(*args: object, **kwargs: object) -> pd.DataFrame:
            raise ImportError("Missing optional dependency 'openpyxl'")

        monkeypatch.setattr(pd, "read_excel", raise_missing_openpyxl)

        with pytest.raises(ImportError, match="requires openpyxl.*declared"):
            read_vector(str(path))


class TestDatasetFromFile:
    @pytest.fixture()
    def gpkg_file(self, tmp_path: Path) -> str:
        gdf = gpd.GeoDataFrame(
            {"id": [1, 2]},
            geometry=[Point(0, 0), Point(1, 1)],
            crs="EPSG:4326",
        )
        path = str(tmp_path / "sample.gpkg")
        gdf.to_file(path, driver="GPKG")
        return path

    def test_creates_dataset_from_gpkg(self, gpkg_file: str) -> None:
        ds = dataset_from_file(gpkg_file)
        assert ds.name is not None
        assert ds.source_path == gpkg_file
        assert ds.crs is not None
        assert ds.format is not None

    def test_nonexistent_file_raises(self) -> None:
        with pytest.raises(Exception):
            dataset_from_file("/nonexistent/file.gpkg")
