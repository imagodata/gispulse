# S4: Setback Compliance — Clermont-Ferrand

<span class="gp-difficulty-badge" style="background: var(--gp-green)">Beginner</span> <span class="gp-mode-badge">CLI + Map</span>

**Capabilities**: `filter`

## Use Case

An urban-planning team wants to illustrate the L111-6 setback rule on Clermont-Ferrand's structural axes (importance 1 and 2 — motorways and national roads). The pipeline collapses to a single step: extract the target network. The whole point of the scenario lives downstream — a planner sketches a building project (polygon or point) on the map and immediately sees whether the setback is honoured, via a server-side DML trigger backed by a client-side gradient.

## IGN BD TOPO V3 Data

| Layer | Content | Features | Key attributes | Source |
|-------|---------|----------|----------------|--------|
| `routes` | BD TOPO road segments | 2,272 | nature, importance, nom_1_gauche | [data.geopf.fr](https://data.geopf.fr) — BDTOPO_V3:troncon_de_route |
| `batiments` | BD TOPO building footprints | ~800 | usage_1, hauteur | [data.geopf.fr](https://data.geopf.fr) — BDTOPO_V3:batiment |

```bash
python examples/prepare_playground_data.py --city clermont-ferrand
gispulse info examples/datasets/clermont_ferrand_bdtopo.gpkg --layer routes
```

## Pipeline (1 step)

```
routes ──► filter (importance ∈ {1,2})              # keep only motorways + national roads
```

::: tip Why a single step?
S4 is about the **declarative rule + reactive DML trigger** combo. Computing length or cost would be filler: the focus is on the live setback evaluation, not on a batch job.
:::

## Rules

```json
{
  "version": 2,
  "steps": [
    {
      "id": "filter_major_roads",
      "capability": "filter",
      "params": { "expression": "importance in ['1','2']" }
    }
  ]
}
```

::: tip Download
[scenario-4-rules.json](/gispulse/playground/scenario-4-rules.json)
:::

## Execution

```bash
gispulse run examples/datasets/clermont_ferrand_bdtopo.gpkg \
  --layer routes \
  --rules playground/scenario-4-rules.json \
  -o output/structural_network.gpkg

gispulse serve output/structural_network.gpkg
```

## DML Trigger — Setback Compliance Check

We attach a **DML trigger** on the `batiments` layer: every INSERT is evaluated against two predicates (geometric + attribute). If the building is **residential** and **within 250 m** of a structural road (importance 1-2 — motorways and national roads), the cascade fires.

The overlay precomputes **5 concentric rings** (50 / 100 / 150 / 200 / 250 m) around that network. They are a UX gradient: the client picks the smallest ring a drawn feature touches and assigns the matching tier color, no server round-trip. The backend trigger remains a single geometric predicate at `buffer_m: 250`.

```json
{
  "name": "alert_road_setback_violation",
  "event": "FEATURE_CREATED",
  "trigger_type": "DML",
  "category": "BUSINESS_RULE",
  "severity": "warning",
  "conditions": {
    "table": "batiments",
    "operations": ["INSERT"]
  },
  "predicates": [
    {
      "type": "geom",
      "op": "intersects",
      "ref_table": "routes",
      "ref_filter": "importance in ('1', '2')",
      "buffer_m": 250
    },
    { "type": "attr", "field": "usage_1", "op": "eq", "value": "Residentiel" }
  ],
  "predicate_logic": "AND",
  "actions": [
    { "action_type": "FLAG_FEATURE", "config": { "field": "_safety_alert", "value": "ROAD_SETBACK_VIOLATION" } },
    { "action_type": "LOG_EVENT", "config": { "message": "Residential building within 250 m of a structural road (importance 1-2) - check L111-6 setback rule", "level": "warning" } },
    { "action_type": "NOTIFY", "config": { "message": "URBA ALERT: new residential building within 250 m of a structural road (motorway or national) in Clermont-Ferrand. Check L111-6 setback rule.", "channel": "urbanisme" } }
  ],
  "enabled": true
}
```

::: tip Download
[scenario-4-trigger.json](/gispulse/playground/scenario-4-trigger.json) — inspired by French Code de l'urbanisme L111-6 (75 m motorway / 25 m major traffic setback). The `importance in ('1','2')` filter sticks to the actual L111-6 scope (top-tier axes); the `buffer_m: 250` radius is widened from the legal text so the alert reads at the metro-area zoom.
:::

::: info Architecture
```
INSERT batiments -> DML Trigger
                     |
                     +- GeomPredicate : intersects(buffer(routes WHERE importance in (1,2), 250 m)) OK
                     +- AttrPredicate : usage_1 == 'Residentiel' OK
                     |
                     v MATCH -> 3-action cascade
                     +- FLAG_FEATURE -> _safety_alert = ROAD_SETBACK_VIOLATION
                     +- LOG_EVENT    -> warning logged
                     +- NOTIFY       -> urbanisme channel

Client (UX) : 5 precomputed rings 50/100/150/200/250 m
              -> drawn feature (polygon OR point) coloured by closest ring it touches
              -> red <= 200 m | orange 200-250 m | green > 250 m
```
:::

## Interactive playground

<ClientOnly><DualMapView scenario="road-setback" :showPipeline="true" :showTriggers="true" /></ClientOnly>

The map shows the **5 concentric rings** (50 / 100 / 150 / 200 / 250 m) around importance 1-2 roads as a red→orange gradient, precomputed by [`build_playground_data.py`](https://github.com/imagodata/gispulse/blob/main/scripts/build_playground_data.py) → `setback_zone.geojson.gz`.

**Draw mode** — pick from the toolbar:

- **Polygon**: sketch a mock building footprint (seeded with `usage_1 = "Residentiel"`). The geometric test checks the full polygon-against-rings intersection.
- **Point**: drop a siting marker. The test picks the tier of the ring that contains the point.

In both cases the client keeps the **smallest `distance_m`** (= the closest road) and applies:

- **tier ≤ 200 m** → **dark red** feature (`#7F0000`), FLAG_FEATURE / LOG_EVENT / NOTIFY cascade fires, severity `warning`
- **only the 250 m ring** (200 < d ≤ 250 m) → **orange** feature (`#E65100`), cascade fires, WARNING tier flagged in the panel
- **no ring intersects** (> 250 m) → **green** feature (`#2E7D32`), `NO MATCH: polygon outside setback zone (> 250 m)`
- **inside the zone but `usage_1 ≠ Residentiel`** → tier color kept, `NO MATCH`, attribute predicate KO

Everything runs in-browser: drawings accumulate in local `drawn_batiments_polys` / `drawn_batiments_pts` layers (`colorField: '_style_color'`); the `alert_road_setback_violation` trigger is loaded from [`scenario-4-trigger.json`](/gispulse/playground/scenario-4-trigger.json) and evaluated (rings + attr predicates) client-side — same intent as the server's `TriggerEvaluator`, no network round-trip per draw.

## Try it live

<TryItLive endpoint="/capabilities" description="lists available capabilities (starting with filter) used by the S4 pipeline" />

<TryItLive endpoint="/datasets" description="lists demo datasets, including clermont_ferrand_bdtopo loaded for this scenario" />

## Next steps

- [S3: Accessibility](/en/playground/road-buffer-poi) — isochrones on the same network
- [Vector capabilities](/en/guide/capabilities#vector) — filter and the rest
- [FTTH template](/en/guide/rules#templates) — complete fiber design pipeline (connectivity, shortest_path, allocation)
