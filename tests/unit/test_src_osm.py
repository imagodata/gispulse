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
    "gispulse_src_osm.source",
    "gispulse_src_osm",
):
    sys.modules.pop(_module, None)

from gispulse_src_osm import materialize_pbf  # noqa: E402
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
