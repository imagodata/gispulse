"""Declarative normaliser: source records → ZoningElement common schema.

Story T2 of EPIC #265 (regulatory data-packs M1). The 8-field schema is
inspired by INSPIRE PlannedLandUse ``ZoningElement``. The normaliser itself
lives in the OSS core; per-country mapping tables live in the data-pack.

Schéma cible (8 champs)::

    geometry        — keep as-is from the source; CRS resolved separately
    zone_code       — short code/identifier of the zone (str)
    zone_label      — human-readable label (str)
    hilucs_class    — best-effort INSPIRE HILUCS class (str | None)
    plan_id         — identifier of the plan that defines the zone
    plan_date       — date the plan was adopted/approved (ISO-8601 str)
    regulation_ref  — link or reference to the regulatory text
    source_country  — ISO-3166-1 alpha-2 country code (e.g. "FR")

Mapping DSL (intentionally tiny — extensible later)::

    mapping = ZoningMapping(
        source_country="FR",
        crs="EPSG:2154",
        fields={
            "zone_code": "libelle",
            "zone_label": "libelong",
            "plan_id": "idurba",
            "plan_date": "dateappro",
            "regulation_ref": "nomfic",
        },
        hilucs={
            # source zone code → HILUCS class (best-effort, optional)
            "U": "1_PrimaryProduction_Residential",
            "AU": "1_PrimaryProduction_Residential",
            "A": "1_PrimaryProduction_Agriculture",
            "N": "4_OtherUses_NaturalAreas",
        },
        hilucs_key="zone_code",  # column to look up in `hilucs`
    )

Then::

    out = normalize(records, mapping)

The DSL keeps the contract simple: a *field* mapping is a flat
``{target: source_key}`` table; HILUCS is a separate lookup so a missing
match leaves the target ``None`` rather than crashing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

__all__ = [
    "ZONING_ELEMENT_FIELDS",
    "ZoningMapping",
    "ZoningNormalisationError",
    "normalize",
    "normalize_record",
]


ZONING_ELEMENT_FIELDS: tuple[str, ...] = (
    "geometry",
    "zone_code",
    "zone_label",
    "hilucs_class",
    "plan_id",
    "plan_date",
    "regulation_ref",
    "source_country",
)
"""The 8 cible fields of the ZoningElement common schema, in canonical order."""


_MAPPABLE = frozenset(ZONING_ELEMENT_FIELDS) - {
    "geometry",  # always passthrough from `geometry_key`
    "hilucs_class",  # resolved via `hilucs` lookup, never a plain rename
    "source_country",  # comes from ZoningMapping.source_country, not records
}


class ZoningNormalisationError(ValueError):
    """Raised on a malformed mapping or an unresolvable required field."""


@dataclass(frozen=True)
class ZoningMapping:
    """Declarative mapping from one source's schema to ``ZoningElement``.

    Args:
        source_country: ISO-3166-1 alpha-2 country code. Mandatory — the
            common schema is unusable without it.
        crs: EPSG identifier (``"EPSG:2154"``) for the source geometries.
            Mandatory — silent-CRS data is the #1 bug we want to prevent.
        fields: target field → source key. Targets must be in
            ``ZONING_ELEMENT_FIELDS`` and not in ``geometry``,
            ``hilucs_class`` or ``source_country`` (these are handled
            specially). Missing target keys land as ``None`` in the output.
        geometry_key: source key for the geometry. Default ``"geometry"``.
        hilucs: optional best-effort lookup from a source value to a HILUCS
            class. Misses leave ``hilucs_class`` as ``None``, never crash.
        hilucs_key: name of the *source* column to look up in ``hilucs``.
            Defaults to ``"zone_code"`` (after target rename), but accepts
            an arbitrary source key when the lookup uses a different column.
    """

    source_country: str
    crs: str
    fields: Mapping[str, str] = field(default_factory=dict)
    geometry_key: str = "geometry"
    hilucs: Mapping[str, str] | None = None
    hilucs_key: str = "zone_code"

    def __post_init__(self) -> None:
        if not self.source_country or len(self.source_country) != 2:
            raise ZoningNormalisationError(
                "ZoningMapping.source_country must be an ISO-3166-1 alpha-2 code"
            )
        if not self.crs or ":" not in self.crs:
            raise ZoningNormalisationError(
                "ZoningMapping.crs must be an explicit EPSG identifier "
                "(e.g. 'EPSG:2154') — silent-CRS data is not allowed"
            )
        bad = set(self.fields) - _MAPPABLE
        if bad:
            raise ZoningNormalisationError(
                f"ZoningMapping.fields keys must be ZoningElement targets and "
                f"not {{geometry, hilucs_class, source_country}} — got {sorted(bad)}"
            )


def normalize_record(
    record: Mapping[str, Any], mapping: ZoningMapping
) -> dict[str, Any]:
    """Normalise a single source record into the 8-field ZoningElement shape."""
    out: dict[str, Any] = dict.fromkeys(ZONING_ELEMENT_FIELDS, None)
    out["source_country"] = mapping.source_country
    out["geometry"] = record.get(mapping.geometry_key)

    for target, source_key in mapping.fields.items():
        out[target] = record.get(source_key)

    if mapping.hilucs:
        # The HILUCS key is a *source* column. If it's been renamed by the
        # field mapping (e.g. zone_code → libelle in FR), look it up in the
        # source record under the source key, not the renamed target.
        source_key = mapping.fields.get(mapping.hilucs_key, mapping.hilucs_key)
        source_value = record.get(source_key)
        if source_value is not None:
            out["hilucs_class"] = mapping.hilucs.get(str(source_value))

    return out


def normalize(
    records: Iterable[Mapping[str, Any]], mapping: ZoningMapping
) -> list[dict[str, Any]]:
    """Normalise an iterable of source records into ZoningElement dicts.

    Order is preserved. Records are processed independently; one record's
    failure does not abort the batch (best-effort: a missing optional field
    yields ``None``, not an exception).
    """
    return [normalize_record(r, mapping) for r in records]
