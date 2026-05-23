# Migration `triggers.yaml` v1 / pipeline v2 → manifeste v3

Le manifeste [`version: 3`](./elt-manifest.md) (ADR 0005) **supersède** les deux formats hérités :

- `triggers.yaml` `version: 1` — `GISPulseConfig` (`runtime/config_loader.py`), historique.
- Pipeline JSON `version: 2` — `PipelineSpec` (`core/pipeline.py`), DAG step-based.

Cette page décrit le calendrier de dépréciation, l'outil `gispulse migrate` et le mapping conceptuel pour les rares cas où une revue manuelle est utile.

## Calendrier de dépréciation

| Version | Statut v1 / v2 | Action requise |
|---|---|---|
| **1.10.0** | Stable | Aucune. v3 introduit en parallèle. |
| **1.10.1** | **Dépréciés** ⚠ | Migrer vers v3 via `gispulse migrate`. Le loader v1/v2 continue de fonctionner mais émet un warning. |
| **2.0.0** | **Supprimés** ❌ | Les loaders v1/v2 disparaissent. Tout fichier non migré refuse de charger. |

> **Décision ADR 0005** (Q-B) — trois chemins de code ne se maintiennent pas indéfiniment. L'écart entre l'introduction (v1.10.1) et la suppression (v2.0.0) laisse une release majeure pour migrer.

## `gispulse migrate` — l'outil de réécriture automatique

```bash
# v1 (flat list of rules) → v3, sortie sur stdout
gispulse migrate old_triggers.yaml

# v2 (PipelineSpec JSON) → v3, écrit dans un fichier YAML
gispulse migrate pipeline.json --output manifest.yaml

# v2 → v3, sortie JSON sur stdout (pour scripting)
gispulse migrate pipeline.json --format json
```

L'outil détecte automatiquement la version :
- **Liste JSON** → v1 (règles à plat, chaîne linéaire).
- **Objet `version: 2`** → v2 (PipelineSpec avec `steps[]`, `ref_layers{}`, `triggers[]`).
- **Objet `version: 3`** → passthrough (déjà v3).

Le manifeste v3 émis **est validé** contre `SCHEMA_V3` avant l'écriture. Un warning est émis si la sortie échoue la validation — quasi-toujours résolu par un ajustement manuel mineur.

## Mapping v2 → v3

```diff
- {                                            + version: 3
-   "version": 2,                              + name: demo
-   "name": "demo",                            + sources:
-   "ref_layers": {                            +   zones: { uri: ./zones.gpkg }
-     "zones": "./zones.gpkg"                  +   input: { uri: <primary_input> }
-   },                                         + models:
-   "steps": [                                 +   s1:
-     {                                        +     select: input
-       "id": "s1",                            +     transform:
-       "capability": "filter",                +       - filter: { expression: "pop > 100" }
-       "params": { "expression": "pop > 100" }+   s2:
-     },                                       +     select: s1
-     {                                        +     transform:
-       "id": "s2",                            +       - buffer: { distance: 50 }
-       "capability": "buffer",                +
-       "params": { "distance": 50 },          +
-       "input": "s1"                          +
-     }                                        +
-   ]                                          +
- }                                            +
```

Points clés :

1. **Chaque step devient un modèle**. L'`id` du step est la clé du modèle ; le `capability` + `params` deviennent un transform.
2. **Le chaînage `input: <step_id>` devient `select: <model_name>`** — sémantique équivalente.
3. **Les `ref_layers` deviennent des `sources`** sous leur alias d'origine.
4. **Un source pseudo `input: { uri: <primary_input> }`** est ajouté pour rendre le manifeste structurellement autosuffisant — c'est le placeholder du primary input que v2 ne déclarait pas.
5. **Les triggers v2** se transcrivent quasi-littéralement sous la section `triggers:` v3 (mêmes champs `on`, `when`, `actions`).

## Mapping v1 → v3

La liste plate v1 devient une chaîne linéaire de modèles, chacun pointant vers le précédent :

```diff
- [                                            + version: 3
-   {                                          + sources:
-     "name": "a",                             +   input: { uri: <primary_input> }
-     "capability": "filter",                  + models:
-     "config": { "expression": "x > 1" }      +   a:
-   },                                         +     select: input
-   {                                          +     transform:
-     "name": "b",                             +       - filter: { expression: "x > 1" }
-     "capability": "buffer",                  +   b:
-     "config": { "distance": 10 }             +     select: a
-   }                                          +     transform:
- ]                                            +       - buffer: { distance: 10 }
```

## Points d'attention lors de la migration

### 1. La référence `input`

Le manifeste v1/v2 n'avait pas de notion de source nommée — le primary input était passé au runtime. `gispulse migrate` introduit un source placeholder `input: { uri: <primary_input> }`. **Remplacez-le par la vraie URI** (`./parcelles.gpkg`, `s3://…`, etc.) avant d'utiliser le manifeste en production.

### 2. Les paramètres `ref_layer` dans les capabilities

Les capabilities qui prennent une référence (`spatial_join`, `attribute_join`, `clip`, `intersects`, …) utilisent en v2 un paramètre `ref_layer: <alias>`. En v3 la convention canonique est `with: <ref>` à l'intérieur du transform :

```yaml
# v2 idiom (toujours supporté en v3 — backward-compatible)
- spatial_join: { ref_layer: zones, predicate: intersects }

# v3 idiom (recommandé)
- spatial_join: { with: zones, predicate: intersects }
```

`with:` est résolu par le compilateur en `ref_layer` côté params + un edge multi-input si la référence pointe vers un autre modèle.

### 3. Les `triggers:` inline restent réactifs

Le bloc `triggers:` v3 conserve la sémantique de `docs-site/guide/rules.md` — DML / schedule / manual + actions. La compilation `gispulse migrate` les conserve tels quels.

### 4. `materialize:` et `refresh:` ne sont pas inférés

`gispulse migrate` produit des modèles sans `materialize:` / `refresh:` — le défaut `view` / `manual` s'applique. Si vous voulez `table` / `incremental` ou `on_change`, ajoutez-les après migration en fonction de votre cas d'usage (voir le [guide manifeste](./elt-manifest.md#materialize-modes-de-matérialisation)).

### 5. Le bloc `assert:` n'est pas inféré non plus

V1/v2 n'avaient pas de data-quality gates déclaratifs — `gispulse migrate` ne devine pas vos invariants. Ajoutez les assertions (`not_null`, `unique`, `geometry_valid`, `expect_rows`) modèle par modèle après la migration ; c'est un investissement minimal qui paye au premier modèle aval qui consomme une sortie corrompue. Voir la [section `assert:` du guide](./elt-manifest.md#assert--data-quality-gates).

## Tester un manifeste migré

Une migration propre tient en trois commandes :

```bash
# 1. Réécrire
gispulse migrate old_pipeline.json --output manifest.yaml

# 2. Inspecter — voir le DAG et le dispatch ELT/ETL prédits
gispulse explain manifest.yaml

# 3. Exécuter (dry-run du loader inclus)
gispulse run manifest.yaml --dry-run
```

`gispulse explain` est particulièrement utile post-migration : il montre quelles capabilities vont rester en Python (flag `⚠ ETL-strict`) et lesquelles vont pousser en SQL — ce que le format v2 ne rendait pas visible.

## API de migration en Python

```python
from gispulse.core.manifest_v3 import migrate_to_v3
import json, yaml

# Charger le v1 ou v2
raw = json.load(open("pipeline.json"))

# Convertir
v3 = migrate_to_v3(raw)

# Écrire en YAML
with open("manifest.yaml", "w") as fp:
    yaml.safe_dump(v3, fp, sort_keys=False, allow_unicode=True)
```

`migrate_to_v3` accepte une liste (v1) ou un dict (v2 / v3). Pour un dict v3, il fait passthrough.

## Voir aussi

- [Manifeste v3 — référence complète](./elt-manifest.md).
- [ADR 0005 — Unified GISPulse manifest](https://github.com/imagodata/gispulse/blob/main/docs/adr/0005-unified-manifest.md) — la spec.
- [`gispulse explain`](./elt-manifest.md#gispulse-explain--inspecter-le-dag-avant-de-courir) — inspecter le manifeste avant exécution.
