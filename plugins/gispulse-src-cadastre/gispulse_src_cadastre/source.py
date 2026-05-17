"""French cadastre DataSource — parcels, communes and buildings.

A :class:`~gispulse.plugins.api.DeclarativeSource`: the plugin only
*declares* the available entries and their :class:`AccessSpec`; the
actual WFS request is delegated to the registered protocol adapter, so
this package ships zero network code.

Data: IGN Géoplateforme WFS, ``CADASTRALPARCELS.PARCELLAIRE_EXPRESS``.
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
_LAYER_PREFIX = "CADASTRALPARCELS.PARCELLAIRE_EXPRESS"

# WFS GetCapabilities URL — the cheap freshness probe target for
# revision() (issue #198). A HEAD against it reads the ETag /
# Last-Modified header without downloading the document.
_WFS_CAPABILITIES = (
    f"{_GEOPLATEFORME_WFS}?SERVICE=WFS&VERSION=2.0.0&REQUEST=GetCapabilities"
)
_REVISION_TIMEOUT_S = 8.0


def _probe_revision(url: str) -> str | None:
    """Return a freshness token for ``url`` via a single HTTP HEAD.

    Derives the token from the ``ETag`` (preferred) or ``Last-Modified``
    response header. Returns ``None`` — meaning "freshness unknown" — on
    any network error or when the endpoint exposes neither header, so
    the source watcher skips it rather than emitting a spurious change.
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


class CadastreSource(DeclarativeSource):
    """French cadastre (Parcellaire Express) exposed as a GISPulse source."""

    name = "cadastre"
    domain = SourceDomain.FONCIER
    payload = Payload.VECTOR
    jurisdiction = "FR"

    def entries(self) -> list[SourceEntryRef]:
        return [
            self._entry_ref("parcelles", "Parcelles cadastrales", "parcelle"),
            self._entry_ref("communes", "Communes cadastrales", "commune"),
            self._entry_ref("batiments", "Bâtiments cadastraux", "batiment"),
        ]

    @staticmethod
    def _entry_ref(entry_id: str, label: str, layer: str) -> SourceEntryRef:
        return SourceEntryRef(
            id=entry_id,
            name=label,
            access=AccessSpec(
                protocol=AccessProtocol.WFS,
                endpoint=_GEOPLATEFORME_WFS,
                params={"typename": f"{_LAYER_PREFIX}:{layer}"},
                format="application/json",
            ),
            # revision() probes the WFS live (issue #198); the declared
            # token stays None so nothing hard-codes a stale millésime.
            revision_token=None,
            metadata={"provider": "IGN", "dataset": "Parcellaire Express"},
        )

    def revision(self, entry_id: str) -> str | None:
        """Cheap freshness token for the source watcher (issue #187/#198).

        Issues a single HTTP HEAD against the Géoplateforme WFS
        GetCapabilities URL and derives the token from its ``ETag`` /
        ``Last-Modified`` header — never a full ``fetch()``. The
        Parcellaire Express millésime is dataset-wide, so every entry
        shares one probe; ``entry_id`` is only validated here.

        Returns ``None`` when the endpoint is unreachable or exposes no
        freshness header — the watcher treats that as "unknown" and
        skips it instead of emitting a spurious ``source.changed``.
        """
        self._entry(entry_id)  # validate the id
        return _probe_revision(_WFS_CAPABILITIES)

    def schema(self, entry_id: str) -> dict:
        """Normalised attribute schema of a cadastre layer."""
        self._entry(entry_id)  # validates the id
        common = {"idu": "str", "geometry": "geometry"}
        if entry_id == "parcelles":
            return {**common, "commune": "str", "section": "str", "numero": "str",
                    "contenance": "int"}
        if entry_id == "communes":
            return {**common, "nom": "str", "code_insee": "str"}
        return {**common, "nature": "str"}  # batiments
