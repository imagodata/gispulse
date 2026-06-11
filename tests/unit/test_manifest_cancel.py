"""Cancel + heartbeat propagation through the manifest execution path.

Found during the live phase-A validation (foncier): POST /runs/{id}/cancel
returned 202 but the external subprocess kept running — run_manifest built
its per-model PipelineExecutor without cancel_check/heartbeat, so manifest
jobs (the cockpit command surface) were not cancellable mid-step and never
heartbeat during long external steps.
"""

from __future__ import annotations

import sys
import time

import pytest

from gispulse.core.manifest_v3 import ManifestV3, ModelSpec
from gispulse.runtime.manifest_runner import run_manifest


def _external_manifest(argv: list[str], name: str = "cancel_probe") -> ManifestV3:
    return ManifestV3(
        name=f"test_{name}",
        sources={},
        models={
            name: ModelSpec(
                name=name,
                select=None,
                transform=[
                    {
                        "step": {
                            "kind": "external",
                            "argv": argv,
                            "timeout_seconds": 120,
                        }
                    }
                ],
            )
        },
    )


class TestManifestCancelPropagation:
    def test_cancel_check_kills_external_step(self, tmp_path):
        """cancel_check=True → the external subprocess is terminated quickly.

        Without propagation the sleep runs its full 60 s and the marker file
        gets written by the subprocess.
        """
        marker = tmp_path / "never_written.txt"
        code = f"import time; time.sleep(60); open(r'{marker}', 'w').write('x')"
        manifest = _external_manifest([sys.executable, "-c", code])

        t0 = time.monotonic()
        # RuntimeError specifically: the executor's failed-step path. A
        # TypeError ("unexpected keyword argument") would also contain
        # "cancel" — do not let a signature mismatch pass as a cancel.
        with pytest.raises(RuntimeError) as excinfo:
            run_manifest(
                manifest,
                cancel_check=lambda: True,
                run_id="r-cancel",
            )
        elapsed = time.monotonic() - t0
        assert elapsed < 30, f"external step not cancelled (took {elapsed:.1f}s)"
        assert "cancelled" in str(excinfo.value).lower()
        assert not marker.exists(), "subprocess ran to completion despite cancel"

    def test_heartbeat_called_during_external_step(self, monkeypatch):
        """The heartbeat callable reaches the external step context.

        The external kind beats every 25 s — shrink the interval so a
        1 s subprocess is enough to observe propagation.
        """
        import gispulse.orchestration.external_step as ext

        monkeypatch.setattr(ext, "_HEARTBEAT_INTERVAL_S", 0.05)
        beats: list[float] = []
        manifest = _external_manifest(
            [sys.executable, "-c", "import time; time.sleep(1)"], name="hb_probe"
        )
        run_manifest(
            manifest,
            heartbeat=lambda: beats.append(time.monotonic()),
            run_id="r-hb",
        )
        assert beats, "heartbeat never called on the manifest path"

    def test_no_cancel_check_still_completes(self):
        """Backward compat: omitting cancel_check/heartbeat changes nothing."""
        manifest = _external_manifest(
            [sys.executable, "-c", "print('ok')"], name="compat_probe"
        )
        result = run_manifest(manifest, run_id="r-compat")
        assert "compat_probe" in result.execution_order


class TestRunnerManifestCancelEndToEnd:
    def test_jobrunner_threads_cancel_check_to_manifest_path(self, tmp_path):
        """JobRunner.run(manifest job, cancel_check=True) fails fast as cancelled."""
        from uuid import uuid4

        import geopandas as gpd
        from shapely.geometry import Point

        from gispulse.core.models import Job, JobStatus
        from gispulse.orchestration.runner import JobRunner
        from gispulse.persistence.repository import InMemoryRepository
        from gispulse.rules.engine import RuleEngine

        marker = tmp_path / "never_written.txt"
        code = f"import time; time.sleep(60); open(r'{marker}', 'w').write('x')"
        manifest = {
            "version": 3,
            "name": "e2e_cancel",
            "models": {
                "e2e_cancel": {
                    "transform": [
                        {
                            "step": {
                                "kind": "external",
                                "argv": [sys.executable, "-c", code],
                                "timeout_seconds": 120,
                            }
                        }
                    ]
                }
            },
        }
        job = Job(
            id=uuid4(),
            name="e2e-cancel",
            parameters={"manifest": manifest, "max_retries": 0},
        )
        gdf = gpd.GeoDataFrame({"id": [1], "geometry": [Point(0, 0)]}, crs="EPSG:4326")
        runner = JobRunner(repository=InMemoryRepository(), rule_engine=RuleEngine())

        t0 = time.monotonic()
        # runner.run raises after marking the job FAILED (same contract the
        # timeout tests rely on).
        with pytest.raises(RuntimeError, match="cancelled"):
            runner.run(job, gdf, cancel_check=lambda: True)
        elapsed = time.monotonic() - t0

        assert job.status == JobStatus.FAILED
        assert elapsed < 30, f"manifest job not cancelled mid-step ({elapsed:.1f}s)"
        assert not marker.exists()


class TestManifestTimeoutElevation:
    def test_step_timeout_elevates_job_timeout_on_manifest_path(self):
        """A step declaring timeout_seconds > job timeout is not killed early.

        Found in live phase-A validation: job timeout default (300 s) was
        applied raw on the manifest path — a dbt step declaring 21600 s
        would die at 5 min. Here: job timeout=1 s, step declares 30 s,
        subprocess sleeps 3 s → without elevation the future times out at
        1 s; with elevation the run completes.
        """
        from uuid import uuid4

        import geopandas as gpd
        from shapely.geometry import Point

        from gispulse.core.models import Job, JobStatus
        from gispulse.orchestration.runner import JobRunner
        from gispulse.persistence.repository import InMemoryRepository
        from gispulse.rules.engine import RuleEngine

        manifest = {
            "version": 3,
            "name": "timeout_probe",
            "models": {
                "timeout_probe": {
                    "transform": [
                        {
                            "step": {
                                "kind": "external",
                                "argv": [
                                    sys.executable,
                                    "-c",
                                    "import time; time.sleep(3); print('done')",
                                ],
                                "timeout_seconds": 30,
                            }
                        }
                    ]
                }
            },
        }
        job = Job(
            id=uuid4(),
            name="timeout-elevation",
            parameters={"manifest": manifest, "timeout": 1, "max_retries": 0},
        )
        gdf = gpd.GeoDataFrame({"id": [1], "geometry": [Point(0, 0)]}, crs="EPSG:4326")
        runner = JobRunner(repository=InMemoryRepository(), rule_engine=RuleEngine())

        updated_job, _ = runner.run(job, gdf)
        assert updated_job.status == JobStatus.COMPLETED, (
            f"job should complete via elevated timeout, got {updated_job.status} "
            f"(error: {updated_job.error_message})"
        )
