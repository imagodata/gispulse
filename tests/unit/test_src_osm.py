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
for _module in (
    "gispulse_src_osm.materialize",
    "gispulse_src_osm.pbf",
    "gispulse_src_osm.source",
    "gispulse_src_osm",
):
    sys.modules.pop(_module, None)

from gispulse_src_osm import materialize_pbf, read_pbf_roads  # noqa: E402
from gispulse_src_osm.pbf import OsmPbfReadError  # noqa: E402
from gispulse_src_osm.source import OsmSource  # noqa: E402

from gispulse.core.plugin_model import (  # noqa: E402
    AccessProtocol,
    FetchMode,
    Payload,
    SourceDomain,
    SourceResult,
)
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


def test_materialize_pbf_delegates_declared_pbf_access_to_download_fetcher(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source: OsmSource,
) -> None:
    dest = tmp_path / "raw" / "belgium.osm.pbf"
    calls = []

    def fake_fetch(self, access, *, mode):
        calls.append((self, access, mode))
        return SourceResult(payload=Payload.VECTOR, mode=mode, data=str(dest))

    monkeypatch.setattr("gispulse_src_osm.materialize.HttpFileFetcher.fetch", fake_fetch)

    result = materialize_pbf(dest)

    assert result == dest
    assert dest.parent.is_dir()
    assert len(calls) == 1
    _fetcher, access, mode = calls[0]
    declared = source.access_for("osm-pbf-be")
    assert mode is FetchMode.MATERIALIZE
    assert access.protocol is AccessProtocol.DOWNLOAD
    assert access.endpoint == declared.endpoint
    assert access.params["local_path"] == str(dest)
    assert "local_path" not in declared.params


def test_materialize_pbf_skips_existing_file_without_force(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dest = tmp_path / "belgium.osm.pbf"
    dest.write_bytes(b"already here")

    def fail_fetch(self, access, *, mode):
        pytest.fail("materialize_pbf should not fetch when dest exists")

    monkeypatch.setattr("gispulse_src_osm.materialize.HttpFileFetcher.fetch", fail_fetch)

    assert materialize_pbf(dest) == dest


def test_materialize_pbf_force_fetches_existing_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dest = tmp_path / "belgium.osm.pbf"
    dest.write_bytes(b"stale")
    calls = []

    def fake_fetch(self, access, *, mode):
        calls.append((access, mode))
        return SourceResult(payload=Payload.VECTOR, mode=mode, data=str(dest))

    monkeypatch.setattr("gispulse_src_osm.materialize.HttpFileFetcher.fetch", fake_fetch)

    assert materialize_pbf(dest, force=True) == dest
    assert len(calls) == 1
    access, mode = calls[0]
    assert mode is FetchMode.MATERIALIZE
    assert access.params["local_path"] == str(dest)


# --- read_pbf_roads: real DuckDB ST_ReadOSM on a synthetic PBF -------------------------

# Brussels-ish nodes (lon, lat); ids deliberately not in way order.
_NODES = {
    5: (4.3500, 50.8500),
    1: (4.3600, 50.8500),
    9: (4.3600, 50.8600),
    7: (4.3700, 50.8600),
}


@pytest.fixture
def duckdb_spatial() -> None:
    duckdb = pytest.importorskip("duckdb")
    conn = duckdb.connect(":memory:")
    try:
        conn.execute("INSTALL spatial; LOAD spatial")
    except duckdb.Error as exc:  # offline CI without a cached extension
        pytest.skip(f"DuckDB spatial extension unavailable: {exc}")
    finally:
        conn.close()


@pytest.fixture
def synthetic_pbf(tmp_path: Path, duckdb_spatial: None) -> Path:
    from tests.unit._synthetic_pbf import write_pbf

    return write_pbf(
        tmp_path / "synthetic.osm.pbf",
        _NODES,
        [  # ways deliberately not written in id order
            (104, [404, 405], {"highway": "path"}),  # 0 resolved node
            (102, [5, 404, 1], {"highway": "service", "surface": "asphalt"}),  # 1 missing node
            # node order is the way order (9, 5, 1, 7), not id order
            (100, [9, 5, 1, 7], {"highway": "residential", "surface": "sett", "name": "A"}),
            (106, [5, 404, 5], {"highway": "service"}),  # resolved nodes all at one spot
            (101, [5, 1], {"highway": "motorway"}),  # no surface tag -> NA
            (103, [5, 404], {"highway": "track", "surface": "gravel"}),  # 1 resolved node
            (105, [5, 1, 9], {"building": "yes"}),  # not a road
        ],
    )


def test_read_pbf_roads_rebuilds_way_geometry_in_node_order(synthetic_pbf: Path) -> None:
    gdf = read_pbf_roads(synthetic_pbf, tags=("surface",), target_crs=None)

    way = gdf.set_index("id").loc[100]
    assert list(way.geometry.coords) == [
        pytest.approx((4.36, 50.86)),
        pytest.approx((4.35, 50.85)),
        pytest.approx((4.36, 50.85)),
        pytest.approx((4.37, 50.86)),
    ]
    assert gdf.crs == "EPSG:4326"
    assert set(gdf.geom_type) == {"LineString"}


def test_read_pbf_roads_column_contract_and_tag_filter(synthetic_pbf: Path) -> None:
    gdf = read_pbf_roads(synthetic_pbf, tags=("surface",), target_crs=None)

    # contract consumed by MILOU: id, surface, LineString geometry (+ highway filter key)
    assert list(gdf.columns) == ["id", "highway", "surface", "geometry"]
    by_id = gdf.set_index("id")
    assert by_id.loc[100, "surface"] == "sett"
    assert by_id.loc[100, "highway"] == "residential"
    assert by_id.loc[102, "surface"] == "asphalt"  # tags stay aligned with their way
    assert list(gdf["id"]) == [100, 101, 102]  # sorted by id
    assert str(gdf["surface"].dtype) == "string"
    assert by_id["surface"].isna()[101]  # tag absent -> missing, not dropped
    assert 105 not in by_id.index  # non-highway way filtered out
    assert "name" not in gdf.columns  # only requested tags are flattened


def test_read_pbf_roads_default_tags_include_highway_surface_footway(
    synthetic_pbf: Path,
) -> None:
    gdf = read_pbf_roads(synthetic_pbf, target_crs=None)

    assert list(gdf.columns) == ["id", "highway", "surface", "footway", "geometry"]


def test_read_pbf_roads_counts_incomplete_ways_in_diagnostics(
    synthetic_pbf: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level("WARNING"):
        gdf = read_pbf_roads(synthetic_pbf, tags=("surface",), target_crs=None)

    # 103 (1 node), 104 (0 node) and 106 (zero-length) dropped
    assert sorted(gdf["id"]) == [100, 101, 102]
    assert gdf.attrs["diagnostics"] == {
        "ways_total": 6,
        "ways_kept": 3,
        "ways_dropped_incomplete": 2,
        "ways_dropped_degenerate": 1,
        "ways_partially_resolved": 1,  # way 102 kept on 2 of its 3 nodes
    }
    assert gdf.attrs["partially_resolved_ids"] == [102]
    assert gdf.geometry.is_valid.all()
    assert "3/6 highway ways dropped" in caplog.text


def test_read_pbf_roads_projection_is_lon_lat_not_lat_lon(synthetic_pbf: Path) -> None:
    """Without always_xy, EPSG:4326 is read as (lat, lon): the layer lands at ~(6.5e6, -3.4e6)."""
    gdf = read_pbf_roads(synthetic_pbf, tags=("surface",), target_crs="EPSG:31370")

    assert gdf.crs == "EPSG:31370"
    minx, miny, maxx, maxy = gdf.total_bounds
    # Brussels in Belgian Lambert 72 is about (149 000, 170 000)
    assert 140_000 < minx < maxx < 160_000
    assert 160_000 < miny < maxy < 180_000
    # same answer as an always_xy reprojection done by geopandas (metre tolerance: pyproj
    # may pick a grid-based BD72 transformation that DuckDB's embedded PROJ lacks)
    expected = read_pbf_roads(synthetic_pbf, tags=("surface",), target_crs=None).to_crs(31370)
    assert gdf.total_bounds == pytest.approx(expected.total_bounds, abs=5.0)


def test_read_pbf_roads_target_crs_none_keeps_wgs84_lon_lat(synthetic_pbf: Path) -> None:
    gdf = read_pbf_roads(synthetic_pbf, target_crs=None)

    assert gdf.crs == "EPSG:4326"
    assert gdf.total_bounds == pytest.approx([4.35, 50.85, 4.37, 50.86])


def test_read_pbf_roads_empty_when_no_highway(tmp_path: Path, duckdb_spatial: None) -> None:
    from tests.unit._synthetic_pbf import write_pbf

    pbf = write_pbf(tmp_path / "none.osm.pbf", _NODES, [(1, [5, 1], {"building": "yes"})])

    gdf = read_pbf_roads(pbf, tags=("surface",), target_crs="EPSG:31370")

    assert len(gdf) == 0
    assert list(gdf.columns) == ["id", "highway", "surface", "geometry"]
    assert str(gdf["surface"].dtype) == "string"
    assert gdf.crs == "EPSG:31370"
    assert gdf.attrs["diagnostics"]["ways_total"] == 0


def test_read_pbf_roads_accepts_non_string_target_crs(synthetic_pbf: Path) -> None:
    gdf = read_pbf_roads(synthetic_pbf, tags=("surface",), target_crs=31370)  # type: ignore[arg-type]

    assert gdf.crs == "EPSG:31370"
    assert 140_000 < gdf.total_bounds[0] < 160_000


def test_read_pbf_roads_tag_keys_cannot_collide_with_internal_columns(
    tmp_path: Path, duckdb_spatial: None
) -> None:
    from tests.unit._synthetic_pbf import write_pbf

    pbf = write_pbf(
        tmp_path / "refs.osm.pbf",
        _NODES,
        [(1, [5, 1], {"highway": "residential", "refs": "N1", "pts": "x"})],
    )

    gdf = read_pbf_roads(pbf, tags=("refs", "pts"), target_crs=None)

    assert list(gdf.columns) == ["id", "highway", "refs", "pts", "geometry"]
    assert (gdf.loc[0, "refs"], gdf.loc[0, "pts"]) == ("N1", "x")


@pytest.mark.parametrize("key", ["id", "geometry"])
def test_read_pbf_roads_rejects_tag_keys_colliding_with_output(tmp_path: Path, key: str) -> None:
    pbf = tmp_path / "x.osm.pbf"
    pbf.write_bytes(b"fake")

    with pytest.raises(OsmPbfReadError) as excinfo:
        read_pbf_roads(pbf, tags=(key,), connection_factory=lambda: object())

    assert excinfo.value.code == "OSM_PBF_TAG_INVALID"


def test_read_pbf_roads_leaves_caller_connection_clean(synthetic_pbf: Path) -> None:
    import duckdb

    conn = duckdb.connect(":memory:")
    conn.execute("LOAD spatial")
    conn.execute("SET disabled_optimizers = 'filter_pushdown'")

    gdf = read_pbf_roads(synthetic_pbf, target_crs=None, connection_factory=lambda: conn)

    assert len(gdf) == 3
    setting = conn.execute("SELECT current_setting('disabled_optimizers')").fetchone()[0]
    assert setting == "filter_pushdown"
    leftovers = conn.execute(
        "SELECT count(*) FROM duckdb_tables() WHERE table_name LIKE '_gp_pbf_%'"
    ).fetchone()[0]
    assert leftovers == 0
    conn.close()


def test_read_pbf_roads_keeps_node_order_on_a_multi_threaded_join(
    tmp_path: Path, duckdb_spatial: None
) -> None:
    """Enough refs for several DuckDB row groups, so join output order is not way order."""
    import random

    import numpy as np

    from tests.unit._synthetic_pbf import write_pbf

    rng = random.Random(42)
    n_nodes, n_ways, per_way = 60_000, 20_000, 10
    nodes = {i: (4.0 + i * 1e-5, 50.0 + (i % 997) * 1e-5) for i in range(1, n_nodes + 1)}
    ways = [
        (w, rng.sample(range(1, n_nodes + 1), per_way), {"highway": "residential"})
        for w in range(1, n_ways + 1)
    ]
    pbf = write_pbf(tmp_path / "big.osm.pbf", nodes, ways)

    gdf = read_pbf_roads(pbf, tags=(), target_crs=None)

    assert len(gdf) == n_ways
    expected = {w: [nodes[r] for r in refs] for w, refs, _ in ways}
    wrong = [
        way_id
        for way_id, geom in zip(gdf["id"], gdf.geometry, strict=True)
        if not np.allclose(np.asarray(geom.coords), expected[way_id], rtol=0, atol=1e-7)
    ]
    assert wrong == []


@pytest.mark.parametrize("duplicate", ["way", "node"])
def test_read_pbf_roads_rejects_duplicate_ids_and_cleans_up(
    tmp_path: Path, duckdb_spatial: None, duplicate: str
) -> None:
    import duckdb

    from tests.unit._synthetic_pbf import write_pbf

    road = (1, [5, 1], {"highway": "residential"})
    if duplicate == "way":
        pbf = write_pbf(tmp_path / "dup.osm.pbf", _NODES, [road, road])
    else:  # same node id at another position, as in a naive merge of extracts
        pbf = write_pbf(
            tmp_path / "dup.osm.pbf", _NODES, [road], extra_node_blocks=[{5: (5.0, 51.0)}]
        )
    conn = duckdb.connect(":memory:")
    conn.execute("LOAD spatial")

    with pytest.raises(OsmPbfReadError) as excinfo:
        read_pbf_roads(pbf, target_crs=None, connection_factory=lambda: conn)

    assert excinfo.value.code == "OSM_PBF_DUPLICATE_IDS"
    leftovers = conn.execute(
        "SELECT count(*) FROM duckdb_tables() WHERE table_name LIKE '_gp_pbf_%'"
    ).fetchone()[0]
    assert leftovers == 0
    conn.close()


def test_read_pbf_roads_ignores_caller_default_order_and_tables(synthetic_pbf: Path) -> None:
    import duckdb

    conn = duckdb.connect(":memory:")
    conn.execute("LOAD spatial")
    conn.execute("SET default_order = 'DESC'")
    conn.execute("CREATE TABLE _gp_pbf_ways AS SELECT 'mine' AS owner")

    gdf = read_pbf_roads(synthetic_pbf, target_crs=None, connection_factory=lambda: conn)

    assert list(gdf["id"]) == [100, 101, 102]
    assert gdf.set_index("id").loc[100].geometry.coords[0] == pytest.approx((4.36, 50.86))
    assert conn.execute("SELECT owner FROM main._gp_pbf_ways").fetchall() == [("mine",)]
    conn.close()


def test_read_pbf_roads_reports_out_of_memory(synthetic_pbf: Path) -> None:
    import duckdb

    conn = duckdb.connect(":memory:")
    conn.execute("LOAD spatial")
    conn.execute("SET memory_limit = '1MB'")

    with pytest.raises(OsmPbfReadError) as excinfo:
        read_pbf_roads(synthetic_pbf, target_crs=None, connection_factory=lambda: conn)

    assert excinfo.value.code == "OSM_PBF_OUT_OF_MEMORY"
    conn.close()


def test_read_pbf_roads_rejects_unknown_target_crs(tmp_path: Path) -> None:
    pbf = tmp_path / "x.osm.pbf"
    pbf.write_bytes(b"fake")

    with pytest.raises(OsmPbfReadError) as excinfo:
        read_pbf_roads(pbf, target_crs="EPSG:not-a-crs", connection_factory=lambda: object())

    assert excinfo.value.code == "OSM_PBF_CRS_INVALID"


def test_read_pbf_roads_wraps_unreadable_pbf(tmp_path: Path, duckdb_spatial: None) -> None:
    pbf = tmp_path / "garbage.osm.pbf"
    pbf.write_bytes(b"not a pbf")

    with pytest.raises(OsmPbfReadError) as excinfo:
        read_pbf_roads(pbf)

    assert excinfo.value.code == "OSM_PBF_READ_FAILED"


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
