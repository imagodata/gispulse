---
title: Contribuer des outils MCP
description: Écrire un plugin GISPulse qui ajoute des tools et resources au serveur MCP via l'entry-point gispulse.mcp_tools.
---

# Contribuer des outils MCP

Le serveur MCP de GISPulse (`gispulse mcp`) expose les use-cases GISPulse à
un assistant LLM via le protocole [Model Context Protocol](https://modelcontextprotocol.io).
Un plugin peut **ajouter ses propres tools et resources** à ce serveur sans
toucher au code de GISPulse, via les entry-points `gispulse.mcp_tools` et
`gispulse.mcp_resources`.

C'est le même mécanisme de découverte que les capabilities et les data
sources : l'`ExtensionHub` scanne les entry-points installés, et le serveur
MCP appelle chaque plugin enregistré au démarrage.

## Le contrat

Un entry-point `gispulse.mcp_tools` doit résoudre vers un objet conforme au
protocole `McpToolFactory` (voir `gispulse.core.plugin_contracts`) :

| Membre              | Type   | Rôle                                              |
| ------------------- | ------ | ------------------------------------------------- |
| `name`              | `str`  | Identifiant du plugin (inventaire `ExtensionHub`, logs). |
| `register(self, mcp)` | méthode | Appelée une fois par serveur ; attache les tools. |

L'`ExtensionHub` instancie la classe (constructeur **sans argument**), puis
`gispulse.adapters.mcp.server.register_plugin_mcp_surface` appelle
`register(server)` sur le serveur FastMCP en cours d'exécution. Un plugin qui
lève une exception pendant `register` est loggé puis ignoré — il ne fait
**jamais** tomber le serveur.

`gispulse.mcp_resources` suit exactement le même contrat (`McpResourceFactory`)
pour contribuer des *resources* au lieu de *tools*.

## Exemple minimal

Le plugin pilote first-party `gispulse.plugins.mcp_pilot` est la référence
exécutable de ce guide :

```python
# mon_plugin/mcp_tools.py
from typing import Any


class MesOutilsMcp:
    """McpToolFactory — contribue un tool au serveur MCP GISPulse."""

    name = "mon-plugin-mcp"

    def register(self, mcp: Any) -> None:
        @mcp.tool()
        def coverage_ftth(commune: str) -> dict[str, Any]:
            """Retourne le taux de couverture FTTH d'une commune.

            Args:
                commune: Code INSEE de la commune.

            Returns:
                Dict avec le taux de couverture.
            """
            # Imports lourds DANS le corps du tool, pas au niveau module.
            from mon_plugin.connecteur import interroger_couverture

            return interroger_couverture(commune)
```

### Déclarer l'entry-point

Dans le `pyproject.toml` du plugin :

```toml
[project.entry-points."gispulse.mcp_tools"]
mon-plugin-mcp = "mon_plugin.mcp_tools:MesOutilsMcp"
```

Après `pip install -e .` (ou l'installation du wheel), l'`ExtensionHub`
découvre l'entry-point automatiquement. Vérification :

```bash
python -c "from importlib.metadata import entry_points; \
  print([e.name for e in entry_points(group='gispulse.mcp_tools')])"
```

## Règles d'écriture

- **Imports légers.** Le module du plugin est importé pendant la découverte
  de l'`ExtensionHub`. Les imports lourds (`geopandas`, `requests`,
  bindings GDAL…) vont **dans le corps du tool**, jamais au niveau module —
  sinon un simple `gispulse mcp` paie le coût d'import de toute la chaîne.

- **Docstrings = schéma.** FastMCP dérive le schéma JSON du tool depuis la
  signature et la docstring. Annotez chaque argument et décrivez la valeur
  de retour : c'est ce que le LLM lit pour décider d'appeler le tool.

- **Renvoyez des dicts JSON-sérialisables.** Pas d'objets `GeoDataFrame`,
  pas de `dataclass` non sérialisable. En cas d'erreur attendue, renvoyez
  `{"error": "..."}` plutôt que de lever — le serveur built-in suit cette
  convention.

- **Scopez les accès fichiers.** Si votre tool prend un chemin, bornez la
  lecture comme le fait le serveur built-in (#204) via
  `gispulse.adapters.mcp.workdir.resolve_in_workdir` :

  ```python
  from gispulse.adapters.mcp.workdir import WorkdirError, resolve_in_workdir

  @mcp.tool()
  def inspecter(path: str) -> dict:
      try:
          chemin = resolve_in_workdir(path)
      except WorkdirError as exc:
          return {"error": str(exc)}
      ...
  ```

  Un serveur MCP est piloté par un LLM non fiable : un `open(path)` non borné
  est une faille de path traversal.

- **Passez par `GISPulseApp`.** Si votre tool a besoin d'un use-case GISPulse
  (catalogue, runtime de triggers, capabilities…), appelez
  `gispulse.app.get_app()` plutôt que de re-câbler le moteur. Le serveur MCP
  est un adaptateur *thin* — votre plugin aussi.

## Tester un plugin MCP

L'`ExtensionHub` est un singleton ; appelez `ExtensionHub.reset()` entre les
tests. On peut injecter un faux entry-point en monkeypatchant
`gispulse.core.plugin_hub.entry_points` :

```python
def test_mon_tool_enregistre(monkeypatch):
    import asyncio
    from gispulse.adapters.mcp import server as mcp_server
    from gispulse.core import plugin_hub

    plugin_hub.ExtensionHub.reset()
    server = mcp_server.create_mcp_server()  # scanne les entry-points réels
    tools = asyncio.run(server.list_tools())
    assert "coverage_ftth" in {t.name for t in tools}
    plugin_hub.ExtensionHub.reset()
```

Le plugin pilote `gispulse-mcp-pilot` est testé dans
`tests/unit/test_mcp_pilot_plugin.py` — un bon point de départ à copier.

## Voir aussi

- `gispulse.plugins.mcp_pilot` — implémentation pilote first-party.
- [Développer un plugin / capability](./developing.md) — capabilities et data sources.
- `gispulse.core.plugin_contracts` — protocoles `McpToolFactory` / `McpResourceFactory`.
