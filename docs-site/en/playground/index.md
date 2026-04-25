# Playground

Six concrete scenarios to get started with GISPulse in 5 minutes, on real **IGN BD TOPO V3** + **DVF Etalab** data (Toulouse, Clermont-Ferrand, Versailles).

Each scenario is a self-contained JSON pipeline: 2-4 steps, runnable with a single CLI command and rendered **step by step** on an interactive map (demo backend required).

## Install

```bash
pip install gispulse
# or from source
git clone https://github.com/imagodata/gispulse && cd gispulse
pip install -e ".[dev]"
```

## Download data

```bash
# All 3 cities (recommended)
python examples/prepare_playground_data.py

# Or a single city
python examples/prepare_playground_data.py --city toulouse
python examples/prepare_playground_data.py --city clermont-ferrand
python examples/prepare_playground_data.py --city versailles
```

| City | Buildings | Roads | Facilities | Specificity |
|------|-----------|-------|------------|-------------|
| **Toulouse** | ~31,000 | 3,680 | 393 | Garonne corridor, dense center |
| **Clermont-Ferrand** | ~5,000 | 2,272 | 590 | Compact network, relief |
| **Versailles** | ~5,000 | 1,617 | 405 | 509 vegetation zones |

## Scenarios

<div class="gp-scenario-grid">

<ScenarioCard
  title="S1 — Flood Risk"
  difficulty="intermediaire"
  description="Toulouse — low-rise buildings (<=15 m) in the 250 m Garonne corridor, ground 0-15 m above water level (altitude_minimale_sol, BD TOPO V3). 4 steps."
  :capabilities="['filter']"
  link="/gispulse/en/playground/urban-flood-risk"
  mode="CLI + Map"
/>

<ScenarioCard
  title="S2 — Commercial Buildings along Arterials"
  difficulty="debutant"
  description="Toulouse — commercial buildings (usage_1 or usage_2 == 'Commercial et services') within 50 m of an IGN arterial road (importance 2-4 — national, departmental, main urban). 3 steps — each step (filtered roads, buildings in buffer, commercial) is a visible layer."
  :capabilities="['filter']"
  link="/gispulse/en/playground/commercial-arterials"
  mode="CLI + Map"
/>

<ScenarioCard
  title="S3 — Health Facility Accessibility"
  difficulty="avance"
  description="Clermont-Ferrand — 10-min walking isochrones (833 m) on the BD TOPO network from health facilities (categorie == 'Santé'), multi-source Dijkstra, metric coverage in m². (Pro.)"
  :capabilities="['filter', 'isochrone', 'area_length']"
  link="/gispulse/en/playground/road-buffer-poi"
  mode="CLI + Map"
/>

<ScenarioCard
  title="S4 — Main Road Network + Urban Setback"
  difficulty="intermediaire"
  description="Clermont-Ferrand — filter by importance (1-4), Lambert93 length, indicative 100 EUR/m cost. DML trigger: draw a building inside the visible 50 m setback zone around motorways/national roads and see the L111-6-inspired cascade fire (FLAG, LOG, NOTIFY)."
  :capabilities="['filter', 'area_length', 'calculate']"
  link="/gispulse/en/playground/road-setback"
  mode="CLI + Map"
/>

<ScenarioCard
  title="S5 — Park accessibility per building"
  difficulty="intermediaire"
  description="Versailles — BD TOPO vegetation + buildings: parks ≥ 1 ha (SCoT IdF), nearest_neighbor distance from each residential building to the closest park, manual classification against WHO / SCoT / ADEME thresholds (300 / 600 / 1000 m). Green-to-red building choropleth. Weekly cron inside the pipeline."
  :capabilities="['area_length', 'filter', 'nearest_neighbor', 'classify']"
  link="/gispulse/en/playground/green-spaces"
  mode="CLI + Map"
/>

<ScenarioCard
  title="S6 — Price-per-m² Map (DVF)"
  difficulty="intermediaire"
  description="Versailles — Etalab DVF mutations 2022-2024, filter residential sales, compute price/m², quintile classes + YlOrRd palette. Color gradient by price-per-m² rendered live on the map."
  :capabilities="['filter', 'calculate', 'classify']"
  link="/gispulse/en/playground/real-estate"
  mode="CLI + Map"
/>

</div>

## Ready-to-use pipelines

**Pipeline v2** format (DAG with steps, triggers, ref_layers), validated by JSON Schema on load.

| Scenario | Pipeline | Trigger |
|----------|----------|---------|
| S1 Flood risk (4 steps) | [scenario-1-rules.json](/gispulse/playground/scenario-1-rules.json) | — |
| S2 Commercial / arterial roads (3 steps) | [scenario-2-rules.json](/gispulse/playground/scenario-2-rules.json) | — |
| S3 Health accessibility (3 steps) | [scenario-3-rules.json](/gispulse/playground/scenario-3-rules.json) | — |
| S4 Road network (3 steps) | [scenario-4-rules.json](/gispulse/playground/scenario-4-rules.json) | [scenario-4-trigger.json](/gispulse/playground/scenario-4-trigger.json) |
| S5 Park accessibility (5 steps, 2 branches) | [scenario-5-rules.json](/gispulse/playground/scenario-5-rules.json) | weekly cron (inside rules) |
| S6 Price-per-m² DVF (8 steps) | [scenario-6-rules.json](/gispulse/playground/scenario-6-rules.json) | — |

::: info Capabilities out of scope here
`spatial_aggregate`, `dissolve`, `connectivity_check`, `shortest_path`, `zonal_stats`, `ndvi`, etc. are covered by the templates gallery and the [capabilities guide](/en/guide/capabilities). The 6 playgrounds favour short, linear workflows (2-8 steps each).
:::
