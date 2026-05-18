"""Unit tests for GISPulse core domain models."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID


from gispulse.core.models import (
    Artifact,
    DataCategory,
    Dataset,
    Job,
    JobStatus,
    Layer,
    MessageStatus,
    ProcessingMode,
    Rule,
    Scenario,
    Trigger,
    TriggerEvent,
)


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class TestDataCategory:
    def test_values(self):
        assert DataCategory.VECTOR.value == "vector"
        assert DataCategory.RASTER.value == "raster"
        assert DataCategory.POINT_CLOUD.value == "point_cloud"
        assert DataCategory.MESH_3D.value == "mesh_3d"
        assert DataCategory.NETWORK.value == "network"
        assert DataCategory.TABULAR_GEO.value == "tabular_geo"
        assert DataCategory.SPATIO_TEMPORAL.value == "spatio_temporal"


class TestProcessingMode:
    def test_values(self):
        assert ProcessingMode.SYNC.value == "SYNC"
        assert ProcessingMode.ASYNC.value == "ASYNC"
        assert ProcessingMode.HYBRID.value == "HYBRID"


class TestMessageStatus:
    def test_values(self):
        assert MessageStatus.NEW.value == "NEW"
        assert MessageStatus.PENDING.value == "PENDING"
        assert MessageStatus.PROCESSING.value == "PROCESSING"
        assert MessageStatus.COMPLETED.value == "COMPLETED"
        assert MessageStatus.FAILED.value == "FAILED"
        assert MessageStatus.DLQ.value == "DLQ"


# ---------------------------------------------------------------------------
# Dataset
# ---------------------------------------------------------------------------


class TestDataset:
    def test_defaults(self):
        ds = Dataset()
        assert isinstance(ds.id, UUID)
        assert ds.name == ""
        assert ds.source_path is None
        assert ds.metadata == {}
        assert isinstance(ds.created_at, datetime)
        # Phase 1 additions
        assert ds.data_category == "vector"
        assert ds.crs == "EPSG:4326"
        assert ds.format is None

    def test_custom_values(self):
        ds = Dataset(
            name="test_dataset",
            source_path="/data/test.gpkg",
            data_category="raster",
            crs="EPSG:2154",
            format="GeoTIFF",
        )
        assert ds.name == "test_dataset"
        assert ds.source_path == "/data/test.gpkg"
        assert ds.data_category == "raster"
        assert ds.crs == "EPSG:2154"
        assert ds.format == "GeoTIFF"

    def test_unique_ids(self):
        ds1 = Dataset()
        ds2 = Dataset()
        assert ds1.id != ds2.id


# ---------------------------------------------------------------------------
# Layer
# ---------------------------------------------------------------------------


class TestLayer:
    def test_defaults(self):
        layer = Layer()
        assert isinstance(layer.id, UUID)
        assert layer.dataset_id is None
        assert layer.name == ""
        assert layer.geometry_type is None
        assert layer.srid == 4326
        assert layer.metadata == {}
        # Phase 1 additions
        assert layer.layer_type == "vector"
        assert layer.has_z is False
        assert layer.has_m is False
        assert layer.feature_count is None

    def test_custom_values(self):
        layer = Layer(
            name="parcels",
            geometry_type="Polygon",
            srid=2154,
            layer_type="vector",
            has_z=True,
            has_m=False,
            feature_count=1500,
        )
        assert layer.name == "parcels"
        assert layer.geometry_type == "Polygon"
        assert layer.srid == 2154
        assert layer.has_z is True
        assert layer.feature_count == 1500


# ---------------------------------------------------------------------------
# Job
# ---------------------------------------------------------------------------


class TestJob:
    def test_defaults(self):
        job = Job()
        assert isinstance(job.id, UUID)
        assert job.status == JobStatus.PENDING
        assert job.dataset_id is None
        assert job.parameters == {}
        assert job.started_at is None
        assert job.completed_at is None

    def test_status_transitions(self):
        job = Job()
        assert job.status == JobStatus.PENDING
        job.status = JobStatus.RUNNING
        assert job.status == JobStatus.RUNNING
        job.status = JobStatus.COMPLETED
        assert job.status == JobStatus.COMPLETED


# ---------------------------------------------------------------------------
# Artifact
# ---------------------------------------------------------------------------


class TestArtifact:
    def test_defaults(self):
        artifact = Artifact()
        assert isinstance(artifact.id, UUID)
        assert artifact.job_id is None
        assert artifact.name == ""
        assert artifact.artifact_type == ""
        assert artifact.path is None
        assert artifact.metadata == {}
        assert isinstance(artifact.created_at, datetime)


# ---------------------------------------------------------------------------
# Rule
# ---------------------------------------------------------------------------


class TestRule:
    def test_defaults(self):
        rule = Rule()
        assert isinstance(rule.id, UUID)
        assert rule.enabled is True
        assert rule.config == {}
        assert rule.scope == "global"

    def test_custom_rule(self):
        rule = Rule(
            name="buffer_rule",
            capability="buffer",
            config={"distance": 100.0, "order": 1},
        )
        assert rule.capability == "buffer"
        assert rule.config["distance"] == 100.0
        assert rule.config["order"] == 1


# ---------------------------------------------------------------------------
# Trigger
# ---------------------------------------------------------------------------


class TestTrigger:
    def test_defaults(self):
        trigger = Trigger()
        assert isinstance(trigger.id, UUID)
        assert trigger.event == TriggerEvent.MANUAL
        assert trigger.enabled is True
        assert trigger.conditions == {}

    def test_event_values(self):
        assert TriggerEvent.DATA_CHANGED.value == "data_changed"
        assert TriggerEvent.GEOMETRY_CHANGED.value == "geometry_changed"
        assert TriggerEvent.STATUS_CHANGED.value == "status_changed"
        assert TriggerEvent.MANUAL.value == "manual"


# ---------------------------------------------------------------------------
# Scenario
# ---------------------------------------------------------------------------


class TestScenario:
    def test_defaults(self):
        scenario = Scenario()
        assert isinstance(scenario.id, UUID)
        assert scenario.name == ""
        assert scenario.dataset_id is None
        assert scenario.jobs == []
        assert scenario.rules == []
        assert scenario.metadata == {}
        assert isinstance(scenario.created_at, datetime)
        # Phase 1 additions
        assert scenario.locked_by is None
        assert scenario.locked_at is None
        assert scenario.version == 1
        assert scenario.graph == {}

    def test_locking(self):
        scenario = Scenario(name="design_v1")
        now = datetime.now(timezone.utc)
        scenario.locked_by = "user@example.com"
        scenario.locked_at = now
        assert scenario.locked_by == "user@example.com"
        assert scenario.locked_at == now

    def test_graph_field(self):
        graph = {"nodes": [{"id": "n1", "type": "buffer"}], "edges": []}
        scenario = Scenario(graph=graph)
        assert scenario.graph["nodes"][0]["id"] == "n1"

    def test_version_increment(self):
        scenario = Scenario()
        assert scenario.version == 1
        scenario.version += 1
        assert scenario.version == 2
