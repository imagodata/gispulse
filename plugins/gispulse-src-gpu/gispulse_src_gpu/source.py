"""French GPU DataSource — Géoportail de l'Urbanisme zones d'urbanisme.

A :class:`~gispulse.plugins.api.DeclarativeSource`: the plugin only
*declares* its access spec; the actual WFS request is delegated to the
:class:`WfsFetcher` registered in the :class:`ProtocolRegistry` (#209),
so this package ships zero network code besides a HEAD probe for
:meth:`revision`.

Data: Géoportail de l'Urbanisme (https://www.geoportail-urbanisme.gouv.fr/)
— the national platform mandated by the loi ALUR / ordonnance 2013-1184
for dematerialised urban-planning documents (PLU, PLUi, POS, cartes
communales, SCoT) plus their prescriptions and informational layers.

This pilot exposes nine WFS feature types of the ``wfs_du`` namespace
that together describe the urban-planning fabric of a parcel:

* the three core layers (``zone_urba``, ``prescription_surf``,
  ``doc_urba``) already consumed by ``gispulse-permis``;
* the two additional prescription geometries (``prescription_lin``,
  ``prescription_pct``);
* the three informational layers (``info_surf``, ``info_lin``,
  ``info_pct``);
* the carte-communale sectors (``secteur_cc``).

.. note::
   GPU is semantically a :class:`RegulatorySource` — every zone or
   prescription carries an applicable rule (a PLU regulation). Promoting
   :class:`GpuSource` to :class:`RegulatorySource` and wiring
   :meth:`ruleset` over the WFS attributes (``libelle``, ``typezone``,
   ``insee``, …) is left to a follow-up plugin once the
   :class:`RuleClause`-to-PLU mapping is stabilised. The declaration,
   schema and :meth:`revision` plumbing are usable today by the source
   watcher and any catalog / marketplace consumer.

.. note::
   Servitudes d'utilité publique (the seven ``wfs_du`` SUP feature types
   — ``servitude``, ``assiette_sup_s/l/p``, ``generateur_sup_s/l/p``,
   ``acte_sup``) are intentionally **not** in this pilot. They are
   conceptually distinct from the urban-planning layers and warrant a
   dedicated ``gispulse-src-sup`` package — most likely also a
   :class:`RegulatorySource`.
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

# IGN Géoplateforme — public WFS endpoint for the Géoportail de
# l'Urbanisme (no API key required).
_GEOPLATEFORME_WFS = "https://data.geopf.fr/wfs/ows"

# WFS GetCapabilities URL — cheap freshness probe target for
# revision() (issue #198). A HEAD against it reads the ``ETag`` /
# ``Last-Modified`` header without downloading the document.
_WFS_CAPABILITIES = (
    f"{_GEOPLATEFORME_WFS}?SERVICE=WFS&VERSION=2.0.0&REQUEST=GetCapabilities"
)
_REVISION_TIMEOUT_S = 8.0

_CNIG_DOWNLOAD_BY_PARTITION = (
    "https://www.geoportail-urbanisme.gouv.fr/api/document/"
    "download-by-partition/{partition}"
)
_GPU_DOCUMENTS_BULK_REVISION = "gpu-cnig-documents-bulk"
_GPU_DOCUMENTS_DEFAULT_PARTITION = "DU_200046977"
_GPU_DOCUMENTS_DEFAULT_DEPARTEMENT = "69"
_GPU_DOCUMENTS_ARCHIVE_FAMILIES = (
    "pack_plu1",
    "pack_plu2",
    "pack_plui",
    "pack_cc",
)


# Entry-id → (display label, WFS typename) — every WFS feature type
# this pilot exposes. Names mirror the layer naming on the Géoportail
# de l'Urbanisme so authors of triggers and pipelines find what they
# expect.
_ENTRIES: dict[str, tuple[str, str]] = {
    # Core zoning
    "zone-urba": (
        "Zones d'urbanisme (PLU, PLUi, POS)",
        "wfs_du:zone_urba",
    ),
    "doc-urba": (
        "Documents d'urbanisme — emprises et métadonnées",
        "wfs_du:doc_urba",
    ),
    "secteur-cc": (
        "Secteurs de carte communale",
        "wfs_du:secteur_cc",
    ),
    # Prescriptions — what the document constrains on the ground
    "prescription-surf": (
        "Prescriptions surfaciques",
        "wfs_du:prescription_surf",
    ),
    "prescription-lin": (
        "Prescriptions linéaires",
        "wfs_du:prescription_lin",
    ),
    "prescription-pct": (
        "Prescriptions ponctuelles",
        "wfs_du:prescription_pct",
    ),
    # Informations — what the document signals (non-constraining)
    "info-surf": (
        "Informations surfaciques",
        "wfs_du:info_surf",
    ),
    "info-lin": (
        "Informations linéaires",
        "wfs_du:info_lin",
    ),
    "info-pct": (
        "Informations ponctuelles",
        "wfs_du:info_pct",
    ),
}


def gpu_du_partition(code: str) -> str:
    """Return the CNIG/GPU DU partition for an INSEE or SIREN code."""
    cleaned = str(code).strip().upper()
    if not cleaned:
        raise ValueError("GPU DU partition code cannot be empty")
    return f"DU_{cleaned}"


def gpu_du_partitions_for_department(
    departement: str,
    *,
    codes_insee: list[str] | tuple[str, ...] = (),
    sirens: list[tuple[str, str]] | tuple[tuple[str, str], ...] = (),
) -> list[str]:
    """Build DU partitions attached to one département.

    Commune DU partitions are inferred from INSEE prefixes. Intercommunal
    DU partitions need an explicit ``(departement, siren)`` attachment
    because the SIREN itself carries no département prefix.
    """
    dept = str(departement).strip().upper()
    partitions: list[str] = []
    for code in codes_insee:
        cleaned = str(code).strip().upper()
        if cleaned.startswith(dept):
            partitions.append(gpu_du_partition(cleaned))
    for siren_dept, siren in sirens:
        if str(siren_dept).strip().upper() == dept:
            partitions.append(gpu_du_partition(siren))
    return partitions


def _probe_revision(url: str) -> str | None:
    """Return a freshness token for ``url`` via a single HTTP HEAD.

    Mirrors :mod:`gispulse_src_cadastre` (#198): derives the token from
    the ``ETag`` (preferred) / ``Last-Modified`` response header.
    Returns ``None`` — meaning "freshness unknown" — on any network
    error or when the endpoint exposes neither header, so the source
    watcher skips it rather than emitting a spurious change.
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


class GpuSource(DeclarativeSource):
    """Géoportail de l'Urbanisme zones / prescriptions / infos."""

    name = "gpu"
    domain = SourceDomain.REGLEMENTAIRE
    payload = Payload.VECTOR
    jurisdiction = "FR"

    def entries(self) -> list[SourceEntryRef]:
        refs = [
            SourceEntryRef(
                id=entry_id,
                name=label,
                access=AccessSpec(
                    protocol=AccessProtocol.WFS,
                    endpoint=_GEOPLATEFORME_WFS,
                    params={"typename": typename},
                    format="application/json",
                ),
                # revision() probes the WFS live (#198) — no stale
                # token hard-coded here.
                revision_token=None,
                # Per-entry classification axes (#227, EPIC #226) — the
                # worldwide catalogue filters on these. Every wfs_du
                # layer in this pilot shares the source-level
                # classification, so repeat it here rather than leave
                # the entry unclassified (axes default to None).
                domain=self.domain,
                payload=self.payload,
                jurisdiction=self.jurisdiction,
                metadata={
                    "provider": "IGN / DGALN",
                    "platform": "Géoportail de l'Urbanisme",
                    "typename": typename,
                },
            )
            for entry_id, (label, typename) in _ENTRIES.items()
        ]
        refs.append(
            SourceEntryRef(
                id="gpu_documents_bulk_index",
                name="Documents GPU CNIG — archives sources bulk par partition",
                access=AccessSpec(
                    protocol=AccessProtocol.DOWNLOAD,
                    endpoint=_CNIG_DOWNLOAD_BY_PARTITION,
                    params={
                        "partition": _GPU_DOCUMENTS_DEFAULT_PARTITION,
                        "departement": _GPU_DOCUMENTS_DEFAULT_DEPARTEMENT,
                    },
                    format="application/zip",
                ),
                revision_token=None,
                domain=self.domain,
                payload=self.payload,
                jurisdiction=self.jurisdiction,
                metadata={
                    "provider": "IGN / DGALN",
                    "platform": "Géoportail de l'Urbanisme",
                    "base_key": "gpu_documents",
                    "archive_family": _GPU_DOCUMENTS_ARCHIVE_FAMILIES,
                    "archive_format": "zip",
                    "format": "cnig-zip",
                    "partition_prefix": "DU_",
                    "partition_code_fields": ("insee", "siren"),
                    "department_param": "departement",
                    "code_insee_param": "code_insee",
                    "siren_param": "siren",
                    "join_keys": ("idurba", "insee"),
                },
            )
        )
        return refs

    def revision(self, entry_id: str) -> str | None:
        """Cheap freshness token for the source watcher (#187/#198).

        One HTTP HEAD against the Géoplateforme WFS GetCapabilities —
        the millésime is service-wide (the Géoportail de l'Urbanisme
        publishes one consolidated GetCapabilities for all ``wfs_du``
        layers), so every entry shares one probe.
        """
        self._entry(entry_id)  # validate the id
        if entry_id == "gpu_documents_bulk_index":
            return _GPU_DOCUMENTS_BULK_REVISION
        return _probe_revision(_WFS_CAPABILITIES)

    def schema(self, entry_id: str) -> dict:
        """Normalised attribute schema per GPU feature type.

        Attribute names follow the ``wfs_du`` schema documented by the
        Géoportail de l'Urbanisme. ``gpu_doc_id`` is the synthetic join
        key (built from ``gpu_doc_id`` exposed as ``idurba`` on every
        layer) plugins can use to attach a feature to its parent
        urban-planning document.
        """
        self._entry(entry_id)  # validates the id
        if entry_id == "gpu_documents_bulk_index":
            return {
                "idurba": "str",
                "insee": "str",
                "siren": "str",
                "partition": "str",
                "typedoc": "str",
                "datappro": "date",
                "geometry": "geometry",
            }
        common = {
            "gid": "int",
            "idurba": "str",      # parent document id (joins to doc-urba)
            "geometry": "geometry",
        }
        if entry_id == "zone-urba":
            return {
                **common,
                "libelle": "str",     # PLU zone label, e.g. "UA", "UB", "A", "N"
                "libelong": "str",    # long-form label
                "typezone": "str",    # zone family (U, AU, A, N)
                "destdomi": "str",    # dominant destination
                "nomfic": "str",      # regulation file name
                "urlfic": "str",      # regulation file URL
            }
        if entry_id == "doc-urba":
            return {
                **common,
                "typedoc": "str",     # PLU / PLUi / POS / CC / RNU
                "datappro": "date",   # approval date
                "datefin": "date",    # end of validity
                "datvalid": "date",   # validation date
                "intercoid": "str",   # EPCI id if PLUi
                "insee": "str",       # commune INSEE code
                "siren": "str",
            }
        if entry_id == "secteur-cc":
            return {
                **common,
                "libelle": "str",
                "libelong": "str",
                "typesect": "str",    # constructible / non-constructible
                "insee": "str",
            }
        if entry_id in {"prescription-surf", "prescription-lin", "prescription-pct"}:
            return {
                **common,
                "libelle": "str",
                "txt": "str",         # free-text description
                "typepsc": "str",     # prescription category
                "stypepsc": "str",    # prescription sub-category
                "nomfic": "str",
                "urlfic": "str",
            }
        # info-surf / info-lin / info-pct
        return {
            **common,
            "libelle": "str",
            "txt": "str",
            "typeinf": "str",         # information category
            "stypeinf": "str",        # information sub-category
            "nomfic": "str",
            "urlfic": "str",
        }
