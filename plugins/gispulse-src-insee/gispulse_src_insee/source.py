"""French INSEE DataSource — statistical units served by Géoplateforme WFS.

A :class:`~gispulse.plugins.api.DeclarativeSource`: the plugin only
declares its access spec; the actual WFS request is delegated to the
registered WFS fetcher. This package ships zero network code besides a
HEAD probe for :meth:`revision`.

The first entry is IRIS (Ilots Regroupés pour l'Information
Statistique), an INSEE infra-communal statistical mesh redistributed
through the IGN Géoplateforme WFS.
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

# IGN Géoplateforme — public WFS endpoint for statistical units.
_GEOPLATEFORME_WFS = "https://data.geopf.fr/wfs/ows"
_WFS_CAPABILITIES = (
    f"{_GEOPLATEFORME_WFS}?SERVICE=WFS&VERSION=2.0.0&REQUEST=GetCapabilities"
)
_REVISION_TIMEOUT_S = 8.0

_IRIS_TYPENAME = "STATISTICALUNITS.IRIS:contour_iris"

_ENTRIES: dict[str, tuple[str, str]] = {
    "iris": (
        "IRIS — découpage infra-communal INSEE",
        _IRIS_TYPENAME,
    ),
}


def _probe_revision(url: str) -> str | None:
    """Return a freshness token for ``url`` via a single HTTP HEAD.

    Derives the token from the ``ETag`` (preferred) / ``Last-Modified``
    response header. Returns ``None`` on any network error or when the
    endpoint exposes neither header, so the source watcher skips it
    rather than emitting a spurious change.
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


class InseeSource(DeclarativeSource):
    """INSEE statistical units exposed through the Géoplateforme WFS."""

    name = "insee"
    domain = SourceDomain.STATISTIQUE
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
                revision_token=None,
                domain=self.domain,
                payload=self.payload,
                jurisdiction=self.jurisdiction,
                metadata={
                    "provider": "IGN / INSEE",
                    "platform": "WFS Géoplateforme",
                    "license": "Licence Ouverte 2.0",
                    "update_cadence": "annuel",
                    "typename": typename,
                },
            )
            for entry_id, (label, typename) in _ENTRIES.items()
        ]

    def revision(self, entry_id: str) -> str | None:
        """Cheap freshness token for the source watcher.

        One HTTP HEAD against the Géoplateforme WFS GetCapabilities. The
        IRIS millésime is service-wide for this WFS declaration, so the
        entry id is only validated here.
        """
        self._entry(entry_id)  # validate the id
        return _probe_revision(_WFS_CAPABILITIES)

    def schema(self, entry_id: str) -> dict:
        """Raw upstream IRIS attributes exposed by the WFS layer."""
        self._entry(entry_id)  # validates the id
        return {
            "code_iris": "str",
            "nom_iris": "str",
            "insee_com": "str",
            "nom_com": "str",
            "type_iris": "str",
            "geometry": "geometry",
        }
