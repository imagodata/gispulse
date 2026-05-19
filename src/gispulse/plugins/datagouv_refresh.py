"""data.gouv.fr catalogue-freshness probe for the worldwide aggregator (A13, #239).

EPIC #226 (v1.9.0). The worldwide catalogue ships a handful of French
open-data entries (``family: opendata-fr``). Their ``revision_token`` is
the freshness token :meth:`WorldwideCatalogSource.revision` returns and
the :class:`SourceWatcherRegistry` (A14) polls.

This module probes the **data.gouv.fr** API for the *current* publication
timestamp of every catalogue entry that opts in via a
``metadata.datagouv_dataset`` slug, and reports which entries have
drifted from the curated ``revision_token``. It backs the
``refresh_worldwide_catalog`` MCP tool — a *priority-low* deliverable of
issue #239.

The probe is **read-only and idempotent**: it never mutates the curated,
comment-rich ``worldwide_catalog.yml`` (a YAML round-trip would strip its
comments), and two calls against an unchanged remote yield the same
report. An operator applies a drift by editing the YAML by hand.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from gispulse.core.logging import get_logger
from gispulse.plugins.worldwide_source import DEFAULT_CATALOG_PATH, load_worldwide_catalog

log = get_logger(__name__)

#: data.gouv.fr open API v1 base — a fixed, allow-listed host. The MCP
#: tool exposes no URL argument, so an untrusted model cannot redirect
#: the probe at an internal service.
DATAGOUV_API = "https://www.data.gouv.fr/api/1"

#: Metadata key a catalogue entry sets to opt into the data.gouv probe —
#: its value is a data.gouv dataset slug or id.
DATAGOUV_METADATA_KEY = "datagouv_dataset"


def _probe_datagouv(dataset_ref: str, *, base: str = DATAGOUV_API) -> str | None:
    """Return the ``last_modified`` timestamp of a data.gouv.fr dataset.

    SSRF-guarded (#199), short-timeout. Returns ``None`` when the dataset
    exposes no usable timestamp.

    Raises:
        Exception: a transport / HTTP error — surfaced per entry by
            :func:`refresh_datagouv_entries` so one bad slug never
            aborts the whole probe.
    """
    import httpx

    from gispulse.core.ssrf import guard_outbound_url

    url = f"{base}/datasets/{dataset_ref}/"
    guard_outbound_url(url)
    resp = httpx.get(
        url,
        follow_redirects=True,
        timeout=10.0,
        headers={"Accept": "application/json"},
    )
    resp.raise_for_status()
    data = resp.json()
    internal = data.get("internal") or {}
    return (
        data.get("last_modified")
        or internal.get("last_modified_internal")
        or data.get("last_update")
    )


def refresh_datagouv_entries(
    catalog_path: Path | None = None,
    *,
    base: str = DATAGOUV_API,
    probe: Callable[..., str | None] | None = None,
) -> dict[str, Any]:
    """Probe data.gouv.fr for every catalogue entry that declares a dataset.

    Walks the worldwide catalogue, and for each entry carrying a
    :data:`DATAGOUV_METADATA_KEY` slug queries data.gouv.fr for its
    current publication timestamp. The returned report says which
    ``revision_token`` values are stale — it changes nothing on disk.

    Args:
        catalog_path: Catalogue file. Defaults to the shipped one.
        base:         data.gouv.fr API base (override for tests).
        probe:        Injected ``(dataset_ref, *, base) -> str | None``
                      seam — defaults to :func:`_probe_datagouv`. Tests
                      pass a stub so CI moves no bytes off the box.

    Returns:
        ``{catalog, checked, entries: [...], stale: [...]}``. Each entry
        record carries ``id`` / ``datagouv_dataset`` / ``current_token``
        / ``datagouv_revision`` / ``up_to_date``, or an ``error`` key.
    """
    probe_fn = probe or _probe_datagouv
    entries = load_worldwide_catalog(catalog_path)
    refreshable = [
        e for e in entries if e.metadata.get(DATAGOUV_METADATA_KEY)
    ]

    records: list[dict[str, Any]] = []
    for entry in refreshable:
        dataset_ref = str(entry.metadata[DATAGOUV_METADATA_KEY])
        record: dict[str, Any] = {
            "id": entry.id,
            "datagouv_dataset": dataset_ref,
            "current_token": entry.revision_token,
        }
        try:
            last_modified = probe_fn(dataset_ref, base=base)
        except Exception as exc:  # noqa: BLE001 — isolate one bad slug
            record["error"] = str(exc)
            log.warning(
                "datagouv_probe_failed", entry=entry.id, dataset=dataset_ref, error=str(exc)
            )
            records.append(record)
            continue
        new_token = f"datagouv:{last_modified}" if last_modified else None
        record["datagouv_revision"] = new_token
        record["up_to_date"] = new_token is not None and new_token == entry.revision_token
        records.append(record)

    stale = [
        r["id"]
        for r in records
        if "error" not in r and not r["up_to_date"]
    ]
    log.info(
        "datagouv_refresh_probed",
        checked=len(records),
        stale=len(stale),
    )
    return {
        "catalog": str(catalog_path or DEFAULT_CATALOG_PATH),
        "checked": len(records),
        "entries": records,
        "stale": stale,
    }


__all__ = [
    "DATAGOUV_API",
    "DATAGOUV_METADATA_KEY",
    "refresh_datagouv_entries",
]
