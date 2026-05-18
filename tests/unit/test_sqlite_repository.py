"""Unit tests for SQLiteRepository."""

from uuid import uuid4

import pytest

from gispulse.core.models import Dataset, Job, JobStatus, Rule, Scenario
from gispulse.persistence.sqlite_repository import SQLiteRepository


@pytest.fixture
def rule_repo(tmp_path):
    return SQLiteRepository(Rule, db_path=tmp_path / "test.db")


@pytest.fixture
def job_repo(tmp_path):
    return SQLiteRepository(Job, db_path=tmp_path / "test.db")


@pytest.fixture
def dataset_repo(tmp_path):
    return SQLiteRepository(Dataset, db_path=tmp_path / "test.db")


@pytest.fixture
def scenario_repo(tmp_path):
    return SQLiteRepository(Scenario, db_path=tmp_path / "test.db")


class TestRuleCRUD:
    def test_save_and_get(self, rule_repo):
        rule = Rule(name="buffer_50m", capability="buffer", config={"distance": 50})
        rule_repo.save(rule)

        loaded = rule_repo.get(rule.id)
        assert loaded is not None
        assert loaded.name == "buffer_50m"
        assert loaded.capability == "buffer"
        assert loaded.config == {"distance": 50}
        assert loaded.enabled is True

    def test_list_all(self, rule_repo):
        r1 = Rule(name="r1", capability="buffer")
        r2 = Rule(name="r2", capability="filter")
        rule_repo.save(r1)
        rule_repo.save(r2)

        all_rules = rule_repo.list_all()
        assert len(all_rules) == 2
        names = {r.name for r in all_rules}
        assert names == {"r1", "r2"}

    def test_delete(self, rule_repo):
        rule = Rule(name="to_delete", capability="buffer")
        rule_repo.save(rule)
        assert rule_repo.delete(rule.id) is True
        assert rule_repo.get(rule.id) is None
        assert rule_repo.delete(rule.id) is False

    def test_update_via_save(self, rule_repo):
        rule = Rule(name="original", capability="buffer")
        rule_repo.save(rule)

        rule.name = "updated"
        rule.config = {"distance": 100}
        rule_repo.save(rule)

        loaded = rule_repo.get(rule.id)
        assert loaded.name == "updated"
        assert loaded.config == {"distance": 100}

    def test_count_and_clear(self, rule_repo):
        rule_repo.save(Rule(name="r1", capability="buffer"))
        rule_repo.save(Rule(name="r2", capability="filter"))
        assert rule_repo.count() == 2

        rule_repo.clear()
        assert rule_repo.count() == 0

    def test_get_nonexistent(self, rule_repo):
        assert rule_repo.get(uuid4()) is None


class TestJobCRUD:
    def test_save_and_get_with_status(self, job_repo):
        job = Job(name="test_job", status=JobStatus.RUNNING)
        job_repo.save(job)

        loaded = job_repo.get(job.id)
        assert loaded is not None
        assert loaded.name == "test_job"
        assert loaded.status == JobStatus.RUNNING
        assert loaded.created_at is not None

    def test_result_path_and_error(self, job_repo):
        job = Job(
            name="failed_job",
            status=JobStatus.FAILED,
            result_path="/tmp/result.gpkg",
            error_message="Something went wrong",
        )
        job_repo.save(job)

        loaded = job_repo.get(job.id)
        assert loaded.result_path == "/tmp/result.gpkg"
        assert loaded.error_message == "Something went wrong"

    def test_job_parameters_json(self, job_repo):
        job = Job(
            name="param_job",
            parameters={"rule_ids": ["abc-123"], "options": {"verbose": True}},
        )
        job_repo.save(job)

        loaded = job_repo.get(job.id)
        assert loaded.parameters["rule_ids"] == ["abc-123"]
        assert loaded.parameters["options"]["verbose"] is True


class TestDatasetCRUD:
    def test_save_and_get(self, dataset_repo):
        ds = Dataset(
            name="parcels",
            source_path="/data/parcels.gpkg",
            crs="EPSG:2154",
            format="GPKG",
            metadata={"layers": ["parcels", "buildings"]},
        )
        dataset_repo.save(ds)

        loaded = dataset_repo.get(ds.id)
        assert loaded is not None
        assert loaded.name == "parcels"
        assert loaded.source_path == "/data/parcels.gpkg"
        assert loaded.crs == "EPSG:2154"
        assert loaded.metadata["layers"] == ["parcels", "buildings"]


class TestScenarioCRUD:
    def test_save_and_get(self, scenario_repo):
        job_id = uuid4()
        rule_id = uuid4()
        sc = Scenario(
            name="flood_risk",
            jobs=[job_id],
            rules=[rule_id],
            metadata={"region": "IDF"},
        )
        scenario_repo.save(sc)

        loaded = scenario_repo.get(sc.id)
        assert loaded is not None
        assert loaded.name == "flood_risk"
        assert loaded.metadata == {"region": "IDF"}


class TestIterAndLen:
    def test_iter(self, rule_repo):
        rule_repo.save(Rule(name="r1", capability="buffer"))
        rule_repo.save(Rule(name="r2", capability="filter"))
        names = {r.name for r in rule_repo}
        assert names == {"r1", "r2"}

    def test_len(self, rule_repo):
        assert len(rule_repo) == 0
        rule_repo.save(Rule(name="r1", capability="buffer"))
        assert len(rule_repo) == 1
