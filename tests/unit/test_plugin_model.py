"""Unit tests for the unified plugin model vocabulary (issue #176)."""

from __future__ import annotations

import dataclasses

import pytest

from core.plugin_model import (
    ENTRYPOINT_GROUPS,
    PROTOCOL_VERSION,
    AccessProtocol,
    AccessSpec,
    FetchMode,
    Origin,
    Payload,
    PluginKind,
    PluginManifest,
    PluginRecord,
    PluginState,
    RuleClause,
    SourceDomain,
    SourceResult,
    Tier,
    Trust,
    WriteMode,
    WriteReport,
    WriteSpec,
    tier_satisfies,
)


# --------------------------------------------------------------------------
# Enums
# --------------------------------------------------------------------------


def test_protocol_version_is_shared_with_contracts() -> None:
    from core import plugin_contracts

    assert plugin_contracts.PROTOCOL_VERSION is PROTOCOL_VERSION


@pytest.mark.parametrize(
    ("enum", "size"),
    [
        (PluginKind, 5),
        (Origin, 2),
        (Trust, 3),
        (PluginState, 4),
        (Tier, 4),
        (SourceDomain, 9),
        (Payload, 5),
        (AccessProtocol, 15),
        (FetchMode, 2),
        (WriteMode, 3),
    ],
)
def test_enum_cardinality(enum: type, size: int) -> None:
    assert len(list(enum)) == size


def test_enums_are_str_serializable() -> None:
    # str-Enum members compare equal to their value — JSON / API friendly.
    assert PluginKind.SOURCE == "source"
    assert SourceDomain.REGLEMENTAIRE.value == "reglementaire"
    assert AccessProtocol.OGC_FEATURES.value == "ogc-features"


def test_entrypoint_groups_cover_single_group_kinds() -> None:
    # EXTENSION spans nine sub-groups and is intentionally absent.
    assert set(ENTRYPOINT_GROUPS) == {
        PluginKind.SOURCE,
        PluginKind.CAPABILITY,
        PluginKind.SINK,
        PluginKind.PROTOCOL,
    }
    assert ENTRYPOINT_GROUPS[PluginKind.SOURCE] == "gispulse.data_sources"
    assert PluginKind.EXTENSION not in ENTRYPOINT_GROUPS


# --------------------------------------------------------------------------
# tier_satisfies
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("current", "required", "ok"),
    [
        (Tier.COMMUNITY, Tier.COMMUNITY, True),
        (Tier.COMMUNITY, Tier.PRO, False),
        (Tier.PRO, Tier.COMMUNITY, True),
        (Tier.PRO, Tier.PRO, True),
        (Tier.PRO, Tier.TEAM, False),
        (Tier.ENTERPRISE, Tier.COMMUNITY, True),
        (Tier.ENTERPRISE, Tier.ENTERPRISE, True),
        (Tier.TEAM, Tier.ENTERPRISE, False),
    ],
)
def test_tier_satisfies(current: Tier, required: Tier, ok: bool) -> None:
    assert tier_satisfies(current, required) is ok


# --------------------------------------------------------------------------
# Dataclasses
# --------------------------------------------------------------------------


def test_manifest_is_frozen() -> None:
    m = PluginManifest(name="cadastre", kind=PluginKind.SOURCE)
    with pytest.raises(dataclasses.FrozenInstanceError):
        m.name = "other"  # type: ignore[misc]


def test_manifest_source_axes() -> None:
    m = PluginManifest(
        name="urbanisme",
        kind=PluginKind.SOURCE,
        origin=Origin.EXTERNAL,
        domain=SourceDomain.REGLEMENTAIRE,
        payload=Payload.VECTOR,
        jurisdiction="FR",
    )
    assert m.domain is SourceDomain.REGLEMENTAIRE
    assert m.jurisdiction == "FR"
    assert m.tier is None  # external plugins don't grant themselves a tier


def test_access_spec_defaults() -> None:
    spec = AccessSpec(protocol=AccessProtocol.WFS, endpoint="https://x/wfs")
    assert spec.params == {} and spec.format is None and spec.auth is None


def test_source_result_reference_mode() -> None:
    r = SourceResult(
        payload=Payload.RASTER,
        mode=FetchMode.REFERENCE,
        reference="https://x/dem.tif",
        crs="EPSG:2154",
    )
    assert r.data is None
    assert r.reference.endswith(".tif")
    assert r.mode is FetchMode.REFERENCE


def test_write_spec_and_report() -> None:
    spec = WriteSpec(protocol=AccessProtocol.DB, destination="postgis://analyse.t")
    assert spec.mode is WriteMode.UPSERT
    report = WriteReport(destination=spec.destination, rows_written=42, created=True)
    assert report.rows_written == 42 and report.rows_failed == 0


def test_rule_clause_is_jurisdiction_agnostic() -> None:
    clause = RuleClause(
        zone_code="UB",
        jurisdiction="FR",
        constraints={"emprise_max": 0.4, "hauteur_max": 12},
        source_doc="reglement_UB.pdf#p14",
    )
    assert clause.constraints["hauteur_max"] == 12
    assert clause.jurisdiction == "FR"


# --------------------------------------------------------------------------
# PluginRecord
# --------------------------------------------------------------------------


def test_plugin_record_defaults_and_availability() -> None:
    rec = PluginRecord(name="h3_aggregate", kind=PluginKind.CAPABILITY)
    assert rec.state is PluginState.DISCOVERED
    assert rec.tier_required is Tier.COMMUNITY
    assert rec.available is False

    rec.state = PluginState.ACTIVE
    assert rec.available is True


def test_plugin_record_as_dict_is_json_safe() -> None:
    rec = PluginRecord(
        name="ftth_coverage",
        kind=PluginKind.CAPABILITY,
        origin=Origin.EXTERNAL,
        trust=Trust.VERIFIED,
        tier_required=Tier.PRO,
        state=PluginState.LOCKED,
        detail="requiert le palier 'pro'",
        entry_point=object(),  # not JSON-safe — must be dropped
        obj=object(),
    )
    d = rec.as_dict()
    assert d == {
        "name": "ftth_coverage",
        "kind": "capability",
        "origin": "external",
        "trust": "verified",
        "tier_required": "pro",
        "state": "locked",
        "detail": "requiert le palier 'pro'",
    }
    assert "entry_point" not in d and "obj" not in d
