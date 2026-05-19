"""Unit tests for A11 (#237) — the virtual-dataset pipeline-prepare hook.

Zero network: every virtual dataset points at a *local* GeoParquet
fixture built at test time, so CI moves no bytes off the box.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gispulse.core.fetchers import register_core_fetchers
from gispulse.core.pipeline import (
    PipelineSpec,
    StepSpec,
    is_virtual_ref,
    prepare_virtual_input,
    prepare_virtual_inputs,
    virtual_refs,
)
from gispulse.core.plugin_model import (
    AccessProtocol,
    AccessSpec,
    Payload,
    SourceDomain,
)
from gispulse.core.sources import SOURCES, ProtocolRegistry, SourceEntryRef
from gispulse.orchestration.pipeline_executor import PipelineExecutor
from gispulse.persistence.virtual_dataset import VIRTUAL_DATASETS, make_virtual_id


# -- fixtures ----------------------------------------------------------------


@pytest.fixture(scope="module")
def geo_parquet(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A 3-point GeoParquet fixture: a real WKB ``geometry`` + ``bbox`` struct."""
    import duckdb

    path = tmp_path_factory.mktemp("pv") / "points.parquet"
    con = duckdb.connect()
    con.load_extension("spatial")
    con.execute(
        f"""
        COPY (
          SELECT id, name,
                 ST_AsWKB(ST_Point(x, y)) AS geometry,
                 {{'xmin': x, 'ymin': y, 'xmax': x, 'ymax': y}} AS bbox
          FROM (VALUES
            (1, 'alpha', 0.5, 0.5),
            (2, 'beta',  10.0, 10.0),
            (3, 'gamma', 1.5, 1.5)
          ) AS t(id, name, x, y)
        ) TO '{path}' (FORMAT PARQUET)
        """
    )
    con.close()
    return path


def _entry(endpoint: str, entry_id: str = "points") -> SourceEntryRef:
    """A REMOTE_TABLE catalogue entry over a single local parquet file."""
    return SourceEntryRef(
        id=entry_id,
        name="Test Points",
        access=AccessSpec(
            protocol=AccessProtocol.REMOTE_TABLE,
            endpoint=endpoint,
            params={"glob": "", "hive_partitioning": False},
        ),
        domain=SourceDomain.OBSERVATION,
        payload=Payload.VECTOR,
        jurisdiction="world",
        metadata={"family": "test"},
    )


@pytest.fixture
def core_protocols() -> ProtocolRegistry:
    """A registry carrying only the core worldwide fetchers."""
    reg = ProtocolRegistry()
    register_core_fetchers(reg)
    return reg


@pytest.fixture(autouse=True)
def _clean_registries():
    """Isolate the process-wide virtual-dataset / source registries."""
    VIRTUAL_DATASETS.clear()
    yield
    VIRTUAL_DATASETS.clear()
    SOURCES.clear()


class _FakeSource:
    """A minimal worldwide-style source exposing one entry."""

    name = "worldwide"

    def __init__(self, entry: SourceEntryRef) -> None:
        self._entry = entry

    def catalog(self, search: str | None = None, **_: object) -> list[SourceEntryRef]:
        return [self._entry]

    def entries(self) -> list[SourceEntryRef]:
        return [self._entry]


# -- is_virtual_ref / virtual_refs ------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("virtual:worldwide/overture-places", True),
        ("virtual:x/y", True),
        ("data/zones.gpkg", False),
        ("", False),
        (None, False),
        (123, False),
    ],
)
def test_is_virtual_ref(value: object, expected: bool) -> None:
    assert is_virtual_ref(value) is expected


def test_virtual_refs_extracts_only_virtual_subset() -> None:
    spec = PipelineSpec(
        name="mixed",
        ref_layers={
            "zones": "data/zones.gpkg",
            "ww": "virtual:worldwide/points",
        },
    )
    assert virtual_refs(spec) == {"ww": "virtual:worldwide/points"}


def test_prepare_virtual_inputs_empty_when_no_virtual_refs(
    core_protocols: ProtocolRegistry,
) -> None:
    spec = PipelineSpec(name="files", ref_layers={"zones": "data/zones.gpkg"})
    assert prepare_virtual_inputs(spec, protocols=core_protocols) == {}


# -- prepare_virtual_input ---------------------------------------------------


def test_prepare_virtual_input_resolves_registered_dataset(
    geo_parquet: Path, core_protocols: ProtocolRegistry
) -> None:
    vds = VIRTUAL_DATASETS.create(_entry(str(geo_parquet)), protocols=core_protocols)
    gdf = prepare_virtual_input(vds.id, protocols=core_protocols)
    assert len(gdf) == 3
    assert gdf.geometry.name == "geometry"
    assert not gdf.geometry.isna().any()


def test_prepare_virtual_input_pushes_bbox_down(
    geo_parquet: Path, core_protocols: ProtocolRegistry
) -> None:
    vds = VIRTUAL_DATASETS.create(_entry(str(geo_parquet)), protocols=core_protocols)
    # bbox (0,0)-(5,5) keeps 'alpha' + 'gamma', drops 'beta' at (10,10).
    gdf = prepare_virtual_input(
        vds.id, bbox=(0.0, 0.0, 5.0, 5.0), protocols=core_protocols
    )
    assert len(gdf) == 2
    assert set(gdf["name"]) == {"alpha", "gamma"}


def test_prepare_virtual_input_lazily_creates_from_source(
    geo_parquet: Path, core_protocols: ProtocolRegistry
) -> None:
    """An id absent from VIRTUAL_DATASETS is resolved via its DataSource."""
    SOURCES.register(_FakeSource(_entry(str(geo_parquet))))
    virtual_id = make_virtual_id("worldwide", "points")
    assert virtual_id not in VIRTUAL_DATASETS
    gdf = prepare_virtual_input(virtual_id, protocols=core_protocols)
    assert len(gdf) == 3
    assert virtual_id in VIRTUAL_DATASETS


def test_prepare_virtual_input_unknown_id_raises(
    core_protocols: ProtocolRegistry,
) -> None:
    SOURCES.register(_FakeSource(_entry("/tmp/x.parquet")))
    with pytest.raises(KeyError):
        prepare_virtual_input(
            "virtual:worldwide/ghost", protocols=core_protocols
        )


# -- prepare_virtual_inputs (the spec-level hook) ----------------------------


def test_prepare_virtual_inputs_resolves_spec_ref_layers(
    geo_parquet: Path, core_protocols: ProtocolRegistry
) -> None:
    vds = VIRTUAL_DATASETS.create(_entry(str(geo_parquet)), protocols=core_protocols)
    spec = PipelineSpec(
        name="hook",
        steps=[StepSpec(id="s", capability="buffer", input="ww")],
        ref_layers={"ww": vds.id, "local": "data/zones.gpkg"},
    )
    resolved = prepare_virtual_inputs(spec, protocols=core_protocols)
    assert set(resolved) == {"ww"}  # the file ref layer is left to read_vector
    assert len(resolved["ww"]) == 3


# -- acceptance: datasetSource (virtual) -> buffer produces a non-empty result


def test_virtual_dataset_feeds_buffer_pipeline(
    geo_parquet: Path, core_protocols: ProtocolRegistry
) -> None:
    """#237 acceptance — a 2-node pipeline (virtual dataset → buffer)."""
    vds = VIRTUAL_DATASETS.create(_entry(str(geo_parquet)), protocols=core_protocols)
    spec = PipelineSpec(
        name="virtual_buffer",
        steps=[StepSpec(id="buf", capability="buffer", params={"distance": 1.0}, input="ww")],
        ref_layers={"ww": vds.id},
    )
    inputs = prepare_virtual_inputs(
        spec, bbox=(0.0, 0.0, 5.0, 5.0), protocols=core_protocols
    )
    # The virtual ref layer doubles as the pipeline's primary input.
    inputs["input"] = inputs["ww"]
    results = PipelineExecutor().execute(spec, inputs)
    assert "buf" in results
    assert not results["buf"].empty
    assert len(results["buf"]) == 2
