"""Story T3 (#270) — declarative format for a `regulatory-zoning` data-pack entry.

A ``regulatory-zoning`` ``DataPackManifest`` ships one entry per (country,
plan-source) combination. The OSS engine validates the shape; the
country-specific *content* (which endpoint, which typename, which mapping)
lives in the data-pack itself (``gispulse-data-regulatory``).

Schema (one entry):

::

    - name:            slug, unique within the pack
      source_country:  ISO-3166-1 alpha-2 (e.g. "FR")
      protocol:        "wfs" | "ogc_api_features"
      endpoint:        base URL of the service
      typename:        collection / typeName
      crs:             explicit EPSG identifier ("EPSG:4326")
      mapping:
        fields:        { zone_code|zone_label|plan_id|plan_date|regulation_ref: source_key }
        hilucs:        optional { source_value: hilucs_string }
        hilucs_key:    optional, default "zone_code"
      bbox:            optional [minx, miny, maxx, maxy]
      max_features:    optional int
      regulation:      optional free-form documentation (description, license, refresh)

Only the engine-meaningful fields are validated; ``regulation`` is opaque
documentation that ships with the pack but is not consumed at fetch time.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

__all__ = [
    "ZONING_ENTRY_REQUIRED_FIELDS",
    "ZONING_ENTRY_OPTIONAL_FIELDS",
    "ZONING_MAPPING_FIELD_TARGETS",
    "ZONING_VALID_PROTOCOLS",
    "RegulatoryZoningEntryError",
    "RegulatoryZoningEntry",
]


ZONING_ENTRY_REQUIRED_FIELDS: tuple[str, ...] = (
    "name",
    "source_country",
    "protocol",
    "endpoint",
    "typename",
    "crs",
    "mapping",
)

ZONING_ENTRY_OPTIONAL_FIELDS: tuple[str, ...] = (
    "bbox",
    "max_features",
    "regulation",
    "auth",
    "version",
    "query",
)

ZONING_VALID_PROTOCOLS: frozenset[str] = frozenset({"wfs", "ogc_api_features"})

# These are the only *fields* a mapping may rename — kept in lockstep with
# the engine's :data:`gispulse.core.zoning_normalizer.ZONING_ELEMENT_FIELDS`
# minus the three fields that are not renames (geometry / hilucs_class /
# source_country). Duplicated here on purpose to keep T3 free of a T2
# import — the two stories can land independently.
ZONING_MAPPING_FIELD_TARGETS: frozenset[str] = frozenset(
    {
        "zone_code",
        "zone_label",
        "plan_id",
        "plan_date",
        "regulation_ref",
    }
)


class RegulatoryZoningEntryError(ValueError):
    """Raised on a malformed regulatory-zoning entry."""


@dataclass(frozen=True)
class RegulatoryZoningEntry:
    """One entry of a ``regulatory-zoning`` data pack.

    Frozen + flat: the engine only needs to read it, never mutate it. The
    ``mapping`` is kept as a plain dict so the data-pack can ship country
    quirks without the OSS engine releasing a new class shape.
    """

    name: str
    source_country: str
    protocol: str
    endpoint: str
    typename: str
    crs: str
    mapping: Mapping[str, Any]
    bbox: tuple[float, float, float, float] | None = None
    max_features: int | None = None
    regulation: Mapping[str, Any] = field(default_factory=dict)
    auth: dict[str, str] | None = None
    version: str = "2.0.0"
    query: Mapping[str, str] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "RegulatoryZoningEntry":
        """Validate and parse one entry dict from a pack manifest."""
        if not isinstance(raw, Mapping):
            raise RegulatoryZoningEntryError(
                "regulatory-zoning entry must be a mapping, got "
                f"{type(raw).__name__}"
            )
        missing = [k for k in ZONING_ENTRY_REQUIRED_FIELDS if not raw.get(k)]
        if missing:
            raise RegulatoryZoningEntryError(
                f"regulatory-zoning entry missing required fields: {missing}"
            )
        unknown = set(raw) - set(
            ZONING_ENTRY_REQUIRED_FIELDS + ZONING_ENTRY_OPTIONAL_FIELDS
        )
        if unknown:
            raise RegulatoryZoningEntryError(
                f"regulatory-zoning entry has unknown fields: {sorted(unknown)}"
            )
        country = str(raw["source_country"])
        if len(country) != 2:
            raise RegulatoryZoningEntryError(
                f"source_country must be ISO-3166-1 alpha-2, got {country!r}"
            )
        protocol = str(raw["protocol"]).lower()
        if protocol not in ZONING_VALID_PROTOCOLS:
            raise RegulatoryZoningEntryError(
                f"protocol must be one of {sorted(ZONING_VALID_PROTOCOLS)}, "
                f"got {protocol!r}"
            )
        crs = str(raw["crs"])
        if ":" not in crs:
            raise RegulatoryZoningEntryError(
                "crs must be an explicit identifier like 'EPSG:4326', "
                f"got {crs!r}"
            )
        mapping = raw["mapping"]
        if not isinstance(mapping, Mapping):
            raise RegulatoryZoningEntryError(
                "mapping must be a mapping, got " f"{type(mapping).__name__}"
            )
        fields_ = mapping.get("fields", {})
        if not isinstance(fields_, Mapping):
            raise RegulatoryZoningEntryError(
                "mapping.fields must be a mapping"
            )
        bad_targets = set(fields_) - ZONING_MAPPING_FIELD_TARGETS
        if bad_targets:
            raise RegulatoryZoningEntryError(
                f"mapping.fields keys must be ZoningElement targets — got "
                f"unknown {sorted(bad_targets)}"
            )

        bbox_raw = raw.get("bbox")
        bbox: tuple[float, float, float, float] | None = None
        if bbox_raw is not None:
            try:
                pts = tuple(float(x) for x in bbox_raw)
            except (TypeError, ValueError) as exc:
                raise RegulatoryZoningEntryError(
                    f"bbox must be 4 numbers, got {bbox_raw!r}"
                ) from exc
            if len(pts) != 4:
                raise RegulatoryZoningEntryError(
                    f"bbox must have 4 numbers, got {len(pts)}"
                )
            bbox = pts  # type: ignore[assignment]

        max_features = raw.get("max_features")
        if max_features is not None and not isinstance(max_features, int):
            raise RegulatoryZoningEntryError(
                "max_features must be an integer when present"
            )

        return cls(
            name=str(raw["name"]),
            source_country=country.upper(),
            protocol=protocol,
            endpoint=str(raw["endpoint"]),
            typename=str(raw["typename"]),
            crs=crs,
            mapping=dict(mapping),
            bbox=bbox,
            max_features=max_features,
            regulation=dict(raw.get("regulation", {}) or {}),
            auth=raw.get("auth"),
            version=str(raw.get("version", "2.0.0")),
            query=dict(raw.get("query", {}) or {}),
        )
