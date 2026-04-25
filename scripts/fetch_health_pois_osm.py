#!/usr/bin/env python3
"""Fetch OSM healthcare POIs for a bbox via Overpass and cache as GeoJSON.

BD TOPO ships only ~47 "Santé" establishments for Clermont-Ferrand — hospitals,
clinics, nursing homes, thermal baths. Individual GPs, specialists, dentists
and pharmacies are absent. OSM fills that gap (`amenity=doctors|clinic|hospital
|dentist|pharmacy|nursing_home` + `healthcare=*`).

The script is a build-time helper: run once locally when the cache needs a
refresh, commit the produced GeoJSON to `examples/datasets/`. `build_playground
_data.py` then reads the cache without any network dependency at build time.

Usage::

    python scripts/fetch_health_pois_osm.py --city clermont-ferrand
    python scripts/fetch_health_pois_osm.py --bbox 3.020,45.740,3.180,45.830 \
        --out examples/datasets/clermont_ferrand_health_osm.geojson
    python scripts/fetch_health_pois_osm.py --city clermont-ferrand --force

"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "examples" / "datasets"

OVERPASS_URL = "https://overpass-api.de/api/interpreter"

# City presets — bbox wide enough to include the isochrone bbox of the related
# scenario plus a margin so nearby facilities feeding into the isochrone are
# captured.
CITIES = {
    "clermont-ferrand": {
        "bbox": (3.020, 45.740, 3.180, 45.830),
        "out": "clermont_ferrand_health_osm.geojson",
    },
}

# Amenity values that represent a healthcare POI.
AMENITY_HEALTH = (
    "hospital",
    "clinic",
    "doctors",
    "dentist",
    "pharmacy",
    "nursing_home",
    "social_facility",
    "veterinary",
)

# Mapping OSM tag -> (nature, categorie). Natures mirror BD TOPO vocabulary
# where possible so the pipeline's `filter_sante` keeps working on a unified
# `categorie == 'Santé'` field.
SOCIAL_FACILITY_FR = {
    "nursing_home": "Maison de retraite",
    "assisted_living": "Résidence services séniors",
    "group_home": "Foyer de vie",
    "shelter": "Foyer d'hébergement",
    "outreach": "Centre médico-social",
    "ambulatory_care": "Soins ambulatoires",
    "healthcare": "Établissement de santé",
}

HEALTHCARE_FR = {
    "hospital": "Hôpital",
    "clinic": "Clinique",
    "doctor": "Médecin",
    "general": "Médecin généraliste",
    "dentist": "Dentiste",
    "pharmacy": "Pharmacie",
    "nursing_home": "Maison de retraite",
    "laboratory": "Laboratoire",
    "physiotherapist": "Kinésithérapeute",
    "psychotherapist": "Psychothérapeute",
    "optometrist": "Optométriste",
    "midwife": "Sage-femme",
    "nurse": "Infirmier",
    "alternative": "Médecine alternative",
    "blood_donation": "Don du sang",
    "dialysis": "Centre de dialyse",
    "centre": "Centre de santé",
    "birthing_center": "Maison de naissance",
    "rehabilitation": "Centre de rééducation",
    "audiologist": "Audioprothésiste",
    "podiatrist": "Podologue",
    "speech_therapist": "Orthophoniste",
    "occupational_therapist": "Ergothérapeute",
    "yes": "Établissement de santé",
}


def classify(tags: dict[str, str]) -> tuple[str, str]:
    amenity = (tags.get("amenity") or "").strip()
    healthcare = (tags.get("healthcare") or "").strip().lower()
    social_facility = (tags.get("social_facility") or "").strip().lower()

    if amenity == "hospital" or healthcare == "hospital":
        return "Hôpital", "Santé"
    if amenity == "clinic" or healthcare == "clinic":
        return "Clinique", "Santé"
    if amenity == "doctors" or healthcare in ("doctor", "general"):
        return "Médecin", "Santé"
    if amenity == "dentist" or healthcare == "dentist":
        return "Dentiste", "Santé"
    if amenity == "pharmacy" or healthcare == "pharmacy":
        return "Pharmacie", "Santé"
    if amenity == "nursing_home" or healthcare == "nursing_home":
        return "Maison de retraite", "Santé"
    if healthcare and healthcare in HEALTHCARE_FR:
        return HEALTHCARE_FR[healthcare], "Santé"
    if amenity == "social_facility":
        return SOCIAL_FACILITY_FR.get(social_facility, "Établissement social"), "Santé"
    if amenity == "veterinary":
        return "Vétérinaire", "Santé"
    if healthcare:
        return healthcare.replace("_", " ").capitalize(), "Santé"
    return "Établissement de santé", "Santé"


def build_query(bbox: tuple[float, float, float, float]) -> str:
    south, west, north, east = bbox[1], bbox[0], bbox[3], bbox[2]
    bbox_str = f"{south},{west},{north},{east}"
    amenity_regex = "|".join(AMENITY_HEALTH)
    return (
        "[out:json][timeout:90];\n"
        "(\n"
        f'  node["amenity"~"^({amenity_regex})$"]({bbox_str});\n'
        f'  way["amenity"~"^({amenity_regex})$"]({bbox_str});\n'
        f'  relation["amenity"~"^({amenity_regex})$"]({bbox_str});\n'
        f'  node["healthcare"]({bbox_str});\n'
        f'  way["healthcare"]({bbox_str});\n'
        f'  relation["healthcare"]({bbox_str});\n'
        ");\n"
        "out center tags;\n"
    )


def fetch(query: str, *, retries: int = 3, backoff: float = 5.0) -> dict:
    data = urllib.parse.urlencode({"data": query}).encode("utf-8")
    req = urllib.request.Request(
        OVERPASS_URL,
        data=data,
        headers={"User-Agent": "gispulse-playground-builder/1.0"},
    )
    last_err: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                return json.load(resp)
        except (urllib.error.URLError, TimeoutError) as exc:
            last_err = exc
            wait = backoff * attempt
            print(
                f"  overpass attempt {attempt}/{retries} failed: {exc} — retry in {wait:.0f}s",
                file=sys.stderr,
            )
            time.sleep(wait)
    raise RuntimeError(f"overpass fetch failed after {retries} attempts: {last_err}")


def to_geojson(overpass: dict) -> dict:
    features: list[dict] = []
    seen: set[tuple[str, int]] = set()
    for el in overpass.get("elements", []):
        osm_type = el.get("type")
        osm_id = el.get("id")
        if not osm_type or osm_id is None:
            continue
        key = (osm_type, osm_id)
        if key in seen:
            continue
        seen.add(key)

        if osm_type == "node":
            lon, lat = el.get("lon"), el.get("lat")
        else:
            center = el.get("center") or {}
            lon, lat = center.get("lon"), center.get("lat")
        if lon is None or lat is None:
            continue

        tags = el.get("tags", {}) or {}
        nature, categorie = classify(tags)
        toponyme = tags.get("name") or tags.get("operator") or nature
        props = {
            "osm_id": f"{osm_type[0]}{osm_id}",
            "categorie": categorie,
            "nature": nature,
            "toponyme": toponyme,
            "amenity": tags.get("amenity"),
            "healthcare": tags.get("healthcare"),
        }
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [lon, lat]},
                "properties": {k: v for k, v in props.items() if v is not None},
            }
        )

    return {"type": "FeatureCollection", "features": features}


def parse_bbox(raw: str) -> tuple[float, float, float, float]:
    parts = [p.strip() for p in raw.split(",")]
    if len(parts) != 4:
        raise argparse.ArgumentTypeError("bbox must be 'minlon,minlat,maxlon,maxlat'")
    return tuple(float(p) for p in parts)  # type: ignore[return-value]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--city", choices=sorted(CITIES.keys()))
    parser.add_argument("--bbox", type=parse_bbox, help="minlon,minlat,maxlon,maxlat")
    parser.add_argument("--out", type=Path, help="Output GeoJSON path")
    parser.add_argument("--force", action="store_true", help="Overwrite existing cache")
    args = parser.parse_args()

    if args.city:
        preset = CITIES[args.city]
        bbox = preset["bbox"]
        out = args.out or (OUT_DIR / preset["out"])
    else:
        if not args.bbox or not args.out:
            parser.error("--city or (--bbox + --out) required")
        bbox = args.bbox
        out = args.out

    if out.exists() and not args.force:
        print(f"[skip] {out} already present (use --force to refetch)")
        return 0

    query = build_query(bbox)
    print(f"[fetch] bbox={bbox} -> {out}")
    overpass = fetch(query)
    fc = to_geojson(overpass)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(fc, ensure_ascii=False), encoding="utf-8")

    # Summary
    from collections import Counter
    counts = Counter(f["properties"].get("nature") for f in fc["features"])
    print(f"[done] wrote {len(fc['features'])} features")
    for nature, n in counts.most_common():
        print(f"  {nature:>28s}: {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
