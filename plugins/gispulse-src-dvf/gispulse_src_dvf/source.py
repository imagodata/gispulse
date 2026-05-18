"""French DVF DataSource — Demandes de Valeurs Foncières (real-estate transactions).

A :class:`~gispulse.plugins.api.DeclarativeSource`: the plugin only
*declares* its access spec; the actual data read is delegated to the
registered :class:`Fetcher` for ``AccessSpec.protocol``. This package
ships zero network code besides a single GET probe for ``revision()``.

Data: Etalab DVF (a.k.a. "Demande de Valeurs Foncières") — every
real-estate transaction registered in France over a rolling 5-year
window, refreshed semestrially (April / October). Source-of-truth on
``data.gouv.fr``; geo-enriched CSV mirror published by Etalab at
``files.data.gouv.fr/geo-dvf/`` carries the latitude/longitude pair
this plugin's schema declares.

DVF mutations are *attribute rows* keyed on cadastral references, not
vector geometry — hence :data:`Payload.TABLE`. The downstream join to a
cadastral parcel is materialised on the canonical ``id_parcelle`` the
schema synthesises from ``code_commune`` + ``prefixe_section`` +
``section`` + ``numero_plan``.

.. note::
   :data:`AccessProtocol.REMOTE_TABLE` is declared **deliberately**:
   DVF is a tabular dataset published as CSV (Etalab) or GeoParquet
   (Cerema/IGN derivatives). Wiring a transport adapter for that
   protocol — most plausibly a DuckDB ``httpfs`` adapter over the
   Etalab CSV mirror — is left to a follow-up plugin of
   ``kind = "protocol"``. Until that adapter ships, calling
   :meth:`fetch` on this source will raise ``LookupError`` from
   :class:`ProtocolRegistry` ("no adapter registered for
   AccessProtocol.REMOTE_TABLE"). The declaration, schema and
   :meth:`revision` plumbing are usable today by the source watcher
   and any catalog/marketplace consumer.
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

# Etalab geo-DVF mirror — the rolling-window full export as a gzipped
# CSV, geocoded (latitude/longitude). Stable URL: ``latest`` is the
# millésime symlink rotated each release.
_DVF_GEO_CSV = (
    "https://files.data.gouv.fr/geo-dvf/latest/csv/full.csv.gz"
)

# Dataset metadata endpoint on data.gouv.fr — returns JSON with a
# top-level ``last_modified`` ISO-8601 timestamp updated on every
# resource refresh. Cheaper than crawling the resources list, and
# ``HEAD`` is not an option because the static.data.gouv.fr edge
# returns neither ``ETag`` nor ``Last-Modified`` for resource files
# (confirmed live on 2026-05-18).
_DVF_METADATA_API = (
    "https://www.data.gouv.fr/api/1/datasets/"
    "demandes-de-valeurs-foncieres/"
)
_REVISION_TIMEOUT_S = 8.0


def _probe_revision(api_url: str) -> str | None:
    """Return ``last_modified`` from the data.gouv.fr dataset API.

    Issues a single small GET against the JSON metadata endpoint and
    extracts the dataset-level ``last_modified`` timestamp. The payload
    is on the order of a few KB — comparable to a HEAD in network cost
    once TLS is amortised, and unlike HEAD it carries an actual
    freshness signal for this provider.

    Returns ``None`` — meaning "freshness unknown" — on any network
    error, non-2xx response, malformed JSON, or missing field, so the
    source watcher skips it rather than emitting a spurious change.
    """
    import httpx  # local import — keeps module import network-free

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


class DvfSource(DeclarativeSource):
    """Etalab DVF (real-estate transactions) exposed as a GISPulse source."""

    name = "dvf"
    domain = SourceDomain.STATISTIQUE
    payload = Payload.TABLE
    jurisdiction = "FR"

    def entries(self) -> list[SourceEntryRef]:
        return [
            self._entry_ref(
                "mutations",
                "Mutations DVF (transactions immobilières)",
            ),
        ]

    @staticmethod
    def _entry_ref(entry_id: str, label: str) -> SourceEntryRef:
        return SourceEntryRef(
            id=entry_id,
            name=label,
            access=AccessSpec(
                protocol=AccessProtocol.REMOTE_TABLE,
                endpoint=_DVF_GEO_CSV,
                # Static params — the AccessSpec is a declaration, not
                # a query. Downstream filtering on commune / section /
                # date / type_local is performed by the consumer
                # (capability, pipeline) on the materialised table.
                params={"format": "csv.gz", "separator": ","},
                format="text/csv",
            ),
            # revision() probes data.gouv.fr live (issue #198); the
            # declared token stays None so nothing hard-codes a stale
            # millésime.
            revision_token=None,
            metadata={
                "provider": "Etalab",
                "dataset": "Demandes de Valeurs Foncières",
                "mirror": "files.data.gouv.fr/geo-dvf",
                "update_cadence": "semestrial",
                "license": "Licence Ouverte 2.0",
            },
        )

    def revision(self, entry_id: str) -> str | None:
        """Cheap freshness token for the source watcher (issue #187/#198).

        Issues a single GET against the data.gouv.fr dataset metadata
        API and returns its top-level ``last_modified`` ISO-8601
        timestamp — never a full ``fetch()``. DVF releases are
        dataset-wide (semestrial), so every entry shares one probe;
        ``entry_id`` is only validated here.

        Returns ``None`` when the endpoint is unreachable, returns
        non-2xx, or omits the ``last_modified`` field — the watcher
        treats that as "unknown" and skips it rather than emit a
        spurious ``source.changed``.
        """
        self._entry(entry_id)  # validate the id
        return _probe_revision(_DVF_METADATA_API)

    def schema(self, entry_id: str) -> dict:
        """Normalised attribute schema of a DVF mutation row.

        Field names mirror the Etalab ``geo-dvf`` CSV header so the
        REMOTE_TABLE adapter can map columns one-to-one (no
        runtime renaming).

        The synthetic ``id_parcelle`` field is the canonical cadastral
        join key built from the four pivot columns
        (``code_commune`` + ``prefixe_section`` + ``section`` +
        ``numero_plan``); downstream plugins like ``gispulse-permis``
        consume this rather than the four raw fields.
        """
        self._entry(entry_id)  # validates the id
        return {
            # Mutation identity
            "id_mutation": "str",
            "date_mutation": "date",
            "numero_disposition": "str",
            "nature_mutation": "str",
            # Economics
            "valeur_fonciere": "float",
            # Local typology
            "type_local": "str",
            "code_type_local": "int",
            "surface_reelle_bati": "float",
            "surface_terrain": "float",
            "nombre_pieces_principales": "int",
            # Geography — administrative
            "code_postal": "str",
            "code_commune": "str",
            "nom_commune": "str",
            "code_departement": "str",
            # Cadastral pivot — the four raw fields and the canonical
            # join key downstream plugins consume.
            "prefixe_section": "str",
            "section": "str",
            "numero_plan": "str",
            "id_parcelle": "str",  # synthesised
            # Geography — geocoded (geo-dvf mirror only)
            "longitude": "float",
            "latitude": "float",
        }
