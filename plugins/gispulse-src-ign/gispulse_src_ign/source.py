"""IGN reference DataSource — BD TOPO + Admin Express vector layers.

A :class:`~gispulse.plugins.api.DeclarativeSource` over the IGN
Géoplateforme WFS. The plugin only *declares* the entries; the WFS
round-trip is run by the registered protocol adapter (issue #192), so
this package ships zero network code beyond the cheap ``revision()``
freshness probe.

GEOFLA is deprecated upstream — ``geofla`` is kept as a legacy alias of
the Admin Express ``communes`` entry.
"""

from __future__ import annotations

from gispulse.plugins.api import (
    AccessProtocol,
    AccessSpec,
    DeclarativeSource,
    Payload,
    SourceDomain,
    SourceEntryRef,
)

# IGN Géoplateforme — public WFS endpoint (no API key required).
_GEOPLATEFORME_WFS = "https://data.geopf.fr/wfs/ows"
_WFS_CAPABILITIES = (
    f"{_GEOPLATEFORME_WFS}?SERVICE=WFS&VERSION=2.0.0&REQUEST=GetCapabilities"
)
_REVISION_TIMEOUT_S = 8.0

# Legacy alias — GEOFLA was retired upstream in favour of Admin Express.
_ALIASES = {"geofla": "communes"}

# entry_id -> (human label, WFS typename)
_ENTRIES: dict[str, tuple[str, str]] = {
    "batiments": ("Bâtiments (BD TOPO)", "BDTOPO_V3:batiment"),
    "routes": ("Tronçons de route (BD TOPO)", "BDTOPO_V3:troncon_de_route"),
    "cours_eau": ("Cours d'eau (BD TOPO)", "BDTOPO_V3:cours_d_eau"),
    "communes": ("Communes (Admin Express)", "ADMINEXPRESS-COG.LATEST:commune"),
    "departements": (
        "Départements (Admin Express)",
        "ADMINEXPRESS-COG.LATEST:departement",
    ),
    "regions": ("Régions (Admin Express)", "ADMINEXPRESS-COG.LATEST:region"),
}


def _probe_revision(url: str) -> str | None:
    """Return a freshness token for ``url`` via a single HTTP HEAD.

    Mirrors ``gispulse-src-cadastre`` (#198): derives the token from the
    ``ETag`` / ``Last-Modified`` header, returns ``None`` on any network
    error so the source watcher skips it rather than firing spuriously.
    """
    import httpx  # local import — keeps module import network-free

    try:
        resp = httpx.head(
            url, timeout=_REVISION_TIMEOUT_S, follow_redirects=True
        )
    except Exception:  # noqa: BLE001 — any transport error ⇒ unknown
        return None
    etag = resp.headers.get("etag")
    if etag:
        return etag.strip('"')
    last_modified = resp.headers.get("last-modified")
    if last_modified:
        return last_modified
    return None


class IgnSource(DeclarativeSource):
    """IGN reference data (BD TOPO + Admin Express) as a GISPulse source."""

    name = "ign"
    domain = SourceDomain.BASE
    payload = Payload.VECTOR
    jurisdiction = "FR"

    def entries(self) -> list[SourceEntryRef]:
        return [
            SourceEntryRef(
                id=entry_id,
                name=label,
                access=AccessSpec(
                    protocol=AccessProtocol.WFS,
                    endpoint=_GEOPLATEFORME_WFS,
                    params={"typename": typename},
                    format="application/json",
                ),
                # revision() probes the WFS live (#198) — no stale token.
                revision_token=None,
                metadata={"provider": "IGN", "typename": typename},
            )
            for entry_id, (label, typename) in _ENTRIES.items()
        ]

    def _entry(self, entry_id: str) -> SourceEntryRef:
        """Resolve the legacy ``geofla`` alias before the normal lookup."""
        return super()._entry(_ALIASES.get(entry_id, entry_id))

    def revision(self, entry_id: str) -> str | None:
        """Cheap freshness token for the source watcher (#197/#198).

        One HTTP HEAD against the Géoplateforme WFS GetCapabilities —
        the millésime is service-wide, so every entry shares one probe.
        """
        self._entry(entry_id)  # validate id / resolve alias
        return _probe_revision(_WFS_CAPABILITIES)
