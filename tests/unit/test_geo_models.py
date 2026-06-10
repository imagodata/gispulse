"""Tests for gispulse.core.geo_models — GeocodedAddress, Parcel, Building,
PartialError, AnalysisTrace, SpatialImpact."""

from __future__ import annotations

import dataclasses

import pytest

from gispulse.core.geo_models import (
    AnalysisTrace,
    Building,
    GeocodedAddress,
    Parcel,
    PartialError,
    SpatialImpact,
)


# ---------------------------------------------------------------------------
# All models re-exported from core.models
# ---------------------------------------------------------------------------


def test_re_exported_from_core_models() -> None:
    from gispulse.core.models import (
        AnalysisTrace as AT,
        Building as B,
        GeocodedAddress as GA,
        Parcel as P,
        PartialError as PE,
        SpatialImpact as SI,
    )
    assert GA is GeocodedAddress
    assert P is Parcel
    assert B is Building
    assert PE is PartialError
    assert AT is AnalysisTrace
    assert SI is SpatialImpact


# ---------------------------------------------------------------------------
# GeocodedAddress
# ---------------------------------------------------------------------------


class TestGeocodedAddress:
    def test_minimal_construction(self) -> None:
        addr = GeocodedAddress(label="1 rue de la Paix, Paris", longitude=2.33, latitude=48.87)
        assert addr.label == "1 rue de la Paix, Paris"
        assert addr.longitude == 2.33
        assert addr.latitude == 48.87

    def test_optional_fields_default_none(self) -> None:
        addr = GeocodedAddress(label="x", longitude=0.0, latitude=0.0)
        assert addr.score is None
        assert addr.city is None
        assert addr.postcode is None
        assert addr.citycode is None

    def test_raw_default_empty_dict(self) -> None:
        addr = GeocodedAddress(label="x", longitude=0.0, latitude=0.0)
        assert addr.raw == {}

    def test_frozen_immutable(self) -> None:
        addr = GeocodedAddress(label="x", longitude=0.0, latitude=0.0)
        with pytest.raises((dataclasses.FrozenInstanceError, TypeError, AttributeError)):
            addr.label = "changed"  # type: ignore[misc]

    def test_full_construction(self) -> None:
        addr = GeocodedAddress(
            label="42 rue Example",
            longitude=2.35,
            latitude=48.88,
            score=0.95,
            city="Paris",
            postcode="75001",
            citycode="75056",
            raw={"source": "ban"},
        )
        assert addr.score == 0.95
        assert addr.citycode == "75056"


# ---------------------------------------------------------------------------
# Parcel
# ---------------------------------------------------------------------------


class TestParcel:
    def test_defaults_all_none(self) -> None:
        p = Parcel()
        assert p.code is None
        assert p.surface_m2 is None
        assert p.geometry is None
        assert p.lookup_method == "point"

    def test_lookup_method_buffered_bbox(self) -> None:
        p = Parcel(lookup_method="buffered_bbox")
        assert p.lookup_method == "buffered_bbox"

    def test_frozen(self) -> None:
        p = Parcel(code="75056000AA0042")
        with pytest.raises((dataclasses.FrozenInstanceError, TypeError, AttributeError)):
            p.code = "changed"  # type: ignore[misc]

    def test_geometry_dict(self) -> None:
        geom = {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]}
        p = Parcel(geometry=geom)
        assert p.geometry == geom

    # P2b — Literal validation
    def test_invalid_lookup_method_raises(self) -> None:
        with pytest.raises(ValueError, match="lookup_method"):
            Parcel(lookup_method="teleport")  # type: ignore[arg-type]

    def test_all_valid_lookup_methods_accepted(self) -> None:
        for method in ("point", "buffered_bbox", "missing"):
            p = Parcel(lookup_method=method)  # type: ignore[arg-type]
            assert p.lookup_method == method


# ---------------------------------------------------------------------------
# Building
# ---------------------------------------------------------------------------


class TestBuilding:
    def test_source_defaults_to_empty_string(self) -> None:
        b = Building()
        assert b.source == ""

    def test_source_is_explicit(self) -> None:
        b = Building(source="ign-pci-express-batiment")
        assert b.source == "ign-pci-express-batiment"

    def test_frozen(self) -> None:
        b = Building(id="abc123")
        with pytest.raises((dataclasses.FrozenInstanceError, TypeError, AttributeError)):
            b.id = "changed"  # type: ignore[misc]

    def test_optional_fields_default_none(self) -> None:
        b = Building()
        assert b.id is None
        assert b.nature is None
        assert b.geometry is None
        assert b.bbox is None


# ---------------------------------------------------------------------------
# PartialError
# ---------------------------------------------------------------------------


class TestPartialError:
    def test_construction(self) -> None:
        pe = PartialError(stage="geocoding", message="timeout")
        assert pe.stage == "geocoding"
        assert pe.message == "timeout"
        assert pe.context == "missing_data"

    def test_explicit_context(self) -> None:
        pe = PartialError(stage="wfs", message="503", context="network")
        assert pe.context == "network"

    def test_frozen(self) -> None:
        pe = PartialError(stage="s", message="m")
        with pytest.raises((dataclasses.FrozenInstanceError, TypeError, AttributeError)):
            pe.stage = "changed"  # type: ignore[misc]

    def test_all_error_contexts_accepted(self) -> None:
        for ctx in ("network", "parse", "missing_data", "config", "truncation"):
            pe = PartialError(stage="x", message="y", context=ctx)  # type: ignore[arg-type]
            assert pe.context == ctx

    # P2b — Literal validation
    def test_invalid_context_raises(self) -> None:
        with pytest.raises(ValueError, match="context"):
            PartialError(stage="s", message="m", context="chaos")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# AnalysisTrace
# ---------------------------------------------------------------------------


class TestAnalysisTrace:
    def test_minimal(self) -> None:
        at = AnalysisTrace(stage="geocoding", label="Geocoding", status="ok")
        assert at.stage == "geocoding"
        assert at.status == "ok"
        assert at.duration_ms == 0
        assert at.source is None
        assert at.details == {}

    def test_all_status_values(self) -> None:
        for status in ("ok", "partial", "error", "skipped"):
            at = AnalysisTrace(stage="x", label="y", status=status)  # type: ignore[arg-type]
            assert at.status == status

    def test_frozen(self) -> None:
        at = AnalysisTrace(stage="s", label="l", status="ok")
        with pytest.raises((dataclasses.FrozenInstanceError, TypeError, AttributeError)):
            at.stage = "changed"  # type: ignore[misc]

    # P2b — Literal validation
    def test_invalid_status_raises(self) -> None:
        with pytest.raises(ValueError, match="status"):
            AnalysisTrace(stage="s", label="l", status="running")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# SpatialImpact
# ---------------------------------------------------------------------------


class TestSpatialImpact:
    def test_minimal(self) -> None:
        si = SpatialImpact(status="measured", relation="full_overlap", method="st_intersection")
        assert si.status == "measured"
        assert si.relation == "full_overlap"
        assert si.overlap_m2 is None

    def test_full(self) -> None:
        geom = {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]}
        si = SpatialImpact(
            status="measured",
            relation="partial_overlap",
            method="pg_st_area",
            overlap_m2=150.0,
            parcel_percent=0.42,
            clipped_geometry=geom,
        )
        assert si.overlap_m2 == 150.0
        assert si.parcel_percent == 0.42
        assert si.clipped_geometry == geom

    def test_frozen(self) -> None:
        si = SpatialImpact(status="not_measured", relation="not_measured", method="none")
        with pytest.raises((dataclasses.FrozenInstanceError, TypeError, AttributeError)):
            si.status = "measured"  # type: ignore[misc]

    # P2b — Literal validation
    def test_invalid_status_raises(self) -> None:
        with pytest.raises(ValueError, match="status"):
            SpatialImpact(status="unknown", relation="not_measured", method="x")  # type: ignore[arg-type]

    def test_invalid_relation_raises(self) -> None:
        with pytest.raises(ValueError, match="relation"):
            SpatialImpact(status="measured", relation="disjoint", method="x")  # type: ignore[arg-type]
