"""Unit tests for A8 (#234) — ``WorldwideCatalogSource``.

Zero network: the source is a pure declaration parsed from
``worldwide_catalog.yml``; the endpoint SSRF check is structural (no DNS
lookup), so these tests need no ``offline_ssrf`` fixture.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from gispulse.core.plugin_model import AccessProtocol, Payload, SourceDomain
from gispulse.core.sources import SOURCES, DataSource
from gispulse.plugins.worldwide_source import (
    DEFAULT_CATALOG_PATH,
    WorldwideCatalogError,
    WorldwideCatalogSource,
    _entry_from_dict,
    load_worldwide_catalog,
    register,
)

_EXPECTED_FAMILIES = {
    "overture-geoparquet",
    "ogc-features",
    "stac-imagery",
    "opendata-fr",
}


def _write_catalog(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "catalog.yml"
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    return path


# -- contract ---------------------------------------------------------------


def test_source_contract() -> None:
    src = WorldwideCatalogSource()
    assert src.name == "worldwide"
    assert isinstance(src, DataSource)  # runtime-checkable Protocol


def test_default_catalog_ships_in_package() -> None:
    assert DEFAULT_CATALOG_PATH.is_file()


# -- shipped catalogue ------------------------------------------------------


def test_loads_shipped_catalogue() -> None:
    entries = WorldwideCatalogSource().entries()
    assert len(entries) >= 13
    # Every entry carries the four classification axes.
    for e in entries:
        assert isinstance(e.domain, SourceDomain)
        assert isinstance(e.payload, Payload)
        assert e.jurisdiction
        assert isinstance(e.access.protocol, AccessProtocol)
        assert e.access.endpoint


def test_four_families_seeded() -> None:
    assert set(WorldwideCatalogSource().families()) == _EXPECTED_FAMILIES


def test_entry_ids_are_unique() -> None:
    entries = WorldwideCatalogSource().entries()
    assert len(entries) == len({e.id for e in entries})


# -- catalog() filtering on the four axes -----------------------------------


def test_catalog_no_filter_returns_all() -> None:
    src = WorldwideCatalogSource()
    assert len(src.catalog()) == len(src.entries())


def test_catalog_filters_by_domain() -> None:
    hits = WorldwideCatalogSource().catalog(domain=SourceDomain.IMAGERIE)
    assert hits
    assert all(e.domain is SourceDomain.IMAGERIE for e in hits)


def test_catalog_filters_by_payload_raster_is_stac_only() -> None:
    hits = WorldwideCatalogSource().catalog(payload=Payload.RASTER)
    assert hits
    assert all(e.access.protocol is AccessProtocol.STAC for e in hits)


def test_catalog_filters_by_jurisdiction() -> None:
    hits = WorldwideCatalogSource().catalog(jurisdiction="FR")
    assert hits
    assert all(e.jurisdiction == "FR" for e in hits)


def test_catalog_filters_by_protocol() -> None:
    hits = WorldwideCatalogSource().catalog(protocol=AccessProtocol.REMOTE_TABLE)
    assert hits
    assert all(e.access.protocol is AccessProtocol.REMOTE_TABLE for e in hits)


def test_catalog_filters_by_family() -> None:
    hits = WorldwideCatalogSource().catalog(family="opendata-fr")
    assert hits
    assert all(e.metadata.get("family") == "opendata-fr" for e in hits)


def test_catalog_search_matches_id_and_name() -> None:
    hits = WorldwideCatalogSource().catalog("overture")
    assert {e.id for e in hits} >= {"overture-places", "overture-buildings"}


def test_catalog_axes_accept_string_values() -> None:
    src = WorldwideCatalogSource()
    assert src.catalog(domain="imagerie") == src.catalog(domain=SourceDomain.IMAGERIE)


def test_catalog_unknown_axis_value_yields_empty() -> None:
    assert WorldwideCatalogSource().catalog(domain="not-a-domain") == []


def test_catalog_combined_filters_narrow() -> None:
    src = WorldwideCatalogSource()
    combined = src.catalog(payload=Payload.RASTER, jurisdiction="world")
    assert combined
    assert all(
        e.payload is Payload.RASTER and e.jurisdiction == "world" for e in combined
    )


# -- revision() -------------------------------------------------------------


def test_revision_returns_declared_token() -> None:
    src = WorldwideCatalogSource()
    assert src.revision("overture-places") == "2025-09-24.0"


def test_revision_none_for_live_entry() -> None:
    # vida-open-buildings declares `revision_token: null`.
    assert WorldwideCatalogSource().revision("vida-open-buildings") is None


def test_revision_unknown_entry_raises() -> None:
    with pytest.raises(KeyError):
        WorldwideCatalogSource().revision("does-not-exist")


# -- SSRF / structural endpoint validation ----------------------------------


def _raw(endpoint: str, protocol: str = "download") -> dict:
    return {
        "id": "probe",
        "name": "Probe",
        "domain": "base",
        "payload": "vector",
        "jurisdiction": "world",
        "access": {"protocol": protocol, "endpoint": endpoint},
    }


def test_endpoint_private_ip_literal_rejected() -> None:
    with pytest.raises(WorldwideCatalogError, match="private"):
        _entry_from_dict(_raw("http://10.0.0.1/data.csv"))


def test_endpoint_loopback_ip_rejected() -> None:
    with pytest.raises(WorldwideCatalogError, match="private|loopback|reserved"):
        _entry_from_dict(_raw("http://127.0.0.1/data.csv"))


def test_endpoint_localhost_hostname_rejected() -> None:
    with pytest.raises(WorldwideCatalogError, match="loopback"):
        _entry_from_dict(_raw("http://localhost/data.csv"))


def test_endpoint_link_local_metadata_ip_rejected() -> None:
    # The classic SSRF target — cloud instance metadata.
    with pytest.raises(WorldwideCatalogError):
        _entry_from_dict(_raw("http://169.254.169.254/latest/meta-data/"))


def test_endpoint_disallowed_scheme_rejected() -> None:
    with pytest.raises(WorldwideCatalogError, match="scheme"):
        _entry_from_dict(_raw("file:///etc/passwd"))


def test_endpoint_public_hostname_accepted() -> None:
    entry = _entry_from_dict(_raw("https://files.data.gouv.fr/x.csv"))
    assert entry.id == "probe"


def test_endpoint_s3_scheme_accepted() -> None:
    entry = _entry_from_dict(_raw("s3://bucket/key/*", protocol="remote-table"))
    assert entry.access.protocol is AccessProtocol.REMOTE_TABLE


# -- load_worldwide_catalog error paths -------------------------------------


def test_load_rejects_non_mapping(tmp_path: Path) -> None:
    path = _write_catalog(tmp_path, "- just\n- a\n- list\n")
    with pytest.raises(WorldwideCatalogError, match="mapping"):
        load_worldwide_catalog(path)


def test_load_rejects_missing_entries_list(tmp_path: Path) -> None:
    path = _write_catalog(tmp_path, "version: 1\n")
    with pytest.raises(WorldwideCatalogError, match="entries"):
        load_worldwide_catalog(path)


def test_load_rejects_duplicate_ids(tmp_path: Path) -> None:
    path = _write_catalog(
        tmp_path,
        """
        version: 1
        entries:
          - id: dup
            name: A
            domain: base
            payload: vector
            jurisdiction: world
            access: {protocol: download, endpoint: 'https://a.example.org/a'}
          - id: dup
            name: B
            domain: base
            payload: vector
            jurisdiction: world
            access: {protocol: download, endpoint: 'https://b.example.org/b'}
        """,
    )
    with pytest.raises(WorldwideCatalogError, match="duplicate"):
        load_worldwide_catalog(path)


def test_load_rejects_unknown_domain(tmp_path: Path) -> None:
    path = _write_catalog(
        tmp_path,
        """
        version: 1
        entries:
          - id: bad
            name: Bad
            domain: galaxy
            payload: vector
            jurisdiction: world
            access: {protocol: download, endpoint: 'https://x.example.org/x'}
        """,
    )
    with pytest.raises(WorldwideCatalogError, match="domain"):
        load_worldwide_catalog(path)


def test_load_rejects_missing_endpoint(tmp_path: Path) -> None:
    path = _write_catalog(
        tmp_path,
        """
        version: 1
        entries:
          - id: bad
            name: Bad
            domain: base
            payload: vector
            jurisdiction: world
            access: {protocol: download}
        """,
    )
    with pytest.raises(WorldwideCatalogError, match="endpoint"):
        load_worldwide_catalog(path)


def test_missing_file_raises() -> None:
    with pytest.raises(WorldwideCatalogError, match="cannot read"):
        load_worldwide_catalog(Path("/no/such/worldwide_catalog.yml"))


# -- register() entry-point hook --------------------------------------------


def test_register_files_source_in_registry() -> None:
    SOURCES.clear()
    try:
        register()
        src = SOURCES.get("worldwide")
        assert isinstance(src, WorldwideCatalogSource)
    finally:
        SOURCES.clear()
