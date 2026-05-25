"""Unit tests for the worldwide-aggregator fetcher base (issues #228, #227).

Covers :class:`LazyFetcher` (mode dispatch, lazy scan metadata, SSRF
guard), :func:`register_core_fetchers`, and the additive
:class:`SourceEntryRef` classification axes (issue #227).
"""

from __future__ import annotations

import pytest

from gispulse.core.fetchers import (
    DUCKDB_SCAN_KEY,
    LazyFetcher,
    register_core_fetchers,
)
from gispulse.core.plugin_model import (
    AccessProtocol,
    AccessSpec,
    FetchMode,
    Payload,
    SourceDomain,
    SourceResult,
)
from gispulse.core.sources import (
    PROTOCOLS,
    DeclarativeSource,
    Fetcher,
    ProtocolRegistry,
    SourceEntryRef,
)
from gispulse.core.ssrf import SSRFError


# --------------------------------------------------------------------------
# Test double
# --------------------------------------------------------------------------


class _FakeFetcher(LazyFetcher):
    """Minimal concrete fetcher — exercises the base without any network."""

    protocol = AccessProtocol.REMOTE_TABLE
    payload = Payload.VECTOR

    def _reference_scan(self, access: AccessSpec, extent: object) -> str:
        bbox = "" if extent is None else f" /* bbox={extent} */"
        return f"read_parquet('{access.endpoint}'){bbox}"

    def _materialize(self, access: AccessSpec, extent: object) -> SourceResult:
        return SourceResult(
            payload=self.payload,
            mode=FetchMode.MATERIALIZE,
            data=f"rows@{access.endpoint}",
        )


class _FakeGDF:
    """Stand-in GeoDataFrame — the WFS fetcher only needs len()."""

    def __init__(self, n: int) -> None:
        self._n = n

    def __len__(self) -> int:
        return self._n


class _WfsDeclarativeSource(DeclarativeSource):
    name = "wfs-test"
    domain = SourceDomain.REGLEMENTAIRE
    payload = Payload.VECTOR

    def entries(self) -> list[SourceEntryRef]:
        return [
            SourceEntryRef(
                id="zones",
                name="Zones",
                access=AccessSpec(
                    protocol=AccessProtocol.WFS,
                    endpoint="https://data.geopf.fr/wfs/ows",
                    params={"typename": "wfs_du:zone_urba"},
                ),
                domain=self.domain,
                payload=self.payload,
                jurisdiction="FR",
            )
        ]


def _access(endpoint: str, **params: object) -> AccessSpec:
    return AccessSpec(
        protocol=AccessProtocol.REMOTE_TABLE,
        endpoint=endpoint,
        params=dict(params),
    )


# --------------------------------------------------------------------------
# LazyFetcher — abstractness & protocol conformance
# --------------------------------------------------------------------------


def test_lazyfetcher_is_abstract() -> None:
    with pytest.raises(TypeError):
        LazyFetcher()  # type: ignore[abstract]


def test_fake_fetcher_satisfies_fetcher_protocol() -> None:
    assert isinstance(_FakeFetcher(), Fetcher)


# --------------------------------------------------------------------------
# Mode dispatch
# --------------------------------------------------------------------------


def test_fetch_reference_builds_lazy_scan() -> None:
    result = _FakeFetcher().fetch(
        _access("https://example.com/data.parquet"), mode=FetchMode.REFERENCE
    )
    assert result.mode is FetchMode.REFERENCE
    assert result.reference == "https://example.com/data.parquet"
    assert result.metadata[DUCKDB_SCAN_KEY] == ("read_parquet('https://example.com/data.parquet')")
    # REFERENCE moves no bytes.
    assert result.data is None


def test_fetch_materialize_is_the_default_mode() -> None:
    result = _FakeFetcher().fetch(_access("https://example.com/data.parquet"))
    assert result.mode is FetchMode.MATERIALIZE
    assert result.data == "rows@https://example.com/data.parquet"


def test_reference_scan_receives_pushdown_extent() -> None:
    result = _FakeFetcher().virtual_table(
        _access("https://example.com/d.parquet"), extent=(0, 0, 1, 1)
    )
    assert "bbox=(0, 0, 1, 1)" in result.metadata[DUCKDB_SCAN_KEY]


# --------------------------------------------------------------------------
# SSRF guard (#199)
# --------------------------------------------------------------------------


@pytest.mark.parametrize("mode", [FetchMode.REFERENCE, FetchMode.MATERIALIZE])
def test_fetch_rejects_private_address(mode: FetchMode) -> None:
    with pytest.raises(SSRFError):
        _FakeFetcher().fetch(_access("http://127.0.0.1/secret.parquet"), mode=mode)


def test_guard_leaves_local_file_paths_alone() -> None:
    # A non-HTTP endpoint (a local file) is not an SSRF vector.
    result = _FakeFetcher().virtual_table(_access("/tmp/local.parquet"))
    assert result.metadata[DUCKDB_SCAN_KEY] == "read_parquet('/tmp/local.parquet')"


# --------------------------------------------------------------------------
# register_core_fetchers
# --------------------------------------------------------------------------


def test_register_core_fetchers_registers_the_full_roster() -> None:
    # A3-A6 (#229-#232), table-file, plus the classic WFS adapter (#192)
    # populate the roster: every first-party protocol used by the catalogue.
    reg = ProtocolRegistry()
    assert register_core_fetchers(reg) == 6
    for protocol in (
        AccessProtocol.REMOTE_TABLE,
        AccessProtocol.OGC_FEATURES,
        AccessProtocol.STAC,
        AccessProtocol.DOWNLOAD,
        AccessProtocol.TABLE_FILE,
    ):
        assert isinstance(reg.get_fetcher(protocol), LazyFetcher)

    from gispulse.adapters.ogc.wfs_fetcher import WfsFetcher

    assert isinstance(reg.get_fetcher(AccessProtocol.WFS), WfsFetcher)


def test_register_core_fetchers_resolves_wfs_for_declarative_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict] = []

    def _fake_fetch_wfs(cfg, *, bbox=None, cql_filter=None, **_kw):
        calls.append({"cfg": cfg, "bbox": bbox, "cql_filter": cql_filter})
        return _FakeGDF(3)

    monkeypatch.setattr(
        "gispulse.adapters.ogc.wfs_client.fetch_wfs",
        _fake_fetch_wfs,
    )

    reg = ProtocolRegistry()
    register_core_fetchers(reg)

    result = _WfsDeclarativeSource(reg).fetch(
        "zones",
        extent=(1.0, 2.0, 3.0, 4.0),
    )

    assert result.payload is Payload.VECTOR
    assert len(result.data) == 3
    assert calls[0]["cfg"].source_type == "wfs"
    assert calls[0]["cfg"].layer_name == "wfs_du:zone_urba"
    assert calls[0]["bbox"] == (1.0, 2.0, 3.0, 4.0)


def test_register_core_fetchers_defaults_to_global_registry() -> None:
    # register_core_fetchers() with no argument files the core adapters
    # into the process-wide PROTOCOLS registry. It is destructive
    # (override=True) — snapshot and restore so this test does not bleed
    # into the #192 adapters that self-register into PROTOCOLS at import.
    saved_fetchers = dict(PROTOCOLS._fetchers)
    saved_writers = dict(PROTOCOLS._writers)
    try:
        assert register_core_fetchers() == 6  # touches PROTOCOLS
        assert isinstance(PROTOCOLS, ProtocolRegistry)
        assert isinstance(PROTOCOLS.get_fetcher(AccessProtocol.REMOTE_TABLE), LazyFetcher)
    finally:
        PROTOCOLS._fetchers.clear()
        PROTOCOLS._fetchers.update(saved_fetchers)
        PROTOCOLS._writers.clear()
        PROTOCOLS._writers.update(saved_writers)


def test_fake_fetcher_registers_into_a_registry() -> None:
    reg = ProtocolRegistry()
    reg.register(_FakeFetcher())
    fetched = reg.get_fetcher(AccessProtocol.REMOTE_TABLE)
    assert isinstance(fetched, _FakeFetcher)


# --------------------------------------------------------------------------
# SourceEntryRef classification axes (issue #227)
# --------------------------------------------------------------------------


def test_source_entry_ref_axes_default_to_none() -> None:
    # Pre-#227 construction still works — axes are additive & optional.
    ref = SourceEntryRef(id="e1", name="Entry", access=_access("https://x/d.parquet"))
    assert ref.domain is None
    assert ref.payload is None
    assert ref.jurisdiction is None


def test_source_entry_ref_accepts_classification_axes() -> None:
    ref = SourceEntryRef(
        id="overture-places",
        name="Overture Places",
        access=_access("https://example.com/places.parquet"),
        domain=SourceDomain.OBSERVATION,
        payload=Payload.VECTOR,
        jurisdiction="world",
    )
    assert ref.domain is SourceDomain.OBSERVATION
    assert ref.payload is Payload.VECTOR
    assert ref.jurisdiction == "world"


# --------------------------------------------------------------------------
# Endpoint templating — resolve {key} placeholders from access.params
# --------------------------------------------------------------------------


def test_resolve_endpoint_noop_when_no_placeholder() -> None:
    # The hot path: untemplated endpoints must be returned unchanged so
    # existing core entries (worldwide_catalog.yml) pay nothing.
    access = _access("https://example.com/data.parquet")
    assert LazyFetcher._resolve_endpoint(access) is access


def test_resolve_endpoint_interpolates_single_placeholder() -> None:
    access = _access("https://example.com/{region}/data.parquet", region="eu")
    resolved = LazyFetcher._resolve_endpoint(access)
    assert resolved.endpoint == "https://example.com/eu/data.parquet"
    # ``params`` is preserved — protocol adapters still read ``layer``,
    # ``lat``/``lon`` etc. from it after resolution.
    assert resolved.params == {"region": "eu"}


def test_resolve_endpoint_interpolates_multiple_placeholders() -> None:
    access = _access(
        "https://host/{a}/{b}/{a}-file.json",
        a="01",
        b="parcelles",
    )
    resolved = LazyFetcher._resolve_endpoint(access)
    assert resolved.endpoint == "https://host/01/parcelles/01-file.json"


def test_resolve_endpoint_missing_param_raises_with_clear_message() -> None:
    access = _access("https://host/{departement}/file-{layer}.gz", departement="75")
    with pytest.raises(ValueError, match=r"requires params \['layer'\]"):
        LazyFetcher._resolve_endpoint(access)


def test_resolve_endpoint_malformed_template_raises() -> None:
    # An unclosed brace is a malformed Python format string — surface
    # the error early instead of letting ``format_map`` crash deep down.
    access = _access("https://host/{unterminated", value="x")
    with pytest.raises(ValueError, match=r"malformed endpoint template"):
        LazyFetcher._resolve_endpoint(access)


def test_resolve_endpoint_rejects_empty_positional_placeholder() -> None:
    # ``{}`` is a positional placeholder — meaningless for a URL template
    # (callers must spell the key) and a silent no-op would hide bugs.
    access = _access("https://host/{}/file.parquet", value="x")
    with pytest.raises(ValueError, match=r"empty placeholder"):
        LazyFetcher._resolve_endpoint(access)


def test_resolve_endpoint_rejects_numeric_placeholder() -> None:
    # ``{0}`` is a positional index — confusing and would interact with
    # ``params["0"]`` in surprising ways. Reject up front.
    access = AccessSpec(
        protocol=AccessProtocol.REMOTE_TABLE,
        endpoint="https://example.com/{0}/file.parquet",
        params={"0": "value"},
    )
    with pytest.raises(ValueError, match=r"positional/attribute/index"):
        LazyFetcher._resolve_endpoint(access)


def test_resolve_endpoint_rejects_attribute_or_index_placeholder() -> None:
    access = _access("https://example.com/{cfg.host}/file", cfg="x")
    with pytest.raises(ValueError, match=r"positional/attribute/index"):
        LazyFetcher._resolve_endpoint(access)


def test_resolve_endpoint_rejects_conversion_flag() -> None:
    # ``{key!r}`` would inject quotes around the value — not what a URL
    # template author meant. Reject so the typo is loud.
    access = _access("https://example.com/{key!r}/file.parquet", key="x")
    with pytest.raises(ValueError, match=r"conversion flag"):
        LazyFetcher._resolve_endpoint(access)


def test_resolve_endpoint_rejects_format_spec() -> None:
    # ``{key:>3}`` would pad the value — again not URL semantics.
    access = _access("https://example.com/{key:>3}/file.parquet", key="x")
    with pytest.raises(ValueError, match=r"format spec"):
        LazyFetcher._resolve_endpoint(access)


def test_resolve_endpoint_escaped_braces_are_not_placeholders() -> None:
    # ``{{`` is a literal brace in ``str.format`` — leave the endpoint
    # alone (no params lookup) and unescape via ``format_map`` at fetch.
    access = _access("https://host/{{literal}}/data.parquet")
    resolved = LazyFetcher._resolve_endpoint(access)
    # ``{`` was present so we go through the parse path, but no fields
    # were found → access returned unchanged.
    assert resolved is access


def test_virtual_table_resolves_template_before_dispatch() -> None:
    result = _FakeFetcher().virtual_table(
        _access("https://example.com/{dpt}/data.parquet", dpt="75")
    )
    # The scan SQL — built by ``_reference_scan`` from the resolved
    # access — must show the interpolated endpoint, not the template.
    assert result.metadata[DUCKDB_SCAN_KEY] == (
        "read_parquet('https://example.com/75/data.parquet')"
    )
    assert result.reference == "https://example.com/75/data.parquet"


def test_materialize_resolves_template_before_dispatch() -> None:
    result = _FakeFetcher().fetch(
        _access("https://example.com/{dpt}/data.parquet", dpt="75"),
        mode=FetchMode.MATERIALIZE,
    )
    assert result.data == "rows@https://example.com/75/data.parquet"


def test_ssrf_guard_runs_against_resolved_endpoint() -> None:
    # A template resolving to a private address must still be rejected —
    # SSRF policy must see the URL that will actually be reached, not
    # the templated form.
    with pytest.raises(SSRFError):
        _FakeFetcher().fetch(
            _access("http://{addr}/secret", addr="127.0.0.1"),
            mode=FetchMode.REFERENCE,
        )
