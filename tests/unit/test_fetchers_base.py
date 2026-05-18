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
from gispulse.core.sources import PROTOCOLS, Fetcher, ProtocolRegistry, SourceEntryRef
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


def _access(endpoint: str) -> AccessSpec:
    return AccessSpec(protocol=AccessProtocol.REMOTE_TABLE, endpoint=endpoint)


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
    assert result.metadata[DUCKDB_SCAN_KEY] == (
        "read_parquet('https://example.com/data.parquet')"
    )
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


def test_register_core_fetchers_empty_roster_is_safe() -> None:
    # A2 ships no concrete fetcher yet — registering must be a no-op, not a
    # crash, until A3-A6 populate the roster.
    assert register_core_fetchers(ProtocolRegistry()) == 0


def test_register_core_fetchers_defaults_to_global_registry() -> None:
    assert register_core_fetchers() == 0  # touches PROTOCOLS, registers nothing
    assert isinstance(PROTOCOLS, ProtocolRegistry)


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
