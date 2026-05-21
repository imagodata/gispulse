# Serveur MCP

GISPulse expose son moteur sous forme d'un **serveur MCP (Model Context
Protocol)** depuis `1.8.0` (EPIC
[#206](https://github.com/imagodata/gispulse/issues/206)). Les clients
compatibles (Claude Desktop, l'extension VS Code Claude Code, et tout
autre client MCP) peuvent appeler les capabilities, parcourir le
catalogue, et **évaluer un `triggers.yaml` en dry-run** sans déclencher
le moindre effet de bord — utile pour piloter un agent qui propose des
règles avant de les exécuter.

L'adaptateur MCP est une façade fine au-dessus de `GISPulseApp` (chantier
B des Foundations) : il **ne maintient aucun état** de son côté ; tout
chemin de fichier reçu est borné par le **workdir MCP**
([§ FS scoping](#filesystem-scoping)).

## Démarrer le serveur

Le binaire `gispulse` expose une sous-commande dédiée :

```bash
# stdio (mode par défaut, à câbler dans une config client MCP)
gispulse mcp

# Servir sur HTTP/SSE (utile en dev + pour des clients HTTP)
gispulse mcp --transport sse --host 127.0.0.1 --port 8765
```

> `gispulse mcp --dry-run` n'existe pas comme drapeau global : le
> *dry-run* est une **propriété de l'outil** `dryrun_trigger` (voir
> ci-dessous). Tous les outils non préfixés `dryrun_*` sont par ailleurs
> en lecture seule (browse catalog, inspect dataset, etc.).

## Configuration côté client

### Claude Desktop / Claude Code

`~/.config/claude/mcp.json` (ou équivalent macOS / Windows) :

```json
{
  "mcpServers": {
    "gispulse": {
      "command": "gispulse",
      "args": ["mcp"],
      "env": {
        "GISPULSE_MCP_WORKDIR": "/home/me/projects/my-data"
      }
    }
  }
}
```

### Autres clients MCP

Tout client respectant la spec MCP `2024-11-05+` peut se connecter au
binaire en mode stdio. Pour SSE/HTTP, viser
`http://127.0.0.1:8765/sse`.

## Filesystem scoping

Issue [#204](https://github.com/imagodata/gispulse/issues/204).

Un serveur MCP est piloté par un **LLM non fiable**. Un appel brut
`open(path)` serait un *sink* de traversée de chemin : un prompt
injecté pourrait lire `/etc/passwd` ou exfiltrer n'importe quel
GeoPackage sur la machine. Chaque outil qui accepte un argument `path`
le passe par
[`gispulse.adapters.mcp.workdir.resolve_in_workdir`](https://github.com/imagodata/gispulse/blob/main/src/gispulse/adapters/mcp/workdir.py),
qui borne la lecture au **workdir MCP** :

```
GISPULSE_MCP_WORKDIR  ← si définie (avec ~ expansé)
        sinon
process cwd           ← cwd du processus serveur
```

Un chemin qui s'évade du workdir est refusé avec
`{"error": "path outside MCP workdir: ..."}`. Le check réutilise
`_check_within_anchors` — la même garde déjà utilisée par `gispulse
triggers`. Le scoping MCP est **plus strict** que celui de la CLI (qui
accepte aussi `$HOME` et `tempfile.gettempdir()`) : l'agent obtient
**une seule racine explicite**.

## Outils disponibles

17 outils sont actuellement enregistrés par
`register_builtin_mcp_surface`. Tout outil d'origine plugin (entry-point
`gispulse.mcp_tools`, issue
[#205](https://github.com/imagodata/gispulse/issues/205)) est chargé
en plus via `register_plugin_mcp_surface`.

### Capabilities

| Outil                   | Signature                              | Effet                                |
|-------------------------|----------------------------------------|--------------------------------------|
| `list_capabilities`     | `() -> list[dict]`                     | Inventaire des capabilities + schémas |
| `get_capability_info`   | `(name: str) -> dict`                  | Détail d'une capability ciblée        |

### Catalogue

| Outil               | Signature                                                                | Effet                                                                |
|---------------------|--------------------------------------------------------------------------|----------------------------------------------------------------------|
| `browse_catalog`    | `(domain=None, search=None, provider=None, limit=25) -> list[dict]`       | Recherche dans le catalogue unifié (projection/basemap/flux/opendata) |
| `get_catalog_entry` | `(entry_id: str) -> dict`                                                | Détail d'une entrée catalogue                                         |

### Templates

| Outil             | Signature                  | Effet                                            |
|-------------------|----------------------------|--------------------------------------------------|
| `list_templates`  | `() -> list[dict]`         | Liste les templates de pipeline embarqués        |
| `get_template`    | `(name: str) -> dict`      | Renvoie le JSON brut d'un template (sans `.json`)|

### Datasets / pipelines (lecture seule, workdir-scopé)

| Outil               | Signature                          | Effet                                          |
|---------------------|------------------------------------|------------------------------------------------|
| `inspect_dataset`   | `(path: str) -> dict`              | Liste les couches d'un GeoPackage              |
| `validate_pipeline` | `(path: str) -> dict`              | Valide un fichier pipeline JSON sans l'exécuter |

### Triggers / change-log (workdir-scopé ; `dryrun_trigger` sans effet)

| Outil                | Signature                                              | Effet                                                                 |
|----------------------|--------------------------------------------------------|-----------------------------------------------------------------------|
| `load_triggers`      | `(path: str) -> dict`                                  | Résumé structurel d'un `triggers.yaml`                                 |
| `list_triggers`      | `(path: str) -> dict`                                  | Liste détaillée des triggers d'une config                              |
| `validate_triggers`  | `(path: str, gpkg: str \| None = None) -> dict`         | Validation structurelle vs le GPKG                                     |
| `inspect_changelog`  | `(gpkg: str, limit: int = 50) -> dict`                  | Statut du `_gispulse_change_log` + N dernières lignes                   |
| `watch_status`       | `(gpkg: str) -> dict`                                  | Couches suivies + compteur de changements en attente                   |
| `dryrun_trigger`     | `(path: str, gpkg: str \| None = None) -> dict`         | **Évaluation sans effet** d'une config trigger (aucun webhook envoyé)  |

### Plugins / sources

| Outil                          | Signature                  | Effet                                                                       |
|--------------------------------|----------------------------|-----------------------------------------------------------------------------|
| `list_plugins`                 | `() -> list[dict]`         | Inventaire `ExtensionHub` (records, états)                                  |
| `list_sources`                 | `() -> list[dict]`         | Sources ETL enregistrées                                                    |
| `refresh_worldwide_catalog`    | `() -> dict`               | Sonde de fraîcheur data.gouv.fr pour les entrées FR de l'agrégateur worldwide |

## Ressources MCP

Cinq ressources sont enregistrées au démarrage, dont deux paramétrées
(template URI `{path}`). Toutes restent **workdir-scopées** :

| URI                                | Contenu                                                  |
|------------------------------------|----------------------------------------------------------|
| `gispulse://capabilities`          | Liste JSON des capabilities                              |
| `gispulse://templates`             | Liste JSON des templates                                  |
| `gispulse://sources`               | Liste JSON des sources ETL                                |
| `gispulse://triggers/{path}`       | Résumé JSON d'un `triggers.yaml`                          |
| `gispulse://changelog/{path}`      | Statut JSON du change-log d'un GPKG                       |

## Exposer ses propres outils MCP

Issue [#205](https://github.com/imagodata/gispulse/issues/205). Un plugin
inscrit un *factory* via le entry-point `gispulse.mcp_tools` :

```toml
# pyproject.toml du plugin
[project.entry-points."gispulse.mcp_tools"]
my_tools = "my_pkg.my_module:MyToolsFactory"
```

```python
# my_pkg/my_module.py
class MyToolsFactory:
    name = "my-tools"

    def register(self, server) -> None:
        @server.tool()
        def my_custom_tool(arg: str) -> dict:
            """Décrire l'outil — la docstring est lue par MCP."""
            return {"echo": arg}
```

L'`ExtensionHub` charge les factories au démarrage du serveur (cf.
[ExtensionHub](./extension-hub#régime-code-plugin)).

## Exemples agentiques

### « Audit foncier » (Claude Desktop)

Le client appelle, dans une seule conversation :

1. `browse_catalog(domain="opendata", search="DVF")` — trouver le
   dataset DVF,
2. `inspect_dataset("data/foncier.gpkg")` — vérifier les couches du
   GPKG local,
3. `list_triggers("configs/audit.yaml")` — résumer les règles
   existantes,
4. `dryrun_trigger("configs/audit.yaml", "data/foncier.gpkg")` —
   simuler une exécution sans envoyer le webhook configuré,
5. proposer un diff de la config à l'utilisateur.

Aucune écriture, aucun appel sortant.

### « Veille catalogue » (cron + MCP HTTP)

Une tâche planifiée appelle `refresh_worldwide_catalog()` sur le port
SSE, parse la réponse, et crée une issue GitHub quand un dataset FR
publié a un `last_modified` plus récent que le `revision_token` du
catalogue.

## Voir aussi

- [ExtensionHub](./extension-hub) — comment charger des outils MCP tiers.
- [Architecture](./architecture) — le rôle de `GISPulseApp`.
- [CLI ↔ portail](./symmetry) — pourquoi le dry-run MCP doit produire le
  même résultat que `gispulse triggers run --dry-run`.

## Références code

- [`gispulse.adapters.mcp.server`](https://github.com/imagodata/gispulse/blob/main/src/gispulse/adapters/mcp/server.py)
- [`gispulse.adapters.mcp.workdir`](https://github.com/imagodata/gispulse/blob/main/src/gispulse/adapters/mcp/workdir.py)
- EPIC [#206](https://github.com/imagodata/gispulse/issues/206), issues
  [#202](https://github.com/imagodata/gispulse/issues/202)–
  [#205](https://github.com/imagodata/gispulse/issues/205)
