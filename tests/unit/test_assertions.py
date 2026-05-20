"""Unit tests for the ``assert:`` data-quality gates (ELT Lot 4F — #252).

Cover the assertion parser, the four built-in kinds (``not_null``,
``unique``, ``geometry_valid``, ``expect_rows``), the severity gate
(error vs warning), and integration into :func:`run_manifest`.
"""

from __future__ import annotations

import geopandas as gpd
import pytest
from shapely.geometry import Point, Polygon

from gispulse.core.assertions import (
    AssertionFailedError,
    AssertionSpec,
    parse_assertions,
    run_assertions,
)
from gispulse.core.manifest_v3 import (
    ManifestV3,
    ModelSpec,
    SourceSpec,
)
from gispulse.runtime.manifest_runner import run_manifest


# ---------------------------------------------------------------------------
# parse_assertions
# ---------------------------------------------------------------------------


def test_parse_assertions_recognises_every_kind():
    specs = parse_assertions(
        [
            {"not_null": ["id"]},
            {"unique": ["id", "code"]},
            {"geometry_valid": "geom"},
            {"expect_rows": {"min": 1, "max": 100}},
        ]
    )
    assert [s.kind for s in specs] == [
        "not_null",
        "unique",
        "geometry_valid",
        "expect_rows",
    ]
    assert all(s.severity == "error" for s in specs)


def test_parse_assertions_respects_severity():
    specs = parse_assertions(
        [
            {"not_null": ["id"], "severity": "warning"},
            {"unique": ["code"]},  # default error
        ]
    )
    assert specs[0].severity == "warning"
    assert specs[1].severity == "error"


def test_parse_assertions_rejects_two_kinds_per_entry():
    with pytest.raises(ValueError, match="exactly one assertion kind"):
        parse_assertions([{"not_null": ["a"], "unique": ["a"]}])


def test_parse_assertions_rejects_unknown_kind():
    with pytest.raises(ValueError, match="unknown kind"):
        parse_assertions([{"never_heard_of": ["a"]}])


def test_parse_assertions_rejects_bad_severity():
    with pytest.raises(ValueError, match="severity"):
        parse_assertions([{"not_null": ["a"], "severity": "loud"}])


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def _layer(ids=(1, 2, 3), codes=("a", "b", "c"), with_geom=True) -> gpd.GeoDataFrame:
    data = {"id": list(ids), "code": list(codes)}
    if with_geom:
        return gpd.GeoDataFrame(
            data, geometry=[Point(i, i) for i in range(len(ids))], crs="EPSG:2154"
        )
    return gpd.GeoDataFrame(data)


def test_not_null_passes_when_no_nulls():
    failures = run_assertions(
        "m", _layer(), [AssertionSpec("not_null", ["id"])], raise_on_error=False
    )
    assert failures == []


def test_not_null_raises_when_a_value_is_null():
    gdf = _layer(ids=(1, None, 3))
    with pytest.raises(AssertionFailedError, match="null"):
        run_assertions("m", gdf, [AssertionSpec("not_null", ["id"])])


def test_unique_detects_duplicates():
    gdf = _layer(ids=(1, 1, 2))
    with pytest.raises(AssertionFailedError, match="duplicate"):
        run_assertions("m", gdf, [AssertionSpec("unique", ["id"])])


def test_unique_composite_key_passes():
    # (id, code) is unique even though id alone has duplicates.
    gdf = _layer(ids=(1, 1, 2), codes=("a", "b", "b"))
    failures = run_assertions(
        "m", gdf, [AssertionSpec("unique", ["id", "code"])], raise_on_error=False
    )
    assert failures == []


def test_geometry_valid_passes_on_clean_polygons():
    gdf = gpd.GeoDataFrame(
        {"id": [1]},
        geometry=[Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])],
        crs="EPSG:2154",
    )
    failures = run_assertions(
        "m", gdf, [AssertionSpec("geometry_valid", True)], raise_on_error=False
    )
    assert failures == []


def test_geometry_valid_flags_bowtie_polygons():
    # Self-intersecting bowtie — ST_IsValid would say False.
    bowtie = Polygon([(0, 0), (1, 1), (0, 1), (1, 0), (0, 0)])
    gdf = gpd.GeoDataFrame({"id": [1]}, geometry=[bowtie], crs="EPSG:2154")
    with pytest.raises(AssertionFailedError, match="invalid"):
        run_assertions("m", gdf, [AssertionSpec("geometry_valid", True)])


def test_expect_rows_min_max():
    gdf = _layer(ids=(1, 2, 3))
    # Pass.
    assert (
        run_assertions(
            "m",
            gdf,
            [AssertionSpec("expect_rows", {"min": 1, "max": 5})],
            raise_on_error=False,
        )
        == []
    )
    # Below min — fail.
    with pytest.raises(AssertionFailedError, match="min"):
        run_assertions("m", gdf, [AssertionSpec("expect_rows", {"min": 10})])
    # Above max — fail.
    with pytest.raises(AssertionFailedError, match="max"):
        run_assertions("m", gdf, [AssertionSpec("expect_rows", {"max": 2})])


def test_warning_severity_does_not_raise():
    """``severity=warning`` failures collect but never raise."""
    gdf = _layer(ids=(1, None), codes=("a", "b"))
    failures = run_assertions(
        "m",
        gdf,
        [AssertionSpec("not_null", ["id"], severity="warning")],
    )
    # No raise, even with raise_on_error=True (default), because the
    # only failure is a warning.
    assert len(failures) == 1
    assert failures[0].severity == "warning"
    assert "null" in failures[0].message


def test_mixed_severities_raise_only_for_errors():
    """A warning + an error → AssertionFailedError fires, but the
    warning still appears in the failure list."""
    gdf = _layer(ids=(1, 1), codes=("a", "b"))  # duplicates → fails 'unique'
    with pytest.raises(AssertionFailedError):
        run_assertions(
            "m",
            gdf,
            [
                AssertionSpec("not_null", ["nope"], severity="warning"),
                AssertionSpec("unique", ["id"], severity="error"),
            ],
        )


# ---------------------------------------------------------------------------
# Integration with run_manifest
# ---------------------------------------------------------------------------


def _source_loader_for(layers):
    def loader(src):
        return layers[src.name]

    return loader


def test_run_manifest_blocks_on_error_assertion():
    src = _layer(ids=(1, 1, 2))  # duplicates
    manifest = ManifestV3(
        sources={"src": SourceSpec(name="src", uri="memory://src")},
        models={
            "m": ModelSpec(
                name="m",
                select="src",
                transform=[],
                assertions=[AssertionSpec("unique", ["id"])],
            ),
        },
    )
    with pytest.raises(AssertionFailedError, match="duplicate"):
        run_manifest(
            manifest, source_loader=_source_loader_for({"src": src})
        )


def test_run_manifest_collects_warnings():
    src = _layer(ids=(1, None, 3))  # null on id
    manifest = ManifestV3(
        sources={"src": SourceSpec(name="src", uri="memory://src")},
        models={
            "m": ModelSpec(
                name="m",
                select="src",
                transform=[],
                assertions=[
                    AssertionSpec("not_null", ["id"], severity="warning"),
                ],
            ),
        },
    )
    result = run_manifest(
        manifest, source_loader=_source_loader_for({"src": src})
    )
    assert len(result.assertion_warnings) == 1
    assert result.assertion_warnings[0].kind == "not_null"
    assert result.assertion_warnings[0].severity == "warning"


def test_loader_parses_assert_block(tmp_path):
    """The YAML loader picks up an ``assert:`` block and the schema
    validator does not reject it."""
    from gispulse.core.manifest_v3 import load_manifest_v3

    path = tmp_path / "m.yaml"
    path.write_text(
        "version: 3\n"
        "sources:\n"
        "  src: { uri: ./x.gpkg }\n"
        "models:\n"
        "  m:\n"
        "    select: src\n"
        "    assert:\n"
        "      - not_null: [id]\n"
        "      - unique: [id, code]\n"
        "      - { geometry_valid: geometry, severity: warning }\n"
        "      - expect_rows: { min: 1 }\n",
        encoding="utf-8",
    )
    manifest = load_manifest_v3(path)
    kinds = [a.kind for a in manifest.models["m"].assertions]
    assert kinds == ["not_null", "unique", "geometry_valid", "expect_rows"]
    sevs = [a.severity for a in manifest.models["m"].assertions]
    assert sevs == ["error", "error", "warning", "error"]
