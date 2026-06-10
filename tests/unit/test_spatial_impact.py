"""Unit tests for the spatial_impact capability and its public API.

Fixtures use pure shapely/geopandas geometries — no file I/O, no network.

Scenarios covered
-----------------
* Disjoint feature   → no intersection, empty result from clip_and_measure_matches.
* Partial overlap ~30 % → status=measured, relation=partial_overlap.
* Full overlap  >98 %   → status=measured, relation=full_overlap.
* suggest_metric_crs    → UTM 31N for metropolitan France, UTM south for southern
  hemisphere, current CRS unchanged for already-projected input,
  antimeridian bounds normalised, out-of-range longitude clamped.
* CRS mismatch (P1a)  → parcel auto-reprojected; overlap_m2 correct order of
  magnitude (not ~0).
* CRS missing (P1a)   → ValueError fast-fail.
* make_valid (P1b)    → bow-tie polygon measured, not silently not_measured.
* Default threshold boundary (P3a) → ~97.9 % → partial, ~98.1 % → full.
* Capability via registry → name "measure_spatial_impact" is findable, execute()
  produces the expected columns.
* Edge cases: empty inputs, None geometries, non-geographic CRS.
"""

from __future__ import annotations

import geopandas as gpd
import pyproj
import pytest
from shapely.geometry import Polygon, box

from gispulse.capabilities.vector.spatial_impact import (
    FULL_OVERLAP_PERCENT,
    MeasureSpatialImpactCapability,
    clip_and_measure_matches,
    measure_spatial_impact,
    suggest_metric_crs,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

# Reference parcel: 1° × 1° square in metropolitan France (WGS-84).
# Roughly 100 × 111 km → ~11 100 km².
_PARCEL_COORDS = [(2.0, 47.0), (3.0, 47.0), (3.0, 48.0), (2.0, 48.0), (2.0, 47.0)]


@pytest.fixture
def parcel_gdf() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {"id": ["parcel"]},
        geometry=[Polygon(_PARCEL_COORDS)],
        crs="EPSG:4326",
    )


@pytest.fixture
def feature_disjoint() -> gpd.GeoDataFrame:
    """Feature completely outside the parcel (lon 10–11, lat 47–48)."""
    return gpd.GeoDataFrame(
        {"id": ["feat_disjoint"]},
        geometry=[box(10.0, 47.0, 11.0, 48.0)],
        crs="EPSG:4326",
    )


@pytest.fixture
def feature_partial() -> gpd.GeoDataFrame:
    """Feature overlapping roughly 30 % of the parcel (lon 2.5–3.5, lat 47–48)."""
    return gpd.GeoDataFrame(
        {"id": ["feat_partial"]},
        geometry=[box(2.5, 47.0, 3.5, 48.0)],
        crs="EPSG:4326",
    )


@pytest.fixture
def feature_full() -> gpd.GeoDataFrame:
    """Feature that fully covers the parcel (slightly larger: lon 1.9–3.1, lat 46.9–48.1)."""
    return gpd.GeoDataFrame(
        {"id": ["feat_full"]},
        geometry=[box(1.9, 46.9, 3.1, 48.1)],
        crs="EPSG:4326",
    )


@pytest.fixture
def parcel_geojson() -> dict:
    return {"type": "Polygon", "coordinates": [_PARCEL_COORDS]}


@pytest.fixture
def feature_partial_geojson() -> dict:
    poly = box(2.5, 47.0, 3.5, 48.0)
    coords = list(poly.exterior.coords)
    return {"type": "Polygon", "coordinates": [coords]}


@pytest.fixture
def feature_full_geojson() -> dict:
    poly = box(1.9, 46.9, 3.1, 48.1)
    coords = list(poly.exterior.coords)
    return {"type": "Polygon", "coordinates": [coords]}


@pytest.fixture
def feature_disjoint_geojson() -> dict:
    poly = box(10.0, 47.0, 11.0, 48.0)
    coords = list(poly.exterior.coords)
    return {"type": "Polygon", "coordinates": [coords]}


# ---------------------------------------------------------------------------
# suggest_metric_crs
# ---------------------------------------------------------------------------


def test_suggest_metric_crs_france(parcel_gdf):
    """Metropolitan France centroid → UTM 31N (EPSG:32631)."""
    crs = suggest_metric_crs(parcel_gdf)
    assert crs == "EPSG:32631"


def test_suggest_metric_crs_southern_hemisphere():
    """Southern hemisphere centroid → UTM south band (base 32700)."""
    gdf = gpd.GeoDataFrame(
        {"id": ["s"]},
        geometry=[box(-45.0, -25.0, -44.0, -24.0)],
        crs="EPSG:4326",
    )
    crs = suggest_metric_crs(gdf)
    assert crs.startswith("EPSG:327")


def test_suggest_metric_crs_projected_unchanged():
    """Already-projected CRS is returned as-is."""
    gdf = gpd.GeoDataFrame(
        {"id": ["p"]},
        geometry=[box(600_000, 6_800_000, 601_000, 6_801_000)],
        crs="EPSG:2154",
    )
    crs = suggest_metric_crs(gdf)
    # Should not re-project; the returned CRS must be equivalent to EPSG:2154.
    assert pyproj.CRS(crs) == pyproj.CRS("EPSG:2154")


def test_suggest_metric_crs_empty_geographic_fallback():
    """Empty geographic GDF falls back to Lambert-93."""
    gdf = gpd.GeoDataFrame(geometry=gpd.GeoSeries([], dtype="geometry"), crs="EPSG:4326")
    crs = suggest_metric_crs(gdf)
    assert crs == "EPSG:2154"


# ---------------------------------------------------------------------------
# clip_and_measure_matches
# ---------------------------------------------------------------------------


def test_clip_disjoint_returns_empty(feature_disjoint, parcel_gdf):
    result = clip_and_measure_matches(feature_disjoint, parcel_gdf)
    assert result.empty


def test_clip_partial_overlap_area(feature_partial, parcel_gdf):
    result = clip_and_measure_matches(feature_partial, parcel_gdf)
    assert len(result) == 1
    overlap = result["overlap_m2"].iloc[0]
    # The intersection is lon 2.5–3.0, lat 47–48 (half of the feature inside parcel).
    # In UTM-31N that's roughly 50 km × 111 km ≈ 5.5e9 m²; accept wide tolerance.
    assert overlap > 1e8  # at least 100 km²
    assert overlap < 2e10  # less than 20 000 km²


def test_clip_full_overlap_area_larger_than_parcel(feature_full, parcel_gdf):
    result = clip_and_measure_matches(feature_full, parcel_gdf)
    assert len(result) == 1
    # Clipped geometry is the parcel itself → overlap_m2 ≈ parcel area.
    overlap = result["overlap_m2"].iloc[0]
    assert overlap > 1e9  # > 1 000 km²


def test_clip_preserves_input_columns(feature_partial, parcel_gdf):
    result = clip_and_measure_matches(feature_partial, parcel_gdf)
    assert "id" in result.columns
    assert result["id"].iloc[0] == "feat_partial"


def test_clip_empty_source_returns_empty(parcel_gdf):
    empty = gpd.GeoDataFrame(geometry=gpd.GeoSeries([], dtype="geometry"), crs="EPSG:4326")
    result = clip_and_measure_matches(empty, parcel_gdf)
    assert result.empty


def test_clip_empty_parcel_returns_empty(feature_partial):
    empty_parcel = gpd.GeoDataFrame(
        geometry=gpd.GeoSeries([], dtype="geometry"), crs="EPSG:4326"
    )
    result = clip_and_measure_matches(feature_partial, empty_parcel)
    assert result.empty


def test_clip_clipped_geometry_is_smaller(feature_partial, parcel_gdf):
    result = clip_and_measure_matches(feature_partial, parcel_gdf)
    original_area = feature_partial.to_crs("EPSG:32631").geometry.area.iloc[0]
    clipped_area = result.to_crs("EPSG:32631").geometry.area.iloc[0]
    # Clipped area must be strictly smaller than original (feature extends outside parcel).
    assert clipped_area < original_area * 0.95


# ---------------------------------------------------------------------------
# measure_spatial_impact (GeoJSON dict API)
# ---------------------------------------------------------------------------


def test_measure_none_inputs_returns_none():
    assert measure_spatial_impact(None, None) is None
    assert measure_spatial_impact({"type": "Polygon", "coordinates": [[]]}, None) is None
    assert measure_spatial_impact(None, {"type": "Polygon", "coordinates": [[]]}) is None


def test_measure_disjoint(feature_disjoint_geojson, parcel_geojson):
    result = measure_spatial_impact(feature_disjoint_geojson, parcel_geojson)
    assert result is not None
    assert result["status"] == "measured"
    assert result["relation"] == "partial_overlap"
    assert result["overlap_m2"] == pytest.approx(0.0)
    assert result["clipped_geometry"] is None


def test_measure_partial_overlap(feature_partial_geojson, parcel_geojson):
    result = measure_spatial_impact(feature_partial_geojson, parcel_geojson)
    assert result is not None
    assert result["status"] == "measured"
    assert result["relation"] == "partial_overlap"
    assert result["overlap_m2"] > 1e8
    assert result["parcel_percent"] is not None
    # ~50 % of parcel covered → clearly not full_overlap
    assert result["parcel_percent"] < FULL_OVERLAP_PERCENT
    assert result["clipped_geometry"] is not None
    assert result["method"] == "shapely.intersects+intersection+area"


def test_measure_full_overlap(feature_full_geojson, parcel_geojson):
    result = measure_spatial_impact(feature_full_geojson, parcel_geojson)
    assert result is not None
    assert result["status"] == "measured"
    assert result["relation"] == "full_overlap"
    assert result["parcel_percent"] is not None
    assert result["parcel_percent"] >= FULL_OVERLAP_PERCENT


def test_measure_custom_full_overlap_threshold(feature_partial_geojson, parcel_geojson):
    """Lowering the threshold can flip a partial to full_overlap."""
    result_low = measure_spatial_impact(
        feature_partial_geojson, parcel_geojson, full_overlap_percent=10.0
    )
    assert result_low is not None
    assert result_low["relation"] == "full_overlap"

    result_high = measure_spatial_impact(
        feature_partial_geojson, parcel_geojson, full_overlap_percent=99.9
    )
    assert result_high is not None
    assert result_high["relation"] == "partial_overlap"


def test_measure_result_keys(feature_partial_geojson, parcel_geojson):
    result = measure_spatial_impact(feature_partial_geojson, parcel_geojson)
    expected_keys = {"status", "relation", "overlap_m2", "parcel_percent", "clipped_geometry", "method"}
    assert expected_keys.issubset(result.keys())


def test_measure_clipped_geometry_is_geojson_dict(feature_partial_geojson, parcel_geojson):
    result = measure_spatial_impact(feature_partial_geojson, parcel_geojson)
    geom = result["clipped_geometry"]
    assert isinstance(geom, dict)
    assert "type" in geom
    assert "coordinates" in geom


# ---------------------------------------------------------------------------
# MeasureSpatialImpactCapability — via registry
# ---------------------------------------------------------------------------


def test_capability_registered():
    """The capability is discoverable under its name in the registry."""
    from gispulse.capabilities.registry import REGISTRY

    # Trigger default capability loading.
    from gispulse.capabilities import registry as _reg
    _reg._ensure_defaults_loaded()  # noqa: SLF001

    assert "measure_spatial_impact" in REGISTRY


def test_capability_execute_basic(feature_partial, parcel_gdf):
    cap = MeasureSpatialImpactCapability()
    result = cap.execute(feature_partial, ref_gdf=parcel_gdf)
    assert len(result) == 1
    assert "overlap_m2" in result.columns
    assert "parcel_percent" in result.columns
    assert "relation" in result.columns
    assert result["relation"].iloc[0] == "partial_overlap"


def test_capability_execute_full_overlap(feature_full, parcel_gdf):
    cap = MeasureSpatialImpactCapability()
    result = cap.execute(feature_full, ref_gdf=parcel_gdf)
    assert len(result) == 1
    assert result["relation"].iloc[0] == "full_overlap"


def test_capability_execute_disjoint_empty(feature_disjoint, parcel_gdf):
    cap = MeasureSpatialImpactCapability()
    result = cap.execute(feature_disjoint, ref_gdf=parcel_gdf)
    assert result.empty


def test_capability_raises_without_ref_gdf(feature_partial):
    cap = MeasureSpatialImpactCapability()
    with pytest.raises(ValueError, match="ref_gdf"):
        cap.execute(feature_partial)


def test_capability_raises_with_empty_ref_gdf(feature_partial):
    empty_ref = gpd.GeoDataFrame(
        geometry=gpd.GeoSeries([], dtype="geometry"), crs="EPSG:4326"
    )
    cap = MeasureSpatialImpactCapability()
    with pytest.raises(ValueError, match="ref_gdf"):
        cap.execute(feature_partial, ref_gdf=empty_ref)


def test_capability_custom_threshold(feature_partial, parcel_gdf):
    cap = MeasureSpatialImpactCapability()
    result_low = cap.execute(feature_partial, ref_gdf=parcel_gdf, full_overlap_percent=5.0)
    assert result_low["relation"].iloc[0] == "full_overlap"

    result_high = cap.execute(feature_partial, ref_gdf=parcel_gdf, full_overlap_percent=99.9)
    assert result_high["relation"].iloc[0] == "partial_overlap"


def test_capability_name():
    assert MeasureSpatialImpactCapability.name == "measure_spatial_impact"


def test_capability_get_schema():
    schema = MeasureSpatialImpactCapability().get_schema()
    assert "properties" in schema
    assert "full_overlap_percent" in schema["properties"]


def test_capability_via_registry_lookup(feature_partial, parcel_gdf):
    """The capability is retrievable from the registry and executable."""
    from gispulse.capabilities.registry import REGISTRY
    from gispulse.capabilities import registry as _reg
    _reg._ensure_defaults_loaded()  # noqa: SLF001

    cap_cls = REGISTRY["measure_spatial_impact"]
    result = cap_cls().execute(feature_partial, ref_gdf=parcel_gdf)
    assert len(result) == 1
    assert "overlap_m2" in result.columns


# ---------------------------------------------------------------------------
# P1a — CRS mismatch / missing
# ---------------------------------------------------------------------------


def test_clip_crs_mismatch_reprojects_correctly(parcel_gdf):
    """A source in EPSG:3857 and parcel in EPSG:4326 must give a correct area.

    Without reprojection the unary_union of the 4326 parcel is compared
    against 3857 coordinates: intersects() returns False or the intersection
    is near-zero, giving overlap_m2 ≈ 0 instead of ~5e9 m².
    """
    # Build the same geometry as feature_partial but in EPSG:3857.
    source_3857 = gpd.GeoDataFrame(
        {"id": ["feat"]},
        geometry=[box(2.5, 47.0, 3.5, 48.0)],
        crs="EPSG:4326",
    ).to_crs("EPSG:3857")

    result = clip_and_measure_matches(source_3857, parcel_gdf)
    assert len(result) == 1
    overlap = result["overlap_m2"].iloc[0]
    # Half of the parcel ~5.5e9 m² — allow ±20 % for projection distortion.
    assert overlap > 1e9, f"overlap_m2={overlap:.3e} unexpectedly small (CRS mismatch not reprojected?)"
    assert overlap < 2e10


def test_clip_missing_source_crs_raises():
    """source_gdf without CRS → ValueError with clear message."""
    source_no_crs = gpd.GeoDataFrame(geometry=[box(2.5, 47.0, 3.5, 48.0)])
    parcel = gpd.GeoDataFrame(geometry=[box(2.0, 47.0, 3.0, 48.0)], crs="EPSG:4326")
    with pytest.raises(ValueError, match="both GeoDataFrames must carry a CRS"):
        clip_and_measure_matches(source_no_crs, parcel)


def test_clip_missing_parcel_crs_raises(feature_partial):
    """parcel_gdf without CRS → ValueError with clear message."""
    parcel_no_crs = gpd.GeoDataFrame(geometry=[box(2.0, 47.0, 3.0, 48.0)])
    with pytest.raises(ValueError, match="both GeoDataFrames must carry a CRS"):
        clip_and_measure_matches(feature_partial, parcel_no_crs)


# ---------------------------------------------------------------------------
# P1b — make_valid
# ---------------------------------------------------------------------------


def _bowtie_polygon() -> Polygon:
    """A figure-8 / bow-tie polygon: self-intersecting at the centre.

    Shapely represents this as an invalid polygon (is_valid=False) that
    make_valid() can repair into two triangles (MultiPolygon).
    """
    # Two triangles sharing a single point at (1, 1) — classic bow-tie.
    return Polygon([(0, 0), (2, 2), (2, 0), (0, 2), (0, 0)])


def test_clip_bowtie_is_measured_not_errored():
    """An invalid bow-tie source polygon must be repaired and measured, not raise."""
    bowtie = _bowtie_polygon()
    assert not bowtie.is_valid, "precondition: fixture must be invalid"

    source = gpd.GeoDataFrame({"id": ["bt"]}, geometry=[bowtie], crs="EPSG:3857")
    # Parcel fully covers the bow-tie bounding box.
    parcel = gpd.GeoDataFrame(
        {"id": ["p"]}, geometry=[box(-1, -1, 3, 3)], crs="EPSG:3857"
    )
    result = clip_and_measure_matches(source, parcel)
    assert len(result) == 1, "bow-tie must produce a result row, not be dropped"
    assert result["overlap_m2"].iloc[0] > 0.0, "repaired bow-tie must have positive area"


def test_measure_bowtie_is_measured_not_not_measured():
    """measure_spatial_impact on an invalid polygon must return status=measured."""
    bowtie = _bowtie_polygon()
    # Use projected coordinates in EPSG:3857 via the GDF path; the GeoJSON
    # dict variant hardcodes EPSG:4326 internally — build a tiny bow-tie in
    # geographic coords close enough to be valid after make_valid.
    # Simpler: test via clip_and_measure_matches directly (already done above).
    # For the dict API, build a geographic bow-tie near France.
    geo_bowtie = Polygon([
        (2.0, 47.0), (3.0, 48.0), (3.0, 47.0), (2.0, 48.0), (2.0, 47.0)
    ])
    assert not geo_bowtie.is_valid
    feature_geom = {"type": "Polygon", "coordinates": [list(geo_bowtie.exterior.coords)]}
    parcel_geom = {"type": "Polygon", "coordinates": [list(box(1.5, 46.5, 3.5, 48.5).exterior.coords)]}

    result = measure_spatial_impact(feature_geom, parcel_geom)
    assert result is not None
    assert result["status"] == "measured", (
        f"bow-tie should be measured after make_valid, got status={result['status']!r}"
    )


# ---------------------------------------------------------------------------
# P2 — suggest_metric_crs antimeridian / out-of-range longitude
# ---------------------------------------------------------------------------


def test_suggest_metric_crs_antimeridian_bounds():
    """Bounds straddling the antimeridian (e.g. 179° to 181° raw) must yield a valid UTM zone."""
    # total_bounds would give minx=179, maxx=181 → raw centroid=180 → zone=(180+180)//6+1=61
    # After normalisation: lon = ((180+180) % 360) - 180 = 0 → zone=31 (or close).
    # Key invariant: result is a valid EPSG in [32601..32660] or [32701..32760].
    gdf = gpd.GeoDataFrame(
        {"id": ["x"]},
        geometry=[box(179.0, 10.0, 181.0, 11.0)],
        crs="EPSG:4326",
    )
    crs = suggest_metric_crs(gdf)
    assert crs.startswith("EPSG:32")
    epsg_num = int(crs.split(":")[1])
    base = (epsg_num // 100) * 100
    zone = epsg_num - base
    assert 1 <= zone <= 60, f"UTM zone {zone} out of valid range [1,60] for crs={crs}"


def test_suggest_metric_crs_out_of_range_longitude():
    """A raw centroid longitude > 360 (pathological input) must not produce zone > 60."""
    # Simulate via a GDF whose bounds produce an extreme raw centroid.
    # We can't set total_bounds directly, so we build a GDF with coords at lon=540
    # (= 180 after normalisation).  GeoPandas/Shapely accept it.
    gdf = gpd.GeoDataFrame(
        {"id": ["x"]},
        geometry=[box(359.0, 0.0, 361.0, 1.0)],
        crs="EPSG:4326",
    )
    crs = suggest_metric_crs(gdf)
    epsg_num = int(crs.split(":")[1])
    zone = epsg_num % 100
    assert 1 <= zone <= 60, f"zone={zone} out of range for crs={crs}"


# ---------------------------------------------------------------------------
# P3a — default threshold boundary (97.9 % → partial, 98.1 % → full)
# ---------------------------------------------------------------------------


def _make_overlap_gdf(overlap_fraction: float) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    """Build a source/parcel pair where source covers exactly *overlap_fraction*
    of the parcel area (in a projected CRS so the ratio is exact).

    Uses EPSG:3857 with a 100×100 unit square parcel.  The source rectangle
    covers ``overlap_fraction * 100`` units of width → exact area ratio.
    """
    # Parcel: [0, 100] × [0, 100]  (area = 10 000 sq units)
    parcel = gpd.GeoDataFrame(
        {"id": ["p"]}, geometry=[box(0, 0, 100, 100)], crs="EPSG:3857"
    )
    # Source covers [0, overlap_fraction*100] × [0, 100] → area = overlap_fraction * 10000
    # We make it slightly larger in y so the intersection equals exactly the
    # required strip of the parcel.
    src_width = overlap_fraction * 100.0
    source = gpd.GeoDataFrame(
        {"id": ["s"]}, geometry=[box(0, 0, src_width, 100)], crs="EPSG:3857"
    )
    return source, parcel


def test_threshold_just_below_default_is_partial():
    """97.9 % overlap → relation must be 'partial_overlap'."""
    source, parcel = _make_overlap_gdf(0.979)
    result = clip_and_measure_matches(source, parcel)
    assert len(result) == 1
    overlap_m2 = result["overlap_m2"].iloc[0]
    parcel_area = float(parcel.geometry.area.sum())  # already projected → exact
    pct = (overlap_m2 / parcel_area) * 100
    assert pct < FULL_OVERLAP_PERCENT, f"pct={pct:.4f} should be below {FULL_OVERLAP_PERCENT}"

    cap = MeasureSpatialImpactCapability()
    cap_result = cap.execute(source, ref_gdf=parcel)
    assert cap_result["relation"].iloc[0] == "partial_overlap"


def test_threshold_just_above_default_is_full():
    """98.1 % overlap → relation must be 'full_overlap'."""
    source, parcel = _make_overlap_gdf(0.981)
    cap = MeasureSpatialImpactCapability()
    cap_result = cap.execute(source, ref_gdf=parcel)
    assert cap_result["relation"].iloc[0] == "full_overlap"


def test_threshold_exactly_at_default_is_full():
    """Exactly 98.0 % overlap → full_overlap (boundary inclusive)."""
    source, parcel = _make_overlap_gdf(0.980)
    cap = MeasureSpatialImpactCapability()
    cap_result = cap.execute(source, ref_gdf=parcel)
    assert cap_result["relation"].iloc[0] == "full_overlap"
