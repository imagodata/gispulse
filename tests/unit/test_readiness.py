"""Tests for gispulse.core.readiness.

All fixtures are pure (tmp_path for parquet templates, plain dicts for context).
No external I/O besides file system via tmp_path.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from gispulse.core.readiness import (
    GuardMode,
    ProbeContext,
    ProbeResult,
    ProbeSpec,
    ReadinessReport,
    ReadinessSpec,
    _PROBE_REGISTRY,
    probe_source,
    register_probe_kind,
    resolve_status,
)


# ---------------------------------------------------------------------------
# Registry isolation: snapshot before each test, restore after
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolate_probe_registry() -> Any:
    """Prevent test-registered kinds from leaking into subsequent tests."""
    snapshot = dict(_PROBE_REGISTRY)
    yield
    _PROBE_REGISTRY.clear()
    _PROBE_REGISTRY.update(snapshot)


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _spec(name: str, kind: str, **probe_kwargs: Any) -> ReadinessSpec:
    return ReadinessSpec(name=name, probe=ProbeSpec(kind=kind, **probe_kwargs))


def _static_spec(name: str, *, ready: bool = True) -> ReadinessSpec:
    return _spec(
        name,
        "static",
        metadata={"ready": ready, "status": "present" if ready else "missing"},
    )


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class TestRegistry:
    def test_register_and_dispatch(self) -> None:
        def _my_probe(spec: ReadinessSpec, scope: str, ctx: ProbeContext) -> ProbeResult:
            return ProbeResult(ready=True, status="ok_test")

        register_probe_kind("_test_custom_kind_a", _my_probe, replace=True)
        spec = _spec("src", "_test_custom_kind_a")
        result = probe_source(spec, "42", ProbeContext())
        assert result.ready is True
        assert result.status == "ok_test"

    def test_double_registration_raises(self) -> None:
        def _fn(s: Any, sc: Any, c: Any) -> ProbeResult:
            return ProbeResult(ready=True, status="x")

        register_probe_kind("_test_double_reg", _fn, replace=True)
        with pytest.raises(ValueError, match="already registered"):
            register_probe_kind("_test_double_reg", _fn, replace=False)

    def test_replace_true_overwrites(self) -> None:
        def _v1(s: Any, sc: Any, c: Any) -> ProbeResult:
            return ProbeResult(ready=False, status="v1")

        def _v2(s: Any, sc: Any, c: Any) -> ProbeResult:
            return ProbeResult(ready=True, status="v2")

        register_probe_kind("_test_replace_kind", _v1, replace=True)
        register_probe_kind("_test_replace_kind", _v2, replace=True)
        spec = _spec("x", "_test_replace_kind")
        result = probe_source(spec, "s", ProbeContext())
        assert result.status == "v2"

    def test_unknown_kind_returns_probe_error_not_exception(self) -> None:
        """An unregistered kind must yield probe_error, not raise."""
        spec = _spec("src", "kind_that_does_not_exist_42")
        result = probe_source(spec, "01", ProbeContext())
        assert result.ready is False
        assert result.status == "probe_error"
        assert "unknown probe kind" in result.message


# ---------------------------------------------------------------------------
# parquet_template
# ---------------------------------------------------------------------------


class TestParquetTemplate:
    def test_present_via_explicit_template(self, tmp_path: Path) -> None:
        parquet = tmp_path / "iris-63.parquet"
        parquet.touch()
        spec = _spec(
            "iris",
            "parquet_template",
            env_names=("IRIS_PARQUET_TEMPLATE",),
        )
        ctx = ProbeContext(templates={"IRIS_PARQUET_TEMPLATE": str(tmp_path / "iris-{scope}.parquet")})
        result = probe_source(spec, "63", ctx)
        assert result.ready is True
        assert result.status == "present"

    def test_missing_file_returns_not_ready(self, tmp_path: Path) -> None:
        spec = _spec(
            "iris",
            "parquet_template",
            env_names=("IRIS_PARQUET_TEMPLATE",),
        )
        ctx = ProbeContext(templates={"IRIS_PARQUET_TEMPLATE": str(tmp_path / "iris-{scope}.parquet")})
        result = probe_source(spec, "99", ctx)
        assert result.ready is False
        assert result.status == "missing"

    def test_fallback_env_var(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        parquet = tmp_path / "dpe-75.parquet"
        parquet.touch()
        monkeypatch.setenv("DPE_PARQUET_TEMPLATE", str(tmp_path / "dpe-{scope}.parquet"))
        spec = _spec(
            "dpe",
            "parquet_template",
            env_names=("DPE_PARQUET_TEMPLATE",),
        )
        result = probe_source(spec, "75", ProbeContext())
        assert result.ready is True

    def test_default_template_used_when_no_env_no_override(self, tmp_path: Path) -> None:
        parquet = tmp_path / "gpu-13.parquet"
        parquet.touch()
        spec = _spec(
            "gpu",
            "parquet_template",
            env_names=("GPU_PARQUET_TEMPLATE",),
            default_template=str(tmp_path / "gpu-{scope}.parquet"),
        )
        # No templates and no env var set → must fall through to default_template
        result = probe_source(spec, "13", ProbeContext(env={}))
        assert result.ready is True

    def test_priority_templates_over_env(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """context.templates must win over env var."""
        env_dir = tmp_path / "env"
        env_dir.mkdir()
        tpl_dir = tmp_path / "tpl"
        tpl_dir.mkdir()
        (tpl_dir / "x-42.parquet").touch()
        # env var points at a directory that does NOT have the file
        monkeypatch.setenv("X_PARQUET_TEMPLATE", str(env_dir / "x-{scope}.parquet"))
        spec = _spec(
            "x",
            "parquet_template",
            env_names=("X_PARQUET_TEMPLATE",),
        )
        ctx = ProbeContext(templates={"X_PARQUET_TEMPLATE": str(tpl_dir / "x-{scope}.parquet")})
        result = probe_source(spec, "42", ctx)
        assert result.ready is True

    def test_no_env_no_default_returns_missing(self) -> None:
        spec = _spec(
            "orphan",
            "parquet_template",
            env_names=("ORPHAN_TEMPLATE",),
        )
        result = probe_source(spec, "01", ProbeContext(env={}))
        assert result.ready is False
        assert "ORPHAN_TEMPLATE" in result.message

    def test_scope_substitution_dept_alias(self, tmp_path: Path) -> None:
        """{dept} placeholder must be substituted like {scope}."""
        parquet = tmp_path / "dvf-33.parquet"
        parquet.touch()
        spec = _spec(
            "dvf",
            "parquet_template",
            env_names=("DVF_PARQUET_TEMPLATE",),
        )
        ctx = ProbeContext(templates={"DVF_PARQUET_TEMPLATE": str(tmp_path / "dvf-{dept}.parquet")})
        result = probe_source(spec, "33", ctx)
        assert result.ready is True

    def test_scope_substitution_departement_alias(self, tmp_path: Path) -> None:
        """{departement} placeholder must also work (full-word alias)."""
        parquet = tmp_path / "iris-44.parquet"
        parquet.touch()
        spec = _spec(
            "iris",
            "parquet_template",
            env_names=("IRIS_PARQUET_TEMPLATE",),
        )
        ctx = ProbeContext(
            templates={"IRIS_PARQUET_TEMPLATE": str(tmp_path / "iris-{departement}.parquet")}
        )
        result = probe_source(spec, "44", ctx)
        assert result.ready is True

    def test_glob_pattern_present(self, tmp_path: Path) -> None:
        (tmp_path / "sup-63-foo.parquet").touch()
        spec = _spec(
            "sup",
            "parquet_template",
            env_names=("SUP_PARQUET_TEMPLATE",),
        )
        ctx = ProbeContext(templates={"SUP_PARQUET_TEMPLATE": str(tmp_path / "sup-{scope}-*.parquet")})
        result = probe_source(spec, "63", ctx)
        assert result.ready is True

    def test_observed_at_populated(self, tmp_path: Path) -> None:
        (tmp_path / "src-01.parquet").touch()
        spec = _spec(
            "src",
            "parquet_template",
            env_names=("SRC_TEMPLATE",),
        )
        ctx = ProbeContext(templates={"SRC_TEMPLATE": str(tmp_path / "src-{scope}.parquet")})
        result = probe_source(spec, "01", ctx)
        assert result.observed_at is not None

    def test_wildcard_scope_on_exact_template_is_not_ready(self, tmp_path: Path) -> None:
        """scope='*' must not glob-match arbitrary files when template has no glob chars."""
        # Create a file that a naive glob of '*' would match
        (tmp_path / "iris-anything.parquet").touch()
        spec = _spec(
            "iris",
            "parquet_template",
            env_names=("IRIS_PARQUET_TEMPLATE",),
        )
        # Template is exact (no glob chars) — scope '*' is a literal filename component
        ctx = ProbeContext(
            templates={"IRIS_PARQUET_TEMPLATE": str(tmp_path / "iris-{scope}.parquet")}
        )
        result = probe_source(spec, "*", ctx)
        # Path "iris-*.parquet" doesn't exist as a literal file → not ready
        assert result.ready is False

    def test_bracket_scope_on_glob_template_is_escaped(self, tmp_path: Path) -> None:
        """scope='[0-9]' in a glob template must match literally, not as a character class."""
        # Create a file that the escaped literal would need to match
        (tmp_path / "sup-[0-9]-foo.parquet").touch()
        spec = _spec(
            "sup",
            "parquet_template",
            env_names=("SUP_PARQUET_TEMPLATE",),
        )
        # Template has glob '*' → scope will be glob.escape'd
        ctx = ProbeContext(
            templates={"SUP_PARQUET_TEMPLATE": str(tmp_path / "sup-{scope}-*.parquet")}
        )
        # A file named "sup-[0-9]-foo.parquet" exists; glob.escape("[0-9]") = "[[]0-9]"
        # so the expanded pattern is "sup-[[]0-9]-*.parquet" which matches the literal name
        result = probe_source(spec, "[0-9]", ctx)
        assert result.ready is True


# ---------------------------------------------------------------------------
# stage_status
# ---------------------------------------------------------------------------


@dataclass
class _FakeItem:
    source: str
    departement: str
    entry: str = ""
    source_revision_token: str = ""


@dataclass
class _FakeStatus:
    item: _FakeItem
    status: str


class TestStageStatus:
    def _make_ctx(self, *statuses: _FakeStatus) -> ProbeContext:
        return ProbeContext(stage_statuses={str(i): s for i, s in enumerate(statuses)})

    def test_green_entry_returns_ready(self) -> None:
        ctx = self._make_ctx(_FakeStatus(_FakeItem("dvf", "63"), "success"))
        spec = _spec("dvf", "stage_status")
        result = probe_source(spec, "63", ctx)
        assert result.ready is True
        assert result.status == "present"

    def test_failed_entry_returns_not_ready(self) -> None:
        ctx = self._make_ctx(_FakeStatus(_FakeItem("dvf", "63"), "error"))
        spec = _spec("dvf", "stage_status")
        result = probe_source(spec, "63", ctx)
        assert result.ready is False

    def test_missing_source_in_stage(self) -> None:
        ctx = self._make_ctx(_FakeStatus(_FakeItem("cadastre", "63"), "success"))
        spec = _spec("dvf", "stage_status")
        result = probe_source(spec, "63", ctx)
        assert result.ready is False
        assert "no stage entries" in result.message

    def test_missing_required_entries(self) -> None:
        ctx = self._make_ctx(
            _FakeStatus(_FakeItem("cadastre", "63", entry="parcelles_bulk"), "success")
        )
        # required_entries lives in ProbeSpec.metadata (probe parameters belong to the probe)
        spec = ReadinessSpec(
            name="cadastre",
            probe=ProbeSpec(
                kind="stage_status",
                metadata={"required_entries": ["parcelles_bulk", "sections_bulk", "communes_bulk"]},
            ),
        )
        result = probe_source(spec, "63", ctx)
        assert result.ready is False
        assert "sections_bulk" in result.message or "communes_bulk" in result.message

    def test_all_required_entries_present(self) -> None:
        ctx = self._make_ctx(
            _FakeStatus(_FakeItem("cadastre", "63", entry="parcelles_bulk"), "success"),
            _FakeStatus(_FakeItem("cadastre", "63", entry="sections_bulk"), "success"),
            _FakeStatus(_FakeItem("cadastre", "63", entry="communes_bulk"), "skipped"),
        )
        spec = ReadinessSpec(
            name="cadastre",
            probe=ProbeSpec(
                kind="stage_status",
                metadata={"required_entries": ["parcelles_bulk", "sections_bulk", "communes_bulk"]},
            ),
        )
        result = probe_source(spec, "63", ctx)
        assert result.ready is True

    def test_empty_stage_statuses(self) -> None:
        ctx = ProbeContext(stage_statuses={})
        spec = _spec("dvf", "stage_status")
        result = probe_source(spec, "63", ctx)
        assert result.ready is False

    def test_no_stage_statuses_in_context(self) -> None:
        spec = _spec("dvf", "stage_status")
        result = probe_source(spec, "63", ProbeContext())
        assert result.ready is False

    def test_skipped_counts_as_green(self) -> None:
        ctx = self._make_ctx(_FakeStatus(_FakeItem("rnb", "75"), "skipped"))
        spec = _spec("rnb", "stage_status")
        result = probe_source(spec, "75", ctx)
        assert result.ready is True

    def test_version_extracted_from_source_revision_token(self) -> None:
        item = _FakeItem("dvf", "13", source_revision_token="2024-Q3")
        ctx = self._make_ctx(_FakeStatus(item, "success"))
        spec = _spec("dvf", "stage_status")
        result = probe_source(spec, "13", ctx)
        assert result.version == "2024-Q3"


# ---------------------------------------------------------------------------
# static
# ---------------------------------------------------------------------------


class TestStatic:
    def test_default_ready(self) -> None:
        spec = ReadinessSpec(
            name="always_ready",
            probe=ProbeSpec(kind="static"),
        )
        result = probe_source(spec, "any", ProbeContext())
        assert result.ready is True
        assert result.status == "static"

    def test_explicitly_not_ready(self) -> None:
        spec = ReadinessSpec(
            name="never_ready",
            probe=ProbeSpec(
                kind="static",
                metadata={"ready": False, "status": "disabled", "message": "turned off"},
            ),
        )
        result = probe_source(spec, "any", ProbeContext())
        assert result.ready is False
        assert result.status == "disabled"
        assert result.message == "turned off"


# ---------------------------------------------------------------------------
# Overrides
# ---------------------------------------------------------------------------


class TestOverrides:
    def test_override_bypasses_probe(self) -> None:
        override = ProbeResult(ready=True, status="override_ok", message="manual")
        ctx = ProbeContext(probe_overrides={("src", "63"): override})
        spec = _spec("src", "parquet_template", env_names=("MISSING_VAR",))
        result = probe_source(spec, "63", ctx)
        assert result is override

    def test_override_key_is_name_scope(self) -> None:
        override = ProbeResult(ready=False, status="forced_missing")
        ctx = ProbeContext(probe_overrides={("mydb", "test-scope"): override})
        spec = _spec("mydb", "static", metadata={"ready": True})
        result = probe_source(spec, "test-scope", ctx)
        assert result is override

    def test_override_does_not_affect_other_scope(self) -> None:
        override = ProbeResult(ready=False, status="forced")
        ctx = ProbeContext(probe_overrides={("src", "63"): override})
        spec = _spec("src", "static", metadata={"ready": True, "status": "present"})
        result = probe_source(spec, "75", ctx)  # different scope
        assert result.ready is True


# ---------------------------------------------------------------------------
# resolve_status — strict mode
# ---------------------------------------------------------------------------


class TestResolveStatusStrict:
    def test_all_ready_strict_ok(self, tmp_path: Path) -> None:
        (tmp_path / "a-01.parquet").touch()
        spec = _spec("a", "parquet_template", env_names=("A_TPL",))
        ctx = ProbeContext(templates={"A_TPL": str(tmp_path / "a-{scope}.parquet")})
        report = resolve_status([spec], ["01"], ctx, mode="strict")
        assert report.ok is True
        assert report.errors == []

    def test_missing_source_strict_error(self, tmp_path: Path) -> None:
        spec = _spec("b", "parquet_template", env_names=("B_TPL",))
        ctx = ProbeContext(templates={"B_TPL": str(tmp_path / "b-{scope}.parquet")})
        report = resolve_status([spec], ["01"], ctx, mode="strict")
        assert report.ok is False
        assert len(report.errors) == 1
        assert "b" in report.errors[0]

    def test_multi_scope_all_errors_collected(self, tmp_path: Path) -> None:
        spec = _spec("c", "parquet_template", env_names=("C_TPL",))
        ctx = ProbeContext(templates={"C_TPL": str(tmp_path / "c-{scope}.parquet")})
        report = resolve_status([spec], ["01", "63", "75"], ctx, mode="strict")
        assert not report.ok
        assert len(report.errors) == 3

    def test_results_row_per_source_per_scope(self, tmp_path: Path) -> None:
        s1 = _static_spec("s1")
        s2 = _static_spec("s2")
        report = resolve_status([s1, s2], ["01", "63"], mode="strict")
        assert len(report.results) == 4
        sources = {r.source for r in report.results}
        scopes = {r.scope for r in report.results}
        assert sources == {"s1", "s2"}
        assert scopes == {"01", "63"}


# ---------------------------------------------------------------------------
# resolve_status — warn mode
# ---------------------------------------------------------------------------


class TestResolveStatusWarn:
    def test_missing_in_warn_is_warning_not_error(self, tmp_path: Path) -> None:
        spec = _spec("d", "parquet_template", env_names=("D_TPL",))
        ctx = ProbeContext(templates={"D_TPL": str(tmp_path / "d-{scope}.parquet")})
        report = resolve_status([spec], ["01"], ctx, mode="warn")
        assert report.ok is True
        assert len(report.warnings) == 1
        assert report.errors == []

    def test_partially_present_warn(self, tmp_path: Path) -> None:
        (tmp_path / "e-01.parquet").touch()
        spec = _spec("e", "parquet_template", env_names=("E_TPL",))
        ctx = ProbeContext(templates={"E_TPL": str(tmp_path / "e-{scope}.parquet")})
        report = resolve_status([spec], ["01", "63"], ctx, mode="warn")
        assert report.ok is True
        assert len(report.warnings) == 1  # only 63 is missing
        assert len(report.results) == 2


# ---------------------------------------------------------------------------
# resolve_status — off mode
# ---------------------------------------------------------------------------


class TestResolveStatusOff:
    def test_off_is_always_ok(self) -> None:
        spec = ReadinessSpec(
            name="broken",
            probe=ProbeSpec(kind="static", metadata={"ready": False}),
        )
        report = resolve_status([spec], ["01", "63"], mode="off")
        assert report.ok is True
        assert report.errors == []
        assert report.warnings == []
        assert report.results == []  # no probing done

    def test_off_mode_string_in_manifest(self) -> None:
        report = resolve_status([], [], mode="off")
        manifest = report.to_manifest()
        assert manifest["status"] == "off"

    def test_invalid_mode_raises(self) -> None:
        with pytest.raises(ValueError, match="mode must be one of"):
            resolve_status([], [], mode="invalid")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# to_manifest / JSON serialisability
# ---------------------------------------------------------------------------


class TestToManifest:
    def _report_for(self, mode: GuardMode, ready: bool, tmp_path: Path) -> ReadinessReport:
        spec = ReadinessSpec(
            name="src",
            probe=ProbeSpec(kind="static", metadata={"ready": ready, "status": "present" if ready else "missing"}),
        )
        return resolve_status([spec], ["01"], mode=mode)

    def test_manifest_ok(self, tmp_path: Path) -> None:
        report = self._report_for("strict", True, tmp_path)
        m = report.to_manifest()
        assert m["status"] == "ok"
        assert m["ok"] is True

    def test_manifest_failed(self, tmp_path: Path) -> None:
        report = self._report_for("strict", False, tmp_path)
        m = report.to_manifest()
        assert m["status"] == "failed"
        assert m["ok"] is False

    def test_manifest_warn(self, tmp_path: Path) -> None:
        report = self._report_for("warn", False, tmp_path)
        m = report.to_manifest()
        assert m["status"] == "warn"
        assert m["ok"] is True

    def test_manifest_off(self) -> None:
        report = resolve_status([], [], mode="off")
        assert report.to_manifest()["status"] == "off"

    def test_manifest_json_serialisable(self, tmp_path: Path) -> None:
        """json.dumps must not raise on any manifest."""
        (tmp_path / "q-63.parquet").touch()
        spec = _spec("q", "parquet_template", env_names=("Q_TPL",))
        ctx = ProbeContext(templates={"Q_TPL": str(tmp_path / "q-{scope}.parquet")})
        report = resolve_status([spec], ["63", "75"], ctx, mode="warn")
        raw = json.dumps(report.to_manifest())
        parsed = json.loads(raw)
        assert parsed["mode"] == "warn"
        # 63 present, 75 missing → one warning
        assert len(parsed["warnings"]) == 1

    def test_manifest_sources_contain_all_results(self, tmp_path: Path) -> None:
        s1 = _static_spec("s1")
        s2 = _static_spec("s2", ready=False)
        report = resolve_status([s1, s2], ["01"], mode="warn")
        m = report.to_manifest()
        sources = {row["source"] for row in m["sources"]}
        assert sources == {"s1", "s2"}

    def test_manifest_details_path_objects_coerced(self, tmp_path: Path) -> None:
        """details containing Path objects must be coerced to str in to_manifest()."""
        artifact = tmp_path / "p-01.parquet"
        artifact.touch()

        # Register a probe kind that intentionally returns a Path in details
        def _probe_with_path_detail(
            spec: ReadinessSpec, scope: str, ctx: ProbeContext
        ) -> ProbeResult:
            return ProbeResult(
                ready=False,
                status="missing",
                message="test probe",
                details={"path": artifact, "nested": {"also": artifact}},
            )

        register_probe_kind("_test_path_detail", _probe_with_path_detail)
        spec = _spec("p", "_test_path_detail")
        report = resolve_status([spec], ["01"], mode="strict")
        manifest = report.to_manifest()
        # Must not raise
        raw = json.dumps(manifest)
        parsed = json.loads(raw)
        # The Path must have been stringified
        found_paths = [
            row["details"]["path"]
            for row in parsed["sources"]
            if "path" in row.get("details", {})
        ]
        assert found_paths, "details.path not present in manifest sources"
        assert found_paths[0] == str(artifact)
        assert parsed["sources"][0]["details"]["nested"]["also"] == str(artifact)


# ---------------------------------------------------------------------------
# Multi-scopes edge cases
# ---------------------------------------------------------------------------


class TestMultiScope:
    def test_empty_specs_gives_empty_report(self) -> None:
        report = resolve_status([], ["01", "63"], mode="strict")
        assert report.ok is True
        assert report.results == []

    def test_empty_scopes_gives_empty_report(self) -> None:
        spec = _static_spec("src")
        report = resolve_status([spec], [], mode="strict")
        assert report.ok is True
        assert report.results == []

    def test_five_scopes_four_sources(self, tmp_path: Path) -> None:
        specs = [_static_spec(f"src{i}") for i in range(4)]
        scopes = [f"0{i}" for i in range(5)]
        report = resolve_status(specs, scopes, mode="strict")
        assert len(report.results) == 20
        assert report.ok is True
