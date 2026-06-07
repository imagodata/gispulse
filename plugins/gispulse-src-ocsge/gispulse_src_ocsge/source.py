"""IGN OCS GE department GeoPackage archives.

OCS GE is distributed by the IGN Geoplateforme download API. This source
declares the per-department GeoPackage archive URL template and leaves
download/materialisation to the core ``DOWNLOAD`` fetcher.
"""

from __future__ import annotations

import re
import urllib.parse
import urllib.request
from dataclasses import dataclass, replace
from functools import lru_cache
from xml.etree import ElementTree

from gispulse.plugins.api import (
    AccessProtocol,
    AccessSpec,
    DeclarativeSource,
    Payload,
    SourceDomain,
    SourceEntryRef,
)

_RESOURCE = "OCSGE"
_RESOURCE_LISTING = "https://data.geopf.fr/telechargement/resource/OCSGE"
_ARTIFICIALISATION_RESOURCE_LISTING = (
    "https://data.geopf.fr/telechargement/resource/OCSGE-ARTIFICIALISATION"
)
_DOWNLOAD_ENDPOINT = (
    "https://data.geopf.fr/telechargement/download/OCSGE/"
    "OCS-GE_{version}__GPKG_{crs}_{zone}_{millesime}/"
    "OCS-GE_{version}__GPKG_{crs}_{zone}_{millesime}.7z"
)
_DEFAULT_DEPARTEMENT = "75"
_DEFAULT_ZONE = "D075"
_DEFAULT_VERSION = "2-0"
_DEFAULT_SAMPLE_CRS = "LAMB93"
_DEFAULT_SAMPLE_MILLESIME = "2021-01-01"
_MILLESIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_CRS_RE = re.compile(r"^[A-Z0-9]+$")
_RESOURCE_TITLE_RE = re.compile(
    r"^OCS-GE_(?P<version>[^_]+)__GPKG_(?P<crs>[^_]+)_"
    r"(?P<zone>D(?:\d{3}|02A|02B))_(?P<millesime>\d{4}-\d{2}-\d{2})$"
)
_ATOM_NS = "{http://www.w3.org/2005/Atom}"
_LISTING_TIMEOUT_SECONDS = 20
_HTTP_USER_AGENT = "gispulse-src-ocsge/0.1"

_DROM_CRS_BY_DEPARTEMENT = {
    "971": "RGAF09UTM20",
    "972": "RGAF09UTM20",
    "973": "RGFG95UTM22",
    "974": "RGR92UTM40S",
    "976": "RGM04UTM38S",
}

_PUBLISHED_DEPARTEMENTS = (
    *(f"{value:02d}" for value in range(1, 20)),
    *(f"{value:02d}" for value in range(21, 96)),
    "2A",
    "2B",
    "971",
    "972",
    "973",
    "974",
    "976",
)
_VALID_DEPARTEMENTS = set(_PUBLISHED_DEPARTEMENTS)

_SCHEMA = {
    "fid": "int",
    "the_geom": "geometry",
    "id": "str",
    "code_cs": "str",
    "code_us": "str",
    "millesime": "str",
    "source": "str",
    "ossature": "int",
    "id_origine": "str",
    "code_or": "str",
}

_ARTIFICIALISATION_HINTS = {
    "status": (
        "indicative raw CS/US hints only; not the official OCSGE-ARTIFICIALISATION derived product"
    ),
    "built_cover": ("CS1.1.1.1",),
    "impervious_or_composite_cover": ("CS1.1.1.2", "CS1.1.2.2"),
    "mineral_cover_except_extraction": {
        "code_cs": "CS1.1.2.1",
        "excluded_code_us": "US1.3",
    },
    "herbaceous_cover_artificial_usages": {
        "code_cs_prefix": "CS2.2",
        "code_us_values": (
            "US2",
            "US3",
            "US5",
            "US235",
            "US4.*",
            "US6.1",
            "US6.2",
        ),
    },
    "threshold_note": (
        "The official derived OCSGE-ARTIFICIALISATION product applies "
        "decree thresholds and topology regrouping; these rules are raw "
        "CS/US hints for parcel-side spatial joins."
    ),
}


@dataclass(frozen=True)
class _OcsgeRelease:
    resource_id: str
    version: str
    crs: str
    zone: str
    millesime: str


def _normalise_departement(raw: object | None) -> str:
    value = str(raw or "").strip().upper()
    if not value:
        raise ValueError("invalid OCS GE department code: empty")
    if value in {"2A", "2B"}:
        return value
    if value.isdigit() and len(value) <= 2:
        value = value.zfill(2)
    if value in _VALID_DEPARTEMENTS:
        return value
    raise ValueError(f"invalid OCS GE department code: {raw!r}")


def _zone_for_departement(departement: str) -> str:
    if departement in {"2A", "2B"}:
        return f"D0{departement}"
    if departement.isdigit() and len(departement) <= 2:
        return f"D{departement.zfill(3)}"
    return f"D{departement}"


def _normalise_millesime(raw: object | None) -> str:
    value = str(raw or "").strip()
    if _MILLESIME_RE.match(value):
        return value
    raise ValueError(f"invalid OCS GE millesime: {raw!r}")


def _normalise_crs(raw: object | None) -> str:
    value = str(raw or "").strip().upper()
    if _CRS_RE.match(value):
        return value
    raise ValueError(f"invalid OCS GE CRS: {raw!r}")


def _fetch_resource_listing(zone: str) -> bytes:
    query = urllib.parse.urlencode({"zone": zone})
    url = f"{_RESOURCE_LISTING}?{query}"
    request = urllib.request.Request(url, headers={"User-Agent": _HTTP_USER_AGENT})
    try:
        with urllib.request.urlopen(
            request,
            timeout=_LISTING_TIMEOUT_SECONDS,
        ) as response:
            return response.read()
    except OSError as exc:
        raise RuntimeError(f"failed to fetch OCS GE resource listing for {zone}") from exc


def _release_from_title(title: str) -> _OcsgeRelease | None:
    match = _RESOURCE_TITLE_RE.match(title)
    if match is None:
        return None
    return _OcsgeRelease(
        resource_id=title,
        version=match.group("version"),
        crs=match.group("crs"),
        zone=match.group("zone"),
        millesime=match.group("millesime"),
    )


def _parse_resource_listing(raw: bytes, zone: str) -> tuple[_OcsgeRelease, ...]:
    try:
        root = ElementTree.fromstring(raw)
    except ElementTree.ParseError as exc:
        raise ValueError(f"invalid OCS GE resource listing XML for {zone}") from exc

    releases: list[_OcsgeRelease] = []
    for entry in root.findall(f"{_ATOM_NS}entry"):
        title = entry.findtext(f"{_ATOM_NS}title")
        if not title:
            continue
        release = _release_from_title(title)
        if release is not None and release.zone == zone:
            releases.append(release)
    return tuple(sorted(releases, key=lambda release: release.resource_id))


@lru_cache(maxsize=128)
def _published_releases_for_zone(zone: str) -> tuple[_OcsgeRelease, ...]:
    releases = _parse_resource_listing(_fetch_resource_listing(zone), zone)
    if releases:
        return releases
    raise ValueError(f"no OCS GE GPKG archive is listed for zone {zone}")


def _available_releases_note(releases: tuple[_OcsgeRelease, ...]) -> str:
    return ", ".join(f"{release.crs}/{release.millesime}" for release in releases)


def _resolve_release(
    *,
    zone: str,
    crs: str | None = None,
    millesime: str | None = None,
) -> _OcsgeRelease:
    releases = _published_releases_for_zone(zone)
    candidates = releases
    if crs is not None:
        candidates = tuple(release for release in candidates if release.crs == crs)
    if millesime is not None:
        candidates = tuple(release for release in candidates if release.millesime == millesime)
    if candidates:
        return max(candidates, key=lambda release: (release.millesime, release.version))

    requested = "/".join(value for value in (crs, millesime) if value is not None)
    if not requested:
        requested = "latest"
    raise ValueError(
        f"unavailable OCS GE archive for zone {zone} ({requested}); "
        f"available GPKG releases: {_available_releases_note(releases)}"
    )


class OcsgeSource(DeclarativeSource):
    """OCS GE coverage/use polygons from IGN, by department and millesime."""

    name = "ocsge"
    domain = SourceDomain.FONCIER
    payload = Payload.VECTOR
    jurisdiction = "FR"

    def entries(self) -> list[SourceEntryRef]:
        return [
            SourceEntryRef(
                id="occupation-sol",
                name="OCS GE - occupation du sol (GPKG departemental)",
                access=AccessSpec(
                    protocol=AccessProtocol.DOWNLOAD,
                    endpoint=_DOWNLOAD_ENDPOINT,
                    params={
                        "resource": _RESOURCE,
                        "version": _DEFAULT_VERSION,
                        "format": "GPKG",
                        "crs": _DEFAULT_SAMPLE_CRS,
                        "departement": _DEFAULT_DEPARTEMENT,
                        "zone": _DEFAULT_ZONE,
                        "millesime": _DEFAULT_SAMPLE_MILLESIME,
                        "layer": "OCCUPATION_SOL",
                        "archive_format": "7z",
                        "data_format": "gpkg",
                    },
                    format="application/x-7z-compressed",
                ),
                revision_token=None,
                domain=self.domain,
                payload=self.payload,
                jurisdiction=self.jurisdiction,
                metadata={
                    "provider": "IGN",
                    "platform": "Géoplateforme / cartes.gouv.fr",
                    "dataset": "OCS GE Nouvelle génération",
                    "license": "Licence Ouverte 2.0",
                    "default_millesime": _DEFAULT_SAMPLE_MILLESIME,
                    "version": _DEFAULT_VERSION,
                    "default_crs": _DEFAULT_SAMPLE_CRS,
                    "drom_crs_by_departement": _DROM_CRS_BY_DEPARTEMENT,
                    "resource": _RESOURCE,
                    "resource_listing_endpoint": _RESOURCE_LISTING,
                    "availability_resolution": (
                        "access_for queries resource_listing_endpoint with the "
                        "resolved zone and selects the latest listed GPKG archive "
                        "unless a millesime/CRS is requested"
                    ),
                    "availability_uniform": False,
                    "derived_artificialisation_resource": (_ARTIFICIALISATION_RESOURCE_LISTING),
                    "archive_format": "7z",
                    "data_format": "gpkg",
                    "included_layers": ("OCCUPATION_SOL", "ZONE_CONSTRUITE"),
                    "table_name": "OCCUPATION_SOL",
                    "coverage_code_field": "code_cs",
                    "usage_code_field": "code_us",
                    "geometry_key": "the_geom",
                    "join_strategy": "spatial_intersects",
                    "spatial_join": "ST_Intersects",
                    "department_param": "departement",
                    "zone_param": "zone",
                    "zone_format": "D{code_departement:0>3}",
                    "millesime_param": "millesime",
                    "bulk_access_for": "access_for",
                    "bulk_access_for_params": (
                        "crs",
                        "millesime",
                        "local_path",
                        "s3_uri",
                        "s3_key",
                    ),
                    "published_departements": _PUBLISHED_DEPARTEMENTS,
                    "artificialisation_hints": _ARTIFICIALISATION_HINTS,
                },
            )
        ]

    def access_for(
        self,
        entry_id: str,
        *,
        departement: str | None = None,
        department: str | None = None,
        code_departement: str | None = None,
        millesime: str | None = None,
        crs: str | None = None,
        local_path: str | None = None,
        s3_uri: str | None = None,
        s3_key: str | None = None,
    ) -> AccessSpec:
        """Return a per-department AccessSpec without mutating the catalog."""
        entry = self._entry(entry_id)
        selected = (
            departement
            if departement is not None
            else department
            if department is not None
            else code_departement
        )
        params = dict(entry.access.params)
        normalised_departement = _normalise_departement(selected or params.get("departement"))
        requested_millesime = _normalise_millesime(millesime) if millesime is not None else None
        requested_crs = _normalise_crs(crs) if crs is not None else None
        release = _resolve_release(
            zone=_zone_for_departement(normalised_departement),
            crs=requested_crs,
            millesime=requested_millesime,
        )
        params["departement"] = normalised_departement
        params["zone"] = release.zone
        params["version"] = release.version
        params["crs"] = release.crs
        params["millesime"] = release.millesime
        params["resource_id"] = release.resource_id
        if local_path is not None:
            params["local_path"] = local_path
        if s3_uri is not None:
            params["s3_uri"] = s3_uri
        if s3_key is not None:
            params["s3_key"] = s3_key
        return replace(entry.access, endpoint=_DOWNLOAD_ENDPOINT.format_map(params), params=params)

    def schema(self, entry_id: str) -> dict[str, str]:
        """Expose the verified OCCUPATION_SOL GeoPackage columns."""
        self._entry(entry_id)
        return dict(_SCHEMA)

    def revision(self, entry_id: str) -> str | None:
        """Return None because the cheap live revision token is not modelled yet."""
        self._entry(entry_id)
        return None
