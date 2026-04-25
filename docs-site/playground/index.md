# Playground

Six scenarios concrets pour prendre en main GISPulse en 5 minutes, sur des donnees reelles **IGN BD TOPO V3** + **DVF Etalab** (Toulouse, Clermont-Ferrand, Versailles).

Chaque scenario est un pipeline JSON auto-portant : 2 a 4 etapes, lancable en une commande CLI et rendu **etape par etape** sur une carte interactive (necessite le backend demo).

## Installation

```bash
pip install gispulse
# ou depuis les sources
git clone https://github.com/imagodata/gispulse && cd gispulse
pip install -e ".[dev]"
```

## Telecharger les donnees

```bash
# Les 3 villes (recommande)
python examples/prepare_playground_data.py

# Ou une seule ville
python examples/prepare_playground_data.py --city toulouse
python examples/prepare_playground_data.py --city clermont-ferrand
python examples/prepare_playground_data.py --city versailles
```

| Ville | Batiments | Routes | Equipements | Specificite |
|-------|-----------|--------|-------------|-------------|
| **Toulouse** | ~31 000 | 3 680 | 393 | Corridor Garonne, centre dense |
| **Clermont-Ferrand** | ~5 000 | 2 272 | 590 | Reseau compact, relief |
| **Versailles** | ~5 000 | 1 617 | 405 | 509 zones de vegetation |

## Scenarios

<div class="gp-scenario-grid">

<ScenarioCard
  title="S1 — Risque Inondation"
  difficulty="intermediaire"
  description="Toulouse — batiments bas (<=15 m) dans le corridor 250 m Garonne, sol 0-15 m au-dessus du niveau de l'eau (altitude_minimale_sol BD TOPO V3). 4 etapes."
  :capabilities="['filter']"
  link="/gispulse/playground/urban-flood-risk"
  mode="CLI + Map"
/>

<ScenarioCard
  title="S2 — Commerces / Axes Structurants"
  difficulty="debutant"
  description="Toulouse — commerces (usage_1 ou usage_2 == 'Commercial et services') a moins de 50 m d'un axe structurant IGN (importance 2-4 — nationales, departementales, voies principales). 3 etapes — chaque step (routes filtrees, batiments dans le buffer, commerces) est un layer visible."
  :capabilities="['filter']"
  link="/gispulse/playground/commercial-arterials"
  mode="CLI + Map"
/>

<ScenarioCard
  title="S3 — Accessibilite Sante"
  difficulty="avance"
  description="Clermont-Ferrand — isochrones 10 min a pied (833 m) sur reseau BD TOPO depuis les etablissements Sante, multi-sources Dijkstra, surface de couverture en m². (Pro.)"
  :capabilities="['filter', 'isochrone', 'area_length']"
  link="/gispulse/playground/road-buffer-poi"
  mode="CLI + Map"
/>

<ScenarioCard
  title="S4 — Reseau Routier + Recul Urbanisme"
  difficulty="intermediaire"
  description="Clermont-Ferrand — filtre par importance (1-4), longueur Lambert93, cout indicatif 100 EUR/m. Trigger DML : dessinez un batiment dans la zone de recul 50 m affichee autour des autoroutes/nationales et voyez la cascade L111-6 s'executer (FLAG, LOG, NOTIFY)."
  :capabilities="['filter', 'area_length', 'calculate']"
  link="/gispulse/playground/road-setback"
  mode="CLI + Map"
/>

<ScenarioCard
  title="S5 — Accessibilité parcs par bâtiment"
  difficulty="intermediaire"
  description="Versailles — BD TOPO vegetation + batiments : parcs ≥ 1 ha (SCoT IdF), distance nearest_neighbor de chaque résidentiel au parc le plus proche, classification manuelle contre seuils OMS/SCoT/ADEME (300 / 600 / 1000 m). Choroplèthe bâtiments vert → rouge. Cron hebdomadaire dans le pipeline."
  :capabilities="['area_length', 'filter', 'nearest_neighbor', 'classify']"
  link="/gispulse/playground/green-spaces"
  mode="CLI + Map"
/>

<ScenarioCard
  title="S6 — Carte du prix au m² (DVF)"
  difficulty="intermediaire"
  description="Versailles — mutations DVF Etalab 2022-2024, filtre ventes residentielles, calcul price/m², quintiles + palette YlOrRd. Gradient de couleur par prix au m² directement sur la carte."
  :capabilities="['filter', 'calculate', 'classify']"
  link="/gispulse/playground/real-estate"
  mode="CLI + Map"
/>

</div>

## Pipelines prets a l'emploi

Format **pipeline v2** (DAG avec steps, triggers, ref_layers), valide par JSON Schema au chargement.

| Scenario | Pipeline | Trigger |
|----------|----------|---------|
| S1 Risque inondation (4 steps) | [scenario-1-rules.json](/gispulse/playground/scenario-1-rules.json) | — |
| S2 Commerces / axes structurants (3 steps) | [scenario-2-rules.json](/gispulse/playground/scenario-2-rules.json) | — |
| S3 Accessibilite Sante (3 steps) | [scenario-3-rules.json](/gispulse/playground/scenario-3-rules.json) | — |
| S4 Reseau routier (3 steps) | [scenario-4-rules.json](/gispulse/playground/scenario-4-rules.json) | [scenario-4-trigger.json](/gispulse/playground/scenario-4-trigger.json) |
| S5 Accessibilité parcs (5 steps, 2 branches) | [scenario-5-rules.json](/gispulse/playground/scenario-5-rules.json) | cron hebdo (intégré au rules) |
| S6 Prix au m² DVF (8 steps) | [scenario-6-rules.json](/gispulse/playground/scenario-6-rules.json) | — |

::: info Capabilities hors-scope de ces demos
`spatial_aggregate`, `dissolve`, `connectivity_check`, `shortest_path`, `zonal_stats`, `ndvi`, etc. sont couvertes par la galerie de templates et le [guide capabilities](/guide/capabilities). Les 6 playgrounds privilegient des workflows lineaires et lisibles (2 a 8 etapes).
:::
