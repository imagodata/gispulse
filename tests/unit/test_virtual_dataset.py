"""Unit tests for A9 (#235) — ``VirtualDatasetRegistry`` and lazy views.

Zero network: the materialisation roundtrip reads a *local* GeoParquet
fixture built at test time, so CI moves no bytes off the box.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gispulse.core.plugin_model import (
    AccessProtocol,
    AccessSpec,
    FetchMode,
    Payload,
    SourceDomain,
    SourceResult,
)
from gispulse.core.sources import ProtocolRegistry, ProtocolNotSupported, SourceEntryRef
from gispulse.persistence.virtual_dataset import (
    VIRTUAL_ID_SCHEME,
    VirtualDataset,
    VirtualDatasetError,
    VirtualDatasetRegistry,
    count_features,
    make_virtual_id,
    materialize_virtual_view,
    parse_virtual_id,
    to_dataset_meta,
)


# -- fixtures ----------------------------------------------------------------


@pytest.fixture(scope="module")
def parquet_fixture(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A 3-row GeoParquet file with an Overture-style ``bbox`` struct column."""
    import duckdb

    path = tmp_path_factory.mktemp("vds") / "places.parquet"
    con = duckdb.connect()
    con.execute(
        f"""
        COPY (
          SELECT * FROM (VALUES
            (1, 'alpha', {{'xmin': 0.0,  'ymin': 0.0,  'xmax': 1.0,  'ymax': 1.0}}),
            (2, 'beta',  {{'xmin': 10.0, 'ymin': 10.0, 'xmax': 11.0, 'ymax': 11.0}}),
            (3, 'gamma', {{'xmin': 0.5,  'ymin': 0.5,  'xmax': 1.5,  'ymax': 1.5}})
          ) AS t(id, name, bbox)
        ) TO '{path}' (FORMAT PARQUET)
        """
    )
    con.close()
    return path


def _entry(endpoint: str) -> SourceEntryRef:
    """A REMOTE_TABLE catalogue entry pointing at a single local parquet."""
    return SourceEntryRef(
        id="overture-places",
        name="Overture Maps — Places",
        access=AccessSpec(
            protocol=AccessProtocol.REMOTE_TABLE,
            endpoint=endpoint,
            params={"glob": "", "hive_partitioning": False},
        ),
        domain=SourceDomain.OBSERVATION,
        payload=Payload.VECTOR,
        jurisdiction="world",
        metadata={"provider": "Overture Maps Foundation"},
    )


@pytest.fixture
def geoparquet_registry() -> ProtocolRegistry:
    """A fetcher registry with only the REMOTE_TABLE adapter."""
    from gispulse.core.fetchers.geoparquet_s3 import GeoParquetS3Fetcher

    reg = ProtocolRegistry()
    reg.register(GeoParquetS3Fetcher())
    return reg


# -- synthetic id helpers ----------------------------------------------------


def test_make_and_parse_virtual_id_roundtrip() -> None:
    vid = make_virtual_id("worldwide", "overture-places")
    assert vid == "virtual:worldwide/overture-places"
    assert vid.startswith(VIRTUAL_ID_SCHEME)
    assert parse_virtual_id(vid) == ("worldwide", "overture-places")


def test_make_virtual_id_rejects_empty() -> None:
    with pytest.raises(VirtualDatasetError):
        make_virtual_id("", "x")
    with pytest.raises(VirtualDatasetError):
        make_virtual_id("worldwide", "")


@pytest.mark.parametrize(
    "bad", ["worldwide/entry", "virtual:noslash", "virtual:/entry", "virtual:src/"]
)
def test_parse_virtual_id_rejects_malformed(bad: str) -> None:
    with pytest.raises(VirtualDatasetError):
        parse_virtual_id(bad)


# -- VirtualDataset properties ----------------------------------------------


def test_virtual_dataset_properties() -> None:
    vds = VirtualDataset(
        id="virtual:worldwide/overture-places",
        source_name="worldwide",
        entry=_entry("/tmp/x.parquet"),
    )
    assert vds.entry_id == "overture-places"
    assert vds.name == "Overture Maps — Places"
    assert vds.view_name == "v_overture_places"
    assert vds.source_uri == "worldwide://overture-places"
    assert vds.payload == "vector"
    assert vds.crs == "EPSG:4326"


def test_to_dataset_meta_shape() -> None:
    vds = VirtualDataset(
        id="virtual:worldwide/overture-places",
        source_name="worldwide",
        entry=_entry("/tmp/x.parquet"),
    )
    meta = to_dataset_meta(vds)
    assert meta["source_type"] == "virtual"
    assert meta["file_size"] == 0
    assert meta["virtual_source_uri"] == "worldwide://overture-places"
    assert meta["feature_count"] is None
    assert meta["virtual_bbox"] is None
    assert meta["data_category"] == "vector"
    assert meta["metadata"]["domain"] == "observation"
    assert meta["metadata"]["jurisdiction"] == "world"
    assert meta["metadata"]["protocol"] == "remote-table"


def test_to_dataset_meta_carries_lazy_stats() -> None:
    vds = VirtualDataset(
        id="virtual:worldwide/overture-places",
        source_name="worldwide",
        entry=_entry("/tmp/x.parquet"),
    )
    meta = to_dataset_meta(vds, feature_count=42, bbox=(0.0, 0.0, 1.0, 1.0))
    assert meta["feature_count"] == 42
    assert meta["virtual_bbox"] == [0.0, 0.0, 1.0, 1.0]


# -- registry ----------------------------------------------------------------


def test_registry_create_get_list_remove(geoparquet_registry: ProtocolRegistry) -> None:
    reg = VirtualDatasetRegistry()
    vds = reg.create(_entry("/tmp/x.parquet"), protocols=geoparquet_registry)
    assert vds.id == "virtual:worldwide/overture-places"
    assert reg.get(vds.id) is vds
    assert vds.id in reg
    assert len(reg) == 1
    assert reg.list() == [vds]
    assert reg.remove(vds.id) is True
    assert reg.remove(vds.id) is False
    assert len(reg) == 0


def test_registry_get_unknown_raises() -> None:
    with pytest.raises(KeyError):
        VirtualDatasetRegistry().get("virtual:worldwide/missing")


def test_registry_clear(geoparquet_registry: ProtocolRegistry) -> None:
    reg = VirtualDatasetRegistry()
    reg.create(_entry("/tmp/x.parquet"), protocols=geoparquet_registry)
    reg.clear()
    assert len(reg) == 0


def test_registry_create_rejects_unsupported_protocol() -> None:
    # An empty registry has no fetcher for REMOTE_TABLE.
    with pytest.raises(ProtocolNotSupported):
        VirtualDatasetRegistry().create(
            _entry("/tmp/x.parquet"), protocols=ProtocolRegistry()
        )


# -- materialisation roundtrip (local parquet, zero network) ----------------


def test_materialize_roundtrip_local_parquet(
    parquet_fixture: Path, geoparquet_registry: ProtocolRegistry
) -> None:
    from gispulse.persistence.duckdb_engine import DuckDBSession

    reg = VirtualDatasetRegistry()
    vds = reg.create(_entry(str(parquet_fixture)), protocols=geoparquet_registry)
    with DuckDBSession() as session:
        view = materialize_virtual_view(session, vds, protocols=geoparquet_registry)
        assert view == "v_overture_places"
        assert count_features(session, view) == 3


def test_materialize_bbox_pushdown_clips_rows(
    parquet_fixture: Path, geoparquet_registry: ProtocolRegistry
) -> None:
    from gispulse.persistence.duckdb_engine import DuckDBSession

    vds = VirtualDatasetRegistry().create(
        _entry(str(parquet_fixture)), protocols=geoparquet_registry
    )
    with DuckDBSession() as session:
        # bbox (0,0)-(5,5): keeps 'alpha' and 'gamma', drops 'beta' at (10,10).
        view = materialize_virtual_view(
            session, vds, bbox=(0.0, 0.0, 5.0, 5.0), protocols=geoparquet_registry
        )
        assert count_features(session, view) == 2


def test_materialize_replaces_existing_view(
    parquet_fixture: Path, geoparquet_registry: ProtocolRegistry
) -> None:
    from gispulse.persistence.duckdb_engine import DuckDBSession

    vds = VirtualDatasetRegistry().create(
        _entry(str(parquet_fixture)), protocols=geoparquet_registry
    )
    with DuckDBSession() as session:
        materialize_virtual_view(session, vds, protocols=geoparquet_registry)
        # A second call with a bbox must CREATE OR REPLACE, not error.
        materialize_virtual_view(
            session, vds, bbox=(0.0, 0.0, 5.0, 5.0), protocols=geoparquet_registry
        )
        assert count_features(session, vds.view_name) == 2


# -- materialisation error path ---------------------------------------------


class _NoScanFetcher:
    """A Fetcher that returns a REFERENCE result carrying no scan SQL."""

    protocol = AccessProtocol.REMOTE_TABLE

    def fetch(
        self,
        access: AccessSpec,
        *,
        extent: object | None = None,
        mode: FetchMode = FetchMode.MATERIALIZE,
    ) -> SourceResult:
        return SourceResult(payload=Payload.VECTOR, mode=FetchMode.REFERENCE)


def test_materialize_raises_when_fetcher_yields_no_scan() -> None:
    from gispulse.persistence.duckdb_engine import DuckDBSession

    reg = ProtocolRegistry()
    reg.register(_NoScanFetcher())
    vds = VirtualDatasetRegistry().create(_entry("/tmp/x.parquet"), protocols=reg)
    with DuckDBSession() as session:
        with pytest.raises(VirtualDatasetError, match="no .* scan"):
            materialize_virtual_view(session, vds, protocols=reg)
