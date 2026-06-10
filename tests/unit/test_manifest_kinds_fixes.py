"""Tests TDD — fixes F1/F2/F3/F4 (review Codex issue #453).

F1 (P1): runner.py — resume avec liste steps vide = None = re-exécution totale
F2 (P2): manifest_runner.py — execution_order pollué par external exclu du filtre
F3 (P2): manifest_v3.py — kind non-string / string vide silencieusement normalisé
F4 (P3): manifest_runner.py — pré-check loader masque « produces no data output »
"""

from __future__ import annotations

import sys

import pytest


# ---------------------------------------------------------------------------
# F3 — _split_transform : kind doit être une string non-vide
# ---------------------------------------------------------------------------


class TestSplitTransformKindValidation:
    """F3 : kind non-string → ValueError ; string vide → ValueError."""

    def test_kind_int_raises_value_error(self):
        """kind: 123 (int) doit lever ValueError, pas normaliser en '123'."""
        from gispulse.core.manifest_v3 import _split_transform

        with pytest.raises(ValueError, match="kind.*string|string.*kind"):
            _split_transform({"step": {"kind": 123, "argv": ["run.py"]}})

    def test_kind_empty_string_raises_value_error(self):
        """kind: '' (string vide) doit lever ValueError, pas silencieusement→capability."""
        from gispulse.core.manifest_v3 import _split_transform

        with pytest.raises(ValueError, match="kind.*empty|empty.*kind|kind.*non.empty"):
            _split_transform({"step": {"kind": "", "argv": ["run.py"]}})

    def test_kind_none_absent_is_capability(self):
        """Absence de kind (pas de clé) → capability (comportement existant inchangé)."""
        from gispulse.core.manifest_v3 import _split_transform

        _, params, effective_kind = _split_transform({"filter": {"expression": "val > 0"}})
        assert effective_kind == "capability"

    def test_kind_none_key_absent_is_capability(self):
        """kind: null (clé absente) → capability."""
        from gispulse.core.manifest_v3 import _split_transform

        _, params, effective_kind = _split_transform({"filter": {}})
        assert effective_kind == "capability"

    def test_kind_float_raises_value_error(self):
        """kind: 1.5 (float) doit lever ValueError."""
        from gispulse.core.manifest_v3 import _split_transform

        with pytest.raises(ValueError, match="kind.*string|string.*kind"):
            _split_transform({"step": {"kind": 1.5, "argv": ["run.py"]}})

    def test_kind_list_raises_value_error(self):
        """kind: ['x'] (list) doit lever ValueError."""
        from gispulse.core.manifest_v3 import _split_transform

        with pytest.raises(ValueError, match="kind.*string|string.*kind"):
            _split_transform({"step": {"kind": ["external"], "argv": ["run.py"]}})

    def test_kind_external_string_ok(self):
        """kind: 'external' (string non-vide) → effective_kind='external'."""
        from gispulse.core.manifest_v3 import _split_transform

        _, params, effective_kind = _split_transform(
            {"shell": {"kind": "external", "argv": ["run.py"]}}
        )
        assert effective_kind == "external"

    def test_validate_manifest_rejects_kind_int(self):
        """validate_manifest remonte l'erreur kind non-string en ManifestValidationError."""
        from gispulse.core.manifest_v3 import (
            ManifestV3,
            ModelSpec,
            ManifestValidationError,
            validate_manifest,
        )

        manifest = ManifestV3(
            sources={},
            models={
                "bad": ModelSpec(
                    name="bad",
                    select=None,
                    transform=[{"shell": {"kind": 42, "argv": ["run.py"]}}],
                )
            },
        )
        with pytest.raises((ManifestValidationError, ValueError), match="kind"):
            validate_manifest(manifest)


# ---------------------------------------------------------------------------
# F2 — execution_order ne doit pas contenir les external exclus du filtre
# ---------------------------------------------------------------------------


class TestExecutionOrderFilterExternal:
    """F2 : modèle external exclu du steps_filter ne doit pas apparaître dans execution_order."""

    def _make_two_external_manifest(self):
        from gispulse.core.manifest_v3 import ManifestV3, ModelSpec

        return ManifestV3(
            sources={},
            models={
                "a": ModelSpec(
                    name="a",
                    select=None,
                    transform=[
                        {
                            "shell_a": {
                                "kind": "external",
                                "argv": [sys.executable, "-c", "print('a')"],
                            }
                        }
                    ],
                ),
                "b": ModelSpec(
                    name="b",
                    select=None,
                    transform=[
                        {
                            "shell_b": {
                                "kind": "external",
                                "argv": [sys.executable, "-c", "print('b')"],
                            }
                        }
                    ],
                ),
            },
        )

    def test_filter_a_only_execution_order(self):
        """steps_filter=['a'] → execution_order=['a'] uniquement, 'b' absent."""
        from gispulse.runtime.manifest_runner import run_manifest

        manifest = self._make_two_external_manifest()
        result = run_manifest(manifest, steps_filter=["a"])
        assert result.execution_order == ["a"], (
            f"Expected ['a'] but got {result.execution_order!r} — "
            "excluded external model 'b' should not appear"
        )

    def test_filter_b_only_execution_order(self):
        """steps_filter=['b'] → execution_order=['b'] uniquement, 'a' absent."""
        from gispulse.runtime.manifest_runner import run_manifest

        manifest = self._make_two_external_manifest()
        result = run_manifest(manifest, steps_filter=["b"])
        assert result.execution_order == ["b"], (
            f"Expected ['b'] but got {result.execution_order!r}"
        )

    def test_filter_none_runs_both(self):
        """Pas de filtre → les deux modèles s'exécutent (régression: ne pas briser)."""
        from gispulse.runtime.manifest_runner import run_manifest

        manifest = self._make_two_external_manifest()
        result = run_manifest(manifest)
        assert set(result.execution_order) == {"a", "b"}

    def test_excluded_external_does_not_run_subprocess(self, tmp_path):
        """Un external exclu du filtre ne doit PAS lancer de subprocess (marqueur fichier)."""
        from gispulse.runtime.manifest_runner import run_manifest
        from gispulse.core.manifest_v3 import ManifestV3, ModelSpec

        marker = tmp_path / "ran.txt"
        # Le script crée le fichier s'il tourne
        script = f"open({str(marker)!r}, 'w').write('ran')"

        manifest = ManifestV3(
            sources={},
            models={
                "a": ModelSpec(
                    name="a",
                    select=None,
                    transform=[
                        {
                            "shell_a": {
                                "kind": "external",
                                "argv": [sys.executable, "-c", "print('a')"],
                            }
                        }
                    ],
                ),
                "should_not_run": ModelSpec(
                    name="should_not_run",
                    select=None,
                    transform=[
                        {
                            "shell_marker": {
                                "kind": "external",
                                "argv": [sys.executable, "-c", script],
                            }
                        }
                    ],
                ),
            },
        )
        run_manifest(manifest, steps_filter=["a"])
        assert not marker.exists(), (
            "should_not_run subprocess was executed despite being excluded from steps_filter"
        )


# ---------------------------------------------------------------------------
# F4 — pré-check loader : erreur claire « produces no data » > « requires engine »
# ---------------------------------------------------------------------------


class TestNoDataDownstreamErrorMessage:
    """F4 : ext(select=None) + downstream(select='ext') sans source_loader → erreur claire."""

    def test_downstream_no_data_error_is_clear(self):
        """L'erreur doit mentionner 'no data' ou 'non-capability', pas 'requires engine'."""
        from gispulse.core.manifest_v3 import ManifestV3, ModelSpec
        from gispulse.runtime.manifest_runner import run_manifest

        manifest = ManifestV3(
            sources={},  # pas de source déclarée
            models={
                "ext": ModelSpec(
                    name="ext",
                    select=None,
                    transform=[
                        {
                            "shell": {
                                "kind": "external",
                                "argv": [sys.executable, "-c", "print('done')"],
                            }
                        }
                    ],
                ),
                "downstream": ModelSpec(
                    name="downstream",
                    select="ext",  # ref vers un modèle no-data
                    transform=[{"filter": {"expression": "val > 0"}}],
                ),
            },
        )
        with pytest.raises((ValueError, RuntimeError, KeyError)) as exc_info:
            # Aucun source_loader ni engine : le seul « select » non-None
            # pointe vers un modèle sans données.
            run_manifest(manifest)

        error_msg = str(exc_info.value).lower()
        assert (
            "no data" in error_msg
            or "non-capability" in error_msg
            or "produces no data" in error_msg
            or "no data output" in error_msg
        ), (
            f"Expected a clear 'no data' error, got: {exc_info.value!r}\n"
            "This should NOT be 'requires either an engine' — that masks the real issue."
        )

    def test_no_false_engine_error_when_only_model_refs(self):
        """run_manifest ne doit pas exiger engine/source_loader quand tous
        les selects pointent vers d'autres modèles (pas vers des sources déclarées)."""
        from gispulse.core.manifest_v3 import ManifestV3, ModelSpec
        from gispulse.runtime.manifest_runner import run_manifest

        # Ce manifest n'a pas de sources déclarées, uniquement des modèles external.
        # Il ne doit pas lever « requires engine or source_loader ».
        manifest = ManifestV3(
            sources={},
            models={
                "ext": ModelSpec(
                    name="ext",
                    select=None,
                    transform=[
                        {
                            "shell": {
                                "kind": "external",
                                "argv": [sys.executable, "-c", "print('ok')"],
                            }
                        }
                    ],
                ),
            },
        )
        # Ne doit PAS lever RuntimeError("requires engine")
        result = run_manifest(manifest)
        assert "ext" in result.execution_order


# ---------------------------------------------------------------------------
# F1 — resume manifest : liste vide → rien à exécuter, pas de re-run total
# ---------------------------------------------------------------------------


class TestManifestResumeNothingToExecute:
    """F1 : quand _replay_resume retourne liste vide, run_manifest N'EST PAS appelé."""

    def _make_ext_manifest_dict(self, marker_path: str | None = None):
        """Manifest avec 1 modèle external qui crée un fichier marqueur si lancé."""
        if marker_path:
            script = f"open({marker_path!r}, 'w').write('ran')"
        else:
            script = "print('hello')"
        return {
            "version": 3,
            "name": "resume_test",
            "sources": {},
            "models": {
                "run_dept_63": {
                    "transform": [
                        {
                            "shell_pipeline": {
                                "kind": "external",
                                "argv": [sys.executable, "-c", script],
                                "timeout_seconds": 30,
                            }
                        }
                    ]
                }
            },
        }

    def _make_completed_source_run(self, run_repo, step_id: str = "run_dept_63"):
        """Crée et persiste un run source COMPLETED avec 1 step COMPLETED."""
        from datetime import datetime, timezone
        from gispulse.core.run_models import PipelineRun, PipelineRunStep
        from gispulse.core.enums import JobStatus

        source_run = PipelineRun(source="job", spec_ref="resume_test", scope="")
        source_run.status = JobStatus.COMPLETED
        source_run.ended_at = datetime.now(timezone.utc)
        source_run.steps = [
            PipelineRunStep(
                step_id=step_id,
                status=JobStatus.COMPLETED,
                ended_at=datetime.now(timezone.utc),
                artifacts={"log_tail": "done"},
            )
        ]
        run_repo.save(source_run)
        return source_run

    def test_resume_empty_steps_does_not_call_run_manifest(self, tmp_path):
        """Quand tous les steps sont COMPLETED dans la source run, run_manifest NE TOURNE PAS."""
        from gispulse.orchestration.runner import JobRunner
        from gispulse.core.models import Job, JobStatus
        from gispulse.persistence.repository import InMemoryRepository
        from gispulse.rules.engine import RuleEngine
        from gispulse.persistence.run_repository import RunRepository
        import geopandas as gpd
        from shapely.geometry import Point

        marker = tmp_path / "ran.txt"
        manifest_dict = self._make_ext_manifest_dict(str(marker))

        run_repo = RunRepository(db_path=tmp_path / "runs.db")
        source_run = self._make_completed_source_run(run_repo)

        job = Job(
            name="resume_test",
            parameters={
                "manifest": manifest_dict,
                "resume_from_run_id": str(source_run.run_id),
            },
        )

        repo = InMemoryRepository()
        engine = RuleEngine(repository=repo)
        runner = JobRunner(repository=repo, rule_engine=engine)
        gdf = gpd.GeoDataFrame({"id": [1]}, geometry=[Point(0, 0)], crs="EPSG:4326")

        updated_job, _ = runner.run(job, gdf, run_repo=run_repo)

        assert updated_job.status == JobStatus.COMPLETED, (
            f"Job should be COMPLETED, got {updated_job.status}: {updated_job.error_message}"
        )
        assert not marker.exists(), (
            "The subprocess ran again despite all steps being COMPLETED in source run. "
            "This is F1: empty steps_filter list was converted to None = no filter = re-run."
        )

    def test_resume_empty_steps_run_manifest_not_called_at_all(self, tmp_path):
        """Quand tous les steps sont COMPLETED, run_manifest NE DOIT PAS être appelé."""
        from unittest.mock import patch, MagicMock
        from gispulse.orchestration.runner import JobRunner
        from gispulse.core.models import Job, JobStatus
        from gispulse.persistence.repository import InMemoryRepository
        from gispulse.rules.engine import RuleEngine
        from gispulse.persistence.run_repository import RunRepository
        from gispulse.runtime.manifest_runner import ManifestRunResult
        import geopandas as gpd
        from shapely.geometry import Point

        run_repo = RunRepository(db_path=tmp_path / "runs.db")
        source_run = self._make_completed_source_run(run_repo)

        job = Job(
            name="resume_test",
            parameters={
                "manifest": self._make_ext_manifest_dict(),
                "resume_from_run_id": str(source_run.run_id),
            },
        )

        repo = InMemoryRepository()
        engine = RuleEngine(repository=repo)
        runner = JobRunner(repository=repo, rule_engine=engine)
        gdf = gpd.GeoDataFrame({"id": [1]}, geometry=[Point(0, 0)], crs="EPSG:4326")

        # Mock run_manifest pour qu'il ne fasse rien (évite la récursion)
        mock_rm = MagicMock(return_value=ManifestRunResult(
            materialized={}, execution_order=[]
        ))

        with patch("gispulse.runtime.manifest_runner.run_manifest", mock_rm):
            updated_job, _ = runner.run(job, gdf, run_repo=run_repo)

        assert updated_job.status == JobStatus.COMPLETED

        # Vérifie que mock_rm n'a PAS été appelé, ou si appelé, pas avec None/[]
        for call in mock_rm.call_args_list:
            sf = call.kwargs.get("steps_filter")
            if sf is None:
                pytest.fail(
                    "run_manifest called with steps_filter=None — "
                    "converted from empty list, re-runs everything (F1)"
                )
            if sf == []:
                pytest.fail(
                    "run_manifest called with steps_filter=[] — "
                    "should skip the call entirely when nothing to execute (F1)"
                )
