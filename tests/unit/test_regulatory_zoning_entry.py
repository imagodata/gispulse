"""Tests for the regulatory-zoning entry format (story T3, issue #270)."""

from __future__ import annotations

from pathlib import Path  # noqa: F401 — used in type hint of tmp_path

import pytest

from gispulse.core.plugin_model import (
    DATA_PACK_CONTENTS,
    DataPackManifest,
)
from gispulse.core.regulatory_zoning_entry import (
    ZONING_ENTRY_REQUIRED_FIELDS,
    ZONING_VALID_PROTOCOLS,
    RegulatoryZoningEntry,
    RegulatoryZoningEntryError,
)


# ---------------------------------------------------------------------------
# Content type is recognised
# ---------------------------------------------------------------------------


def test_regulatory_zoning_is_a_recognised_content_type() -> None:
    assert "regulatory-zoning" in DATA_PACK_CONTENTS


def test_manifest_with_regulatory_zoning_loads() -> None:
    raw = {
        "name": "regulatory-zoning-eu-nw",
        "content": "regulatory-zoning",
        "version": "1.0.0",
        "tier": "pro",
        "entries": [],
    }
    m = DataPackManifest.from_dict(raw)
    assert m.content == "regulatory-zoning"
    assert m.tier.value == "pro"


# ---------------------------------------------------------------------------
# Happy path — full FR entry
# ---------------------------------------------------------------------------


def _fr_entry() -> dict:
    return {
        "name": "gpu-zone-urba",
        "source_country": "FR",
        "protocol": "wfs",
        "endpoint": "https://data.geopf.fr/wfs/ows",
        "typename": "wfs_du:zone_urba",
        "crs": "EPSG:4326",
        "mapping": {
            "fields": {
                "zone_code": "libelle",
                "zone_label": "libelong",
                "plan_id": "idurba",
                "plan_date": "dateappro",
                "regulation_ref": "nomfic",
            },
            "hilucs_key": "zone_code",
            "hilucs": {
                "U": "1_PrimaryProduction_Residential",
                "AU": "1_PrimaryProduction_Residential",
                "A": "1_PrimaryProduction_Agriculture",
                "N": "4_OtherUses_NaturalAreas",
            },
        },
        "max_features": 1000,
        "regulation": {
            "description": "Plan Local d'Urbanisme",
            "license": "Etalab 2.0",
            "refresh_freq": "monthly",
        },
    }


def test_full_fr_entry_roundtrips() -> None:
    entry = RegulatoryZoningEntry.from_dict(_fr_entry())
    assert entry.name == "gpu-zone-urba"
    assert entry.source_country == "FR"
    assert entry.protocol == "wfs"
    assert entry.endpoint == "https://data.geopf.fr/wfs/ows"
    assert entry.typename == "wfs_du:zone_urba"
    assert entry.crs == "EPSG:4326"
    assert entry.mapping["fields"]["zone_code"] == "libelle"
    assert entry.max_features == 1000
    assert entry.regulation["license"] == "Etalab 2.0"
    assert entry.bbox is None  # not provided


def test_lowercase_country_is_normalised_to_uppercase() -> None:
    raw = _fr_entry()
    raw["source_country"] = "fr"
    entry = RegulatoryZoningEntry.from_dict(raw)
    assert entry.source_country == "FR"


def test_bbox_accepted_when_4_numbers() -> None:
    raw = _fr_entry()
    raw["bbox"] = [2.0, 48.5, 2.6, 49.0]
    entry = RegulatoryZoningEntry.from_dict(raw)
    assert entry.bbox == (2.0, 48.5, 2.6, 49.0)


# ---------------------------------------------------------------------------
# Required-field validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("missing", list(ZONING_ENTRY_REQUIRED_FIELDS))
def test_missing_required_field_rejected(missing: str) -> None:
    raw = _fr_entry()
    del raw[missing]
    with pytest.raises(RegulatoryZoningEntryError, match="required fields"):
        RegulatoryZoningEntry.from_dict(raw)


def test_unknown_field_rejected() -> None:
    raw = _fr_entry()
    raw["totally_made_up"] = True
    with pytest.raises(RegulatoryZoningEntryError, match="unknown fields"):
        RegulatoryZoningEntry.from_dict(raw)


# ---------------------------------------------------------------------------
# Type validation
# ---------------------------------------------------------------------------


def test_non_mapping_input_rejected() -> None:
    with pytest.raises(RegulatoryZoningEntryError, match="must be a mapping"):
        RegulatoryZoningEntry.from_dict(["not", "a", "mapping"])  # type: ignore[arg-type]


def test_invalid_country_code_rejected() -> None:
    raw = _fr_entry()
    raw["source_country"] = "FRA"
    with pytest.raises(RegulatoryZoningEntryError, match="alpha-2"):
        RegulatoryZoningEntry.from_dict(raw)


@pytest.mark.parametrize("protocol", ["", "rest", "ogc", "wms"])
def test_invalid_protocol_rejected(protocol: str) -> None:
    raw = _fr_entry()
    raw["protocol"] = protocol
    with pytest.raises(RegulatoryZoningEntryError, match="protocol"):
        RegulatoryZoningEntry.from_dict(raw)


def test_protocol_is_case_insensitive() -> None:
    raw = _fr_entry()
    raw["protocol"] = "WFS"
    entry = RegulatoryZoningEntry.from_dict(raw)
    assert entry.protocol == "wfs"


def test_crs_must_be_explicit_identifier() -> None:
    raw = _fr_entry()
    raw["crs"] = "4326"  # no 'EPSG:' prefix
    with pytest.raises(RegulatoryZoningEntryError, match="EPSG"):
        RegulatoryZoningEntry.from_dict(raw)


# ---------------------------------------------------------------------------
# Mapping validation
# ---------------------------------------------------------------------------


def test_mapping_must_be_a_dict() -> None:
    raw = _fr_entry()
    raw["mapping"] = "not-a-dict"
    with pytest.raises(RegulatoryZoningEntryError, match="mapping must be"):
        RegulatoryZoningEntry.from_dict(raw)


def test_mapping_fields_unknown_target_rejected() -> None:
    raw = _fr_entry()
    raw["mapping"]["fields"]["geometry"] = "geom"  # not a renameable target
    with pytest.raises(
        RegulatoryZoningEntryError, match="ZoningElement targets"
    ):
        RegulatoryZoningEntry.from_dict(raw)


def test_mapping_can_be_minimal_with_just_fields() -> None:
    raw = _fr_entry()
    raw["mapping"] = {"fields": {"zone_code": "code"}}
    entry = RegulatoryZoningEntry.from_dict(raw)
    assert entry.mapping["fields"] == {"zone_code": "code"}


# ---------------------------------------------------------------------------
# bbox + max_features tolerance
# ---------------------------------------------------------------------------


def test_bbox_with_3_numbers_rejected() -> None:
    raw = _fr_entry()
    raw["bbox"] = [1, 2, 3]
    with pytest.raises(RegulatoryZoningEntryError, match="4 numbers"):
        RegulatoryZoningEntry.from_dict(raw)


def test_bbox_with_garbage_rejected() -> None:
    raw = _fr_entry()
    raw["bbox"] = ["one", "two", "three", "four"]
    with pytest.raises(RegulatoryZoningEntryError, match="bbox"):
        RegulatoryZoningEntry.from_dict(raw)


def test_max_features_must_be_int_when_present() -> None:
    raw = _fr_entry()
    raw["max_features"] = "1000"
    with pytest.raises(RegulatoryZoningEntryError, match="max_features"):
        RegulatoryZoningEntry.from_dict(raw)


# ---------------------------------------------------------------------------
# End-to-end via DataPackManifest — a real pack manifest validates entry-by-entry
# ---------------------------------------------------------------------------


def test_pack_manifest_with_entries_keeps_them_as_raw_dicts(tmp_path: Path) -> None:
    """T3 contract: DataPackManifest holds entries as opaque dicts; the
    data-pack consumer validates entries via RegulatoryZoningEntry.from_dict
    on demand. This decouples pack format evolution from manifest loading."""
    raw = {
        "name": "regulatory-zoning-eu-nw",
        "content": "regulatory-zoning",
        "version": "1.0.0",
        "tier": "pro",
        "entries": [_fr_entry()],
    }
    m = DataPackManifest.from_dict(raw)
    assert len(m.entries) == 1
    # opaque on the manifest side
    assert m.entries[0]["typename"] == "wfs_du:zone_urba"
    # validates cleanly on demand
    entry = RegulatoryZoningEntry.from_dict(m.entries[0])
    assert entry.source_country == "FR"


def test_protocols_list_is_two_only() -> None:
    """Defensive: keep the protocol surface small until a real need appears."""
    assert ZONING_VALID_PROTOCOLS == {"wfs", "ogc_api_features"}
