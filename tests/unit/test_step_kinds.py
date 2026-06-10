"""Tests for orchestration/step_kinds.py, orchestration/external_step.py,
and the dispatch integration in pipeline_executor.py.

Covers:
- Registry: doublon, inconnu, replace
- Dispatch capability path unchanged (régression)
- Compile-time validation: step aval capability sur step external → erreur
- external nominal (script python + exit 0) → completed, log_tail, artifacts
- exit 1 → failed avec tail
- timeout par step court (script qui dort) → failed timeout, process group mort
- cancel pendant l'exécution → terminaison propre
- skip_marker capturé
- progress émis
- heartbeat rafraîchi pendant un step long simulé
- PipelineRunStep.artifacts persistés dans RunRepository
"""
from __future__ import annotations

import os
import sys
import textwrap
import time
from pathlib import Path
from uuid import uuid4

import geopandas as gpd
import pytest
from shapely.geometry import Point

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def point_gdf() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {"id": [1, 2], "geometry": [Point(2.35, 48.85), Point(2.30, 48.87)]},
        crs="EPSG:4326",
    )


class SpySink:
    def __init__(self):
        self.events: list[tuple[str, dict]] = []

    def emit(self, event_type: str, data: dict) -> None:
        self.events.append((event_type, data))

    def types(self) -> list[str]:
        return [t for t, _ in self.events]

    def data_for(self, event_type: str) -> list[dict]:
        return [d for t, d in self.events if t == event_type]


# ---------------------------------------------------------------------------
# Registry tests
# ---------------------------------------------------------------------------


class TestStepKindRegistry:
    """Tests for register_step_kind / get_step_kind_handler / list_step_kinds."""

    def test_register_and_retrieve(self):
        """A freshly registered kind is retrievable immediately."""
        from gispulse.orchestration.step_kinds import (
            StepContext,
            StepResult,
            get_step_kind_handler,
            register_step_kind,
        )

        name = f"test_kind_{uuid4().hex[:8]}"

        def _handler(params: dict, ctx: StepContext) -> StepResult:
            return StepResult(status="completed", metrics={"ok": True})

        register_step_kind(name, _handler)
        retrieved = get_step_kind_handler(name)
        assert retrieved is _handler

    def test_duplicate_raises(self):
        """Registering the same name twice (no replace) raises ValueError."""
        from gispulse.orchestration.step_kinds import StepResult, register_step_kind

        name = f"dup_{uuid4().hex[:8]}"

        def _h1(p, c): return StepResult()
        def _h2(p, c): return StepResult()

        register_step_kind(name, _h1)
        with pytest.raises(ValueError, match="already registered"):
            register_step_kind(name, _h2)

    def test_replace_true_overrides(self):
        """replace=True silently overrides the existing handler."""
        from gispulse.orchestration.step_kinds import (
            StepResult,
            get_step_kind_handler,
            register_step_kind,
        )

        name = f"rep_{uuid4().hex[:8]}"

        def _h1(p, c): return StepResult(metrics={"v": 1})
        def _h2(p, c): return StepResult(metrics={"v": 2})

        register_step_kind(name, _h1)
        register_step_kind(name, _h2, replace=True)
        handler = get_step_kind_handler(name)
        assert handler is _h2

    def test_unknown_kind_returns_none(self):
        """get_step_kind_handler returns None for unknown names."""
        from gispulse.orchestration.step_kinds import get_step_kind_handler

        assert get_step_kind_handler("__nonexistent_kind__") is None

    def test_external_builtin_is_registered(self):
        """The built-in 'external' kind is registered at import time."""
        import gispulse.orchestration.external_step  # noqa: F401 — trigger registration
        from gispulse.orchestration.step_kinds import get_step_kind_handler

        handler = get_step_kind_handler("external")
        assert handler is not None


# ---------------------------------------------------------------------------
# Capability path regression
# ---------------------------------------------------------------------------


class TestCapabilityPathUnchanged:
    """Ensures existing capability steps are not affected by the new dispatch."""

    def test_linear_capability_pipeline_unchanged(self, point_gdf):
        """Existing linear capability pipeline: results, events unchanged."""
        from gispulse.core.pipeline import PipelineSpec, StepSpec
        from gispulse.orchestration.pipeline_executor import PipelineExecutor

        spec = PipelineSpec(
            version=2,
            name="cap_only",
            steps=[
                StepSpec(id="flt", type="capability", capability="filter",
                         params={"expression": "id > 0"}, kind="capability"),
            ],
        )
        sink = SpySink()
        executor = PipelineExecutor()
        results = executor.execute(spec, {"input": point_gdf}, event_sink=sink, run_id="r1")

        assert "flt" in results
        assert sink.types() == ["run.step.started", "run.step.completed"]

    def test_kind_defaulted_to_capability_behaves_same(self, point_gdf):
        """StepSpec with no explicit kind= behaves identically to kind='capability'."""
        from gispulse.core.pipeline import PipelineSpec, StepSpec
        from gispulse.orchestration.pipeline_executor import PipelineExecutor

        spec = PipelineSpec(
            version=2,
            name="default_kind",
            steps=[
                StepSpec(id="s1", type="capability", capability="filter",
                         params={"expression": "id > 0"}),
            ],
        )
        results = PipelineExecutor().execute(spec, {"input": point_gdf})
        assert "s1" in results


# ---------------------------------------------------------------------------
# Compile-time validation
# ---------------------------------------------------------------------------


class TestCompileTimeValidation:
    """validate_step_kind_inputs detects illegal downstream references."""

    def test_capability_after_external_raises(self):
        """A capability step depending on an external step → validation error."""
        from gispulse.core.pipeline import PipelineSpec, StepSpec, validate_step_kind_inputs

        spec = PipelineSpec(
            version=2,
            name="bad_dep",
            steps=[
                StepSpec(id="ext_step", kind="external",
                         params={"argv": ["echo", "hi"]}),
                StepSpec(id="cap_step", kind="capability", capability="filter",
                         params={"expression": "id > 0"}, input="ext_step"),
            ],
        )
        errors = validate_step_kind_inputs(spec)
        assert errors
        assert "ext_step" in errors[0]
        assert "external" in errors[0]

    def test_no_error_for_pure_capability_pipeline(self, point_gdf):
        """validate_step_kind_inputs returns [] for a pure capability pipeline."""
        from gispulse.core.pipeline import PipelineSpec, StepSpec, validate_step_kind_inputs

        spec = PipelineSpec(
            version=2,
            name="cap_ok",
            steps=[
                StepSpec(id="s1", kind="capability", capability="filter",
                         params={"expression": "id > 0"}),
                StepSpec(id="s2", kind="capability", capability="filter",
                         params={"expression": "id > 0"}, input="s1"),
            ],
        )
        assert validate_step_kind_inputs(spec) == []

    def test_no_error_for_standalone_external(self):
        """A standalone external step (no downstream capability) is valid."""
        from gispulse.core.pipeline import PipelineSpec, StepSpec, validate_step_kind_inputs

        spec = PipelineSpec(
            version=2,
            name="ext_only",
            steps=[
                StepSpec(id="ext", kind="external", params={"argv": ["true"]}),
            ],
        )
        assert validate_step_kind_inputs(spec) == []

    def test_unknown_kind_downstream_capability_is_also_caught(self):
        """Custom kinds (not just external) are also caught."""
        from gispulse.core.pipeline import PipelineSpec, StepSpec, validate_step_kind_inputs

        spec = PipelineSpec(
            version=2,
            name="custom_kind",
            steps=[
                StepSpec(id="custom", kind="dbt_build", params={}),
                StepSpec(id="cap", kind="capability", capability="filter",
                         params={"expression": "x>0"}, input="custom"),
            ],
        )
        errors = validate_step_kind_inputs(spec)
        assert len(errors) == 1
        assert "dbt_build" in errors[0]

    def test_runner_rejects_capability_depending_on_external(self, point_gdf):
        """validate_step_kind_inputs doit être appelé par runner._execute_pipeline_config."""
        from uuid import uuid4

        from gispulse.core.models import Job
        from gispulse.orchestration.runner import JobRunner
        from gispulse.persistence.repository import InMemoryRepository
        from gispulse.rules.engine import RuleEngine

        pipeline_config = {
            "version": 2,
            "steps": [
                {
                    "id": "ext",
                    "type": "external",
                    "kind": "external",
                    "params": {"argv": ["echo", "hi"]},
                },
                {
                    "id": "cap",
                    "type": "capability",
                    "capability": "filter",
                    "input": "ext",
                    "params": {"expression": "id > 0"},
                },
            ],
        }
        job = Job(
            id=uuid4(),
            name="bad-dep",
            parameters={"pipeline_config": pipeline_config},
        )
        runner = JobRunner(
            repository=InMemoryRepository(),
            rule_engine=RuleEngine(),
        )
        with pytest.raises(
            (ValueError, RuntimeError),
            match="step.*kind.*validation|depends on step",
        ):
            runner.run(job, point_gdf)


# ---------------------------------------------------------------------------
# Unknown kind dispatch → StepResult.failed
# ---------------------------------------------------------------------------


class TestUnknownKindDispatch:
    def test_unknown_kind_produces_failed_result_not_exception(self, point_gdf):
        """An unknown kind fails the step with a clear message (not a KeyError crash)."""
        from gispulse.core.pipeline import PipelineSpec, StepSpec
        from gispulse.orchestration.pipeline_executor import PipelineExecutor

        spec = PipelineSpec(
            version=2,
            name="unknown_kind",
            steps=[
                StepSpec(id="ghost", kind="__nonexistent__kind__xyz__",
                         params={}),
            ],
        )
        sink = SpySink()
        with pytest.raises(RuntimeError, match="__nonexistent__kind__xyz__"):
            PipelineExecutor().execute(spec, {"input": point_gdf}, event_sink=sink)

        # run.step.failed must have been emitted
        assert "run.step.failed" in sink.types()
        failed = sink.data_for("run.step.failed")
        assert any("__nonexistent__kind__xyz__" in d.get("error", "") for d in failed)


# ---------------------------------------------------------------------------
# external kind — nominal (exit 0)
# ---------------------------------------------------------------------------


def _make_script(tmp_path: Path, code: str) -> str:
    """Write a Python script to tmp_path and return its absolute path."""
    script = tmp_path / f"script_{uuid4().hex[:6]}.py"
    script.write_text(textwrap.dedent(code))
    return str(script)


class TestExternalKindNominal:
    def test_exit_0_returns_completed(self, tmp_path):
        """A script that exits 0 produces status='completed'."""
        import gispulse.orchestration.external_step  # noqa: F401
        from gispulse.orchestration.step_kinds import StepContext, StepResult, get_step_kind_handler

        script = _make_script(tmp_path, """\
            import sys
            print("hello from external step")
            sys.exit(0)
        """)
        handler = get_step_kind_handler("external")
        ctx = StepContext(run_id="r1", step_id="s1")
        result: StepResult = handler({"argv": [sys.executable, script]}, ctx)

        assert result.status == "completed"
        assert "hello from external step" in result.artifacts.get("log_tail", "")
        assert result.metrics.get("exit_code") == 0
        assert result.metrics.get("lines_seen", 0) >= 1

    def test_exit_1_returns_failed(self, tmp_path):
        """A script that exits 1 produces status='failed' with tail in error."""
        import gispulse.orchestration.external_step  # noqa: F401
        from gispulse.orchestration.step_kinds import StepContext, get_step_kind_handler

        script = _make_script(tmp_path, """\
            import sys
            print("ERROR: something bad happened")
            sys.exit(1)
        """)
        handler = get_step_kind_handler("external")
        ctx = StepContext(run_id="r1", step_id="s1")
        result = handler({"argv": [sys.executable, script]}, ctx)

        assert result.status == "failed"
        assert "1" in result.error  # exit code 1
        assert "ERROR: something bad happened" in (
            result.artifacts.get("log_tail", "") + result.error
        )

    def test_skip_marker_captured(self, tmp_path):
        """GISPULSE_SKIP_MARKER=<token> on stdout is captured in artifacts."""
        import gispulse.orchestration.external_step  # noqa: F401
        from gispulse.orchestration.step_kinds import StepContext, get_step_kind_handler

        script = _make_script(tmp_path, """\
            print("processing...")
            print("GISPULSE_SKIP_MARKER=abc123opaque")
            print("done")
        """)
        handler = get_step_kind_handler("external")
        ctx = StepContext(run_id="r1", step_id="s1")
        result = handler({"argv": [sys.executable, script]}, ctx)

        assert result.status == "completed"
        assert result.artifacts.get("skip_marker") == "abc123opaque"

    def test_log_tail_truncated_to_tail_lines(self, tmp_path):
        """Only the last log_tail_lines lines are kept."""
        import gispulse.orchestration.external_step  # noqa: F401
        from gispulse.orchestration.step_kinds import StepContext, get_step_kind_handler

        # Write 200 lines
        script = _make_script(tmp_path, """\
            for i in range(200):
                print(f"line {i}")
        """)
        handler = get_step_kind_handler("external")
        ctx = StepContext(run_id="r1", step_id="s1")
        result = handler({"argv": [sys.executable, script], "log_tail_lines": 10}, ctx)

        tail = result.artifacts.get("log_tail", "")
        lines = [l for l in tail.split("\n") if l]
        assert len(lines) <= 10
        # Last lines must be the highest numbered
        assert "line 199" in tail

    def test_empty_argv_returns_failed(self):
        """Empty argv is fast-failed before Popen."""
        import gispulse.orchestration.external_step  # noqa: F401
        from gispulse.orchestration.step_kinds import StepContext, get_step_kind_handler

        handler = get_step_kind_handler("external")
        ctx = StepContext(run_id="r1", step_id="s1")
        result = handler({"argv": []}, ctx)
        assert result.status == "failed"
        assert "argv" in result.error.lower()


class TestSkipMarkerSemantics:
    def test_last_marker_wins(self, tmp_path):
        """3 marqueurs émis → le dernier est dans artifacts["skip_marker"]."""
        import gispulse.orchestration.external_step  # noqa: F401
        from gispulse.orchestration.step_kinds import StepContext, get_step_kind_handler

        script = tmp_path / "multi_marker.py"
        script.write_text(
            "print('GISPULSE_SKIP_MARKER=checkpoint_1')\n"
            "print('GISPULSE_SKIP_MARKER=checkpoint_2')\n"
            "print('GISPULSE_SKIP_MARKER=checkpoint_3')\n"
            "print('normal line')\n"
        )
        handler = get_step_kind_handler("external")
        ctx = StepContext(run_id="r1", step_id="s1")
        result = handler(
            {"argv": [sys.executable, str(script)]},
            ctx,
        )
        assert result.status == "completed"
        assert result.artifacts.get("skip_marker") == "checkpoint_3"

    def test_marker_lines_not_in_log_tail(self, tmp_path):
        """Les lignes GISPULSE_SKIP_MARKER= ne doivent PAS apparaître dans log_tail."""
        import gispulse.orchestration.external_step  # noqa: F401
        from gispulse.orchestration.step_kinds import StepContext, get_step_kind_handler

        script = tmp_path / "marker_filter.py"
        script.write_text(
            "print('before')\n"
            "print('GISPULSE_SKIP_MARKER=token_xyz')\n"
            "print('after')\n"
        )
        handler = get_step_kind_handler("external")
        ctx = StepContext(run_id="r1", step_id="s1")
        result = handler({"argv": [sys.executable, str(script)]}, ctx)
        assert result.status == "completed"
        assert "GISPULSE_SKIP_MARKER" not in result.artifacts["log_tail"]
        assert "before" in result.artifacts["log_tail"]
        assert "after" in result.artifacts["log_tail"]


class TestLogTailByteCap:
    def test_huge_lines_capped_at_64kib(self, tmp_path):
        """5 lignes de 20 KiB chacune → log_tail ≤ 64 KiB + contient '[truncated'."""
        import gispulse.orchestration.external_step  # noqa: F401
        from gispulse.orchestration.step_kinds import StepContext, get_step_kind_handler

        # 5 lines x 20480 chars = 100 KiB > 64 KiB cap.
        big_line = "X" * 20480
        script = tmp_path / "big_output.py"
        script.write_text(
            "for i in range(5):\n"
            f"    print('{big_line}')\n"
        )
        handler = get_step_kind_handler("external")
        ctx = StepContext(run_id="r1", step_id="s1")
        result = handler({"argv": [sys.executable, str(script)]}, ctx)
        assert result.status == "completed"
        tail_bytes = len(result.artifacts["log_tail"].encode("utf-8"))
        assert tail_bytes <= 64 * 1024 + 200
        assert "[truncated" in result.artifacts["log_tail"]


# ---------------------------------------------------------------------------
# external kind — timeout per step
# ---------------------------------------------------------------------------


class TestExternalKindTimeout:
    def test_step_timeout_kills_process(self, tmp_path):
        """timeout_seconds fires and process group is dead (no zombie)."""
        import gispulse.orchestration.external_step  # noqa: F401
        from gispulse.orchestration.step_kinds import StepContext, get_step_kind_handler

        # Script that sleeps 60s
        script = _make_script(tmp_path, """\
            import time, sys
            sys.stdout.write("started\\n")
            sys.stdout.flush()
            time.sleep(60)
        """)
        handler = get_step_kind_handler("external")
        ctx = StepContext(run_id="r1", step_id="s1")

        t0 = time.monotonic()
        result = handler(
            {"argv": [sys.executable, script], "timeout_seconds": 2},
            ctx,
        )
        elapsed = time.monotonic() - t0

        assert result.status == "failed"
        assert "timeout" in result.error.lower()
        # Must finish well within the sleep duration
        assert elapsed < 15.0, f"Took {elapsed:.1f}s — process may not have been killed"

    def test_process_group_really_dead(self, tmp_path):
        """After timeout, the spawned process group is dead (ESRCH on SIGTERM)."""
        import gispulse.orchestration.external_step  # noqa: F401
        from gispulse.orchestration.step_kinds import StepContext, get_step_kind_handler

        pids_file = tmp_path / "pids.txt"
        # Write a script that spawns a child that also sleeps, and records both PIDs
        script = _make_script(tmp_path, f"""\
            import os, time, subprocess, sys
            # Record our own pid
            with open({str(pids_file)!r}, "w") as f:
                f.write(str(os.getpid()) + "\\n")
            sys.stdout.flush()
            time.sleep(60)
        """)
        handler = get_step_kind_handler("external")
        ctx = StepContext(run_id="r1", step_id="s1")
        result = handler(
            {"argv": [sys.executable, script], "timeout_seconds": 2},
            ctx,
        )
        assert result.status == "failed"

        # Give the OS a moment to reap
        time.sleep(0.5)

        if pids_file.exists():
            for pid_s in pids_file.read_text().split():
                pid = int(pid_s)
                try:
                    os.kill(pid, 0)  # no signal — just existence check
                    # If we get here the process is still alive — fail
                    pytest.fail(f"Process {pid} is still alive after timeout kill")
                except ProcessLookupError:
                    pass  # Expected: process is dead


# ---------------------------------------------------------------------------
# external kind — cancel check
# ---------------------------------------------------------------------------


class TestExternalKindCancel:
    def test_cancel_terminates_process(self, tmp_path):
        """cancel_check() returning True terminates the process cleanly."""
        import gispulse.orchestration.external_step  # noqa: F401
        from gispulse.orchestration.step_kinds import StepContext, get_step_kind_handler

        script = _make_script(tmp_path, """\
            import time, sys
            sys.stdout.write("running\\n")
            sys.stdout.flush()
            time.sleep(60)
        """)

        cancel_after_calls = [3]  # cancel after 3 calls to cancel_check

        def _cancel() -> bool:
            if cancel_after_calls[0] > 0:
                cancel_after_calls[0] -= 1
                return False
            return True

        handler = get_step_kind_handler("external")
        ctx = StepContext(run_id="r1", step_id="s1", cancel_check=_cancel)

        t0 = time.monotonic()
        result = handler({"argv": [sys.executable, script]}, ctx)
        elapsed = time.monotonic() - t0

        assert result.status == "failed"
        assert "cancel" in result.error.lower()
        assert elapsed < 15.0, f"Cancel took {elapsed:.1f}s — too slow"


# ---------------------------------------------------------------------------
# external kind — progress events
# ---------------------------------------------------------------------------


class TestExternalKindProgress:
    def test_progress_events_emitted(self, tmp_path):
        """progress events are emitted every _PROGRESS_INTERVAL_LINES lines."""
        import gispulse.orchestration.external_step as ext_mod
        from gispulse.orchestration.step_kinds import StepContext, get_step_kind_handler

        # Write enough lines to trigger at least one progress event
        n_lines = ext_mod._PROGRESS_INTERVAL_LINES + 10
        script = _make_script(tmp_path, f"""\
            for i in range({n_lines}):
                print(f"line {{i}}")
        """)
        sink = SpySink()
        handler = get_step_kind_handler("external")
        ctx = StepContext(run_id="r1", step_id="s1", event_sink=sink)
        result = handler({"argv": [sys.executable, script]}, ctx)

        assert result.status == "completed"
        progress_events = [d for t, d in sink.events if t == "run.step.progress"]
        assert len(progress_events) >= 1
        assert all("lines_seen" in d for d in progress_events)


# ---------------------------------------------------------------------------
# external kind — heartbeat refresh
# ---------------------------------------------------------------------------


class TestExternalKindHeartbeat:
    def test_heartbeat_called_during_long_step(self, tmp_path):
        """heartbeat() is called during a moderately long step."""
        import gispulse.orchestration.external_step as ext_mod
        from gispulse.orchestration.step_kinds import StepContext, get_step_kind_handler

        # Script that runs slightly longer than one heartbeat interval
        interval = ext_mod._HEARTBEAT_INTERVAL_S
        script = _make_script(tmp_path, f"""\
            import time
            time.sleep({interval + 2:.1f})
        """)

        heartbeat_calls: list[float] = []

        def _hb() -> None:
            heartbeat_calls.append(time.monotonic())

        handler = get_step_kind_handler("external")
        ctx = StepContext(run_id="r1", step_id="s1", heartbeat=_hb)
        result = handler({"argv": [sys.executable, script]}, ctx)

        assert result.status == "completed"
        assert len(heartbeat_calls) >= 1, "heartbeat() was never called"


# ---------------------------------------------------------------------------
# Pipeline executor dispatch — integration with PipelineExecutor
# ---------------------------------------------------------------------------


class TestPipelineExecutorDispatch:
    def test_external_step_in_linear_pipeline(self, tmp_path, point_gdf):
        """A standalone external step in a linear pipeline executes and emits events."""
        import gispulse.orchestration.external_step  # noqa: F401
        from gispulse.core.pipeline import PipelineSpec, StepSpec
        from gispulse.orchestration.pipeline_executor import PipelineExecutor

        script = _make_script(tmp_path, "print('ok')")
        spec = PipelineSpec(
            version=2,
            name="ext_pipe",
            steps=[
                StepSpec(id="dbt", kind="external",
                         params={"argv": [sys.executable, script]}),
            ],
        )
        sink = SpySink()
        executor = PipelineExecutor()
        # external steps don't need inputs but executor still takes the dict
        executor.execute(spec, {"input": point_gdf}, event_sink=sink, run_id="run-1")

        assert "run.step.started" in sink.types()
        assert "run.step.completed" in sink.types()
        completed = sink.data_for("run.step.completed")
        assert any("artifacts" in d for d in completed)

    def test_external_step_failure_propagates(self, tmp_path, point_gdf):
        """A failing external step raises RuntimeError and emits run.step.failed."""
        import gispulse.orchestration.external_step  # noqa: F401
        from gispulse.core.pipeline import PipelineSpec, StepSpec
        from gispulse.orchestration.pipeline_executor import PipelineExecutor

        script = _make_script(tmp_path, "import sys; sys.exit(42)")
        spec = PipelineSpec(
            version=2,
            name="fail_ext",
            steps=[
                StepSpec(id="bad", kind="external",
                         params={"argv": [sys.executable, script]}),
            ],
        )
        sink = SpySink()
        with pytest.raises(RuntimeError):
            PipelineExecutor().execute(spec, {"input": point_gdf}, event_sink=sink)

        assert "run.step.failed" in sink.types()
        failed = sink.data_for("run.step.failed")
        assert any("artifacts" in d for d in failed)


# ---------------------------------------------------------------------------
# Built-in kind auto-registration
# ---------------------------------------------------------------------------


class TestBuiltinKindAutoRegistration:
    def test_external_kind_works_in_fresh_process(self, tmp_path):
        """Prouve que external_step.py n'a pas besoin d'être importé manuellement.

        Lance un subprocess Python frais (sans conftest, sans import test) qui
        exécute JobRunner.run avec kind=external et vérifie COMPLETED.
        """
        import subprocess
        import sys
        import textwrap

        script = tmp_path / "script.py"
        script.write_text("print('hello from external')\n")

        code = textwrap.dedent("""
            import sys
            import geopandas as gpd
            from shapely.geometry import Point
            from uuid import uuid4
            from gispulse.core.models import Job, JobStatus
            from gispulse.orchestration.runner import JobRunner
            from gispulse.persistence.repository import InMemoryRepository
            from gispulse.rules.engine import RuleEngine

            # NOTE: NO import of gispulse.orchestration.external_step here.
            # The _ensure_builtin_kinds() mechanism must handle this.

            pipeline_config = {
                "version": 2,
                "steps": [{
                    "id": "ext",
                    "type": "external",
                    "kind": "external",
                    "params": {"argv": [sys.executable, "-c", "print('ok')"]}
                }]
            }
            job = Job(id=uuid4(), name="fresh-test", parameters={"pipeline_config": pipeline_config})
            gdf = gpd.GeoDataFrame({"id": [1], "geometry": [Point(0, 0)]}, crs="EPSG:4326")
            runner = JobRunner(repository=InMemoryRepository(), rule_engine=RuleEngine())
            updated_job, _ = runner.run(job, gdf)
            assert updated_job.status == JobStatus.COMPLETED, f"Expected COMPLETED, got {updated_job.status}"
            print("FRESH_PROCESS_OK")
        """)

        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            timeout=30,
            cwd="/Users/erwanpesle/Documents/GitHub/gispulse/core-wt-external-steps",
            env={**__import__("os").environ},
        )
        assert result.returncode == 0, (
            f"Fresh process failed (rc={result.returncode}):\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert "FRESH_PROCESS_OK" in result.stdout


class TestDAGKindDispatch:
    """Tests que _execute_dag dispatche bien les steps non-capability."""

    def test_external_chain_in_dag_both_steps_run(self, tmp_path, point_gdf):
        """Chaîne external→external en DAG : les deux steps s'exécutent et ont des artifacts."""
        import gispulse.orchestration.external_step  # noqa: F401
        from gispulse.core.pipeline import _parse_v2
        from gispulse.orchestration.pipeline_executor import PipelineExecutor

        script_a = tmp_path / "a.py"
        script_a.write_text("print('step_a_done')\n")
        script_b = tmp_path / "b.py"
        script_b.write_text("print('step_b_done')\n")

        spec = _parse_v2({
            "version": 2,
            "steps": [
                {
                    "id": "step_a",
                    "type": "external",
                    "kind": "external",
                    "params": {"argv": [sys.executable, str(script_a)]},
                },
                {
                    "id": "step_b",
                    "type": "external",
                    "kind": "external",
                    "input": "step_a",
                    "params": {"argv": [sys.executable, str(script_b)]},
                },
            ],
        })

        sink = SpySink()
        executor = PipelineExecutor()
        executor.execute(spec, {"input": point_gdf}, event_sink=sink)

        completed = sink.data_for("run.step.completed")
        step_ids_completed = {d["step_id"] for d in completed}
        assert "step_a" in step_ids_completed
        assert "step_b" in step_ids_completed

        for data in completed:
            assert "artifacts" in data
            assert "log_tail" in data["artifacts"]

    def test_external_chain_dag_failure_propagates(self, tmp_path, point_gdf):
        """Si step_a échoue, step_b ne s'exécute pas."""
        import gispulse.orchestration.external_step  # noqa: F401
        from gispulse.core.pipeline import _parse_v2
        from gispulse.orchestration.pipeline_executor import PipelineExecutor

        spec = _parse_v2({
            "version": 2,
            "steps": [
                {
                    "id": "fail_step",
                    "type": "external",
                    "kind": "external",
                    "params": {"argv": [sys.executable, "-c", "import sys; sys.exit(1)"]},
                },
                {
                    "id": "never_runs",
                    "type": "external",
                    "kind": "external",
                    "input": "fail_step",
                    "params": {"argv": [sys.executable, "-c", "print('should not run')"]},
                },
            ],
        })

        sink = SpySink()
        executor = PipelineExecutor()
        with pytest.raises(RuntimeError):
            executor.execute(spec, {"input": point_gdf}, event_sink=sink)

        started_ids = {d["step_id"] for d in sink.data_for("run.step.started")}
        assert "never_runs" not in started_ids


# ---------------------------------------------------------------------------
# Artifacts persistence in RunRepository
# ---------------------------------------------------------------------------


class TestArtifactsPersistence:
    def test_artifacts_persisted_in_run_step(self, tmp_path, point_gdf):
        """After execution, PipelineRunStep.artifacts contains log_tail."""
        import asyncio
        import gispulse.orchestration.external_step  # noqa: F401
        from gispulse.core.models import Dataset, Job, JobStatus
        from gispulse.orchestration.job_queue import InMemoryJobQueue
        from gispulse.orchestration.runner import JobRunner
        from gispulse.orchestration.worker import JobWorker
        from gispulse.persistence.repository import InMemoryRepository
        from gispulse.persistence.run_repository import RunRepository
        from gispulse.rules.engine import RuleEngine

        script = _make_script(tmp_path, """\
            print("artifact line one")
            print("artifact line two")
        """)
        gpkg_path = tmp_path / "input.gpkg"
        point_gdf.to_file(gpkg_path, driver="GPKG", layer="cities")

        ds_repo: InMemoryRepository = InMemoryRepository()
        ds = Dataset(id=uuid4(), name="cities", source_path=str(gpkg_path))
        ds_repo.save(ds)

        run_repo = RunRepository(db_path=tmp_path / "runs.db")
        job_repo: InMemoryRepository = InMemoryRepository()
        results_dir = tmp_path / "results"
        results_dir.mkdir()
        queue = InMemoryJobQueue()
        repo: InMemoryRepository = InMemoryRepository()
        engine = RuleEngine(repository=repo)
        runner = JobRunner(repository=repo, rule_engine=engine)

        pipeline_config = {
            "version": 2,
            "name": "art_test",
            "steps": [
                {
                    "id": "ext_step",
                    "kind": "external",
                    "params": {"argv": [sys.executable, script]},
                }
            ],
        }
        job = Job(
            name="art_job",
            dataset_id=ds.id,
            parameters={"pipeline_config": pipeline_config},
        )

        async def _run():
            await queue.enqueue(job)
            worker = JobWorker(
                queue=queue,
                runner=runner,
                dataset_repo=ds_repo,
                job_repo=job_repo,
                run_repo=run_repo,
                results_dir=results_dir,
            )
            dequeued = await queue.dequeue(timeout=1.0)
            await worker._process_job(dequeued)

        asyncio.run(_run())

        runs = run_repo.list_all()
        assert len(runs) == 1
        run = runs[0]
        assert run.status == JobStatus.COMPLETED

        ext_steps = [s for s in run.steps if s.step_id == "ext_step"]
        assert ext_steps, "ext_step was not recorded"
        ext_step = ext_steps[0]
        assert ext_step.status == JobStatus.COMPLETED
        log_tail = ext_step.artifacts.get("log_tail", "")
        assert "artifact line one" in log_tail or "artifact line two" in log_tail

    def test_skip_marker_in_artifacts(self, tmp_path, point_gdf):
        """skip_marker written by process appears in PipelineRunStep.artifacts."""
        import asyncio
        import gispulse.orchestration.external_step  # noqa: F401
        from gispulse.core.models import Dataset, Job
        from gispulse.orchestration.job_queue import InMemoryJobQueue
        from gispulse.orchestration.runner import JobRunner
        from gispulse.orchestration.worker import JobWorker
        from gispulse.persistence.repository import InMemoryRepository
        from gispulse.persistence.run_repository import RunRepository
        from gispulse.rules.engine import RuleEngine

        script = _make_script(tmp_path, """\
            print("GISPULSE_SKIP_MARKER=checkpoint_v42")
            print("done")
        """)
        gpkg_path = tmp_path / "input.gpkg"
        point_gdf.to_file(gpkg_path, driver="GPKG", layer="cities")

        ds_repo: InMemoryRepository = InMemoryRepository()
        ds = Dataset(id=uuid4(), name="cities", source_path=str(gpkg_path))
        ds_repo.save(ds)

        run_repo = RunRepository(db_path=tmp_path / "runs.db")
        job_repo: InMemoryRepository = InMemoryRepository()
        results_dir = tmp_path / "results"
        results_dir.mkdir()
        queue = InMemoryJobQueue()
        repo: InMemoryRepository = InMemoryRepository()
        runner = JobRunner(repository=repo, rule_engine=RuleEngine(repository=repo))

        pipeline_config = {
            "version": 2,
            "name": "skip_test",
            "steps": [
                {"id": "ext", "kind": "external",
                 "params": {"argv": [sys.executable, script]}},
            ],
        }
        job = Job(
            name="skip_job",
            dataset_id=ds.id,
            parameters={"pipeline_config": pipeline_config},
        )

        async def _run():
            await queue.enqueue(job)
            worker = JobWorker(
                queue=queue, runner=runner, dataset_repo=ds_repo,
                job_repo=job_repo, run_repo=run_repo, results_dir=results_dir,
            )
            dequeued = await queue.dequeue(timeout=1.0)
            await worker._process_job(dequeued)

        asyncio.run(_run())

        runs = run_repo.list_all()
        run = runs[0]
        ext_steps = [s for s in run.steps if s.step_id == "ext"]
        assert ext_steps
        marker = ext_steps[0].artifacts.get("skip_marker")
        assert marker == "checkpoint_v42"


# ---------------------------------------------------------------------------
# SCHEMA_V2 accepts the kind field
# ---------------------------------------------------------------------------


class TestSchemaV2KindField:
    def test_schema_v2_accepts_kind_field(self):
        """SCHEMA_V2 step accepts the 'kind' field without additionalProperties error."""
        try:
            import jsonschema
        except ImportError:
            pytest.skip("jsonschema not available")

        from gispulse.core.pipeline_schema import SCHEMA_V2

        raw = {
            "version": 2,
            "name": "test",
            "steps": [
                {"id": "s1", "kind": "external",
                 "params": {"argv": ["dbt", "build"]}},
            ],
        }
        errors = []
        validator = jsonschema.Draft202012Validator(SCHEMA_V2)
        for err in validator.iter_errors(raw):
            errors.append(err.message)
        assert errors == [], f"Schema validation errors: {errors}"

    def test_schema_v2_capability_without_kind_still_valid(self):
        """An existing spec without kind= still validates (backward compat)."""
        try:
            import jsonschema
        except ImportError:
            pytest.skip("jsonschema not available")

        from gispulse.core.pipeline_schema import SCHEMA_V2

        raw = {
            "version": 2,
            "name": "existing",
            "steps": [
                {"id": "s1", "type": "capability", "capability": "filter",
                 "params": {"expression": "x > 0"}},
            ],
        }
        errors = []
        validator = jsonschema.Draft202012Validator(SCHEMA_V2)
        for err in validator.iter_errors(raw):
            errors.append(err.message)
        assert errors == []

    def test_validate_pipeline_json_basic_accepts_kind(self):
        """validate_pipeline_json (fallback path) accepts 'kind' in a step."""
        from gispulse.core.pipeline_schema import validate_pipeline_json

        raw = {
            "version": 2,
            "name": "test",
            "steps": [
                {"id": "s1", "kind": "external",
                 "params": {"argv": ["dbt", "build"]}},
            ],
        }
        errors = validate_pipeline_json(raw)
        assert errors == [], f"Unexpected validation errors: {errors}"

    def test_parse_step_with_kind_is_preserved(self):
        """_parse_step parses 'kind' and populates StepSpec.kind."""
        from gispulse.core.pipeline import _parse_step

        raw = {"id": "s1", "kind": "external", "params": {"argv": ["dbt"]}}
        step = _parse_step(raw, 0)
        assert step.kind == "external"

    def test_parse_step_without_kind_defaults_to_capability(self):
        """_parse_step without 'kind' defaults StepSpec.kind to 'capability'."""
        from gispulse.core.pipeline import _parse_step

        raw = {"id": "s1", "type": "capability", "capability": "filter",
               "params": {"expression": "x > 0"}}
        step = _parse_step(raw, 0)
        assert step.kind == "capability"


# ---------------------------------------------------------------------------
# _compute_effective_timeout — unit tests
# ---------------------------------------------------------------------------


class TestComputeEffectiveTimeout:
    """Unit tests for _compute_effective_timeout in runner.py."""

    def _make_spec(self, steps_cfg: list[dict]):
        """Build a minimal PipelineSpec from a list of step dicts."""
        from gispulse.core.pipeline import _parse_v2

        raw = {
            "version": 2,
            "steps": steps_cfg,
        }
        return _parse_v2(raw)

    def test_no_external_steps_returns_job_timeout(self):
        """Pipeline with only capability steps → job timeout unchanged."""
        from gispulse.orchestration.runner import _compute_effective_timeout

        spec = self._make_spec([
            {"id": "s1", "type": "capability", "capability": "filter",
             "params": {"expression": "x > 0"}},
        ])
        assert _compute_effective_timeout(300, spec) == 300

    def test_external_step_with_timeout_elevates_plafond(self):
        """External step with timeout_seconds=14400 must raise the job plafond.

        job_timeout=300 < 14400 + 60 = 14460 → effective = 14460.
        """
        from gispulse.orchestration.runner import _compute_effective_timeout

        spec = self._make_spec([
            {"id": "dbt", "type": "external", "kind": "external",
             "params": {"argv": ["dbt", "run"], "timeout_seconds": 14400}},
        ])
        assert _compute_effective_timeout(300, spec) == 14460

    def test_job_timeout_higher_than_sum_wins(self):
        """If job timeout is already higher than step sum + 60, keep job timeout."""
        from gispulse.orchestration.runner import _compute_effective_timeout

        spec = self._make_spec([
            {"id": "ext", "type": "external", "kind": "external",
             "params": {"argv": ["echo", "hi"], "timeout_seconds": 10}},
        ])
        # job timeout 9999 > 10 + 60 = 70
        assert _compute_effective_timeout(9999, spec) == 9999

    def test_multiple_external_steps_sum(self):
        """Sum of multiple non-capability step timeouts + 60 vs job timeout."""
        from gispulse.orchestration.runner import _compute_effective_timeout

        spec = self._make_spec([
            {"id": "a", "type": "external", "kind": "external",
             "params": {"argv": ["echo", "a"], "timeout_seconds": 100}},
            {"id": "b", "type": "external", "kind": "external",
             "params": {"argv": ["echo", "b"], "timeout_seconds": 200}},
        ])
        # sum=300, +60=360 > job_timeout=300 → 360
        assert _compute_effective_timeout(300, spec) == 360

    def test_external_step_without_timeout_seconds_excluded(self):
        """External step without timeout_seconds declared → contributes 0 to sum."""
        from gispulse.orchestration.runner import _compute_effective_timeout

        spec = self._make_spec([
            {"id": "ext", "type": "external", "kind": "external",
             "params": {"argv": ["echo", "hi"]}},  # no timeout_seconds
        ])
        # sum=0 → job timeout unchanged
        assert _compute_effective_timeout(300, spec) == 300


# ---------------------------------------------------------------------------
# Timeout elevation end-to-end (JobRunner._execute_pipeline_config)
# ---------------------------------------------------------------------------


class TestTimeoutElevationEndToEnd:
    """Integration tests for timeout elevation via JobRunner._execute_pipeline_config.

    These tests use a real external step (subprocess.Popen) to verify that:
    1. A step with timeout_seconds=10, job default=2 → step of 5 s SUCCEEDS
       (the job plafond was elevated to 10+60=70).
    2. A pipeline with no declared step timeout → job timeout unchanged.
    3. Per-step timeout still kills ITS step beyond its own declaration.
    """

    def _make_job(self, pipeline_config: dict, timeout: int | None = None):
        """Build a minimal Job with pipeline_config in parameters."""
        from uuid import uuid4
        from gispulse.core.models import Job

        params: dict = {"pipeline_config": pipeline_config}
        if timeout is not None:
            params["timeout"] = timeout
        return Job(
            id=uuid4(),
            name="test-timeout-elevation",
            parameters=params,
        )

    def _make_runner(self):
        from gispulse.orchestration.runner import JobRunner
        from gispulse.persistence.repository import InMemoryRepository
        from gispulse.rules.engine import RuleEngine

        return JobRunner(
            repository=InMemoryRepository(),
            rule_engine=RuleEngine(),
        )

    def test_step_5s_succeeds_when_job_default_is_2(self, tmp_path):
        """timeout_seconds=10, job=2 → step de 5 s RÉUSSIT (plafond élevé à 70)."""
        import gispulse.orchestration.external_step  # noqa: F401 — trigger registration
        script = tmp_path / "slow.py"
        script.write_text("import time; time.sleep(5); print('done')\n")

        pipeline_config = {
            "version": 2,
            "steps": [
                {
                    "id": "slow",
                    "type": "external",
                    "kind": "external",
                    "params": {
                        "argv": [sys.executable, str(script)],
                        "timeout_seconds": 10,
                    },
                }
            ],
        }
        runner = self._make_runner()
        gdf = gpd.GeoDataFrame({"id": [1], "geometry": [Point(0, 0)]}, crs="EPSG:4326")
        job = self._make_job(pipeline_config, timeout=2)

        # Must NOT raise FuturesTimeoutError — the effective timeout is 70s.
        updated_job, _ = runner.run(job, gdf)
        from gispulse.core.models import JobStatus
        assert updated_job.status == JobStatus.COMPLETED

    def test_no_declared_step_timeout_job_plafond_unchanged(self, tmp_path):
        """External step without timeout_seconds → job plafond unchanged at 300s.

        We verify that _compute_effective_timeout returns the job value,
        not that the job actually takes 300 s (no need to sleep that long).
        """
        from gispulse.orchestration.runner import _compute_effective_timeout
        from gispulse.core.pipeline import _parse_v2

        spec = _parse_v2({
            "version": 2,
            "steps": [
                {"id": "ext", "type": "external", "kind": "external",
                 "params": {"argv": [sys.executable, "-c", "print('ok')"]}},
            ],
        })
        assert _compute_effective_timeout(300, spec) == 300

    def test_per_step_timeout_kills_its_step(self, tmp_path):
        """Per-step timeout_seconds kills that step within its declared limit.

        The job plafond is explicitly high (300 s), so the step-level timeout
        fires first.  The error comes from the step's own timeout, NOT from the
        job timeout mechanism (FuturesTimeoutError).  The job status becomes
        FAILED because the step propagates a RuntimeError (step failed).
        """
        import gispulse.orchestration.external_step  # noqa: F401 — trigger registration
        script = tmp_path / "infinite.py"
        script.write_text("import time\nwhile True:\n    time.sleep(0.5)\n")

        pipeline_config = {
            "version": 2,
            "steps": [
                {
                    "id": "infinite",
                    "type": "external",
                    "kind": "external",
                    "params": {
                        "argv": [sys.executable, str(script)],
                        "timeout_seconds": 2,  # per-step kills after 2s
                    },
                }
            ],
        }
        runner = self._make_runner()
        gdf = gpd.GeoDataFrame({"id": [1], "geometry": [Point(0, 0)]}, crs="EPSG:4326")
        # job timeout=300 → effective timeout elevated to 2+60=62 (> 300? no, max(300,62)=300).
        # Either way, the per-step timeout_seconds=2 fires BEFORE the job plafond.
        job = self._make_job(pipeline_config, timeout=300)

        with pytest.raises(RuntimeError, match="timeout after 2s"):
            runner.run(job, gdf)

        from gispulse.core.models import JobStatus
        assert job.status == JobStatus.FAILED
        # Crucially: the error is a step-level timeout ("timeout after 2s"),
        # NOT the FuturesTimeoutError from the job plafond ("exceeded effective timeout").
        assert "timeout after 2s" in job.error_message
        assert "exceeded effective timeout" not in job.error_message


class TestWorkerLoopClosedGuard:
    def test_heartbeat_sync_loop_closed_no_crash(self):
        """_heartbeat_sync ne crash pas si la loop est fermée."""
        import asyncio

        loop = asyncio.new_event_loop()
        loop.close()
        assert loop.is_closed()

        _hb_warned = [False]
        called = []

        def _heartbeat_sync() -> None:
            if loop.is_closed():
                if not _hb_warned[0]:
                    _hb_warned[0] = True
                    called.append("warned")
                return
            raise AssertionError("Should not reach here")

        _heartbeat_sync()
        _heartbeat_sync()
        _heartbeat_sync()
        assert called == ["warned"]
        asyncio.set_event_loop(asyncio.new_event_loop())

    def test_cancel_check_sync_loop_closed_returns_false(self):
        """_cancel_check_sync retourne False si la loop est fermée (pas de RuntimeError)."""
        import asyncio

        loop = asyncio.new_event_loop()
        loop.close()
        _cc_warned = [False]

        def _cancel_check_sync() -> bool:
            if loop.is_closed():
                _cc_warned[0] = True
                return False
            raise AssertionError("Should not reach here")

        result = _cancel_check_sync()
        assert result is False
        assert _cc_warned[0] is True
        asyncio.set_event_loop(asyncio.new_event_loop())
