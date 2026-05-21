# ExtensionHub

`ExtensionHub` est le **point d'entrée unique** pour étendre GISPulse —
qu'il s'agisse de plugins Python (capabilities, routers, sinks…) ou de
contenus déclaratifs (templates, basemaps, projections, zonages). Il a
été livré en `1.8.0` (chantier C des Foundations) et exposé tel quel
dans `2.0.0` ; `PluginHub` reste disponible comme alias deprecated
jusqu'en `2.1.0`.

## Pourquoi le rename ?

Le rename `PluginHub` → `ExtensionHub` n'est pas cosmétique. Il acte une
décision d'architecture : **l'extensibilité de GISPulse ne se réduit
plus aux plugins de code**. La v1.8.0 introduit explicitement un second
régime — les *data packs* — qui n'embarquent **aucun** code Python et se
chargent à partir d'un manifeste déclaratif. Garder le nom `PluginHub`
aurait laissé entendre que tout passe par un entry-point Python à
importer ; `ExtensionHub` reflète l'inventaire unifié des deux régimes.

```diff
- from core.plugin_hub import PluginHub
+ from gispulse.core.plugin_hub import ExtensionHub
```

L'alias `PluginHub = ExtensionHub` est défini dans le même module ;
aucun changement de code n'est nécessaire avant la `2.1.0` (voir la
[migration 2.0](../migration-2.0#what-changed-and-what-didn-t)).

## Les deux régimes en un coup d'œil

```
                        ┌─────────────────────────────────┐
                        │           ExtensionHub          │
                        │  (singleton, lazy, thread-safe) │
                        └────────────┬────────────────────┘
                                     │
              ┌──────────────────────┴───────────────────────┐
              │                                              │
        ╔════════════╗                              ╔══════════════╗
        ║  Régime    ║                              ║   Régime     ║
        ║   CODE     ║                              ║  DATA PACK   ║
        ╚════════════╝                              ╚══════════════╝
              │                                              │
   ┌──────────┴──────────┐                       ┌───────────┴────────────┐
   │ entry_points        │                       │ bundle OSS             │
   │ ─ gispulse.routers  │                       │ ─ templates/manifest.yml│
   │ ─ gispulse.capabili │                       │                        │
   │ ─ gispulse.sources  │                       │ entry_point            │
   │ ─ gispulse.sinks    │                       │ ─ gispulse.data_packs  │
   │ ─ gispulse.protocol │                       │                        │
   │ ─ gispulse.mcp_*    │                       │ folder                 │
   │ ─ gispulse.middlewa │                       │ ─ $GISPULSE_DATA_PACKS_DIR │
   │ ─ gispulse.licence  │                       │                        │
   │ ─ ...               │                       └────────────────────────┘
   └─────────────────────┘
   import + instanciation                       lecture YAML/JSON
   trust = first_party / verified                trust = idem
           / community                           signature Ed25519 facultative
```

| Régime         | Source                                        | Ce qui est chargé                            |
|----------------|-----------------------------------------------|----------------------------------------------|
| **Code**       | entry-points Python via `importlib.metadata`  | classes (capabilities, routers, middleware…) |
| **Data pack**  | manifestes YAML/JSON (bundle / PyPI / folder) | `DataPackManifest` purement déclaratif       |

Les deux régimes produisent des `PluginRecord` unifiés, classés par
`PluginKind` (`SOURCE` / `CAPABILITY` / `SINK` / `PROTOCOL` / `EXTENSION`
/ `DATA_PACK`). Le `tier`/`trust` est résolu pour les deux de la même
façon — un seul gate par activation, pas de chemin parallèle.

## Régime « code plugin »

Treize entry-point groups sont scannés. Les neuf premiers extensionnent
l'app FastAPI hôte (router, middleware, providers, lifecycle, MCP), les
quatre derniers étendent le graphe ETL :

| Entry-point group               | Rôle                                                           |
|---------------------------------|----------------------------------------------------------------|
| `gispulse.routers`              | Sous-routers FastAPI (admin, billing…)                         |
| `gispulse.middleware`           | Middlewares ASGI                                               |
| `gispulse.auth_provider`        | Providers d'authentification (`AuthProvider`)                  |
| `gispulse.billing_provider`     | Providers de facturation (`BillingProvider`)                   |
| `gispulse.licence_provider`     | Providers de licence (`LicenceProvider`)                       |
| `gispulse.connectors`           | Connecteurs de session (DuckDB / PostGIS / …)                  |
| `gispulse.lifecycle`            | Hooks de cycle de vie de l'app                                 |
| `gispulse.mcp_tools`            | Outils MCP supplémentaires (cf. [page MCP](./mcp))             |
| `gispulse.mcp_resources`        | Ressources MCP supplémentaires                                 |
| `gispulse.catalog_providers`    | Catalog providers (legacy ETL, promus en `DATA_PACK` v1.8)     |
| `gispulse.data_sources`         | Sources ETL (`DeclarativeSource`)                              |
| `gispulse.data_sinks`           | Sinks ETL                                                      |
| `gispulse.protocols`            | Adaptateurs de transport (fetch/write)                         |

### Exposer un capability

```toml
# pyproject.toml du paquet plugin
[project.entry-points."gispulse.capabilities"]
my_capability = "my_pkg.my_module:capability_factory"
```

Le moteur boucle sur le contrat (`Capability`, `DeclarativeSource`,
`AuthProvider`…) défini par `gispulse.core.plugin_contracts`. Le
`PROTOCOL_VERSION` (actuellement `"1.1"`) doit être déclaré côté plugin
via `requires_protocol = ">=1.0,<2.0"` — un mismatch déclenche un warn
mais ne bloque pas l'activation (issue #182).

### Trust et tier — gating commercial

Trois niveaux de confiance dérivés du registre marketplace
(`marketplace/registry.json`) :

| Trust          | Origine typique                                                            |
|----------------|----------------------------------------------------------------------------|
| `first_party`  | Distributions `gispulse` / `gispulse-enterprise`                            |
| `verified`     | Paquet PyPI tiers listé dans `marketplace/registry.json` avec un tier connu |
| `community`    | Paquet PyPI tiers inconnu du registre                                      |

La variable `GISPULSE_PLUGINS_ALLOW_UNVERIFIED` (défaut `true`)
contrôle si les plugins `community` peuvent s'activer ; sur un déploiement
sensible, la fixer à `false` rejette tout plugin hors marketplace.

Le tier (`community` / `pro` / `team` / `enterprise`) est récupéré depuis
le registre, croisé avec la licence active (résolue par
`LicenceProvider.current()`), et un plugin demandant un tier supérieur à
celui de l'org bascule en `PluginState.LOCKED`.

## Régime « data pack »

Détaillé sur la page [Data packs](./data-packs). Récapitulatif :

- déclaratif (`DataPackManifest`), zéro `import`,
- trois canaux : bundle OSS, entry-point `gispulse.data_packs`, dossier
  `GISPULSE_DATA_PACKS_DIR`,
- signature Ed25519 optionnelle pour les manifestes `EXTERNAL` (story
  G1a #271),
- contenus typés : `template-pack` / `source-catalog` / `basemap-pack` /
  `projection-pack` / `regulatory-zoning`.

## Cycle de vie d'un record

```
discovered  →  resolve  →  gate  →  activate
                              ↘  LOCKED  (tier non satisfait)
                              ↘  FAILED  (exception au load)
                              ↘  ACTIVE  (chargé et câblé)
```

Inspectable via `gispulse plugins list` (CLI) ou l'outil MCP
`list_plugins()` ([page MCP](./mcp)).

## Migration depuis `1.7.x`

| Avant 1.8.0                           | Après 1.8.0 / 2.0.0                                       |
|---------------------------------------|-----------------------------------------------------------|
| `from core.plugin_hub import PluginHub` | `from gispulse.core.plugin_hub import ExtensionHub`        |
| `PluginHub.get()`                     | `ExtensionHub.get()` (alias `PluginHub.get()` toujours OK) |
| Templates ajoutés via copie dans `templates/` | Manifeste `template-pack` (PyPI ou folder)         |
| Catalog providers via `gispulse.catalog_providers` | toujours scanné, promus en `DataPack` côté inventaire   |

Voir aussi la [migration 2.0](../migration-2.0).

## Références code

- [`gispulse.core.plugin_hub.ExtensionHub`](https://github.com/imagodata/gispulse/blob/main/src/gispulse/core/plugin_hub.py)
- [`gispulse.core.plugin_model`](https://github.com/imagodata/gispulse/blob/main/src/gispulse/core/plugin_model.py) — enums `PluginKind`, `Tier`, `Trust`, `PluginState`
- [`gispulse.core.plugin_contracts`](https://github.com/imagodata/gispulse/blob/main/src/gispulse/core/plugin_contracts.py) — `Capability`, `AuthProvider`, …
