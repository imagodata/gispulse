#!/usr/bin/env python3
"""
Generate lightweight static GeoJSON datasets for the docs-site playground.

Reads source GPKGs in examples/datasets/, applies (per scenario):
  - Bbox clip (tight window around the scenario center)
  - Feature decimation (cap max features per layer)
  - Douglas-Peucker simplification (topology-safe when possible)
  - Column pruning (keep only fields referenced by rules / popups)
  - Gzip compression

Output: docs-site/public/playground/data/<scenario>/<layer>.geojson(.gz)
        docs-site/public/playground/data/manifest.json

Goal: each scenario page loads < 300 kB of vector data to stay freeze-free
in the browser (GitHub Pages, no backend).

Usage:
    python scripts/build_playground_data.py
    python scripts/build_playground_data.py --scenario flood-risk
    python scripts/build_playground_data.py --dry-run
"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

try:
    import geopandas as gpd
    from shapely import box
except ImportError:
    print("Required: pip install geopandas shapely", file=sys.stderr)
    sys.exit(1)


ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT / "examples" / "datasets"
OUT_DIR = ROOT / "docs-site" / "public" / "playground" / "data"

# Default simplification tolerance in degrees (EPSG:4326).
# 1e-5 deg ~= 1.1 m at 45 N — imperceptible at zoom <= 17.
DEFAULT_TOLERANCE = 1e-5

# Fields kept in popups (+ any extra fields listed per layer).
DEFAULT_KEEP = ("id", "nom", "toponyme", "usage_1", "nature", "importance")

# Size budgets enforced in --strict mode (gzipped bytes).
# A scenario is expected to stay freeze-safe on 3G / low-power devices if all
# of its layers together fit in 100 kB after gzip.
LAYER_BUDGET_BYTES = 80 * 1024
SCENARIO_BUDGET_BYTES = 100 * 1024


@dataclass
class LayerSpec:
    source_layer: str
    # 0 (default) disables the cap entirely — every source feature in the
    # scenario bbox ships. Caps were originally a freeze-safety knob (100 kB
    # gzip target) but ended up shipping a 1-in-26 sample of buildings; we
    # now ship full datasets and let the browser handle 50k-class layers.
    # Override only when a layer must be intentionally subsampled.
    max_features: int = 0
    simplify: float = DEFAULT_TOLERANCE
    keep_fields: tuple[str, ...] = ()
    where: str | None = None  # pandas query expression applied after load
    # When True, polygon/line geometries are replaced with their centroid
    # (computed in a metric CRS so the result sits inside the source footprint
    # even at 45 deg latitude). Intended for POI layers whose server-side
    # semantics are point-like (e.g. Dijkstra sources) but whose BD TOPO
    # geometry is a building footprint.
    as_point: bool = False
    # Metric CRS used for the centroid reprojection. Only read when
    # ``as_point`` is True.
    point_crs_meters: str = "EPSG:2154"
    # Optional: read from a standalone file (GeoJSON/GPKG) under
    # ``examples/datasets/`` instead of the scenario's ``source_gpkg``. Used
    # when a layer comes from a different origin — e.g. OSM Overpass cache for
    # the accessibility scenario's exhaustive health POI set, which BD TOPO
    # cannot supply (only lists 47 Santé establishments, no individual GPs).
    source_file: str | None = None


@dataclass
class DerivedLayerSpec:
    """Layer derived from a sibling processed layer (e.g. dissolved buffer).

    Two kinds:

    - ``kind="buffer"`` (default): single dissolved buffer at ``distance_m``.
    - ``kind="buffer_rings"``: concentric annuli at every distance in
      ``distances_m`` (sorted ASC). The inner ring is a full disc of radius
      ``distances_m[0]``; subsequent rings are ``disc(d_i) - disc(d_{i-1})``,
      so they tile the area without overlap. Each output feature carries a
      ``distance_m`` property (closest-edge tier the ring stands for) and a
      ``_style_color`` for the per-feature colorField paint path.

    Common steps: filter (``where``), reproject to metric CRS, buffer/dissolve,
    simplify, reproject back to EPSG:4326.
    """
    source_layer: str
    kind: str = "buffer"
    distance_m: float = 30.0
    distances_m: tuple[float, ...] | None = None
    ring_colors: tuple[str, ...] | None = None
    where: str | None = None
    crs_meters: str = "EPSG:2154"
    simplify_m: float = 5.0


@dataclass
class ScenarioSpec:
    slug: str
    title: str
    source_gpkg: str
    bbox_4326: tuple[float, float, float, float]
    layers: dict[str, LayerSpec]
    center: tuple[float, float] = field(default=(0.0, 0.0))
    zoom: int = 14
    derived_layers: dict[str, DerivedLayerSpec] = field(default_factory=dict)


SCENARIOS: list[ScenarioSpec] = [
    ScenarioSpec(
        slug="flood-risk",
        title="S1 - Toulouse / Risque inondation Garonne",
        source_gpkg="toulouse_bdtopo.gpkg",
        bbox_4326=(1.420, 43.590, 1.470, 43.620),
        center=(1.445, 43.605),
        zoom=15,
        layers={
            "batiments": LayerSpec(
                source_layer="batiments",
                # simplify 5e-5 ≈ 5 m at 45N — invisible at zoom <= 14, keeps
                # the ~31k Toulouse footprints under 1.2 MB gzipped.
                simplify=5e-5,
                keep_fields=(
                    "usage_1", "hauteur",
                    "altitude_minimale_sol", "altitude_maximale_toit",
                    "nombre_d_etages", "nombre_de_logements",
                ),
            ),
            "cours_eau": LayerSpec(
                source_layer="cours_eau",
                simplify=5e-5,
                keep_fields=("toponyme", "largeur"),
            ),
            "surfaces_eau": LayerSpec(
                source_layer="surfaces_eau",
                simplify=5e-5,
                keep_fields=("toponyme", "nature"),
            ),
        },
    ),
    ScenarioSpec(
        slug="data-quality",
        title="S2 - Toulouse / Batiments pres voies locales",
        source_gpkg="toulouse_bdtopo.gpkg",
        bbox_4326=(1.430, 43.595, 1.460, 43.615),
        center=(1.445, 43.605),
        zoom=15,
        layers={
            "batiments": LayerSpec(
                source_layer="batiments",
                # ~13k buildings over the tighter 3×2 km central Toulouse
                # window — full source ships.
                simplify=5e-5,
                keep_fields=("usage_1", "hauteur"),
            ),
            "routes": LayerSpec(
                source_layer="routes",
                simplify=2e-5,
                keep_fields=("nom_1_gauche", "importance", "nature"),
                where="importance in ['5', '6']",
            ),
        },
    ),
    ScenarioSpec(
        slug="accessibility",
        title="S3 - Clermont Auvergne Metropole / Ecoles-sante isochrones",
        source_gpkg="clermont_ferrand_bdtopo.gpkg",
        bbox_4326=(3.020, 45.740, 3.180, 45.830),
        center=(3.100, 45.785),
        zoom=12,
        layers={
            "equipements": LayerSpec(
                # OSM Overpass cache — richer than BD TOPO equipements, which
                # only lists 47 "Santé" establishments for Clermont (hospitals,
                # clinics, nursing homes, thermal baths). OSM adds the POIs
                # actually useful for a "can I walk to a GP?" question: ~75
                # pharmacies, ~29 GPs, 21 labs, dentists, specialists,
                # veterinaries, social facilities — 223 points in the scenario
                # bbox. Refresh the cache with
                # ``python scripts/fetch_health_pois_osm.py --city clermont-ferrand --force``
                source_layer="equipements",
                source_file="clermont_ferrand_health_osm.geojson",
                # Already tagged categorie='Santé' by the fetcher; pre-filter
                # remains explicit so the pipeline's first step is a no-op on
                # the static bundle.
                where="categorie == 'Santé'",
                keep_fields=("toponyme", "nature", "categorie", "amenity", "healthcare"),
            ),
            "routes": LayerSpec(
                source_layer="routes",
                # Full BD TOPO routes (~15k troncons in the Clermont bbox) —
                # no `where` filter. The isochrone Dijkstra runs on the
                # complete network (incl. importance 5/6 local streets and
                # service roads), so the static base layer must mirror that
                # to keep the visualisation honest. Filtering here would
                # have hidden ~78% of the troncons and made the rendered
                # rings look disconnected from the underlying graph.
                simplify=5e-5,
                keep_fields=("nature", "importance"),
            ),
            "batiments": LayerSpec(
                source_layer="batiments",
                # ~77k batiments over the full Clermont metropole bbox — full
                # source ships. simplify 5e-5 ≈ 5 m at 45N keeps the gzipped
                # payload tractable (~2.5 MB). Pipeline `limit` in
                # PipelinePanel must match (raised to 100k for the same
                # reason — the API otherwise re-trims classify_by_ring
                # output below the source size).
                simplify=5e-5,
                keep_fields=("usage_1",),
            ),
        },
    ),
    ScenarioSpec(
        slug="road-setback",
        title="S4 - Clermont Auvergne Metropole / Reseau principal + recul urbanisme",
        source_gpkg="clermont_ferrand_bdtopo.gpkg",
        bbox_4326=(3.020, 45.740, 3.180, 45.830),
        center=(3.100, 45.785),
        zoom=12,
        layers={
            "routes": LayerSpec(
                source_layer="routes",
                simplify=5e-5,
                keep_fields=("nom_1_gauche", "importance", "nature"),
                # Narrowed to top-tier axes (autoroutes + nationales) — matches
                # the L111-6 scope the demo claims to mimic. With wider 1-4
                # ribbons the 250 m outer ring would saturate the viewport.
                where="importance in ['1', '2']",
            ),
            # No `batiments` here: the road-setback playground demonstrates a
            # DML trigger fired by a *user-drawn* footprint. Real BDTOPO
            # batiments compete with the drawn polygon for attention and
            # don't contribute to the trigger evaluation (client-side draw
            # lands in `drawn_batiments_polys` / `drawn_batiments_pts`).
        },
        derived_layers={
            # 5 concentric annuli (50/100/150/200/250 m) around importance 1-2
            # routes. The client paints each tier with the per-feature
            # `_style_color` and uses the rings to grade a drawn polygon:
            #   intersects ring with distance_m <= 200 → red (alert)
            #   intersects only the 250 m ring         → orange (warning)
            #   no intersection                        → green (compliant)
            # Backend trigger stays binary on a 250 m disc — the gradient is
            # purely a UX signal of "how close to the road".
            "setback_zone": DerivedLayerSpec(
                source_layer="routes",
                kind="buffer_rings",
                distances_m=(50.0, 100.0, 150.0, 200.0, 250.0),
                ring_colors=(
                    "#B71C1C",  # 0-50 m   deep red
                    "#D32F2F",  # 50-100   red
                    "#E53935",  # 100-150  light red
                    "#EF5350",  # 150-200  pinkish red
                    "#FB8C00",  # 200-250  orange (warning band)
                ),
                where="importance in ['1', '2']",
                crs_meters="EPSG:2154",
                simplify_m=8.0,
            ),
        },
    ),
    ScenarioSpec(
        slug="green-spaces",
        title="S5 - Versailles / Accessibilite parcs par batiment",
        source_gpkg="versailles_bdtopo.gpkg",
        # bbox extended east to source extent (was 1.960-2.170): the previous
        # cap dropped the entire eastern half of versailles_bdtopo (~18k
        # batiments + ~200 vegetation patches), so nearest_park measurements
        # at the east border of Versailles fell back on parks outside the
        # supplied reference layer. Source bounds: batiments 2.060-2.213,
        # vegetation 2.059-2.230 → clip a hair beyond to keep edge polygons.
        bbox_4326=(2.050, 48.755, 2.235, 48.845),
        center=(2.142, 48.800),
        zoom=12,
        layers={
            "vegetation": LayerSpec(
                source_layer="vegetation",
                # Full source — ~1.4k vegetation polygons over the wider
                # Versailles cadre. 1e-4 deg (~11 m) simplify is invisible
                # at zoom 12 where Fausses-Reposes spans dozens of km.
                simplify=1e-4,
                # BD TOPO zone_de_vegetation has no toponyme field — cleabs is
                # the only stable identifier (IGN pivot). Used in the popup as
                # "nearest park id".
                keep_fields=("cleabs", "nature"),
            ),
            "batiments": LayerSpec(
                source_layer="batiments",
                # ~47k batiments — full Versailles source ships.
                simplify=5e-5,
                keep_fields=("usage_1", "hauteur"),
            ),
        },
    ),
    ScenarioSpec(
        slug="real-estate",
        title="S6 - Versailles / Carte prix au m2 DVF",
        source_gpkg="versailles_bdtopo.gpkg",
        # Aligned on S5 green-spaces extended extent: covers Versailles
        # commune + Le Chesnay-Rocquencourt + Viroflay + Buc + Jouy-en-Josas.
        # The forested west/south stays in-bbox but yields ~no DVF mutations,
        # so the choropleth concentrates naturally on the urban tissue.
        bbox_4326=(2.050, 48.755, 2.235, 48.845),
        center=(2.142, 48.800),
        zoom=12,
        layers={
            "dvf_ventes": LayerSpec(
                source_layer="dvf_ventes",
                # ~8.9k mutations 2022-2024 over Versailles + 8 surrounding
                # communes — full source ships.
                simplify=0,  # points — nothing to simplify
                keep_fields=(
                    "id_mutation",
                    "date_mutation",
                    "nature_mutation",
                    "type_local",
                    "valeur_fonciere",
                    "surface_reelle_bati",
                    "nombre_pieces_principales",
                ),
            ),
        },
    ),
]


def _prune_columns(gdf: gpd.GeoDataFrame, keep: Iterable[str]) -> gpd.GeoDataFrame:
    wanted = set(DEFAULT_KEEP) | set(keep) | {"geometry"}
    keep_cols = [c for c in gdf.columns if c in wanted]
    if "geometry" not in keep_cols:
        keep_cols.append("geometry")
    return gdf[keep_cols].copy()


def _derive_layer(
    processed: dict[str, gpd.GeoDataFrame],
    spec: DerivedLayerSpec,
) -> gpd.GeoDataFrame | None:
    base = processed.get(spec.source_layer)
    if base is None or base.empty:
        return None
    gdf = base
    if spec.where:
        try:
            gdf = gdf.query(spec.where)
        except Exception as exc:
            print(f"    ! derived where failed: {exc}", file=sys.stderr)
            return None
    if gdf.empty:
        return None

    if spec.kind not in ("buffer", "buffer_rings"):
        print(f"    ! unknown derived kind: {spec.kind}", file=sys.stderr)
        return None

    metric = gdf.to_crs(spec.crs_meters)

    if spec.kind == "buffer":
        # resolution=3 keeps cap/joins readable while cutting vertex count ~5x vs
        # shapely's default of 16 — critical because the buffer has to fit in the
        # scenario size budget (100 kB gzip, see SCENARIO_BUDGET_BYTES).
        buffered = metric.buffer(spec.distance_m, resolution=3, cap_style=1, join_style=1)
        # Dissolve into a single MultiPolygon so the client can do one fast
        # point-in-polygon test against it without paying per-road cost.
        dissolved = buffered.union_all()
        # Simplify in metric units — imperceptible at the buffer width but critical
        # when the source filter covers a dense urban network.
        dissolved = dissolved.simplify(spec.simplify_m, preserve_topology=True)
        out = gpd.GeoDataFrame(geometry=[dissolved], crs=spec.crs_meters).to_crs(4326)
        return out

    # kind == "buffer_rings"
    if not spec.distances_m:
        print("    ! buffer_rings requires distances_m", file=sys.stderr)
        return None
    distances = sorted(spec.distances_m)
    colors = spec.ring_colors or tuple([None] * len(distances))
    if len(colors) != len(distances):
        print(
            f"    ! ring_colors length {len(colors)} != distances {len(distances)}",
            file=sys.stderr,
        )
        return None

    # Compute one dissolved disc per distance, then derive non-overlapping
    # annuli as disc_i - disc_{i-1}. Annuli let the client paint each tier
    # without alpha stacking and let the trigger eval find the smallest
    # intersected ring = closest distance to a road.
    discs = []
    for d in distances:
        buf = metric.buffer(d, resolution=3, cap_style=1, join_style=1).union_all()
        buf = buf.simplify(spec.simplify_m, preserve_topology=True)
        discs.append(buf)

    rows = []
    prev = None
    for d, disc, color in zip(distances, discs, colors):
        ring = disc if prev is None else disc.difference(prev)
        prev = disc
        if ring.is_empty:
            continue
        props = {"distance_m": float(d)}
        if color:
            props["_style_color"] = color
        rows.append({**props, "geometry": ring})

    if not rows:
        return None
    out = gpd.GeoDataFrame(rows, crs=spec.crs_meters).to_crs(4326)
    return out


def _process_layer(
    src: Path,
    bbox: tuple[float, float, float, float],
    spec: LayerSpec,
) -> gpd.GeoDataFrame | None:
    if spec.source_file:
        alt = SRC_DIR / spec.source_file
        if not alt.exists():
            print(f"  ! source_file missing: {alt}", file=sys.stderr)
            return None
        try:
            # GeoJSON has no layer concept — skip ``layer=`` for single-layer
            # formats so pyogrio doesn't raise.
            if alt.suffix.lower() in (".geojson", ".json"):
                gdf = gpd.read_file(alt, bbox=bbox)
            else:
                gdf = gpd.read_file(alt, layer=spec.source_layer, bbox=bbox)
        except Exception as exc:
            print(f"  ! read failed {alt.name}: {exc}", file=sys.stderr)
            return None
    else:
        try:
            gdf = gpd.read_file(src, layer=spec.source_layer, bbox=bbox)
        except Exception as exc:
            print(f"  ! read failed {spec.source_layer}: {exc}", file=sys.stderr)
            return None

    if gdf.empty:
        print(f"  . {spec.source_layer}: empty after bbox clip")
        return None

    if gdf.crs and gdf.crs.to_epsg() != 4326:
        gdf = gdf.to_crs(4326)

    if spec.where:
        try:
            before = len(gdf)
            gdf = gdf.query(spec.where)
            print(f"    where filter: {before} -> {len(gdf)}")
        except Exception as exc:
            print(f"    ! where expression failed: {exc}", file=sys.stderr)

    if gdf.empty:
        return None

    # Clip geometries to bbox (exact) to shrink large features crossing the window.
    clip_geom = box(*bbox)
    gdf["geometry"] = gdf.geometry.intersection(clip_geom)
    gdf = gdf[~gdf.geometry.is_empty & gdf.geometry.notna()]

    if gdf.empty:
        return None

    if spec.simplify and spec.simplify > 0:
        gdf["geometry"] = gdf.geometry.simplify(spec.simplify, preserve_topology=True)
        gdf = gdf[~gdf.geometry.is_empty & gdf.geometry.notna()]

    if spec.as_point:
        # Reproject to metric CRS for a centroid that actually sits inside
        # the footprint — shapely's 4326 centroid would be lat/lng-weighted
        # and drift on elongated BD TOPO buildings.
        projected = gdf.to_crs(spec.point_crs_meters).geometry.centroid
        gdf = gdf.set_geometry(gpd.GeoSeries(projected, crs=spec.point_crs_meters).to_crs(4326))
        gdf = gdf[~gdf.geometry.is_empty & gdf.geometry.notna()]

    if spec.max_features > 0 and len(gdf) > spec.max_features:
        # Deterministic decimation: keep every k-th feature.
        # Skipped when max_features == 0 (the default) — full dataset ships.
        k = len(gdf) // spec.max_features + 1
        gdf = gdf.iloc[::k].head(spec.max_features)
        print(f"    decimated -> {len(gdf)} (cap {spec.max_features})")

    gdf = _prune_columns(gdf, spec.keep_fields)
    return gdf


def _entry_from_disk(sc: ScenarioSpec, sc_dir: Path) -> dict | None:
    """Reconstruct a manifest entry from on-disk data files.

    Used when the source GPKG is unavailable but pre-built data still lives
    under ``docs-site/public/playground/data/<slug>/``. Counts features by
    inflating each file (gzipped or raw) just enough to read its
    ``features`` array length.
    """
    layer_names = list(sc.layers.keys()) + list(sc.derived_layers.keys())
    entry: dict = {
        "slug": sc.slug,
        "title": sc.title,
        "center": list(sc.center),
        "zoom": sc.zoom,
        "bbox": list(sc.bbox_4326),
        "layers": {},
    }
    total_bytes = 0
    found = False
    for name in layer_names:
        gz = sc_dir / f"{name}.geojson.gz"
        raw = sc_dir / f"{name}.geojson"
        target = gz if gz.exists() else (raw if raw.exists() else None)
        if target is None:
            entry["layers"][name] = {"features": 0, "size_bytes": 0, "file": None}
            continue
        size = target.stat().st_size
        try:
            opener = gzip.open if target.suffix == ".gz" else open
            with opener(target, "rb") as f:
                data = json.loads(f.read())
            features = len(data.get("features", []))
        except Exception as exc:
            print(f"    ! could not read {target.name}: {exc}", file=sys.stderr)
            features = 0
        entry["layers"][name] = {
            "features": int(features),
            "size_bytes": int(size),
            "file": f"{sc.slug}/{target.name}",
        }
        total_bytes += size
        found = True
    if not found:
        return None
    entry["total_size_bytes"] = total_bytes
    return entry


def build(
    scenarios: list[ScenarioSpec],
    out_dir: Path,
    *,
    dry_run: bool = False,
    compress: bool = True,
    strict: bool = False,
) -> dict:
    manifest: dict = {
        "generated_by": "scripts/build_playground_data.py",
        "crs": "EPSG:4326",
        "scenarios": [],
    }
    violations: list[str] = []

    for sc in scenarios:
        src = SRC_DIR / sc.source_gpkg
        if not src.exists():
            sc_dir = out_dir / sc.slug
            if sc_dir.is_dir() and any(sc_dir.iterdir()):
                # Source GPKG missing but pre-built data is on disk — emit a
                # manifest entry from the existing files so scenarios stay
                # listed even on hosts that don't ship every source dataset.
                print(f"[{sc.slug}] manifest-only — source {src.name} missing, reusing on-disk data")
                entry = _entry_from_disk(sc, sc_dir)
                if entry is not None:
                    manifest["scenarios"].append(entry)
                continue
            print(f"[{sc.slug}] SKIP — source {src} missing", file=sys.stderr)
            continue

        print(f"[{sc.slug}] {sc.title}")
        sc_dir = out_dir / sc.slug
        entry: dict = {
            "slug": sc.slug,
            "title": sc.title,
            "center": list(sc.center),
            "zoom": sc.zoom,
            "bbox": list(sc.bbox_4326),
            "layers": {},
        }

        total_bytes = 0
        processed: dict[str, gpd.GeoDataFrame] = {}
        for name, spec in sc.layers.items():
            print(f"  + {name} ({spec.source_layer})")
            gdf = _process_layer(src, sc.bbox_4326, spec)
            if gdf is None or gdf.empty:
                entry["layers"][name] = {"features": 0, "size_bytes": 0, "file": None}
                continue
            processed[name] = gdf

            fname = f"{name}.geojson"
            # GeoJSON with truncated precision to keep bytes down.
            payload = gdf.to_json(drop_id=True, to_wgs84=True)
            # shrink precision manually via round-trip (keeps it robust vs shapely versions)
            data = json.loads(payload)
            for feat in data.get("features", []):
                _round_coords(feat.get("geometry"))
            payload_bytes = json.dumps(data, separators=(",", ":"), ensure_ascii=False).encode("utf-8")

            if dry_run:
                size = len(payload_bytes)
                print(f"    would write {fname} ({len(gdf)} feats, {size/1024:.1f} kB)")
            else:
                sc_dir.mkdir(parents=True, exist_ok=True)
                target = sc_dir / fname
                if compress:
                    target = sc_dir / f"{fname}.gz"
                    with gzip.open(target, "wb", compresslevel=9) as f:
                        f.write(payload_bytes)
                else:
                    target.write_bytes(payload_bytes)
                size = target.stat().st_size
                print(f"    wrote {target.relative_to(ROOT)} ({len(gdf)} feats, {size/1024:.1f} kB)")

            total_bytes += size
            entry["layers"][name] = {
                "features": int(len(gdf)),
                "size_bytes": int(size),
                "file": f"{sc.slug}/{fname}{'.gz' if compress else ''}",
            }

            if size > LAYER_BUDGET_BYTES:
                violations.append(
                    f"[{sc.slug}.{name}] {size/1024:.1f} kB > budget {LAYER_BUDGET_BYTES/1024:.0f} kB"
                )

        for name, dspec in sc.derived_layers.items():
            print(f"  + {name} (derived {dspec.kind} from {dspec.source_layer})")
            gdf = _derive_layer(processed, dspec)
            if gdf is None or gdf.empty:
                entry["layers"][name] = {"features": 0, "size_bytes": 0, "file": None}
                continue

            fname = f"{name}.geojson"
            payload = gdf.to_json(drop_id=True, to_wgs84=True)
            data = json.loads(payload)
            for feat in data.get("features", []):
                _round_coords(feat.get("geometry"))
            payload_bytes = json.dumps(data, separators=(",", ":"), ensure_ascii=False).encode("utf-8")

            if dry_run:
                size = len(payload_bytes)
                print(f"    would write {fname} ({len(gdf)} feats, {size/1024:.1f} kB)")
            else:
                sc_dir.mkdir(parents=True, exist_ok=True)
                target = sc_dir / fname
                if compress:
                    target = sc_dir / f"{fname}.gz"
                    with gzip.open(target, "wb", compresslevel=9) as f:
                        f.write(payload_bytes)
                else:
                    target.write_bytes(payload_bytes)
                size = target.stat().st_size
                print(f"    wrote {target.relative_to(ROOT)} ({len(gdf)} feats, {size/1024:.1f} kB)")

            total_bytes += size
            entry["layers"][name] = {
                "features": int(len(gdf)),
                "size_bytes": int(size),
                "file": f"{sc.slug}/{fname}{'.gz' if compress else ''}",
            }

            if size > LAYER_BUDGET_BYTES:
                violations.append(
                    f"[{sc.slug}.{name}] {size/1024:.1f} kB > budget {LAYER_BUDGET_BYTES/1024:.0f} kB"
                )

        entry["total_size_bytes"] = total_bytes
        manifest["scenarios"].append(entry)
        marker = ""
        if total_bytes > SCENARIO_BUDGET_BYTES:
            violations.append(
                f"[{sc.slug}] total {total_bytes/1024:.1f} kB > budget {SCENARIO_BUDGET_BYTES/1024:.0f} kB"
            )
            marker = "  (OVER BUDGET)"
        print(f"  = total {total_bytes/1024:.1f} kB{marker}")

    if not dry_run:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "manifest.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"\nmanifest -> {(out_dir / 'manifest.json').relative_to(ROOT)}")

    if violations:
        print("\nSize budget violations:", file=sys.stderr)
        for v in violations:
            print(f"  - {v}", file=sys.stderr)
        if strict:
            raise SystemExit(
                "\nAborting: --strict enforces size budgets "
                f"(layer <= {LAYER_BUDGET_BYTES/1024:.0f} kB, scenario <= {SCENARIO_BUDGET_BYTES/1024:.0f} kB)"
            )

    return manifest


def _round_coords(geom: dict | None, precision: int = 6) -> None:
    """In-place rounding of GeoJSON coordinates to `precision` decimals (~11 cm at 45N)."""
    if not geom:
        return
    t = geom.get("type")
    c = geom.get("coordinates")
    if c is None:
        return

    def _r(v):
        if isinstance(v, (int, float)):
            return round(v, precision)
        return [_r(x) for x in v]

    geom["coordinates"] = _r(c)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Build static playground datasets")
    p.add_argument("--scenario", help="Only build a specific scenario slug")
    p.add_argument("--dry-run", action="store_true", help="Log sizes without writing")
    p.add_argument("--no-gzip", action="store_true", help="Write raw .geojson (default gzipped)")
    p.add_argument(
        "--strict",
        action="store_true",
        help=f"Fail on layer > {LAYER_BUDGET_BYTES//1024} kB or scenario > {SCENARIO_BUDGET_BYTES//1024} kB",
    )
    args = p.parse_args(argv)

    selected = SCENARIOS
    if args.scenario:
        selected = [s for s in SCENARIOS if s.slug == args.scenario]
        if not selected:
            print(f"Unknown scenario: {args.scenario}", file=sys.stderr)
            return 2

    build(
        selected,
        OUT_DIR,
        dry_run=args.dry_run,
        compress=not args.no_gzip,
        strict=args.strict,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
