"""French cadastre DataSource — WFS point-query + Etalab bulk download.

A :class:`~gispulse.plugins.api.DeclarativeSource` covering the two
public redistributions of the DGFiP Plan Cadastral Informatisé (PCI
vecteur):

* **WFS — IGN Géoplateforme** (``data.geopf.fr/wfs/ows``,
  ``CADASTRALPARCELS.PARCELLAIRE_EXPRESS``). Three entries
  (``parcelles`` / ``communes`` / ``batiments``) for point-query and
  bbox-scoped reads against a live feature service.
* **Bulk — Etalab** (``cadastre.data.gouv.fr/data/etalab-cadastre``).
  Four entries (``parcelles_bulk`` / ``communes_bulk`` /
  ``sections_bulk`` / ``batiments_bulk``) targeting the quarterly
  per-département GeoJSON archives. Each bulk endpoint is a ``{key}``
  URL template — ``{departement}`` is resolved at fetch time so a
  single :class:`SourceEntryRef` covers all 101 départements without
  inflating the catalogue (cf. ``core/plugin_model.py``).

The two redistributions share the same upstream producer (DGFiP) and
millésime cadence, but the redistributor, the access protocol, the
freshness signal and the spatial granularity differ — hence the
``redistributor`` field in each entry's ``metadata``.
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

# Etalab bulk redistribution of the DGFiP PCI vecteur — quarterly
# millésime, GeoJSON-gzipped archives per département. The ``latest``
# segment is a stable alias rotating to the most recent release. The
# ``{departement}`` placeholder is resolved by ``dispatch_fetch`` from
# ``access.params`` at fetch time (cf. ``core/plugin_model.py``).
_ETALAB_CADASTRE_BASE = (
    "https://cadastre.data.gouv.fr/data/etalab-cadastre/latest/geojson/departements"
)

# data.gouv.fr dataset API — a small JSON metadata document carrying a
# top-level ``last_modified`` ISO-8601 timestamp updated on every
# resource refresh. Used by revision() for the bulk entries: HEAD on the
# .json.gz files alone won't surface a millésime change because the
# ``latest`` symlink target is stable between releases.
_ETALAB_DATASET_API = (
    "https://www.data.gouv.fr/api/1/datasets/cadastre/"
)
_REVISION_TIMEOUT_S = 8.0

# Per-département bulk entries declare the layer name once via this
# ``layer`` slot in ``access.params`` (next to ``departement``); the
# fetcher does not consume it but keeping it on the access keeps the
# entry self-describing for catalogue introspection.
_BULK_LAYERS: dict[str, str] = {
    "parcelles_bulk": "parcelles",
    "communes_bulk": "communes",
    "sections_bulk": "sections",
    "batiments_bulk": "batiments",
}

_BULK_LABELS: dict[str, str] = {
    "parcelles_bulk": "Parcelles cadastrales (bulk Etalab, par département)",
    "communes_bulk": "Communes cadastrales (bulk Etalab, par département)",
    "sections_bulk": "Sections cadastrales (bulk Etalab, par département)",
    "batiments_bulk": "Bâtiments cadastraux (bulk Etalab, par département)",
}


def _probe_revision_head(url: str) -> str | None:
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


def _probe_revision_datagouv(api_url: str) -> str | None:
    """Return ``last_modified`` from the data.gouv.fr dataset API.

    Same pattern as ``gispulse-src-dvf``: a small GET against the JSON
    metadata endpoint, returns the top-level ``last_modified`` string.
    The bulk archives behind the ``latest`` alias do not surface a
    millésime through HTTP headers (the symlink target is stable across
    releases), so a HEAD on the .json.gz is useless — the dataset-level
    API is the only honest signal.

    Returns ``None`` on any network error, non-2xx response, malformed
    JSON, or missing field — the watcher treats that as "unknown".
    """
    import httpx

    try:
        resp = httpx.get(
            api_url, timeout=_REVISION_TIMEOUT_S, follow_redirects=True
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception:  # noqa: BLE001 — any transport error ⇒ unknown
        return None
    token = data.get("last_modified") if isinstance(data, dict) else None
    return token if isinstance(token, str) and token else None


def _is_bulk_entry(entry_id: str) -> bool:
    return entry_id.endswith("_bulk")


class CadastreSource(DeclarativeSource):
    """French cadastre exposed as a GISPulse source — WFS + bulk redistributions."""

    name = "cadastre"
    domain = SourceDomain.FONCIER
    payload = Payload.VECTOR
    jurisdiction = "FR"

    def entries(self) -> list[SourceEntryRef]:
        return [
            # IGN Géoplateforme — WFS live, point-query / bbox-scoped.
            self._wfs_entry_ref("parcelles", "Parcelles cadastrales (WFS IGN)", "parcelle"),
            self._wfs_entry_ref("communes", "Communes cadastrales (WFS IGN)", "commune"),
            self._wfs_entry_ref("batiments", "Bâtiments cadastraux (WFS IGN)", "batiment"),
            # Etalab — quarterly bulk archive per département.
            self._bulk_entry_ref("parcelles_bulk"),
            self._bulk_entry_ref("communes_bulk"),
            self._bulk_entry_ref("sections_bulk"),
            self._bulk_entry_ref("batiments_bulk"),
        ]

    @staticmethod
    def _wfs_entry_ref(entry_id: str, label: str, layer: str) -> SourceEntryRef:
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
            domain=SourceDomain.FONCIER,
            payload=Payload.VECTOR,
            jurisdiction="FR",
            metadata={
                "provider": "DGFiP",
                "redistributor": "IGN Géoplateforme",
                "dataset": "Plan Cadastral Informatisé (PCI vecteur)",
                "redistribution": "Parcellaire Express",
                "update_cadence": "continuous",
                "license": "Licence Ouverte 2.0",
            },
        )

    @staticmethod
    def _bulk_entry_ref(entry_id: str) -> SourceEntryRef:
        layer = _BULK_LAYERS[entry_id]
        # Endpoint carries a ``{departement}`` template — resolved by
        # ``ProtocolRegistry.dispatch_fetch`` at fetch time from
        # ``access.params``. The default ``"75"`` is a placeholder for
        # the per-entry catalogue view; real ingest passes a département
        # via a derived AccessSpec or by overriding ``params``.
        return SourceEntryRef(
            id=entry_id,
            name=_BULK_LABELS[entry_id],
            access=AccessSpec(
                protocol=AccessProtocol.DOWNLOAD,
                endpoint=(
                    f"{_ETALAB_CADASTRE_BASE}/{{departement}}/"
                    f"cadastre-{{departement}}-{layer}.json.gz"
                ),
                params={"departement": "75", "layer": layer},
                format="application/geo+json+gzip",
            ),
            revision_token=None,
            domain=SourceDomain.FONCIER,
            payload=Payload.VECTOR,
            jurisdiction="FR",
            metadata={
                "provider": "DGFiP",
                "redistributor": "Etalab",
                "dataset": "Plan Cadastral Informatisé (PCI vecteur)",
                "redistribution": "Cadastre Etalab",
                "mirror": "cadastre.data.gouv.fr",
                "base_key": layer,
                "archive_format": "json.gz",
                "data_format": "geojson",
                "update_cadence": "quarterly",
                "license": "Licence Ouverte 2.0",
            },
        )

    def revision(self, entry_id: str) -> str | None:
        """Cheap freshness token, dispatched by entry family.

        WFS entries use a HEAD probe against the Géoplateforme
        GetCapabilities URL (issue #198). Bulk entries use a GET against
        the ``data.gouv.fr`` dataset metadata API — the ``latest`` alias
        on the .json.gz files does not surface a release change through
        HTTP headers, so the dataset-level ``last_modified`` is the only
        honest signal. Returns ``None`` when the probe is unreachable —
        the watcher treats that as "unknown".
        """
        self._entry(entry_id)  # validate the id
        if _is_bulk_entry(entry_id):
            return _probe_revision_datagouv(_ETALAB_DATASET_API)
        return _probe_revision_head(_WFS_CAPABILITIES)

    def schema(self, entry_id: str) -> dict:
        """Normalised attribute schema of a cadastre layer."""
        self._entry(entry_id)  # validates the id
        # Bulk entries surface the Etalab GeoJSON column shape, slightly
        # richer than the WFS Parcellaire Express (created/updated, the
        # ``arpente`` flag, etc.).
        if entry_id == "parcelles":
            return {
                "idu": "str", "geometry": "geometry",
                "commune": "str", "section": "str", "numero": "str",
                "contenance": "int",
            }
        if entry_id == "communes":
            return {"idu": "str", "geometry": "geometry", "nom": "str", "code_insee": "str"}
        if entry_id == "batiments":
            return {"idu": "str", "geometry": "geometry", "nature": "str"}
        # Bulk shapes verified against live Etalab GeoJSON for dpt 75
        # (cadastre.data.gouv.fr/data/etalab-cadastre/latest/geojson/...).
        if entry_id == "parcelles_bulk":
            return {
                "id": "str", "geometry": "geometry",
                "commune": "str", "prefixe": "str", "section": "str",
                "numero": "str", "contenance": "int", "arpente": "bool",
                "created": "date", "updated": "date",
            }
        if entry_id == "communes_bulk":
            return {
                "id": "str", "geometry": "geometry",
                "nom": "str", "created": "date", "updated": "date",
            }
        if entry_id == "sections_bulk":
            return {
                "id": "str", "geometry": "geometry",
                "commune": "str", "prefixe": "str", "code": "str",
                "created": "date", "updated": "date",
            }
        if entry_id == "batiments_bulk":
            # Etalab GeoJSON bâtiments features have no feature-id; the
            # identity is decomposed across ``type`` / ``nom`` / ``commune``.
            return {
                "geometry": "geometry",
                "type": "str", "nom": "str", "commune": "str",
                "created": "date", "updated": "date",
            }
        # Unreachable — ``_entry()`` above raised for unknown ids.
        return {}
