"""Precompute the S3 accessibility pipeline outputs locally.

The demo Cloud Run instance takes ~86 s to run the isochrone Dijkstra over
the full Clermont network, plus another ~5 s for classify_by_ring on 77 k
batiments. Two concurrent users on the playground page reliably 500s the
pod (OOM / serialise contention) — and the response itself routinely
breaches Cloud Run's 32 MB per-request cap.

Bypass it: run the same capabilities locally during the docs build, ship
each step output as a `.geojson.gz` next to the static layer bundle, and
let the playground replay them client-side via PipelinePanel's
`staticPipelineResults` mapping. No API hit, instant render, full 77 k
classified batiments visible.

Inputs: examples/datasets/clermont_ferrand_health_osm.geojson +
examples/datasets/clermont_ferrand_bdtopo.gpkg.

Outputs (under docs-site/public/playground/data/accessibility/):
  - filter_sante_step.geojson.gz       — equipements where categorie=='Santé'
  - isochrone_rings_step.geojson.gz    — 4 dissolved annuli
  - classify_by_ring_step.geojson.gz   — 77 k batiments with access_color

Pipeline mirror keys must match the rule names in
docs-site/public/playground/scenario-3-rules.json.

Requires GISPULSE_TIER=pro + a license key (or
GISPULSE_LICENCE_SKIP_VERIFY=1 for local/CI runs without a real key).
"""

from __future__ import annotations

import gzip
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import geopandas as gpd  # noqa: E402

# Auto-set dev license if absent so CI / local runs don't need a real key.
# The signature check is bypassed by GISPULSE_LICENCE_SKIP_VERIFY=1; the
# payload itself is parsed normally.
if not os.environ.get("GISPULSE_LICENSE_KEY"):
    from gispulse.persistence.tier import make_test_license_key  # noqa: E402

    os.environ["GISPULSE_TIER"] = "pro"
    os.environ["GISPULSE_LICENSE_KEY"] = make_test_license_key("pro")
    os.environ["GISPULSE_LICENCE_SKIP_VERIFY"] = "1"

from gispulse.capabilities.network import IsochroneCapability  # noqa: E402
from gispulse.capabilities.vector.classify import ClassifyByRingCapability  # noqa: E402

OUT_DIR = ROOT / "docs-site" / "public" / "playground" / "data" / "accessibility"

# Geometry simplification (deg) — invisible at zoom <= 14, halves payload.
ISOCHRONE_SIMPLIFY = 1e-5  # ~1 m at 45 N
BATIMENT_SIMPLIFY = 5e-5   # ~5 m at 45 N


def _save_gz(gdf: gpd.GeoDataFrame, path: Path, simplify_deg: float | None = None) -> None:
    if simplify_deg:
        gdf = gdf.copy()
        gdf.geometry = gdf.geometry.simplify(simplify_deg)
    js = json.loads(gdf.to_json())
    raw = json.dumps(js, separators=(",", ":")).encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wb", compresslevel=9) as f:
        f.write(raw)
    print(f"  wrote {path.name}: gz={path.stat().st_size / 1024:.0f} kB n={len(gdf)}")


def main() -> int:
    src_health = ROOT / "examples" / "datasets" / "clermont_ferrand_health_osm.geojson"
    src_bdtopo = ROOT / "examples" / "datasets" / "clermont_ferrand_bdtopo.gpkg"
    if not src_health.exists() or not src_bdtopo.exists():
        print("[precompute_accessibility] missing source data, skipping", file=sys.stderr)
        return 0  # not fatal — CI without the GPKGs falls back to live API

    t0 = time.time()
    equipements = gpd.read_file(src_health)
    sante = equipements[equipements["categorie"] == "Santé"].copy()

    # Use the FULL routes network — the demo API runs the isochrone against
    # `ref_layer: routes` with no where filter, so the Dijkstra graph
    # includes every troncon. Restricting to importance 1-4 here (~3.3k of
    # 15k) shrinks each isochrone disc 3x and visually reads as "ribbons
    # along the trunk roads" instead of proper walking-time polygons.
    routes = gpd.read_file(src_bdtopo, layer="routes")

    batiments = gpd.read_file(src_bdtopo, layer="batiments")
    print(f"load: {time.time() - t0:.1f}s — sante={len(sante)} routes={len(routes)} bati={len(batiments)}")

    # Step 1: filter_sante (already done above, just save the subset)
    keep_sante = ["geometry", "toponyme", "nature", "categorie", "amenity", "healthcare"]
    sante_min = sante[[c for c in keep_sante if c in sante.columns]].copy()
    _save_gz(sante_min, OUT_DIR / "filter_sante_step.geojson.gz")

    # Step 2: isochrone with cost_budgets (multi-budget single Dijkstra pass)
    t = time.time()
    # Parameters MUST match docs-site/public/playground/scenario-3-rules.json
    # — the precompute mirrors the live API behaviour exactly, so the
    # static replay matches what the user would get from
    # /pipelines/execute-steps. edge_buffer_m=200 (vs the network-strict
    # 40 m) fills the gaps between parallel streets so the dissolved
    # isochrone reads as an "intuitive walking-time area" rather than
    # ribbons hugging road centerlines — keep this value in sync with the
    # rules JSON above if you change either.
    iso = IsochroneCapability().execute(
        sante,
        cost_budgets=[500, 750, 1000, 1500],
        crs_meters="EPSG:2154",
        edge_buffer_m=200,
        dissolve=True,
        ref_gdf=routes,
    )
    print(f"isochrone: {time.time() - t:.1f}s, n={len(iso)}")
    _save_gz(iso, OUT_DIR / "isochrone_rings_step.geojson.gz", simplify_deg=ISOCHRONE_SIMPLIFY)

    # Step 3: classify_by_ring on full batiments
    t = time.time()
    classified = ClassifyByRingCapability().execute(
        batiments,
        ref_gdfs=[iso],
        ring_field="cost_budget",
        class_col="access_class",
        color_col="access_color",
        value_col="access_ring",
        palette=["#1a9850", "#fee08b", "#fdae61", "#f46d43", "#a50026"],
        use_centroid=True,
        ring_simplify_tolerance=10.0,
    )
    print(f"classify: {time.time() - t:.1f}s, n={len(classified)}")
    keep_cls = ["geometry", "usage_1", "access_ring", "access_class", "access_color"]
    classified_min = classified[[c for c in keep_cls if c in classified.columns]].copy()
    _save_gz(classified_min, OUT_DIR / "classify_by_ring_step.geojson.gz", simplify_deg=BATIMENT_SIMPLIFY)

    print(f"TOTAL: {time.time() - t0:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
