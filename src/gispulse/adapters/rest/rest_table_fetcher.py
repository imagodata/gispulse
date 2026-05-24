"""Paginated tabular-JSON REST fetcher — ``AccessProtocol.REST_TABLE``.

Issue #196. Sibling of :class:`RestGeoJsonFetcher` (#192): where that
adapter reads a GeoJSON ``FeatureCollection``, this one reads a tabular
JSON REST API that answers ``{"data": [...], "next": ...}`` — the shape
served by Géorisques (``/api/v1/...``), BAN and RNB.

The fetcher is **materialize-only**: a paginated REST API has no
zero-copy DuckDB scan, so :attr:`~core.plugin_model.FetchMode.REFERENCE`
raises. Rows are streamed to a local newline-delimited JSON (JSONL) file
and the path is returned in :attr:`SourceResult.data` with
``payload = Payload.TABLE`` for a downstream key-based spatial join.

Like :class:`RestGeoJsonFetcher`, importing this module self-registers
the fetcher in the process-wide :data:`core.sources.PROTOCOLS` registry
(idempotent), so the ETL fetch path has a real ``rest-table`` adapter to
dispatch to.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
import time
from typing import Any
from urllib.parse import urlencode, urljoin, urlsplit

from gispulse.core.logging import get_logger
from gispulse.core.plugin_model import (
    AccessProtocol,
    AccessSpec,
    FetchMode,
    Payload,
    SourceResult,
)

log = get_logger(__name__)

_DEFAULT_TIMEOUT_S = 20.0
#: Hard ceiling on pages followed when the AccessSpec does not set one —
#: a tabular API with a runaway ``next`` must never loop unbounded.
_DEFAULT_MAX_PAGES = 1000


def _same_origin(a: str, b: str) -> bool:
    """True if ``a`` and ``b`` share scheme + host + port.

    A paginated ``next`` link must not steer the fetch to another host —
    a malicious catalogue entry could otherwise exfiltrate the request or
    pivot to an internal service across pages.
    """
    sa, sb = urlsplit(a), urlsplit(b)
    return (sa.scheme, sa.netloc) == (sb.scheme, sb.netloc)


def _get_json(url: str, timeout: float) -> dict[str, Any]:
    """GET ``url`` and return the parsed JSON body."""
    import httpx

    # follow_redirects is OFF: httpx would otherwise chase a 3xx to an
    # arbitrary host *after* the SSRF/same-origin guard has cleared the
    # original URL, re-opening the very hole the guard closes (#199).
    resp = httpx.get(
        url,
        timeout=timeout,
        follow_redirects=False,
        headers={"Accept": "application/json"},
    )
    resp.raise_for_status()
    return resp.json()


class RestTableFetcher:
    """:class:`~core.sources.Fetcher` for ``AccessProtocol.REST_TABLE``."""

    protocol = AccessProtocol.REST_TABLE
    payload = Payload.TABLE

    def fetch(
        self,
        access: AccessSpec,
        *,
        extent: Any | None = None,
        mode: FetchMode = FetchMode.MATERIALIZE,
    ) -> SourceResult:
        if mode is FetchMode.REFERENCE:
            raise NotImplementedError("REST_TABLE is materialize-only")

        params = dict(access.params or {})
        timeout = float(params.get("timeout", _DEFAULT_TIMEOUT_S))
        pagination = dict(params.get("pagination") or {})
        data_key = pagination.get("data_key", "data")
        next_key = pagination.get("next_key")
        # "list" (default): rows live in body[data_key]; "object": the whole
        # JSON body is a single row (non-paginated single-object endpoints,
        # e.g. Géorisques RGA/SSP 2024+).
        row_shape = pagination.get("row_shape", "list")
        # HTTP statuses that mean "no data here" rather than an error — the
        # walk treats them as an empty result (e.g. Géorisques tri_zonage 404).
        empty_statuses = set(pagination.get("empty_statuses") or [])
        empty_body_is_empty = bool(pagination.get("empty_body_is_empty", False))
        max_pages = int(pagination.get("max_pages", _DEFAULT_MAX_PAGES))
        max_rows = pagination.get("max_rows")
        max_rows = int(max_rows) if max_rows is not None else None
        max_total_seconds = pagination.get("max_total_seconds")
        max_total_seconds = (
            float(max_total_seconds) if max_total_seconds is not None else None
        )
        deadline = (
            time.monotonic() + max_total_seconds
            if max_total_seconds is not None
            else None
        )

        import httpx

        from gispulse.core.ssrf import guard_outbound_url

        query = dict(params.get("query") or {})
        origin = access.endpoint
        if query:
            sep = "&" if "?" in origin else "?"
            origin = f"{origin}{sep}{urlencode(query)}"
        rows: list[Any] = []
        page_count = 0
        seen: set[str] = set()
        url: str | None = origin
        while url:
            guard_outbound_url(url)
            seen.add(url)
            try:
                body = _get_json(url, timeout)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in empty_statuses:
                    break  # "no data here" — leave the result empty
                raise
            except json.JSONDecodeError:
                if empty_body_is_empty:
                    break  # empty/malformed body configured as "no data here"
                raise
            page_count += 1
            if row_shape == "object":
                rows.append(body)  # the whole body is a single row
            else:
                page_rows = body.get(data_key)
                if isinstance(page_rows, list):  # ignore a non-list data_key
                    rows.extend(page_rows)
            if max_rows is not None and len(rows) >= max_rows:
                del rows[max_rows:]
                break
            if page_count >= max_pages:
                break
            if deadline is not None and time.monotonic() >= deadline:
                break
            nxt = body.get(next_key) if next_key else None
            if nxt:
                # Resolve a relative ``next`` ("?page=2") against the current
                # page URL, then re-guard the absolute result.
                candidate = urljoin(url, str(nxt))
                if candidate not in seen and _same_origin(origin, candidate):
                    url = candidate
                else:
                    url = None
            else:
                url = None

        local_path = params.get("local_path")
        if not local_path:
            handle = tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False)
            handle.close()
            local_path = handle.name
        digest = hashlib.sha256()
        with open(local_path, "wb") as fh:
            for row in rows:
                line = (json.dumps(row, separators=(",", ":")) + "\n").encode("utf-8")
                digest.update(line)
                fh.write(line)

        log.info(
            "rest_table_materialized",
            endpoint=access.endpoint,
            row_count=len(rows),
            page_count=page_count,
        )
        return SourceResult(
            payload=Payload.TABLE,
            mode=FetchMode.MATERIALIZE,
            data=local_path,
            metadata={
                "row_count": len(rows),
                "page_count": page_count,
                "source_url": access.endpoint,
                "sha256": digest.hexdigest(),
            },
        )


def register_rest_table_fetcher(registry: Any | None = None) -> None:
    """Register a :class:`RestTableFetcher` under ``REST_TABLE`` — idempotent."""
    from gispulse.core.sources import PROTOCOLS, ProtocolNotSupported

    target = registry if registry is not None else PROTOCOLS
    try:
        target.get_fetcher(AccessProtocol.REST_TABLE)
        return  # already registered
    except ProtocolNotSupported:
        pass
    target.register(RestTableFetcher())


# Importing this module wires the fetcher into the global registry (#192 pattern).
register_rest_table_fetcher()


__all__ = ["RestTableFetcher", "register_rest_table_fetcher"]
