# S4 : Recul Reglementaire — Clermont-Ferrand

<span class="gp-difficulty-badge" style="background: var(--gp-green)">Debutant</span> <span class="gp-mode-badge">CLI + Map</span>

**Capabilities** : `filter`

## Cas d'usage

Un service urbanisme veut illustrer la regle de recul L111-6 sur les axes structurants de Clermont-Ferrand (importance 1 et 2 — autoroutes et nationales). Le pipeline se reduit a une etape : extraire le reseau cible. Tout l'interet du scenario est ensuite cote terrain : un instructeur dessine un projet de batiment (polygone ou point) sur la carte et voit immediatement si le recul est respecte, via un trigger DML cote serveur double d'un gradient visuel cote client.

## Donnees IGN BD TOPO V3

| Couche | Contenu | Features | Attributs cles | Source |
|--------|---------|----------|----------------|--------|
| `routes` | Troncons de route BD TOPO | 2 272 | nature, importance, nom_1_gauche | [data.geopf.fr](https://data.geopf.fr) — BDTOPO_V3:troncon_de_route |

> Pas de couche `batiments` chargee : le scenario evalue le recul d'un **batiment dessine en direct par l'utilisateur** (cf. mode dessin plus bas), pas d'un parc existant.

```bash
python examples/prepare_playground_data.py --city clermont-ferrand
gispulse info examples/datasets/clermont_ferrand_bdtopo.gpkg --layer routes
```

## Pipeline (1 etape)

```
routes ──► filter (importance ∈ {1,2})              # ne retient que les autoroutes + nationales
```

::: tip Pourquoi un seul step ?
Le scenario S4 sert a montrer le couple **regle declarative + trigger DML reactif**. Calculer un lineaire ou un cout serait du remplissage : le focus est sur l'evaluation en direct du recul, pas sur un batch metier.
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

::: tip Telecharger
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

## Trigger DML — Controle de recul reglementaire

On attache un **trigger DML** sur la couche `batiments` : chaque INSERT est evalue contre deux predicats (geometrique + attributaire). Si le batiment est **residentiel** et **a moins de 250 m** d'un axe structurant (importance 1-2 — autoroutes et nationales), la cascade d'actions se declenche.

La couche overlay precalcule **5 anneaux concentriques** (50 / 100 / 150 / 200 / 250 m) autour de ce reseau. Ils servent de gradient visuel : le client positionne chaque feature dessinee dans le bon palier et choisit la couleur sans aller-retour serveur. Le trigger backend reste un seul predicat geometrique `buffer_m: 250`.

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
    { "action_type": "NOTIFY", "config": { "message": "ALERT URBA: nouveau bati residentiel a moins de 250 m d'un axe structurant (autoroute ou nationale) a Clermont-Ferrand. Verifier recul reglementaire (Code urba L111-6).", "channel": "urbanisme" } }
  ],
  "enabled": true
}
```

::: tip Telecharger
[scenario-4-trigger.json](/gispulse/playground/scenario-4-trigger.json) — inspire du recul L111-6 du Code de l'urbanisme (75 m autoroutes / 25 m voies a grande circulation). Le filtre `importance in ('1','2')` colle au perimetre reel L111-6 (top-tier) ; le rayon `buffer_m: 250` est elargi par rapport au texte de loi pour rester lisible a l'echelle de l'agglomeration.
:::

::: info Architecture
```
INSERT batiments -> DML Trigger
                     |
                     +- GeomPredicate : intersects(buffer(routes WHERE importance in (1,2), 250 m)) OK
                     +- AttrPredicate : usage_1 == 'Residentiel' OK
                     |
                     v MATCH -> cascade 3 actions
                     +- FLAG_FEATURE -> _safety_alert = ROAD_SETBACK_VIOLATION
                     +- LOG_EVENT    -> warning journalise
                     +- NOTIFY       -> canal urbanisme

Client (UX) : 5 anneaux precalcules 50/100/150/200/250 m
              -> feature dessinee (polygone OU point) coloree par anneau intersecte le plus proche
              -> rouge <= 200 m | orange 200-250 m | vert > 250 m
```
:::

## Playground interactif

<ClientOnly><DualMapView scenario="road-setback" :showPipeline="true" :showTriggers="true" /></ClientOnly>

La carte affiche en gradient rouge → orange les **5 anneaux concentriques** (50 / 100 / 150 / 200 / 250 m) autour des routes importance 1-2, precalcules par [`build_playground_data.py`](https://github.com/imagodata/gispulse/blob/main/scripts/build_playground_data.py) → `setback_zone.geojson.gz`.

**Mode dessin** — choisis dans la toolbar :

- **Polygon** : trace l'empreinte d'un batiment fictif (seede en `usage_1 = "Residentiel"`). Le test geometrique mesure l'intersection complete contre chaque anneau.
- **Point** : place un point d'implantation. Le test prend le palier de l'anneau qui contient le point.

Dans les deux cas, le client garde le **plus petit `distance_m`** (= la route la plus proche) et applique :

- **palier ≤ 200 m** → feature **rouge fonce** (`#7F0000`), cascade FLAG_FEATURE / LOG_EVENT / NOTIFY declenchee, severite `warning`
- **palier 250 m uniquement** (200 < d ≤ 250 m) → feature **orange** (`#E65100`), cascade declenchee, palier WARNING marque dans le panel
- **aucun anneau intersecte** (> 250 m) → feature **verte** (`#2E7D32`), `NO MATCH : polygone hors de la zone de recul (> 250 m)`
- **dans la zone mais `usage_1 ≠ Residentiel`** → couleur du palier conservee mais `NO MATCH`, predicat attributaire KO

Tout se passe cote client : les dessins s'empilent dans des calques locaux `drawn_batiments_polys` / `drawn_batiments_pts` (`colorField: '_style_color'`), le trigger `alert_road_setback_violation` est charge depuis [`scenario-4-trigger.json`](/gispulse/playground/scenario-4-trigger.json) et l'evaluation (anneaux + predicats attr) s'execute dans le navigateur — meme intention que le `TriggerEvaluator` serveur, sans requete reseau par dessin.

## Essayer en live

<TryItLive endpoint="/capabilities" description="liste les capabilities disponibles (filter en tete) utilisees par le pipeline S4" />

<TryItLive endpoint="/datasets" description="liste les datasets demo, dont clermont_ferrand_bdtopo charge pour ce scenario" />

## Pour aller plus loin

- [S3 : Accessibilite](/playground/road-buffer-poi) — isochrones sur le meme reseau
- [Capabilities vecteur](/guide/capabilities#vecteur) — filter et la suite
- [Template FTTH](/guide/rules#templates) — pipeline complet de design fibre (connectivity, shortest_path, allocation)
