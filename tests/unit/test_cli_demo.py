"""Tests for ``gispulse demo list|run`` (src/gispulse/cli_demo.py).

Most tests run against a tiny fake repository (manifest + template + samples)
built in ``tmp_path``; the last ones exercise the real ``templates/`` and
``examples/`` of the checkout.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import geopandas as gpd
import pytest
from shapely.geometry import box
from typer.testing import CliRunner

from gispulse import cli_demo
from gispulse.cli import app
from gispulse.cli_demo import (
    _RAW_BASE,
    DemoError,
    SampleResolver,
    apply_overrides,
    leaf_steps,
    parse_manifest,
    parse_param,
    sha256_of,
)

runner = CliRunner()
REPO_ROOT = Path(__file__).resolve().parents[2]

_TINY_TEMPLATE = {
    "version": 2,
    "name": "tiny_buffer",
    "description": "Buffer squares, keep the ones inside a zone, measure them.",
    "domain": "test",
    "steps": [
        {"id": "buf", "type": "capability", "capability": "buffer", "params": {"distance": 1}},
        {
            "id": "inzone",
            "type": "capability",
            "capability": "intersects",
            "params": {"ref_layer": "zones"},
            "input": "buf",
        },
        {"id": "surf", "type": "capability", "capability": "area_length", "params": {}, "input": "buf"},
    ],
}


def _write_gpkg(path: Path, layer: str, geoms: list, **cols: list) -> str:
    gdf = gpd.GeoDataFrame(cols, geometry=geoms, crs="EPSG:2154")
    gdf.to_file(path, layer=layer, driver="GPKG")
    return sha256_of(path)


@pytest.fixture
def fake_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A checkout-like tree: templates/manifest.yml + template JSON + examples/datasets/*.gpkg."""
    root = tmp_path / "repo"
    (root / "templates").mkdir(parents=True)
    datasets = root / "examples" / "datasets"
    datasets.mkdir(parents=True)

    squares = [box(x, 0, x + 10, 10) for x in (0, 100, 200)]
    sha_tiny = _write_gpkg(datasets / "tiny.gpkg", "squares", squares, name=["a", "b", "c"])
    # The zone layer is NOT named after the alias -> exercises the extraction path.
    sha_zones = _write_gpkg(datasets / "zones.gpkg", "zone_layer", [box(-5, -5, 115, 15)], z=["Z1"])

    (root / "templates" / "tiny_buffer.json").write_text(json.dumps(_TINY_TEMPLATE), encoding="utf-8")
    manifest = f"""
name: fake-templates
content: template-pack
version: 0.0.0
entries:
  - name: tiny_buffer
    title: tiny_buffer
    description: "Buffer squares, keep the ones inside a zone, measure them."
    steps: 3
    domain: test
    file: tiny_buffer.json
    demo:
      input: datasets/tiny.gpkg
      layer: squares
      ref_layers:
        zones: datasets/zones.gpkg#zone_layer
      params:
        buf.distance: 2
      files:
        datasets/tiny.gpkg: {{sha256: "{sha_tiny}", size: {(datasets / 'tiny.gpkg').stat().st_size}}}
        datasets/zones.gpkg: {{sha256: "{sha_zones}", size: {(datasets / 'zones.gpkg').stat().st_size}}}
      note: "three squares"
  - name: other_no_demo
    title: other_no_demo
    description: "A template without a bundled sample."
    steps: 1
    domain: test
    file: other_no_demo.json
"""
    (root / "templates" / "manifest.yml").write_text(manifest, encoding="utf-8")

    monkeypatch.setattr(cli_demo, "_REPO_ROOT", root)
    monkeypatch.setattr(cli_demo, "_default_cache_dir", lambda: tmp_path / "cache")
    return root


# ---------------------------------------------------------------------------
# Manifest parsing + `demo list`
# ---------------------------------------------------------------------------


class TestManifest:
    def test_parse_marks_entries_without_demo(self, fake_repo: Path) -> None:
        entries = parse_manifest((fake_repo / "templates" / "manifest.yml").read_text())
        by_name = {e.name: e for e in entries}
        assert by_name["other_no_demo"].demo is None
        demo = by_name["tiny_buffer"].demo
        assert demo is not None
        assert demo.layer == "squares"
        assert demo.ref_layers == {"zones": "datasets/zones.gpkg#zone_layer"}
        assert demo.params == {"buf.distance": 2}
        assert demo.sample_paths() == ["datasets/tiny.gpkg", "datasets/zones.gpkg"]
        assert demo.size_bytes > 0
        assert demo.sha256_for("datasets/tiny.gpkg") == sha256_of(
            fake_repo / "examples" / "datasets" / "tiny.gpkg"
        )
        assert demo.sha256_for("datasets/missing.gpkg") is None

    def test_parse_tolerates_empty_manifest(self) -> None:
        assert parse_manifest("") == []
        assert parse_manifest("entries:\n  - title: nameless\n") == []

    def test_demo_list_marks_no_bundled_sample(self, fake_repo: Path) -> None:
        result = runner.invoke(app, ["demo", "list"])
        assert result.exit_code == 0, result.output
        assert "1 with a sample dataset, 1 without" in result.output
        lines = {line.split()[0]: line for line in result.output.splitlines() if line.strip()}
        assert "three squares" in lines["tiny_buffer"]
        assert "no bundled sample" in lines["other_no_demo"]
        assert "gispulse demo run <template>" in result.output


# ---------------------------------------------------------------------------
# Sample resolver
# ---------------------------------------------------------------------------


class TestSampleResolver:
    REL = "examples/datasets/x.gpkg"
    PAYLOAD = b"not really a gpkg"
    SHA = hashlib.sha256(PAYLOAD).hexdigest()

    @staticmethod
    def _boom(url: str) -> bytes:
        raise AssertionError(f"unexpected download of {url}")

    def test_data_dir_hit(self, tmp_path: Path) -> None:
        data_dir = tmp_path / "data"
        (data_dir / "datasets").mkdir(parents=True)
        (data_dir / "datasets" / "x.gpkg").write_bytes(self.PAYLOAD)
        resolver = SampleResolver(
            data_dir=data_dir, cache_dir=tmp_path / "cache", fetcher=self._boom, repo_roots=[]
        )
        assert resolver.resolve(self.REL, sha256=self.SHA) == data_dir / "datasets" / "x.gpkg"

    def test_data_dir_flat_layout_hit(self, tmp_path: Path) -> None:
        data_dir = tmp_path / "flat"
        data_dir.mkdir()
        (data_dir / "x.gpkg").write_bytes(self.PAYLOAD)
        (data_dir / "manifest.yml").write_bytes(b"entries: []\n")
        resolver = SampleResolver(
            data_dir=data_dir, cache_dir=tmp_path / "cache", fetcher=self._boom, repo_roots=[]
        )
        assert resolver.resolve(self.REL) == data_dir / "x.gpkg"
        assert resolver.resolve("templates/manifest.yml") == data_dir / "manifest.yml"

    def test_data_dir_is_a_repo_like_root_for_samples_and_templates(self, tmp_path: Path) -> None:
        """``--data-dir`` serves ``examples/…`` AND ``templates/…`` — no checkout, cache or network."""
        data_dir = tmp_path / "copy-of-repo"
        (data_dir / "examples" / "datasets").mkdir(parents=True)
        (data_dir / "templates").mkdir()
        (data_dir / "examples" / "datasets" / "x.gpkg").write_bytes(self.PAYLOAD)
        (data_dir / "templates" / "manifest.yml").write_bytes(b"entries: []\n")
        (data_dir / "templates" / "t.json").write_bytes(b"{}")
        resolver = SampleResolver(
            data_dir=data_dir, cache_dir=tmp_path / "cache", fetcher=self._boom, repo_roots=[]
        )
        assert resolver.resolve(self.REL, sha256=self.SHA) == data_dir / self.REL
        assert resolver.resolve("templates/manifest.yml") == data_dir / "templates" / "manifest.yml"
        assert resolver.resolve("templates/t.json") == data_dir / "templates" / "t.json"

    def test_checkout_hit(self, tmp_path: Path) -> None:
        root = tmp_path / "repo"
        (root / "examples" / "datasets").mkdir(parents=True)
        (root / "examples" / "datasets" / "x.gpkg").write_bytes(self.PAYLOAD)
        resolver = SampleResolver(cache_dir=tmp_path / "cache", fetcher=self._boom, repo_roots=[root])
        assert resolver.resolve(self.REL) == root / self.REL

    def test_cache_hit_when_hash_matches(self, tmp_path: Path) -> None:
        cache = tmp_path / "cache"
        cached = cache / "v1.2.3" / self.REL
        cached.parent.mkdir(parents=True)
        cached.write_bytes(self.PAYLOAD)
        resolver = SampleResolver(cache_dir=cache, fetcher=self._boom, repo_roots=[], ref="v1.2.3")
        assert resolver.resolve(self.REL, sha256=self.SHA) == cached

    def test_stale_cache_is_redownloaded(self, tmp_path: Path) -> None:
        cache = tmp_path / "cache"
        cached = cache / "v9.9.9" / self.REL
        cached.parent.mkdir(parents=True)
        cached.write_bytes(b"stale")
        calls: list[str] = []

        def fetch(url: str) -> bytes:
            calls.append(url)
            return self.PAYLOAD

        resolver = SampleResolver(cache_dir=cache, fetcher=fetch, repo_roots=[], ref="v9.9.9")
        assert resolver.resolve(self.REL, sha256=self.SHA) == cached
        assert calls == [f"{_RAW_BASE}/v9.9.9/{self.REL}"]
        assert cached.read_bytes() == self.PAYLOAD

    def test_download_is_cached_and_verified(self, tmp_path: Path) -> None:
        cache = tmp_path / "cache"
        calls: list[str] = []

        def fetch(url: str) -> bytes:
            calls.append(url)
            return self.PAYLOAD

        resolver = SampleResolver(cache_dir=cache, fetcher=fetch, repo_roots=[], ref="v1.2.3")
        path = resolver.resolve(self.REL, sha256=self.SHA)
        assert path == cache / "v1.2.3" / self.REL
        assert path.read_bytes() == self.PAYLOAD
        assert calls == [f"{_RAW_BASE}/v1.2.3/{self.REL}"]
        # Second call: served from the cache, no download.
        resolver.fetcher = self._boom
        assert resolver.resolve(self.REL, sha256=self.SHA) == path

    def test_cache_is_keyed_on_the_download_ref(self, tmp_path: Path) -> None:
        """An upgrade (new ref) never serves the manifest cached by the previous version."""
        cache = tmp_path / "cache"
        old = cache / "v1.0.0" / "templates" / "manifest.yml"
        old.parent.mkdir(parents=True)
        old.write_bytes(b"entries: [old]\n")
        calls: list[str] = []

        def fetch(url: str) -> bytes:
            calls.append(url)
            return b"entries: [new]\n"

        resolver = SampleResolver(cache_dir=cache, fetcher=fetch, repo_roots=[], ref="v2.0.0")
        path = resolver.resolve("templates/manifest.yml")
        assert path == cache / "v2.0.0" / "templates" / "manifest.yml"
        assert path.read_bytes() == b"entries: [new]\n"
        assert calls == [f"{_RAW_BASE}/v2.0.0/templates/manifest.yml"]
        assert old.read_bytes() == b"entries: [old]\n"  # untouched

    def test_sha256_mismatch_is_an_error_and_nothing_is_cached(self, tmp_path: Path) -> None:
        cache = tmp_path / "cache"
        resolver = SampleResolver(
            cache_dir=cache, fetcher=lambda _url: b"tampered", repo_roots=[], ref="v1.2.3"
        )
        with pytest.raises(DemoError, match="SHA-256 mismatch"):
            resolver.resolve(self.REL, sha256=self.SHA)
        assert not (cache / "v1.2.3" / self.REL).exists()
        assert not list(cache.rglob("*.part")) if cache.exists() else True

    def test_path_traversal_is_rejected(self, tmp_path: Path) -> None:
        resolver = SampleResolver(cache_dir=tmp_path / "cache", fetcher=self._boom, repo_roots=[])
        with pytest.raises(DemoError, match="escapes"):
            resolver.resolve("examples/../../etc/passwd")

    def test_release_ref_uses_tag_for_release_versions(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import gispulse

        monkeypatch.setattr(gispulse, "__version__", "2.5.1")
        assert cli_demo._release_ref() == "v2.5.1"
        monkeypatch.setattr(gispulse, "__version__", "2.6.0.dev3")
        assert cli_demo._release_ref() == "main"
        monkeypatch.setattr(gispulse, "__version__", "unknown")
        assert cli_demo._release_ref() == "main"


# ---------------------------------------------------------------------------
# Parameter overrides
# ---------------------------------------------------------------------------


class TestOverrides:
    def test_parse_param_values(self) -> None:
        assert parse_param("step.expression=risk_level == 'high'") == (
            "step.expression",
            "risk_level == 'high'",
        )
        assert parse_param("buf.distance=25") == ("buf.distance", 25)
        assert parse_param("join.columns=[\"a\", \"b\"]") == ("join.columns", ["a", "b"])
        assert parse_param("s.flag=true") == ("s.flag", True)

    @pytest.mark.parametrize("raw", ["nodot=1", "step.=1", ".param=1", "step.param", ""])
    def test_parse_param_rejects_malformed(self, raw: str) -> None:
        with pytest.raises(DemoError):
            parse_param(raw)

    def test_apply_overrides_edits_steps_in_memory(self, fake_repo: Path) -> None:
        from gispulse.core.pipeline import load_pipeline

        spec = load_pipeline(fake_repo / "templates" / "tiny_buffer.json")
        applied = apply_overrides(spec, {"buf.distance": 7, "surf.area_col": "a_m2"})
        by_id = {s.id: s for s in spec.steps}
        assert by_id["buf"].params["distance"] == 7
        assert by_id["surf"].params["area_col"] == "a_m2"
        assert applied == ["buf.distance = 7", 'surf.area_col = "a_m2"']
        with pytest.raises(DemoError, match="does not match a step"):
            apply_overrides(spec, {"nope.distance": 1})

    def test_leaf_steps_ignore_ref_consumers(self, fake_repo: Path) -> None:
        from gispulse.core.pipeline import load_pipeline

        spec = load_pipeline(fake_repo / "templates" / "tiny_buffer.json")
        assert [s.id for s in leaf_steps(spec)] == ["inzone", "surf"]


# ---------------------------------------------------------------------------
# `demo run` on the fake repository
# ---------------------------------------------------------------------------


class TestDemoRunFakeRepo:
    def test_run_end_to_end(self, fake_repo: Path, tmp_path: Path) -> None:
        from gispulse.persistence.io import list_layers, read_vector

        out = tmp_path / "out"
        result = runner.invoke(app, ["demo", "run", "tiny_buffer", "--out", str(out)])
        assert result.exit_code == 0, result.output
        assert "buf.distance = 2" in result.output
        assert "Output layers" in result.output
        assert "Next steps" in result.output

        gpkg = out / "tiny_buffer.gpkg"
        assert set(list_layers(str(gpkg))) == {"inzone", "surf"}
        assert len(read_vector(str(gpkg), layer="surf")) == 3
        assert len(read_vector(str(gpkg), layer="inzone")) == 2  # squares a and b are in the zone
        # Inputs staged next to the result; the ref alias got its own container
        # (named apart from the raw copy, which is itself called zones.gpkg).
        assert (out / "inputs" / "tiny.gpkg").is_file()
        assert list_layers(str(out / "inputs" / "zones.gpkg")) == ["zone_layer"]
        assert list_layers(str(out / "inputs" / "zones_zone_layer.gpkg")) == ["zones"]
        replay = json.loads((out / "pipeline.json").read_text(encoding="utf-8"))
        assert replay["ref_layers"] == {"zones": "inputs/zones_zone_layer.gpkg"}
        assert next(s for s in replay["steps"] if s["id"] == "buf")["params"]["distance"] == 2

    def test_cli_param_overrides_manifest_param(self, fake_repo: Path, tmp_path: Path) -> None:
        out = tmp_path / "out"
        result = runner.invoke(
            app, ["demo", "run", "tiny_buffer", "--out", str(out), "--param", "buf.distance=40"]
        )
        assert result.exit_code == 0, result.output
        assert "buf.distance = 40" in result.output
        assert "buf.distance = 2" not in result.output

    def test_existing_out_dir_needs_force(self, fake_repo: Path, tmp_path: Path) -> None:
        out = tmp_path / "out"
        result = runner.invoke(app, ["demo", "run", "tiny_buffer", "--out", str(out)])
        assert result.exit_code == 0, result.output
        assert (out / cli_demo._MARKER).is_file()
        (out / "stale.txt").write_text("x")
        result = runner.invoke(app, ["demo", "run", "tiny_buffer", "--out", str(out)])
        assert result.exit_code == 1
        assert "already exists" in result.output
        assert (out / "stale.txt").exists()
        result = runner.invoke(app, ["demo", "run", "tiny_buffer", "--out", str(out), "--force"])
        assert result.exit_code == 0, result.output
        assert not (out / "stale.txt").exists()
        assert (out / "tiny_buffer.gpkg").is_file()
        assert (out / cli_demo._MARKER).is_file()

    def test_force_refuses_a_directory_without_the_marker(self, fake_repo: Path, tmp_path: Path) -> None:
        """``--out ~/Documents --force`` must not wipe a folder the demo did not write."""
        documents = tmp_path / "Documents"
        documents.mkdir()
        (documents / "thesis.docx").write_text("precious")
        result = runner.invoke(
            app, ["demo", "run", "tiny_buffer", "--out", str(documents), "--force"]
        )
        assert result.exit_code == 1
        assert "not a gispulse demo output directory" in result.output
        assert (documents / "thesis.docx").read_text() == "precious"
        assert not (documents / "tiny_buffer.gpkg").exists()

    def test_empty_existing_out_dir_is_reused(self, fake_repo: Path, tmp_path: Path) -> None:
        out = tmp_path / "empty"
        out.mkdir()
        result = runner.invoke(app, ["demo", "run", "tiny_buffer", "--out", str(out)])
        assert result.exit_code == 0, result.output
        assert (out / "tiny_buffer.gpkg").is_file()

    def test_offline_with_data_dir_and_no_checkout(
        self, fake_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A wheel-only install, offline, with a copy of the repo tree passed as --data-dir."""
        monkeypatch.setattr(cli_demo, "_REPO_ROOT", tmp_path / "no-checkout")
        monkeypatch.setattr(cli_demo, "_default_cache_dir", lambda: tmp_path / "empty-cache")
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        monkeypatch.chdir(elsewhere)

        def no_network(url: str) -> bytes:
            raise DemoError(f"offline: {url}")

        monkeypatch.setattr(cli_demo, "_http_fetch", no_network)

        # Without --data-dir nothing can be found and the message says what to do.
        result = runner.invoke(app, ["demo", "list"])
        assert result.exit_code == 1
        assert "cannot find templates/manifest.yml" in result.output
        assert "--data-dir" in result.output

        result = runner.invoke(app, ["demo", "list", "--data-dir", str(fake_repo)])
        assert result.exit_code == 0, result.output
        assert "tiny_buffer" in result.output
        out = tmp_path / "out"
        result = runner.invoke(
            app, ["demo", "run", "tiny_buffer", "--data-dir", str(fake_repo), "--out", str(out)]
        )
        assert result.exit_code == 0, result.output
        assert (out / "tiny_buffer.gpkg").is_file()
        assert not (tmp_path / "empty-cache").exists()

    def test_missing_extra_is_reported_before_staging(
        self, fake_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        manifest = fake_repo / "templates" / "manifest.yml"
        text = manifest.read_text(encoding="utf-8")
        assert "      params:\n" in text
        manifest.write_text(
            text.replace("      params:\n", "      requires_extras: [network]\n      params:\n", 1),
            encoding="utf-8",
        )
        out = tmp_path / "out"

        monkeypatch.setattr(cli_demo, "_EXTRA_MODULES", {"network": ("gispulse_no_such_module_xyz",)})
        result = runner.invoke(app, ["demo", "list"])
        assert result.exit_code == 0, result.output
        assert "network" in next(line for line in result.output.splitlines() if "tiny_buffer" in line)
        result = runner.invoke(app, ["demo", "run", "tiny_buffer", "--out", str(out)])
        assert result.exit_code == 1
        assert 'pip install "gispulse[network]"' in result.output
        assert not out.exists()  # nothing staged

        monkeypatch.setattr(cli_demo, "_EXTRA_MODULES", {"network": ("json",)})
        result = runner.invoke(app, ["demo", "run", "tiny_buffer", "--out", str(out)])
        assert result.exit_code == 0, result.output

        monkeypatch.setattr(cli_demo, "_EXTRA_MODULES", {"raster": ("json",)})
        result = runner.invoke(app, ["demo", "run", "tiny_buffer", "--out", str(out), "--force"])
        assert result.exit_code == 1
        assert "unknown extra 'network'" in result.output

    def test_executed_count_matches_the_steps_that_ran(self, fake_repo: Path, tmp_path: Path) -> None:
        out = tmp_path / "out"
        result = runner.invoke(app, ["demo", "run", "tiny_buffer", "--out", str(out)])
        assert result.exit_code == 0, result.output
        assert "3 step(s) executed" in result.output
        # Two leaves were written: the replay hint must say `gispulse run` writes one.
        assert "writes the final step only (surf)" in result.output

    def test_unknown_template_and_missing_sample(self, fake_repo: Path, tmp_path: Path) -> None:
        result = runner.invoke(app, ["demo", "run", "nope", "--out", str(tmp_path / "o")])
        assert result.exit_code == 1
        assert "unknown template 'nope'" in result.output
        assert "tiny_buffer" in result.output
        result = runner.invoke(app, ["demo", "run", "other_no_demo", "--out", str(tmp_path / "o")])
        assert result.exit_code == 1
        assert "no bundled sample" in result.output

    def test_bad_override_is_a_readable_error(self, fake_repo: Path, tmp_path: Path) -> None:
        result = runner.invoke(
            app,
            ["demo", "run", "tiny_buffer", "--out", str(tmp_path / "o"), "--param", "buf.nope=1"],
        )
        assert result.exit_code == 1
        assert "pipeline failed" in result.output
        assert "nope" in result.output

    def test_missing_data_dir_is_an_error(self, fake_repo: Path, tmp_path: Path) -> None:
        result = runner.invoke(app, ["demo", "list", "--data-dir", str(tmp_path / "absent")])
        assert result.exit_code == 1
        assert "not a directory" in result.output


# ---------------------------------------------------------------------------
# Real repository: manifest consistency + bundled demos
# ---------------------------------------------------------------------------


def _real_demo_entries() -> list[cli_demo.DemoEntry]:
    manifest = REPO_ROOT / "templates" / "manifest.yml"
    if not manifest.is_file() or not (REPO_ROOT / "examples" / "datasets").is_dir():
        pytest.skip("requires the repository templates/ and examples/ directories")
    return [e for e in parse_manifest(manifest.read_text(encoding="utf-8")) if e.demo is not None]


def test_real_manifest_has_at_least_three_demos_with_valid_templates() -> None:
    from gispulse.core.pipeline import load_pipeline

    entries = _real_demo_entries()
    assert {"cadastre_topology_cleanup", "risques_inondation_parcelles", "services_aires_influence"} <= {
        e.name for e in entries
    }
    for entry in entries:
        spec = load_pipeline(REPO_ROOT / "templates" / entry.file)  # schema-validated
        assert spec.steps, entry.name
        assert entry.demo is not None
        for key in entry.demo.params:
            step_id = key.partition(".")[0]
            assert step_id in {s.id for s in spec.steps}, f"{entry.name}: override {key}"
        for extra in entry.demo.requires_extras:
            assert extra in cli_demo._EXTRA_MODULES, f"{entry.name}: unknown extra {extra}"
    by_name = {e.name: e for e in entries}
    assert by_name["reseau_topology_prep"].demo is not None
    assert by_name["reseau_topology_prep"].demo.requires_extras == ["network"]


def test_real_templates_declare_no_pro_requirement() -> None:
    """Every capability is Community on this branch: no template may still flag requires_pro."""
    _real_demo_entries()
    for path in sorted((REPO_ROOT / "templates").glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            assert not payload.get("requires_pro"), f"{path.name} still flags requires_pro"
    index = (REPO_ROOT / "templates" / "INDEX.md").read_text(encoding="utf-8")
    assert "(PRO" not in index
    manifest = (REPO_ROOT / "templates" / "manifest.yml").read_text(encoding="utf-8")
    assert "Pro capability" not in manifest


def test_real_manifest_hashes_match_the_bundled_samples() -> None:
    for entry in _real_demo_entries():
        assert entry.demo is not None
        for rel in entry.demo.sample_paths():
            assert rel in entry.demo.files, f"{entry.name}: {rel} has no files entry"
        for rel, meta in entry.demo.files.items():
            path = REPO_ROOT / "examples" / rel
            assert path.is_file(), f"{entry.name}: missing sample {rel}"
            assert sha256_of(path) == meta["sha256"], f"{entry.name}: stale sha256 for {rel}"
            assert path.stat().st_size == int(meta["size"]), f"{entry.name}: stale size for {rel}"


@pytest.mark.parametrize(
    ("template", "expected_layers"),
    [
        ("risques_inondation_parcelles", {"indicateurs", "bilan_zones"}),
        ("services_aires_influence", {"indicateurs"}),
    ],
)
def test_demo_run_fast_bundled_demos(template: str, expected_layers: set[str], tmp_path: Path) -> None:
    from gispulse.persistence.io import list_layers, read_vector

    _real_demo_entries()
    out = tmp_path / template
    result = runner.invoke(app, ["demo", "run", template, "--out", str(out)])
    assert result.exit_code == 0, result.output
    gpkg = out / f"{template}.gpkg"
    assert set(list_layers(str(gpkg))) == expected_layers
    for layer in expected_layers:
        gdf = read_vector(str(gpkg), layer=layer)
        assert len(gdf) > 0
        # Surfaces are measured in the layer's own metric CRS (EPSG:2154), not
        # in the area_length default EPSG:3857 (which was off by ~1.9x here).
        assert gdf.crs is not None and gdf.crs.to_epsg() == 2154
        assert "area_m2" in gdf.columns
        assert gdf["area_m2"].to_numpy() == pytest.approx(gdf.geometry.area.to_numpy(), rel=1e-6)


def test_flood_demo_exposed_surfaces_are_true(tmp_path: Path) -> None:
    """indicateurs = exposed part of each parcel per level; bilan_zones = dissolved footprint per level."""
    from gispulse.persistence.io import read_vector

    _real_demo_entries()
    out = tmp_path / "flood"
    result = runner.invoke(app, ["demo", "run", "risques_inondation_parcelles", "--out", str(out)])
    assert result.exit_code == 0, result.output
    gpkg = out / "risques_inondation_parcelles.gpkg"
    indicateurs = read_vector(str(gpkg), layer="indicateurs")
    bilan = read_vector(str(gpkg), layer="bilan_zones")
    zones = read_vector(str(REPO_ROOT / "examples" / "datasets" / "muret_flood_zones.gpkg")).to_crs(2154)
    zone_ha = zones.set_index("risk_level").geometry.area / 10_000

    assert set(indicateurs["risk_level"]) == {"high", "medium"}
    # One row per parcel x zone: no parcel listed twice within a level.
    assert not indicateurs.duplicated(["parcelle_id", "risk_level"]).any()
    # The exposed part of a parcel never exceeds the parcel's own (geometric) area.
    parcels = read_vector(str(out / "inputs" / "muret_parcels.gpkg"), layer="parcels").to_crs(2154)
    parcel_m2 = parcels.assign(m2=parcels.geometry.area).groupby("parcelle_id")["m2"].max()
    whole = parcel_m2.reindex(indicateurs["parcelle_id"]).to_numpy()
    assert (indicateurs["area_m2"].to_numpy() <= whole * (1 + 1e-6) + 1e-3).all()
    assert set(bilan.columns) == {"risk_level", "area_m2", "surface_ha", "geometry"}
    assert list(bilan["risk_level"]) == ["high", "medium"]
    for level, group in indicateurs.groupby("risk_level"):
        exposed = float(group["surface_ha"].sum())
        footprint = float(bilan.loc[bilan["risk_level"] == level, "surface_ha"].iloc[0])
        assert exposed <= zone_ha[level], level
        assert footprint <= zone_ha[level], level
        # Dissolving counts parcel overlaps once: footprint <= sum, and close to it.
        assert footprint <= exposed + 1e-3, level
        assert footprint == pytest.approx(exposed, rel=1e-3), level


def test_services_demo_every_cell_has_its_school(tmp_path: Path) -> None:
    from gispulse.persistence.io import read_vector

    _real_demo_entries()
    out = tmp_path / "services"
    result = runner.invoke(app, ["demo", "run", "services_aires_influence", "--out", str(out)])
    assert result.exit_code == 0, result.output
    cells = read_vector(str(out / "services_aires_influence.gpkg"), layer="indicateurs")
    assert len(cells) == 10
    assert cells["name"].notna().all(), cells["name"].tolist()
    assert cells["name"].is_unique
    assert set(cells["amenity"]) == {"school", "kindergarten"}


@pytest.mark.slow
def test_demo_run_cadastre_topology_cleanup_end_to_end(tmp_path: Path) -> None:
    """The command advertised in the README, on the bundled Muret parcels (~20 s)."""
    from gispulse.persistence.io import list_layers, read_vector

    _real_demo_entries()
    out = tmp_path / "cadastre"
    result = runner.invoke(app, ["demo", "run", "cadastre_topology_cleanup", "--out", str(out)])
    assert result.exit_code == 0, result.output
    assert "8 step(s) executed" in result.output
    gpkg = out / "cadastre_topology_cleanup.gpkg"
    assert set(list_layers(str(gpkg))) == {"issues_report", "surfaces_finales"}
    cleaned = read_vector(str(gpkg), layer="surfaces_finales")
    assert 17_000 < len(cleaned) <= 17_212
    assert "area_m2" in cleaned.columns
