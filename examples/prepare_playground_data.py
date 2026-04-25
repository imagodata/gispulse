#!/usr/bin/env python3
"""
Prepare real IGN BD TOPO V3 datasets for GISPulse playground scenarios (S1-S6).

Downloads via WFS from the French Geoplateforme (no API key required):
- Toulouse:          batiments, routes, POIs, hydrographie, cours d'eau
- Clermont-Ferrand:  batiments, routes, POIs, hydrographie
- Versailles:        batiments, routes, POIs, vegetation

All data is 100% real — zero synthetic features.

Usage:
    python examples/prepare_playground_data.py
    python examples/prepare_playground_data.py --city toulouse
    python examples/prepare_playground_data.py --city clermont-ferrand
    python examples/prepare_playground_data.py --city versailles
"""

import argparse
import sys
import time
from pathlib import Path

try:
    import geopandas as gpd
    import pandas as pd
    import requests
except ImportError:
    print("Required: pip install geopandas requests")
    sys.exit(1)

OUT_DIR = Path(__file__).parent / "datasets"
OUT_DIR.mkdir(exist_ok=True)

# ---------------------------------------------------------------------------
# WFS Configuration
# ---------------------------------------------------------------------------

WFS_BASE = "https://data.geopf.fr/wfs/ows"
WFS_VERSION = "2.0.0"
MAX_FEATURES = 50000  # high enough to get all features within tight bbox

# ---------------------------------------------------------------------------
# City definitions — bbox in EPSG:4326 [xmin, ymin, xmax, ymax]
# ---------------------------------------------------------------------------

CITIES = {
    "toulouse": {
        "label": "Toulouse",
        "center": [1.445, 43.605],
        "bbox": "1.42,43.59,1.47,43.62",
        "bbox_hydro": "1.41,43.58,1.48,43.63",
        "zoom": 15,
        "layers": {
            "batiments": {
                "typeName": "BDTOPO_V3:batiment",
                "max": 50000,
            },
            "routes": {
                "typeName": "BDTOPO_V3:troncon_de_route",
                "max": 20000,
            },
            "equipements": {
                "typeName": "BDTOPO_V3:zone_d_activite_ou_d_interet",
                "max": 5000,
            },
            "surfaces_eau": {
                "typeName": "BDTOPO_V3:surface_hydrographique",
                "max": 500,
                "use_hydro_bbox": True,
            },
            "cours_eau": {
                "typeName": "BDTOPO_V3:cours_d_eau",
                "max": 500,
                "use_hydro_bbox": True,
            },
        },
    },
    "clermont-ferrand": {
        "label": "Clermont-Ferrand + 1re couronne",
        "center": [3.100, 45.785],
        "bbox": "3.02,45.74,3.18,45.83",
        "zoom": 12,
        "layers": {
            "batiments": {
                "typeName": "BDTOPO_V3:batiment",
                "max": 100000,
            },
            "routes": {
                "typeName": "BDTOPO_V3:troncon_de_route",
                "max": 15000,
            },
            "equipements": {
                "typeName": "BDTOPO_V3:zone_d_activite_ou_d_interet",
                "max": 5000,
            },
            "surfaces_eau": {
                "typeName": "BDTOPO_V3:surface_hydrographique",
                "max": 500,
            },
        },
    },
    "versailles": {
        "label": "Versailles",
        "center": [2.125, 48.805],
        # Widened 2026-04-24 to cover Versailles + Le Chesnay + Viroflay +
        # Chaville + Velizy-Villacoublay + Jouy-en-Josas + Buc + Saint-Cyr +
        # Bailly. Previously 2.10,48.79,2.15,48.82 (~3.7×3.3 km) which left
        # all outer communes empty of batiments while the vegetation layer
        # extended over the whole zone — the playground map looked broken
        # with green forests but no buildings in 5 surrounding communes.
        "bbox": "2.062,48.766,2.212,48.835",
        "bbox_veg": "2.062,48.766,2.212,48.835",
        "zoom": 12,
        "layers": {
            "batiments": {
                "typeName": "BDTOPO_V3:batiment",
                "max": 50000,
            },
            "routes": {
                "typeName": "BDTOPO_V3:troncon_de_route",
                "max": 20000,
            },
            "equipements": {
                "typeName": "BDTOPO_V3:zone_d_activite_ou_d_interet",
                "max": 5000,
            },
            "vegetation": {
                "typeName": "BDTOPO_V3:zone_de_vegetation",
                "max": 5000,
                "use_veg_bbox": True,
            },
        },
        # DVF (Demandes de Valeurs Foncieres) — Etalab open data.
        # S6 playground uses these points to build a price/m² map. Eight
        # communes covered to fill the wider S5-aligned bbox
        # (1.96/48.77/2.17/48.87) — Versailles centre, Le Chesnay-Rocquencourt
        # (north of A86), Viroflay (NE), Velizy-Villacoublay (E), Jouy-en-Josas
        # / Buc (S), Saint-Cyr-l'Ecole / Bailly (W). All in dept 78.
        "dvf": {
            "insees": [
                "78646",  # Versailles
                "78158",  # Le Chesnay-Rocquencourt (merger code, post-2019)
                "78686",  # Viroflay
                "78640",  # Velizy-Villacoublay
                "78322",  # Jouy-en-Josas
                "78117",  # Buc
                "78545",  # Saint-Cyr-l'Ecole
                "78043",  # Bailly
            ],
            "dept": "78",
            "years": ["2022", "2023", "2024"],
        },
    },
}


# ---------------------------------------------------------------------------
# WFS download helpers
# ---------------------------------------------------------------------------


def wfs_get_feature_count(type_name: str, bbox: str) -> int:
    """Get the number of features matching a WFS query (resultType=hits)."""
    params = {
        "service": "WFS",
        "version": WFS_VERSION,
        "request": "GetFeature",
        "typeName": type_name,
        "bbox": f"{bbox},EPSG:4326",
        "resultType": "hits",
    }
    resp = requests.get(WFS_BASE, params=params, timeout=30)
    resp.raise_for_status()
    # Parse numberMatched from XML
    import re

    match = re.search(r'numberMatched="(\d+)"', resp.text)
    return int(match.group(1)) if match else 0


def wfs_download_geojson(type_name: str, bbox: str, count: int, start_index: int = 0) -> dict:
    """Download a GeoJSON FeatureCollection from WFS."""
    params = {
        "service": "WFS",
        "version": WFS_VERSION,
        "request": "GetFeature",
        "typeName": type_name,
        "bbox": f"{bbox},EPSG:4326",
        "outputFormat": "application/json",
        "count": str(count),
        "startIndex": str(start_index),
    }
    resp = requests.get(WFS_BASE, params=params, timeout=120)
    resp.raise_for_status()
    return resp.json()


def download_layer(type_name: str, bbox: str, max_features: int) -> gpd.GeoDataFrame | None:
    """Download a BD TOPO layer via WFS with pagination if needed."""
    total = wfs_get_feature_count(type_name, bbox)
    if total == 0:
        print(f"    0 features found, skipping")
        return None

    to_download = min(total, max_features)
    print(f"    {total} features available, downloading {to_download}...")

    page_size = 1000
    frames = []
    downloaded = 0

    while downloaded < to_download:
        chunk = min(page_size, to_download - downloaded)
        geojson = wfs_download_geojson(type_name, bbox, chunk, start_index=downloaded)

        features = geojson.get("features", [])
        if not features:
            break

        gdf = gpd.GeoDataFrame.from_features(features, crs="EPSG:4326")
        frames.append(gdf)
        downloaded += len(features)
        print(f"      {downloaded}/{to_download} downloaded")

        if len(features) < chunk:
            break

        time.sleep(0.5)  # be polite to IGN servers

    if not frames:
        return None

    result = gpd.GeoDataFrame(pd.concat(frames, ignore_index=True), crs="EPSG:4326")
    return result


# ---------------------------------------------------------------------------
# Column cleaning per layer type
# ---------------------------------------------------------------------------


def clean_batiments(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Keep relevant columns for building analysis.

    Altimetric columns (`altitude_minimale_sol`, `altitude_maximale_toit`, ...)
    are kept because the S1 flood-risk pipeline filters buildings whose ground
    altitude sits 0-15 m above the Garonne reference level (BD TOPO V3 already
    embeds these Z values per footprint, no external DTM required).
    """
    keep = [
        "cleabs", "nature", "usage_1", "usage_2", "hauteur",
        "altitude_minimale_sol", "altitude_maximale_sol",
        "altitude_minimale_toit", "altitude_maximale_toit",
        "nombre_de_logements", "nombre_d_etages", "etat_de_l_objet",
        "materiaux_des_murs", "materiaux_de_la_toiture",
        "construction_legere", "date_creation", "date_modification",
        "geometry",
    ]
    available = [c for c in keep if c in gdf.columns]
    return gdf[available].copy()


def clean_routes(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Keep relevant columns for road network analysis."""
    keep = [
        "cleabs", "nature", "nom_voie_ban_gauche", "importance",
        "nombre_de_voies", "largeur_de_chaussee", "vitesse_moyenne_vl",
        "sens_de_circulation", "acces_vehicule_leger",
        "insee_commune_gauche", "date_modification",
        "geometry",
    ]
    available = [c for c in keep if c in gdf.columns]
    result = gdf[available].copy()
    if "nom_voie_ban_gauche" in result.columns:
        result = result.rename(columns={"nom_voie_ban_gauche": "nom_voie"})
    if "insee_commune_gauche" in result.columns:
        result = result.rename(columns={"insee_commune_gauche": "insee_commune"})
    return result


def clean_equipements(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Keep relevant columns for POI/facility analysis."""
    keep = [
        "cleabs", "categorie", "nature", "nature_detaillee", "toponyme",
        "importance", "etat_de_l_objet", "fictif",
        "insee_commune", "commune", "adresse_postale",
        "date_creation", "date_modification",
        "geometry",
    ]
    available = [c for c in keep if c in gdf.columns]
    return gdf[available].copy()


def clean_hydrographie(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Keep relevant columns for hydrographic surfaces."""
    keep = [
        "cleabs", "nature", "persistance", "salinite",
        "origine", "toponyme", "statut",
        "date_creation", "date_modification",
        "geometry",
    ]
    available = [c for c in keep if c in gdf.columns]
    return gdf[available].copy()


def clean_cours_eau(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Keep relevant columns for watercourses."""
    keep = [
        "cleabs", "code_hydrographique", "toponyme",
        "statut", "importance", "caractere_permanent",
        "influence_de_la_maree",
        "date_creation", "date_modification",
        "geometry",
    ]
    available = [c for c in keep if c in gdf.columns]
    return gdf[available].copy()


def clean_vegetation(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Keep relevant columns for vegetation zones."""
    keep = [
        "cleabs", "nature",
        "date_creation", "date_modification",
        "geometry",
    ]
    available = [c for c in keep if c in gdf.columns]
    return gdf[available].copy()


CLEANERS = {
    "batiments": clean_batiments,
    "routes": clean_routes,
    "equipements": clean_equipements,
    "surfaces_eau": clean_hydrographie,
    "cours_eau": clean_cours_eau,
    "vegetation": clean_vegetation,
}


# ---------------------------------------------------------------------------
# OSM health POI overlay — replaces BD TOPO `equipements` Santé rows
# ---------------------------------------------------------------------------
#
# BD TOPO V3 only lists ~47 "Santé" equipements for Clermont (hospitals,
# clinics, nursing homes, thermes) and misses individual GPs, pharmacies,
# dentists, labs. The S3 playground promises 223 POIs via the OSM Overpass
# cache committed at `examples/datasets/<city>_health_osm.geojson`
# (produced by `scripts/fetch_health_pois_osm.py`). We overwrite the
# `equipements` layer with the OSM point set so the backend-served playground
# matches what the docs describe — a single point layer, `categorie == 'Santé'`
# works as the pipeline's first filter.

HEALTH_OSM_OVERRIDE = {
    "clermont-ferrand": "clermont_ferrand_health_osm.geojson",
}


def apply_health_osm_override(city_key: str, out_path: Path) -> None:
    """Replace the `equipements` layer with OSM health POIs if a cache exists."""
    cache_name = HEALTH_OSM_OVERRIDE.get(city_key)
    if not cache_name:
        return
    cache_path = OUT_DIR / cache_name
    if not cache_path.exists():
        print(f"    WARNING: OSM health cache missing: {cache_path}")
        print(f"    -> run: python scripts/fetch_health_pois_osm.py --city {city_key}")
        return

    print(f"\n  [equipements] override with OSM health POIs ({cache_path.name})")
    gdf = gpd.read_file(cache_path)
    if gdf.empty:
        print("    WARNING: OSM cache is empty, keeping BD TOPO equipements")
        return

    keep = ["osm_id", "categorie", "nature", "toponyme", "amenity", "healthcare", "geometry"]
    gdf = gdf[[c for c in keep if c in gdf.columns]].copy()
    if gdf.crs is None:
        gdf = gdf.set_crs("EPSG:4326")

    # GPKG rewrites the layer — previous BD TOPO polygon equipements are dropped.
    gdf.to_file(out_path, driver="GPKG", layer="equipements")
    print(f"    -> equipements: {len(gdf)} OSM health POIs (overwrote BD TOPO)")


# ---------------------------------------------------------------------------
# DVF (Demandes de Valeurs Foncieres) — Etalab open data
# Mutations immobilieres depuis 2014, mises a jour 2x/an. CSV per year/commune
# with WGS84 lat/lon cols.
# ---------------------------------------------------------------------------

DVF_URL_TEMPLATE = (
    "https://files.data.gouv.fr/geo-dvf/latest/csv/"
    "{year}/communes/{dept}/{insee}.csv"
)


def download_dvf(
    insees: str | list[str], dept: str, years: list[str]
) -> gpd.GeoDataFrame | None:
    """Download DVF mutations across one or many communes / years.

    Etalab publishes one CSV per year + commune with WGS84 longitude/latitude
    columns. We concat the cartesian product of (insees x years), geom-build
    from the coordinate pair, drop rows without coordinates (some DVF rows
    are unlocatable). A single string is accepted for backward compat with
    older configs.
    """
    if isinstance(insees, str):
        insees = [insees]

    frames: list[pd.DataFrame] = []
    for insee in insees:
        for year in years:
            url = DVF_URL_TEMPLATE.format(year=year, dept=dept, insee=insee)
            print(f"    DVF {insee} {year}: {url}")
            try:
                resp = requests.get(url, timeout=120)
                resp.raise_for_status()
            except requests.RequestException as e:
                print(f"    DVF {insee} {year} failed: {e}")
                continue
            from io import BytesIO

            # Etalab serves the CSV as UTF-8 bytes but without a charset hint;
            # read from raw content to avoid requests' fallback to ISO-8859-1
            # on resp.text.
            df = pd.read_csv(
                BytesIO(resp.content), encoding="utf-8", low_memory=False
            )
            print(f"    DVF {insee} {year}: {len(df)} rows")
            frames.append(df)

    if not frames:
        return None

    df = pd.concat(frames, ignore_index=True)
    df = df.dropna(subset=["longitude", "latitude"])
    if df.empty:
        return None

    geometry = gpd.points_from_xy(df["longitude"], df["latitude"])
    gdf = gpd.GeoDataFrame(df, geometry=geometry, crs="EPSG:4326")
    return gdf


def clean_dvf(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Keep a minimal schema for price-per-m2 analysis."""
    keep = [
        "id_mutation", "date_mutation", "nature_mutation",
        "valeur_fonciere", "type_local",
        "surface_reelle_bati", "nombre_pieces_principales",
        "surface_terrain", "code_postal", "code_commune",
        "nom_commune", "id_parcelle",
        "geometry",
    ]
    available = [c for c in keep if c in gdf.columns]
    result = gdf[available].copy()

    # Coerce numeric columns that arrive as strings in the raw JSON
    for col in ("valeur_fonciere", "surface_reelle_bati",
                "nombre_pieces_principales", "surface_terrain"):
        if col in result.columns:
            result[col] = pd.to_numeric(result[col], errors="coerce")

    # Drop rows with no usable price OR no built surface
    if "valeur_fonciere" in result.columns and "surface_reelle_bati" in result.columns:
        result = result[
            result["valeur_fonciere"].notna()
            & (result["valeur_fonciere"] > 0)
            & result["surface_reelle_bati"].notna()
            & (result["surface_reelle_bati"] > 0)
        ].copy()

    return result


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------


def prepare_city(city_key: str) -> None:
    """Download all layers for a city and write a combined GPKG."""
    city = CITIES[city_key]
    label = city["label"]
    bbox = city["bbox"]

    slug = city_key.replace("-", "_")
    out_path = OUT_DIR / f"{slug}_bdtopo.gpkg"

    print(f"\n{'='*60}")
    print(f"  {label} — BD TOPO V3")
    print(f"{'='*60}")

    layers_written = 0

    for layer_name, layer_cfg in city["layers"].items():
        type_name = layer_cfg["typeName"]
        max_feat = layer_cfg.get("max", MAX_FEATURES)

        # Determine bbox: some layers use wider bbox
        layer_bbox = bbox
        if layer_cfg.get("use_hydro_bbox") and "bbox_hydro" in city:
            layer_bbox = city["bbox_hydro"]
        if layer_cfg.get("use_veg_bbox") and "bbox_veg" in city:
            layer_bbox = city["bbox_veg"]

        print(f"\n  [{layer_name}] {type_name}")
        print(f"    bbox: {layer_bbox}")

        gdf = download_layer(type_name, layer_bbox, max_feat)
        if gdf is None or gdf.empty:
            print(f"    SKIP: no data")
            continue

        cleaner = CLEANERS.get(layer_name)
        if cleaner:
            gdf = cleaner(gdf)

        gdf.to_file(out_path, driver="GPKG", layer=layer_name)
        layers_written += 1
        print(f"    -> {layer_name}: {len(gdf)} features written")

    # OSM health POI override — only fires for cities that have a committed
    # `*_health_osm.geojson` cache. Runs after BD TOPO write so the layer is
    # replaced rather than appended.
    apply_health_osm_override(city_key, out_path)

    # DVF (optional, city-specific) — Etalab mutations for price/m² analysis
    if "dvf" in city:
        dvf_cfg = city["dvf"]
        # Backward compat: legacy "insee" (single string) or new "insees" (list).
        insees = dvf_cfg.get("insees") or dvf_cfg["insee"]
        label_communes = ", ".join(insees) if isinstance(insees, list) else insees
        print(f"\n  [dvf_ventes] DVF Etalab — commune(s) {label_communes}")
        dvf_gdf = download_dvf(insees, dvf_cfg["dept"], dvf_cfg["years"])
        if dvf_gdf is not None and not dvf_gdf.empty:
            dvf_gdf = clean_dvf(dvf_gdf)
            dvf_gdf.to_file(out_path, driver="GPKG", layer="dvf_ventes")
            layers_written += 1
            print(f"    -> dvf_ventes: {len(dvf_gdf)} mutations written")
        else:
            print(f"    SKIP: no DVF data")

    if layers_written > 0:
        size_mb = out_path.stat().st_size / 1e6
        print(f"\n  OUTPUT: {out_path} ({layers_written} layers, {size_mb:.1f} MB)")
    else:
        print(f"\n  WARNING: no layers downloaded for {label}")


def main():
    parser = argparse.ArgumentParser(description="Download IGN BD TOPO V3 data for GISPulse playground")
    parser.add_argument(
        "--city",
        choices=list(CITIES.keys()) + ["all"],
        default="all",
        help="City to download (default: all)",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("GISPulse Playground — IGN BD TOPO V3 (100%% real data)")
    print("=" * 60)
    print(f"Source: {WFS_BASE}")
    print(f"Output: {OUT_DIR}/")

    cities = list(CITIES.keys()) if args.city == "all" else [args.city]

    for city_key in cities:
        prepare_city(city_key)

    print(f"\n{'='*60}")
    print("Datasets ready:")
    print("=" * 60)
    for f in sorted(OUT_DIR.glob("*_bdtopo.gpkg")):
        print(f"  {f.name:35s} {f.stat().st_size / 1e6:6.1f} MB")


if __name__ == "__main__":
    main()
