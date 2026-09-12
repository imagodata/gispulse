"""Tests TDD — manifest v3 steps non-capability + select optionnel (issue #453).

Volets :
A. Compilation d'un transform item avec clé réservée ``kind``
B. ``select`` optionnel pour les modèles 100 % non-capability
C. Exécution bout-en-bout run_manifest d'un modèle external sans select
D. Modèle downstream référençant un modèle sans select → erreur claire
E. /manifests/validate accepte le manifest external
F. steps_filter interaction
G. POST /manifests/run 202 bout-en-bout
"""

from __future__ import annotations

import sys

import pytest


# ---------------------------------------------------------------------------
# A. Compilation : transform item avec clé ``kind``
# ---------------------------------------------------------------------------


class TestCompileKindStep:
    """_split_transform / _compile_model_steps avec un item non-capability."""

    def _compile(self, item: dict, model_name: str = "m", source_name: str = "s"):
        """Helper : compile un seul transform item vers une liste de StepSpec."""
        from gispulse.core.manifest_v3 import _compile_model_steps, ModelSpec, SourceSpec

        model = ModelSpec(
            name=model_name,
            select=source_name,
            transform=[item],
        )
        sources = {source_name: SourceSpec(name=source_name, uri="memory://s")}
        return _compile_model_steps(model, sources, {model_name})

    def test_kind_external_produces_step_with_kind(self):
        """Un item avec kind=external doit produire un StepSpec(kind='external')."""
        steps = self._compile({
            "shell_pipeline": {
                "kind": "external",
                "argv": ["python", "run.py"],
                "timeout_seconds": 60,
            }
        })
        assert len(steps) == 1
        s = steps[0]
        assert s.kind == "external"
        assert s.type == "external"
        assert s.capability == ""
        assert "argv" in s.params
        assert "kind" not in s.params  # clé réservée retirée des params
        assert s.params["timeout_seconds"] == 60

    def test_kind_external_id_equals_model_name_for_single_step(self):
        """Le dernier (et unique) step = model name."""
        steps = self._compile({
            "shell_pipeline": {
                "kind": "external",
                "argv": ["uv", "run", "python", "ingest.py"],
            }
        })
        assert steps[0].id == "m"

    def test_kind_external_intermediate_id(self):
        """Avec 2 transforms dont le 1er non-capability, id intermédiaire = m__t0."""
        from gispulse.core.manifest_v3 import _compile_model_steps, ModelSpec, SourceSpec

        model = ModelSpec(
            name="m",
            select="s",
            transform=[
                {"shell_step": {"kind": "external", "argv": ["step1.py"]}},
                {"shell_step2": {"kind": "external", "argv": ["step2.py"]}},
            ],
        )
        sources = {"s": SourceSpec(name="s", uri="memory://s")}
        steps = _compile_model_steps(model, sources, {"m"})
        assert steps[0].id == "m__t0"
        assert steps[1].id == "m"

    def test_kind_capability_explicit_keeps_capability_path(self):
        """kind: capability explicite → comportement actuel (capability = nom de l'item)."""
        steps = self._compile({
            "filter": {
                "kind": "capability",
                "expression": "val > 0",
            }
        })
        assert steps[0].kind == "capability"
        assert steps[0].capability == "filter"
        assert steps[0].type == "capability"
        assert "kind" not in steps[0].params
        assert "expression" in steps[0].params

    def test_kind_absent_keeps_capability_path(self):
        """Pas de clé kind → comportement actuel (capability)."""
        steps = self._compile({
            "filter": {"expression": "val > 0"}
        })
        assert steps[0].kind == "capability"
        assert steps[0].capability == "filter"

    def test_kind_empty_string_raises_value_error(self):
        """kind='' (vide) → ValueError (F3 : pas de normalisation silencieuse)."""
        from gispulse.core.manifest_v3 import _split_transform

        with pytest.raises(ValueError, match="non-empty"):
            _split_transform({"filter": {"kind": "", "expression": "val > 0"}})

    def test_with_on_noncapability_raises(self):
        """with: sur un item non-capability → ManifestValidationError explicite."""
        from gispulse.core.manifest_v3 import _compile_model_steps, ModelSpec, SourceSpec

        model = ModelSpec(
            name="m",
            select="s",
            transform=[
                {
                    "shell_pipeline": {
                        "kind": "external",
                        "argv": ["ingest.py"],
                        "with": "other",  # illégal sur non-capability
                    }
                }
            ],
        )
        sources = {
            "s": SourceSpec(name="s", uri="memory://s"),
            "other": SourceSpec(name="other", uri="memory://other"),
        }
        with pytest.raises(ValueError, match="with.*non.capability|non.capability.*with"):
            _compile_model_steps(model, sources, {"m"})

    def test_intra_model_chaining_preserved_for_two_external_steps(self):
        """Deux steps external : le second a input=m__t0 (chaînage intra-modèle)."""
        from gispulse.core.manifest_v3 import _compile_model_steps, ModelSpec, SourceSpec

        model = ModelSpec(
            name="m",
            select="s",
            transform=[
                {"step_a": {"kind": "external", "argv": ["a.py"]}},
                {"step_b": {"kind": "external", "argv": ["b.py"]}},
            ],
        )
        sources = {"s": SourceSpec(name="s", uri="memory://s")}
        steps = _compile_model_steps(model, sources, {"m"})
        # Le step_b (index 1, id=m) doit référencer step_a (m__t0)
        assert steps[1].input == "m__t0"

    def test_timeout_seconds_in_params(self):
        """timeout_seconds passé dans params (le handler external le lit de là)."""
        steps = self._compile({
            "shell_pipeline": {
                "kind": "external",
                "argv": ["run.py"],
                "timeout_seconds": 21600,
            }
        })
        assert steps[0].params.get("timeout_seconds") == 21600


# ---------------------------------------------------------------------------
# B. select optionnel pour les modèles 100 % non-capability
# ---------------------------------------------------------------------------


class TestSelectOptional:
    """ModelSpec.select peut être None quand tous les transforms sont non-capability."""

    def _build_manifest(self, model_dict: dict) -> object:
        """Helper : construit un ManifestV3 minimal via _parse_models."""
        from gispulse.core.manifest_v3 import _parse_models, ManifestV3

        # On injecte un modèle sans select
        models = _parse_models({"ext_model": model_dict})
        manifest = ManifestV3(
            sources={},
            models=models,
        )
        return manifest

    def test_parse_model_without_select_succeeds(self):
        """Un modèle sans select=... se parse sans lever d'exception."""
        from gispulse.core.manifest_v3 import _parse_models

        models = _parse_models({
            "ext_model": {
                "transform": [
                    {"shell_pipeline": {"kind": "external", "argv": ["run.py"]}}
                ]
            }
        })
        m = models["ext_model"]
        assert m.select is None

    def test_modelspec_select_defaults_to_none(self):
        """ModelSpec.select vaut None par défaut (optionnel)."""
        from gispulse.core.manifest_v3 import ModelSpec

        m = ModelSpec(name="x")
        assert m.select is None

    def test_validate_manifest_accepts_all_noncapability(self):
        """validate_manifest ne lève pas quand tous les steps sont non-capability sans select."""
        from gispulse.core.manifest_v3 import ManifestV3, ModelSpec, validate_manifest

        manifest = ManifestV3(
            sources={},
            models={
                "ext_model": ModelSpec(
                    name="ext_model",
                    select=None,
                    transform=[
                        {"shell_pipeline": {"kind": "external", "argv": ["run.py"]}}
                    ],
                )
            },
        )
        # Ne doit pas lever
        warnings = validate_manifest(manifest)
        assert isinstance(warnings, list)

    def test_validate_manifest_rejects_capability_without_select(self):
        """Un step capability sans select → ManifestValidationError explicite."""
        from gispulse.core.manifest_v3 import ManifestV3, ModelSpec, ManifestValidationError, validate_manifest

        manifest = ManifestV3(
            sources={"s": __import__("gispulse.core.manifest_v3", fromlist=["SourceSpec"]).SourceSpec(name="s", uri="m://s")},
            models={
                "bad": ModelSpec(
                    name="bad",
                    select=None,  # manquant mais transform capability
                    transform=[{"filter": {"expression": "val > 0"}}],
                )
            },
        )
        with pytest.raises(ManifestValidationError, match="select.*required|capability.*select"):
            validate_manifest(manifest)

    def test_validate_manifest_rejects_no_transform_no_select(self):
        """Modèle sans transform ET sans select → erreur (passthrough sans source)."""
        from gispulse.core.manifest_v3 import ManifestV3, ModelSpec, ManifestValidationError, validate_manifest

        manifest = ManifestV3(
            sources={},
            models={
                "bad": ModelSpec(
                    name="bad",
                    select=None,
                    transform=[],
                )
            },
        )
        with pytest.raises(ManifestValidationError):
            validate_manifest(manifest)

    def test_compile_noncapability_model_no_select(self):
        """compile_to_pipeline sur un modèle external sans select produit des steps valides."""
        from gispulse.core.manifest_v3 import ManifestV3, ModelSpec, compile_to_pipeline

        manifest = ManifestV3(
            sources={},
            models={
                "ext_model": ModelSpec(
                    name="ext_model",
                    select=None,
                    transform=[
                        {"shell_pipeline": {"kind": "external", "argv": ["run.py"]}}
                    ],
                )
            },
        )
        spec = compile_to_pipeline(manifest)
        assert len(spec.steps) == 1
        s = spec.steps[0]
        assert s.id == "ext_model"
        assert s.kind == "external"
        assert s.input is None  # pas de chaînage en amont

    def test_compile_first_step_input_none_without_select(self):
        """Premier step d'un modèle sans select a input=None."""
        from gispulse.core.manifest_v3 import ManifestV3, ModelSpec, compile_to_pipeline

        manifest = ManifestV3(
            sources={},
            models={
                "m": ModelSpec(
                    name="m",
                    select=None,
                    transform=[
                        {"s1": {"kind": "external", "argv": ["a.py"]}},
                        {"s2": {"kind": "external", "argv": ["b.py"]}},
                    ],
                )
            },
        )
        spec = compile_to_pipeline(manifest)
        assert spec.steps[0].input is None
        assert spec.steps[1].input == "m__t0"


# ---------------------------------------------------------------------------
# C. Exécution bout-en-bout run_manifest — modèle external sans select
# ---------------------------------------------------------------------------


class TestRunManifestExternal:
    """run_manifest avec un modèle 100 % external sans select."""

    def _make_manifest_external(self, script_content: str, model_name: str = "run_dept"):
        """Construit un ManifestV3 avec un seul modèle external qui exécute un script Python."""
        from gispulse.core.manifest_v3 import ManifestV3, ModelSpec

        return ManifestV3(
            name="test_external",
            sources={},
            models={
                model_name: ModelSpec(
                    name=model_name,
                    select=None,
                    transform=[
                        {
                            "shell_pipeline": {
                                "kind": "external",
                                "argv": [sys.executable, "-c", script_content],
                                "timeout_seconds": 30,
                            }
                        }
                    ],
                )
            },
        )

    def test_run_completed_status(self):
        """run_manifest d'un modèle external → execution_order contient le modèle."""
        from gispulse.runtime.manifest_runner import run_manifest

        manifest = self._make_manifest_external("print('hello')")
        result = run_manifest(manifest)
        assert "run_dept" in result.execution_order

    def test_run_no_data_materialized(self):
        """Un modèle external ne produit pas de GeoDataFrame — pas dans materialized."""
        from gispulse.runtime.manifest_runner import run_manifest

        manifest = self._make_manifest_external("import sys; sys.exit(0)")
        result = run_manifest(manifest)
        # Le modèle est dans executed_order mais materialized ne contient pas de
        # GeoDataFrame (le modèle external ne matérialise rien)
        assert "run_dept" not in result.materialized

    def test_run_step_artifacts_log_tail(self):
        """artifacts du step external contiennent log_tail."""
        from gispulse.runtime.manifest_runner import run_manifest

        class _SpySink:
            def __init__(self):
                self.events: list[tuple[str, dict]] = []
            def emit(self, t, d):
                self.events.append((t, d))

        manifest = self._make_manifest_external("print('output line')")
        spy = _SpySink()
        run_manifest(manifest, event_sink=spy)

        completed = [d for t, d in spy.events if t == "run.step.completed"]
        assert completed, "Aucun événement run.step.completed émis"
        artifacts = completed[-1].get("artifacts", {})
        assert "log_tail" in artifacts

    def test_run_failing_script_raises(self):
        """Un script qui retourne exit 1 → RuntimeError remontée par run_manifest."""
        from gispulse.runtime.manifest_runner import run_manifest

        manifest = self._make_manifest_external("import sys; sys.exit(1)")
        with pytest.raises(RuntimeError, match="failed"):
            run_manifest(manifest)

    def test_run_print_hello_completed(self):
        """Subprocess réel `python -c print` → run COMPLETED, no exception."""
        from gispulse.runtime.manifest_runner import run_manifest

        manifest = self._make_manifest_external("print('hello from external')")
        result = run_manifest(manifest)
        assert result.execution_order == ["run_dept"]


# ---------------------------------------------------------------------------
# D. Modèle downstream référençant un modèle sans select → erreur claire
# ---------------------------------------------------------------------------


class TestDownstreamNoSelectError:
    """Un modèle downstream qui sélectionne un modèle sans select (external) → erreur."""

    def test_downstream_select_raises_on_resolve(self):
        """_resolve d'un modèle external (sans données) → ValueError explicite."""
        from gispulse.core.manifest_v3 import ManifestV3, ModelSpec
        from gispulse.runtime.manifest_runner import run_manifest

        manifest = ManifestV3(
            sources={"s": __import__("gispulse.core.manifest_v3", fromlist=["SourceSpec"]).SourceSpec(name="s", uri="m://s")},
            models={
                "ext": ModelSpec(
                    name="ext",
                    select=None,
                    transform=[
                        {"shell": {"kind": "external", "argv": [sys.executable, "-c", "print('done')"]}}
                    ],
                ),
                "downstream": ModelSpec(
                    name="downstream",
                    select="ext",  # référence un modèle sans données
                    transform=[{"filter": {"expression": "val > 0"}}],
                ),
            },
        )

        import geopandas as gpd
        from shapely.geometry import Point

        def _loader(src):
            return gpd.GeoDataFrame(
                {"id": [1], "val": [10]},
                geometry=[Point(0, 0)],
                crs="EPSG:4326",
            )

        with pytest.raises((ValueError, RuntimeError, KeyError), match="no data|produces no data|external|non.capability"):
            run_manifest(manifest, source_loader=_loader)


# ---------------------------------------------------------------------------
# E. /manifests/validate accepte le manifest external
# ---------------------------------------------------------------------------


class TestManifestValidateEndpoint:
    """POST /manifests/validate avec un manifest external."""

    @pytest.fixture(autouse=True)
    def _disable_rate_limiter(self):
        from gispulse.adapters.http.rate_limit import limiter
        limiter.enabled = False

    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient
        from gispulse.adapters.http.app import create_app
        import warnings
        warnings.filterwarnings("ignore")
        app = create_app()
        with TestClient(app) as c:
            yield c

    def _external_manifest(self):
        return {
            "version": 3,
            "name": "ext_manifest",
            "sources": {},
            "models": {
                "run_dept_63": {
                    "transform": [
                        {
                            "shell_pipeline": {
                                "kind": "external",
                                "argv": [sys.executable, "-c", "print('ok')"],
                                "timeout_seconds": 21600,
                            }
                        }
                    ]
                }
            },
        }

    def test_validate_external_manifest_returns_valid(self, client):
        """POST /manifests/validate avec manifest external → valid=True."""
        resp = client.post(
            "/manifests/validate",
            json={"manifest": self._external_manifest()},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["valid"] is True, f"Errors: {body.get('errors')}"

    def test_validate_external_manifest_compiled_steps(self, client):
        """compiled_steps_count == 1 pour un manifest avec 1 modèle 1 transform."""
        resp = client.post(
            "/manifests/validate",
            json={"manifest": self._external_manifest()},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["compiled_steps_count"] == 1

    def test_validate_known_capabilities_not_checked_for_external(self, client):
        """Les steps non-capability ne sont pas soumis au check de capabilities."""
        # Un nom de step arbitraire non enregistré comme capability → OK pour external
        manifest = {
            "version": 3,
            "sources": {},
            "models": {
                "m": {
                    "transform": [
                        {
                            "completely_unknown_capability_xyz": {
                                "kind": "external",
                                "argv": ["run.py"],
                            }
                        }
                    ]
                }
            },
        }
        resp = client.post(
            "/manifests/validate",
            json={"manifest": manifest},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["valid"] is True, f"Errors: {body.get('errors')}"


# ---------------------------------------------------------------------------
# F. steps_filter interaction avec modèle sans select
# ---------------------------------------------------------------------------


class TestStepsFilterInteractionExternal:
    """validate_steps_filter_models : un modèle sans select est skippable trivialement."""

    def test_filter_including_external_model_no_error(self):
        """steps_filter incluant un modèle external ne produit pas d'erreur orphan."""
        from gispulse.core.manifest_v3 import ManifestV3, ModelSpec
        from gispulse.runtime.manifest_runner import validate_steps_filter_models

        manifest = ManifestV3(
            sources={},
            models={
                "ext": ModelSpec(
                    name="ext",
                    select=None,
                    transform=[
                        {"shell": {"kind": "external", "argv": ["run.py"]}}
                    ],
                )
            },
        )
        # ne doit pas lever
        validate_steps_filter_models(manifest, ["ext"])

    def test_filter_excluding_external_upstream_no_error(self):
        """Un modèle capability downstream dont l'upstream external est exclu → OK
        (pas de GeoDataFrame à propager depuis external)."""
        from gispulse.core.manifest_v3 import ManifestV3, ModelSpec, SourceSpec
        from gispulse.runtime.manifest_runner import validate_steps_filter_models

        manifest = ManifestV3(
            sources={"s": SourceSpec(name="s", uri="memory://s")},
            models={
                "ext": ModelSpec(
                    name="ext",
                    select=None,
                    transform=[{"shell": {"kind": "external", "argv": ["run.py"]}}],
                ),
                # Note: ce downstream doit avoir une source valide pour se compiler
                "cap": ModelSpec(
                    name="cap",
                    select="s",
                    transform=[{"filter": {"expression": "val > 0"}}],
                ),
            },
        )
        # filter = seulement "cap" (ext exclu) → OK car ext est non-capability
        validate_steps_filter_models(manifest, ["cap"])


# ---------------------------------------------------------------------------
# G. POST /manifests/run 202 bout-en-bout
# ---------------------------------------------------------------------------


class TestManifestRunEndpointExternal:
    """POST /manifests/run avec manifest external → 202 Accepted."""

    @pytest.fixture(autouse=True)
    def _disable_rate_limiter(self):
        from gispulse.adapters.http.rate_limit import limiter
        limiter.enabled = False

    @pytest.fixture
    def client(self, tmp_path):
        from fastapi.testclient import TestClient
        from gispulse.adapters.http.app import create_app
        import warnings
        warnings.filterwarnings("ignore")
        app = create_app()
        with TestClient(app) as c:
            yield c

    def _external_manifest(self):
        return {
            "version": 3,
            "name": "ext_run_test",
            "sources": {},
            "models": {
                "run_dept_63": {
                    "transform": [
                        {
                            "shell_pipeline": {
                                "kind": "external",
                                "argv": [sys.executable, "-c", "print('done')"],
                                "timeout_seconds": 30,
                            }
                        }
                    ]
                }
            },
        }

    def test_post_run_returns_202(self, client):
        """POST /manifests/run manifest external → 202 avec job_id."""
        resp = client.post(
            "/manifests/run",
            json={"manifest": self._external_manifest()},
        )
        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert "job_id" in body
        assert body["status"] == "pending"

    def test_post_run_returns_manifest_name(self, client):
        """manifest_name dans la réponse 202 correspond au name du manifest."""
        resp = client.post(
            "/manifests/run",
            json={"manifest": self._external_manifest()},
        )
        assert resp.status_code == 202
        body = resp.json()
        assert body["manifest_name"] == "ext_run_test"

    def test_post_run_invalid_manifest_returns_422(self, client):
        """Un manifest invalide retourne 422 sans créer de job."""
        resp = client.post(
            "/manifests/run",
            json={"manifest": {"version": 3, "models": {"bad": {"select": "nonexistent_source"}}}},
        )
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# H. Schema v3 — model sans select accepté par SCHEMA_V3
# ---------------------------------------------------------------------------


class TestSchemaV3SelectOptional:
    """SCHEMA_V3 doit accepter un model sans la clé select quand il a des transforms."""

    def test_schema_accepts_model_without_select_with_transform(self):
        """SCHEMA_V3 accepte un modèle sans select si il a un transform."""
        from gispulse.core.pipeline_schema import validate_pipeline_json

        raw = {
            "version": 3,
            "sources": {},
            "models": {
                "ext_model": {
                    "transform": [
                        {"shell_pipeline": {"kind": "external", "argv": ["run.py"]}}
                    ]
                }
            },
        }
        errors = validate_pipeline_json(raw)
        assert errors == [], f"Schema errors: {errors}"

    def test_schema_still_requires_select_for_capability_model(self):
        """SCHEMA_V3 est inchangé pour les modèles capability (select toujours obligatoire
        via la validation sémantique, pas nécessairement le schéma JSON)."""
        # Note: le schéma JSON devient permissif sur select, c'est validate_manifest
        # qui impose la contrainte sémantique. Ce test vérifie simplement que le
        # schéma JSON accepte les manifests existants (rétrocompatibilité).
        from gispulse.core.pipeline_schema import validate_pipeline_json

        raw = {
            "version": 3,
            "sources": {"s": {"uri": "memory://s"}},
            "models": {
                "m": {
                    "select": "s",
                    "transform": [{"filter": {"expression": "val > 0"}}],
                }
            },
        }
        errors = validate_pipeline_json(raw)
        assert errors == [], f"Schema errors: {errors}"
