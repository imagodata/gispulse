"""Tests for VectorFileFetcher (local KML/KMZ/vector files).

TDD: ces tests sont écrits AVANT l'implémentation.
Zéro réseau, zéro géodonnée client — fixtures KML synthétiques en tmp_path.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import geopandas as gpd
import pytest

from gispulse.core.fetchers.vector_file import VectorFileFetcher
from gispulse.core.plugin_model import (
    AccessProtocol,
    AccessSpec,
    FetchMode,
    Payload,
)


# ---------------------------------------------------------------------------
# Fixtures : KML/KMZ synthétiques éphémères (jamais commitées)
# ---------------------------------------------------------------------------

_KML_WITH_LINESTRING = """\
<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <Placemark>
      <name>Route A</name>
      <LineString>
        <coordinates>2.3522,48.8566,0 2.3530,48.8570,0</coordinates>
      </LineString>
    </Placemark>
    <Placemark>
      <name>Route B</name>
      <LineString>
        <coordinates>4.8357,45.7640,0 4.8360,45.7645,0</coordinates>
      </LineString>
    </Placemark>
  </Document>
</kml>
"""


@pytest.fixture()
def kml_file(tmp_path: Path) -> Path:
    """Fichier KML synthétique local avec 2 LineStrings."""
    path = tmp_path / "routes.kml"
    path.write_text(_KML_WITH_LINESTRING, encoding="utf-8")
    return path


@pytest.fixture()
def kmz_file(tmp_path: Path) -> Path:
    """Fichier KMZ synthétique (doc.kml zippé) avec 2 LineStrings."""
    path = tmp_path / "routes.kmz"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("doc.kml", _KML_WITH_LINESTRING)
    return path


def _access(endpoint: str, **params: object) -> AccessSpec:
    return AccessSpec(
        protocol=AccessProtocol.LOCAL_FILE,
        endpoint=endpoint,
        params=params,
    )


# ---------------------------------------------------------------------------
# Contrat déclaratif
# ---------------------------------------------------------------------------


def test_protocol_is_local_file() -> None:
    assert VectorFileFetcher.protocol is AccessProtocol.LOCAL_FILE


def test_payload_is_vector() -> None:
    assert VectorFileFetcher.payload is Payload.VECTOR


# ---------------------------------------------------------------------------
# Matérialisation KML
# ---------------------------------------------------------------------------


def test_materialize_kml_returns_geodataframe(kml_file: Path) -> None:
    result = VectorFileFetcher().fetch(
        _access(str(kml_file)),
        mode=FetchMode.MATERIALIZE,
    )
    assert result.mode is FetchMode.MATERIALIZE
    assert isinstance(result.data, gpd.GeoDataFrame)


def test_materialize_kml_has_expected_row_count(kml_file: Path) -> None:
    result = VectorFileFetcher().fetch(
        _access(str(kml_file)),
        mode=FetchMode.MATERIALIZE,
    )
    assert len(result.data) == 2


def test_materialize_kml_has_geometry_column(kml_file: Path) -> None:
    result = VectorFileFetcher().fetch(
        _access(str(kml_file)),
        mode=FetchMode.MATERIALIZE,
    )
    assert "geometry" in result.data.columns
    assert result.data.geometry.notna().all()


# ---------------------------------------------------------------------------
# Matérialisation KMZ
# ---------------------------------------------------------------------------


def test_materialize_kmz_returns_geodataframe(kmz_file: Path) -> None:
    result = VectorFileFetcher().fetch(
        _access(str(kmz_file)),
        mode=FetchMode.MATERIALIZE,
    )
    assert result.mode is FetchMode.MATERIALIZE
    assert isinstance(result.data, gpd.GeoDataFrame)


def test_materialize_kmz_has_expected_row_count(kmz_file: Path) -> None:
    result = VectorFileFetcher().fetch(
        _access(str(kmz_file)),
        mode=FetchMode.MATERIALIZE,
    )
    assert len(result.data) == 2


def test_materialize_kmz_has_geometry_column(kmz_file: Path) -> None:
    result = VectorFileFetcher().fetch(
        _access(str(kmz_file)),
        mode=FetchMode.MATERIALIZE,
    )
    assert "geometry" in result.data.columns
    assert result.data.geometry.notna().all()


def test_materialize_kmz_sets_source_result_crs(kmz_file: Path) -> None:
    # KMZ/KML are normalised to EPSG:4326 by read_vector; SourceResult.crs
    # must reflect the GeoDataFrame CRS (not stay None).
    result = VectorFileFetcher().fetch(
        _access(str(kmz_file)),
        mode=FetchMode.MATERIALIZE,
    )
    assert result.crs is not None
    assert result.crs == result.data.crs.to_string()
    assert result.data.crs.to_epsg() == 4326


# ---------------------------------------------------------------------------
# Erreurs — fast-fail
# ---------------------------------------------------------------------------


def test_fetch_raises_file_not_found_on_missing_path() -> None:
    with pytest.raises(FileNotFoundError):
        VectorFileFetcher().fetch(
            _access("/nonexistent/routes.kml"),
            mode=FetchMode.MATERIALIZE,
        )


def test_fetch_raises_value_error_on_unsupported_extension(tmp_path: Path) -> None:
    path = tmp_path / "data.xyz"
    path.write_text("dummy", encoding="utf-8")
    with pytest.raises(ValueError):
        VectorFileFetcher().fetch(
            _access(str(path)),
            mode=FetchMode.MATERIALIZE,
        )


# ---------------------------------------------------------------------------
# Référence lazy — non supporté (NotImplementedError explicite)
# ---------------------------------------------------------------------------


def test_reference_scan_raises_not_implemented(kml_file: Path) -> None:
    with pytest.raises(NotImplementedError):
        VectorFileFetcher().fetch(
            _access(str(kml_file)),
            mode=FetchMode.REFERENCE,
        )
