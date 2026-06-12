"""Unit tests for the gispulse-src-osm plugin.

Zero-network: the plugin only declares Geofabrik OSM extracts. Core fetchers own
HTTP download and VSI streaming (incl. /vsizip/ for a single archive member).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_PKG = Path(__file__).resolve().parents[2] / "plugins" / "gispulse-src-osm"
_PKG_PATH = str(_PKG)
if _PKG_PATH in sys.path:
    sys.path.remove(_PKG_PATH)
sys.path.insert(0, _PKG_PATH)
for _module in ("gispulse_src_osm.pbf", "gispulse_src_osm.source", "gispulse_src_osm"):
    sys.modules.pop(_module, None)

from gispulse_src_osm import read_pbf_roads  # noqa: E402
from gispulse_src_osm.pbf import OsmPbfReadError  # noqa: E402
from gispulse_src_osm.source import OsmSource  # noqa: E402

from gispulse.core.plugin_model import AccessProtocol, Payload, SourceDomain  # noqa: E402
from gispulse.core.sources import DataSource  # noqa: E402

pytestmark = pytest.mark.usefixtures("offline_ssrf")


@pytest.fixture
def source() -> OsmSource:
    return OsmSource()


def test_pyproject_declares_osm_entrypoint_and_manifest() -> None:
    tomllib = pytest.importorskip("tomllib")
    pyproject = tomllib.loads((_PKG / "pyproject.toml").read_text())

    assert pyproject["project"]["entry-points"]["gispulse.data_sources"] == {
        "osm": "gispulse_src_osm:register"
    }
    manifest = pyproject["tool"]["gispulse"]["plugin"]
    assert manifest["kind"] == "source"
    assert manifest["domain"] == "base"
    assert manifest["jurisdiction"] == "BE"


def test_is_a_base_vector_datasource(source: OsmSource) -> None:
    assert isinstance(source, DataSource)
    assert source.name == "osm"
    assert source.domain is SourceDomain.BASE
    assert source.payload is Payload.VECTOR


def test_roads_be_entry_targets_zip_member_via_download(source: OsmSource) -> None:
    access = source.access_for("osm-roads-be")
    assert access.protocol is AccessProtocol.DOWNLOAD
    assert access.endpoint.endswith("belgium-latest-free.shp.zip")
    # le membre routes est ciblé pour une lecture /vsizip/ (pas d'extraction totale)
    assert access.params["archive_member"] == "gis_osm_roads_free_1.shp"
    assert access.params["archive_format"] == "zip"
    assert access.params["source_crs"] == "EPSG:4326"
    assert access.params["target_crs"] == "EPSG:31370"


def test_entries_exposes_roads_be(source: OsmSource) -> None:
    ids = {entry.id for entry in source.entries()}
    assert "osm-roads-be" in ids
    assert "osm-pbf-be" in ids


def test_schema_has_fclass_not_surface(source: OsmSource) -> None:
    # Le shapefile gratuit Geofabrik porte fclass (type de voie), PAS le tag surface.
    schema = source.schema("osm-roads-be")
    assert "fclass" in schema
    assert "geometry" in schema
    assert "surface" not in schema


def test_pbf_entry_exposes_full_extract_and_reader_hint(source: OsmSource) -> None:
    access = source.access_for("osm-pbf-be")

    assert access.protocol is AccessProtocol.DOWNLOAD
    assert access.endpoint.endswith("belgium-latest.osm.pbf")
    assert access.format == "application/x-osm-pbf"
    assert access.params["reader"] == "duckdb.ST_ReadOSM"
    assert source.schema("osm-pbf-be")["surface"] == "str"


def test_read_pbf_roads_builds_duckdb_query_and_flattens_tags(
    tmp_path: Path,
) -> None:
    import pandas as pd
    from shapely.geometry import LineString

    pbf = tmp_path / "belgium-latest.osm.pbf"
    pbf.write_bytes(b"fake")
    road_wkb = LineString([(4.0, 50.0), (4.1, 50.1)]).wkb

    class FakeCursor:
        def fetchdf(self) -> pd.DataFrame:
            return pd.DataFrame(
                {
                    "id": [42],
                    "highway": ["residential"],
                    "surface": ["sett"],
                    "geometry": [road_wkb],
                }
            )

    class FakeConnection:
        sql: str | None = None
        params: list[str] | None = None

        def execute(self, sql: str, params: list[str]) -> FakeCursor:
            self.sql = sql
            self.params = params
            return FakeCursor()

    conn = FakeConnection()

    gdf = read_pbf_roads(
        pbf,
        tags=("surface",),
        target_crs=None,
        connection_factory=lambda: conn,
    )

    assert len(gdf) == 1
    assert gdf.crs == "EPSG:4326"
    assert gdf.loc[0, "highway"] == "residential"
    assert gdf.loc[0, "surface"] == "sett"
    assert conn.params == [str(pbf)]
    assert conn.sql is not None
    assert "ST_ReadOSM(?)" in conn.sql
    assert "tags['surface']::VARCHAR AS \"surface\"" in conn.sql
    assert "tags['highway'] IS NOT NULL" in conn.sql


def test_read_pbf_roads_rejects_unsafe_tag_key(tmp_path: Path) -> None:
    pbf = tmp_path / "belgium-latest.osm.pbf"
    pbf.write_bytes(b"fake")

    with pytest.raises(OsmPbfReadError) as excinfo:
        read_pbf_roads(
            pbf,
            tags=("surface']; DROP TABLE osm; --",),
            connection_factory=lambda: object(),
        )

    assert excinfo.value.code == "OSM_PBF_TAG_INVALID"
