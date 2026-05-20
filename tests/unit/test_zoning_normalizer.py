"""Tests for the ZoningElement declarative normaliser (issue #268, story T2)."""

from __future__ import annotations

import pytest

from gispulse.core.zoning_normalizer import (
    ZONING_ELEMENT_FIELDS,
    ZoningMapping,
    ZoningNormalisationError,
    normalize,
    normalize_record,
)


# ---------------------------------------------------------------------------
# Schema contract — the 8 fields, in canonical order
# ---------------------------------------------------------------------------


def test_schema_has_exactly_eight_fields() -> None:
    assert len(ZONING_ELEMENT_FIELDS) == 8
    assert ZONING_ELEMENT_FIELDS == (
        "geometry",
        "zone_code",
        "zone_label",
        "hilucs_class",
        "plan_id",
        "plan_date",
        "regulation_ref",
        "source_country",
    )


# ---------------------------------------------------------------------------
# Acceptance criterion: full mapping produces all 8 target fields
# ---------------------------------------------------------------------------


def test_normalize_full_french_record() -> None:
    """Given a typical FR GPU record + mapping, the output carries the 8 cibles."""
    mapping = ZoningMapping(
        source_country="FR",
        crs="EPSG:2154",
        fields={
            "zone_code": "libelle",
            "zone_label": "libelong",
            "plan_id": "idurba",
            "plan_date": "dateappro",
            "regulation_ref": "nomfic",
        },
        hilucs={
            "U": "1_PrimaryProduction_Residential",
            "AU": "1_PrimaryProduction_Residential",
            "A": "1_PrimaryProduction_Agriculture",
            "N": "4_OtherUses_NaturalAreas",
        },
        hilucs_key="zone_code",  # looked up after rename via mapping.fields
    )
    record = {
        "geometry": "POLYGON((0 0, 1 0, 1 1, 0 1, 0 0))",
        "libelle": "U",
        "libelong": "Zone urbaine",
        "idurba": "75056_PLU_2025",
        "dateappro": "2025-06-18",
        "nomfic": "https://gpu.gouv.fr/75/PLU/2025/reglement.pdf",
    }
    out = normalize_record(record, mapping)

    assert set(out) == set(ZONING_ELEMENT_FIELDS)
    assert out["geometry"] == "POLYGON((0 0, 1 0, 1 1, 0 1, 0 0))"
    assert out["zone_code"] == "U"
    assert out["zone_label"] == "Zone urbaine"
    assert out["hilucs_class"] == "1_PrimaryProduction_Residential"
    assert out["plan_id"] == "75056_PLU_2025"
    assert out["plan_date"] == "2025-06-18"
    assert out["regulation_ref"] == "https://gpu.gouv.fr/75/PLU/2025/reglement.pdf"
    assert out["source_country"] == "FR"


# ---------------------------------------------------------------------------
# Acceptance: best-effort HILUCS — no match yields None, no crash
# ---------------------------------------------------------------------------


def test_hilucs_unknown_value_yields_none_not_crash() -> None:
    mapping = ZoningMapping(
        source_country="FR",
        crs="EPSG:2154",
        fields={"zone_code": "libelle"},
        hilucs={"U": "1_PrimaryProduction_Residential"},
        hilucs_key="zone_code",
    )
    out = normalize_record({"libelle": "TOTALLY_UNKNOWN_CODE"}, mapping)
    assert out["zone_code"] == "TOTALLY_UNKNOWN_CODE"
    assert out["hilucs_class"] is None  # no match, but no exception


def test_hilucs_missing_source_field_yields_none_not_crash() -> None:
    mapping = ZoningMapping(
        source_country="DK",
        crs="EPSG:25832",
        fields={"zone_code": "kode"},
        hilucs={"U": "1_PrimaryProduction_Residential"},
        hilucs_key="zone_code",
    )
    out = normalize_record({}, mapping)  # source field absent altogether
    assert out["zone_code"] is None
    assert out["hilucs_class"] is None


def test_hilucs_completely_omitted_is_legal() -> None:
    """A mapping with no HILUCS table at all leaves the column ``None``."""
    mapping = ZoningMapping(
        source_country="NL",
        crs="EPSG:28992",
        fields={"zone_code": "code"},
    )
    out = normalize_record({"code": "W"}, mapping)
    assert out["zone_code"] == "W"
    assert out["hilucs_class"] is None


# ---------------------------------------------------------------------------
# Acceptance: CRS must be explicit
# ---------------------------------------------------------------------------


def test_missing_crs_rejected() -> None:
    with pytest.raises(ZoningNormalisationError, match="EPSG identifier"):
        ZoningMapping(source_country="FR", crs="")


def test_bare_crs_string_rejected() -> None:
    with pytest.raises(ZoningNormalisationError, match="EPSG identifier"):
        ZoningMapping(source_country="FR", crs="2154")  # missing 'EPSG:' prefix


def test_explicit_crs_is_carried_on_the_mapping_for_callers() -> None:
    """The CRS lives on the mapping — callers can use it to set GeoSeries.crs."""
    m = ZoningMapping(source_country="FR", crs="EPSG:2154", fields={})
    assert m.crs == "EPSG:2154"


# ---------------------------------------------------------------------------
# Acceptance: source_country must be ISO alpha-2
# ---------------------------------------------------------------------------


def test_source_country_must_be_iso_alpha2() -> None:
    with pytest.raises(ZoningNormalisationError, match="alpha-2"):
        ZoningMapping(source_country="FRA", crs="EPSG:2154")
    with pytest.raises(ZoningNormalisationError, match="alpha-2"):
        ZoningMapping(source_country="", crs="EPSG:2154")


# ---------------------------------------------------------------------------
# Mapping validation: only ZoningElement targets allowed as field keys
# ---------------------------------------------------------------------------


def test_field_keys_must_be_zoning_element_targets() -> None:
    with pytest.raises(ZoningNormalisationError, match="ZoningElement targets"):
        ZoningMapping(
            source_country="FR",
            crs="EPSG:2154",
            fields={"some_random_target": "libelle"},
        )


def test_geometry_key_is_not_a_field_target() -> None:
    """geometry is handled via geometry_key, not via the field map."""
    with pytest.raises(ZoningNormalisationError, match="ZoningElement targets"):
        ZoningMapping(
            source_country="FR",
            crs="EPSG:2154",
            fields={"geometry": "geom"},
        )


def test_hilucs_class_is_not_a_field_target() -> None:
    """hilucs_class comes from the lookup, never from a direct rename."""
    with pytest.raises(ZoningNormalisationError, match="ZoningElement targets"):
        ZoningMapping(
            source_country="FR",
            crs="EPSG:2154",
            fields={"hilucs_class": "categorie"},
        )


def test_source_country_is_not_a_field_target() -> None:
    """source_country comes from the mapping, never from a record column."""
    with pytest.raises(ZoningNormalisationError, match="ZoningElement targets"):
        ZoningMapping(
            source_country="FR",
            crs="EPSG:2154",
            fields={"source_country": "pays"},
        )


# ---------------------------------------------------------------------------
# Batch behaviour
# ---------------------------------------------------------------------------


def test_normalize_iterable_preserves_order_and_count() -> None:
    mapping = ZoningMapping(
        source_country="DK",
        crs="EPSG:25832",
        fields={"zone_code": "kode"},
    )
    records = [{"kode": "BO"}, {"kode": "ER"}, {"kode": "FR"}]
    out = normalize(records, mapping)
    assert len(out) == 3
    assert [row["zone_code"] for row in out] == ["BO", "ER", "FR"]


def test_normalize_partial_record_does_not_abort_batch() -> None:
    """A record missing all source keys still produces a None-filled row."""
    mapping = ZoningMapping(
        source_country="NL",
        crs="EPSG:28992",
        fields={"zone_code": "code", "zone_label": "naam"},
    )
    records = [{"code": "W", "naam": "Wonen"}, {}]
    out = normalize(records, mapping)
    assert out[0]["zone_code"] == "W"
    assert out[0]["zone_label"] == "Wonen"
    assert out[1]["zone_code"] is None
    assert out[1]["zone_label"] is None
    # constants are still set
    assert out[1]["source_country"] == "NL"


# ---------------------------------------------------------------------------
# HILUCS lookup on a non-renamed source key
# ---------------------------------------------------------------------------


def test_hilucs_lookup_uses_source_key_when_no_rename() -> None:
    """When hilucs_key matches an unrenamed source column, look it up directly."""
    mapping = ZoningMapping(
        source_country="DE",
        crs="EPSG:25832",
        fields={"zone_label": "name"},
        hilucs={"W": "1_PrimaryProduction_Residential"},
        hilucs_key="art",  # the source column itself, not a target
    )
    out = normalize_record({"art": "W", "name": "Wohngebiet"}, mapping)
    assert out["hilucs_class"] == "1_PrimaryProduction_Residential"
    assert out["zone_label"] == "Wohngebiet"
