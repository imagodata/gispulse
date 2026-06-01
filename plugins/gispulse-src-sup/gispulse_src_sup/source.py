"""French SUP DataSource — Servitudes d'Utilité Publique WFS layers.

This package is a :class:`~gispulse.plugins.api.DeclarativeSource`: it
declares the public WFS endpoint, typenames and optional CQL filters.
Actual materialisation is delegated to the host ``WfsFetcher`` through
``AccessProtocol.WFS``.

The filtered ``heritage-abf`` and ``risk-ppr-zoning`` entries are views
over ``wfs_sup:assiette_sup_s``. They are intentionally not promoted to
product-level rules here; this source exposes raw SUP data only.
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

_GEOPLATEFORME_WFS = "https://data.geopf.fr/wfs/ows"
_WFS_CAPABILITIES = (
    f"{_GEOPLATEFORME_WFS}?SERVICE=WFS&VERSION=2.0.0&REQUEST=GetCapabilities"
)
_REVISION_TIMEOUT_S = 8.0

_CNIG_DOWNLOAD_BY_PARTITION = (
    "https://www.geoportail-urbanisme.gouv.fr/api/document/"
    "download-by-partition/{partition}"
)
_PACK_SUP_DEFAULT_PARTITION = "172014607_SUP_69_AC1"
_PACK_SUP_DEFAULT_CODE_GEO = "69"
_PACK_SUP_DEFAULT_CATEGORIE = "AC1"

# Entry-id -> (display label, WFS typename, optional CQL filter).
#
# gispulse-permis keeps SUP type constants as lowercase (ac1, pm1bis...).
# The WFS-facing convention declared here is uppercase CnIG codes in
# cql_filter, matching the provided authoritative task inputs.
_ENTRIES: dict[str, tuple[str, str, str | None]] = {
    "servitude": (
        "SUP — servitudes",
        "wfs_sup:servitude",
        None,
    ),
    "assiette-surf": (
        "SUP — assiettes surfaciques",
        "wfs_sup:assiette_sup_s",
        None,
    ),
    "assiette-lin": (
        "SUP — assiettes linéaires",
        "wfs_sup:assiette_sup_l",
        None,
    ),
    "assiette-pct": (
        "SUP — assiettes ponctuelles",
        "wfs_sup:assiette_sup_p",
        None,
    ),
    "generateur-surf": (
        "SUP — générateurs surfaciques",
        "wfs_sup:generateur_sup_s",
        None,
    ),
    "generateur-lin": (
        "SUP — générateurs linéaires",
        "wfs_sup:generateur_sup_l",
        None,
    ),
    "generateur-pct": (
        "SUP — générateurs ponctuels",
        "wfs_sup:generateur_sup_p",
        None,
    ),
    "heritage-abf": (
        "SUP — assiettes patrimoine et abords ABF",
        "wfs_sup:assiette_sup_s",
        "suptype IN ('AC1','AC2','AC4')",
    ),
    "risk-ppr-zoning": (
        "SUP — assiettes zonages PPR",
        "wfs_sup:assiette_sup_s",
        "suptype IN ('PM1','PM1BIS','PM3')",
    ),
}


def sup_partition(code_geo: str, categorie: str, *, id_gest: str | None = None) -> str:
    """Return a CNIG/GPU SUP partition with optional gestionnaire prefix."""
    code = str(code_geo).strip().upper()
    cat = str(categorie).strip().upper()
    if not code:
        raise ValueError("SUP codeGeo cannot be empty")
    if not cat:
        raise ValueError("SUP categorie cannot be empty")
    prefix = f"{str(id_gest).strip()}_" if id_gest else ""
    return f"{prefix}SUP_{code}_{cat}"


def _probe_revision(url: str) -> str | None:
    """Return a freshness token for ``url`` via a single HTTP HEAD."""
    import httpx  # local import keeps module import network-free

    try:
        resp = httpx.head(
            url, timeout=_REVISION_TIMEOUT_S, follow_redirects=True
        )
    except Exception:  # noqa: BLE001 - transport errors mean unknown freshness
        return None
    etag = resp.headers.get("etag")
    if etag:
        return etag.strip('"')
    last_modified = resp.headers.get("last-modified")
    if last_modified:
        return last_modified
    return None


def _probe_redirect_revision(url: str) -> str | None:
    """Return Location / ETag / Last-Modified from a cheap HEAD probe."""
    import httpx

    try:
        resp = httpx.head(
            url, timeout=_REVISION_TIMEOUT_S, follow_redirects=False
        )
    except Exception:  # noqa: BLE001 - transport errors mean unknown freshness
        return None
    location = resp.headers.get("location")
    if location:
        return location
    etag = resp.headers.get("etag")
    if etag:
        return etag.strip('"')
    last_modified = resp.headers.get("last-modified")
    if last_modified:
        return last_modified
    return None


def _resolve_endpoint(access: AccessSpec) -> str:
    if "{" not in access.endpoint:
        return access.endpoint
    return access.endpoint.format_map(access.params)


class SupSource(DeclarativeSource):
    """Géoplateforme SUP WFS layers and filtered assiette views."""

    name = "sup"
    domain = SourceDomain.REGLEMENTAIRE
    payload = Payload.VECTOR
    jurisdiction = "FR"

    def entries(self) -> list[SourceEntryRef]:
        refs: list[SourceEntryRef] = []
        for entry_id, (label, typename, cql_filter) in _ENTRIES.items():
            params = {"typename": typename}
            metadata = {
                "provider": "IGN / Géoportail de l'Urbanisme",
                "platform": "WFS SUP",
                "typename": typename,
            }
            if cql_filter:
                params["cql_filter"] = cql_filter
                metadata["suptype_filter"] = cql_filter
            refs.append(
                SourceEntryRef(
                    id=entry_id,
                    name=label,
                    access=AccessSpec(
                        protocol=AccessProtocol.WFS,
                        endpoint=_GEOPLATEFORME_WFS,
                        params=params,
                        format="application/json",
                    ),
                    revision_token=None,
                    domain=self.domain,
                    payload=self.payload,
                    jurisdiction=self.jurisdiction,
                    metadata=metadata,
                )
            )
        refs.append(
            SourceEntryRef(
                id="pack_sup",
                name="SUP CNIG — archives sources bulk par partition",
                access=AccessSpec(
                    protocol=AccessProtocol.DOWNLOAD,
                    endpoint=_CNIG_DOWNLOAD_BY_PARTITION,
                    params={
                        "partition": _PACK_SUP_DEFAULT_PARTITION,
                        "codeGeo": _PACK_SUP_DEFAULT_CODE_GEO,
                        "categorie": _PACK_SUP_DEFAULT_CATEGORIE,
                    },
                    format="application/zip",
                ),
                revision_token=None,
                domain=self.domain,
                payload=self.payload,
                jurisdiction=self.jurisdiction,
                metadata={
                    "provider": "IGN / Géoportail de l'Urbanisme",
                    "platform": "Géoportail de l'Urbanisme téléchargement",
                    "base_key": "pack_sup",
                    "archive_format": "zip",
                    "format": "cnig-zip",
                    "partition_pattern": "{idGest_}SUP_<codeGeo>_<categorie>",
                    "code_geo_default": _PACK_SUP_DEFAULT_CODE_GEO,
                    "department_param": "codeGeo",
                    "category_param": "categorie",
                    "join_keys": ("idsup", "suptype"),
                },
            )
        )
        return refs

    def schema(self, entry_id: str) -> dict:
        """Raw WFS SUP attributes shared by layers and filtered views."""
        self._entry(entry_id)
        if entry_id == "pack_sup":
            return {
                "gid": "int",
                "suptype": "str",
                "idsup": "str",
                "partition": "str",
                "nomsuplitt": "str",
                "geometry": "geometry",
            }
        return {
            "gid": "int",
            "suptype": "str",
            "idsup": "str",
            "nomsuplitt": "str",
            "geometry": "geometry",
        }

    def revision(self, entry_id: str) -> str | None:
        """Cheap freshness token from WFS headers or bulk redirect Location."""
        entry = self._entry(entry_id)
        if entry_id == "pack_sup":
            return _probe_redirect_revision(_resolve_endpoint(entry.access))
        return _probe_revision(_WFS_CAPABILITIES)
