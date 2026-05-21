# MCP server

GISPulse exposes its engine as an **MCP (Model Context Protocol)
server** since `1.8.0` (EPIC
[#206](https://github.com/imagodata/gispulse/issues/206)). Compatible
clients (Claude Desktop, the VS Code Claude Code extension, and any
other MCP client) can call capabilities, browse the catalog, and
**evaluate a `triggers.yaml` in dry-run** without triggering any side
effect — useful for driving an agent that proposes rules before
executing them.

The MCP adapter is a thin façade on top of `GISPulseApp` (chantier B of
the Foundations work): it **holds no state** of its own; every file
path it receives is bounded by the **MCP workdir** ([§ FS
scoping](#filesystem-scoping)).

## Starting the server

The `gispulse` binary has a dedicated sub-command:

```bash
# stdio (default — wire into an MCP client config)
gispulse mcp

# Serve over HTTP/SSE (handy in dev + for HTTP clients)
gispulse mcp --transport sse --host 127.0.0.1 --port 8765
```

> `gispulse mcp --dry-run` does **not** exist as a global flag: dry-run
> is a **per-tool property** of `dryrun_trigger` (see below). Every
> non-`dryrun_*` tool is otherwise read-only anyway (browse catalog,
> inspect dataset, etc.).

## Client-side configuration

### Claude Desktop / Claude Code

`~/.config/claude/mcp.json` (or macOS / Windows equivalent):

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

### Other MCP clients

Any client complying with MCP spec `2024-11-05+` can connect to the
binary over stdio. For SSE/HTTP, target
`http://127.0.0.1:8765/sse`.

## Filesystem scoping

Issue [#204](https://github.com/imagodata/gispulse/issues/204).

An MCP server is driven by an **untrusted LLM**. A raw `open(path)`
call would be a path-traversal sink: a prompt-injected agent could read
`/etc/passwd` or exfiltrate any GeoPackage on the host. Every tool that
accepts a `path` argument routes it through
[`gispulse.adapters.mcp.workdir.resolve_in_workdir`](https://github.com/imagodata/gispulse/blob/main/src/gispulse/adapters/mcp/workdir.py),
which bounds the read to the **MCP workdir**:

```
GISPULSE_MCP_WORKDIR  ← when set (~ is expanded)
        otherwise
process cwd           ← cwd of the server process
```

A path that escapes the workdir is refused with
`{"error": "path outside MCP workdir: ..."}`. The check reuses
`_check_within_anchors` — the same guard already trusted by `gispulse
triggers`. MCP scoping is **stricter** than the CLI one (which also
accepts `$HOME` and `tempfile.gettempdir()`): the agent gets **a single
explicit root**.

## Available tools

17 tools are currently registered by
`register_builtin_mcp_surface`. Any tool sourced from a plugin
(`gispulse.mcp_tools` entry-point, issue
[#205](https://github.com/imagodata/gispulse/issues/205)) is loaded on
top by `register_plugin_mcp_surface`.

### Capabilities

| Tool                    | Signature                              | Effect                              |
|-------------------------|----------------------------------------|-------------------------------------|
| `list_capabilities`     | `() -> list[dict]`                     | Capability inventory + schemas      |
| `get_capability_info`   | `(name: str) -> dict`                  | Detail of a single capability       |

### Catalog

| Tool                | Signature                                                                | Effect                                                              |
|---------------------|--------------------------------------------------------------------------|---------------------------------------------------------------------|
| `browse_catalog`    | `(domain=None, search=None, provider=None, limit=25) -> list[dict]`       | Search the unified catalog (projection/basemap/flux/opendata)        |
| `get_catalog_entry` | `(entry_id: str) -> dict`                                                | Detail of a single catalog entry                                     |

### Templates

| Tool              | Signature                  | Effect                                            |
|-------------------|----------------------------|---------------------------------------------------|
| `list_templates`  | `() -> list[dict]`         | List the built-in pipeline templates              |
| `get_template`    | `(name: str) -> dict`      | Return the raw JSON of a template (no `.json`)    |

### Datasets / pipelines (read-only, workdir-scoped)

| Tool                | Signature                          | Effect                                          |
|---------------------|------------------------------------|-------------------------------------------------|
| `inspect_dataset`   | `(path: str) -> dict`              | List GeoPackage layers                          |
| `validate_pipeline` | `(path: str) -> dict`              | Schema-validate a pipeline JSON file            |

### Triggers / change-log (workdir-scoped; `dryrun_trigger` is side-effect-free)

| Tool                 | Signature                                              | Effect                                                                 |
|----------------------|--------------------------------------------------------|------------------------------------------------------------------------|
| `load_triggers`      | `(path: str) -> dict`                                  | Structural summary of a `triggers.yaml`                                 |
| `list_triggers`      | `(path: str) -> dict`                                  | Detailed trigger list of a config                                       |
| `validate_triggers`  | `(path: str, gpkg: str \| None = None) -> dict`         | Structural validation against the GPKG                                  |
| `inspect_changelog`  | `(gpkg: str, limit: int = 50) -> dict`                  | `_gispulse_change_log` status + last N rows                              |
| `watch_status`       | `(gpkg: str) -> dict`                                  | Tracked layers + pending change-log count                                |
| `dryrun_trigger`     | `(path: str, gpkg: str \| None = None) -> dict`         | **Side-effect-free** evaluation of a trigger config (no webhook fired)   |

### Plugins / sources

| Tool                          | Signature                  | Effect                                                                       |
|-------------------------------|----------------------------|------------------------------------------------------------------------------|
| `list_plugins`                | `() -> list[dict]`         | `ExtensionHub` inventory (records, states)                                   |
| `list_sources`                | `() -> list[dict]`         | Registered ETL sources                                                       |
| `refresh_worldwide_catalog`   | `() -> dict`               | data.gouv.fr freshness probe for the worldwide aggregator's FR entries        |

## MCP resources

Five resources are registered at startup, including two parameterised
ones (URI template `{path}`). All remain **workdir-scoped**:

| URI                                | Content                                                  |
|------------------------------------|----------------------------------------------------------|
| `gispulse://capabilities`          | JSON list of capabilities                                |
| `gispulse://templates`             | JSON list of templates                                   |
| `gispulse://sources`               | JSON list of ETL sources                                 |
| `gispulse://triggers/{path}`       | JSON summary of a `triggers.yaml`                        |
| `gispulse://changelog/{path}`      | JSON change-log status of a GPKG                         |

## Contributing MCP tools

Issue [#205](https://github.com/imagodata/gispulse/issues/205). A plugin
registers a factory through the `gispulse.mcp_tools` entry-point:

```toml
# plugin pyproject.toml
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
            """Describe the tool — the docstring is exposed via MCP."""
            return {"echo": arg}
```

`ExtensionHub` loads the factories at server startup (see
[ExtensionHub](./extension-hub#code-plugin-regime)).

## Agentic examples

### “Real-estate audit” (Claude Desktop)

The client calls, within a single conversation:

1. `browse_catalog(domain="opendata", search="DVF")` — find the DVF
   dataset,
2. `inspect_dataset("data/foncier.gpkg")` — verify the layers of the
   local GPKG,
3. `list_triggers("configs/audit.yaml")` — summarise the existing
   rules,
4. `dryrun_trigger("configs/audit.yaml", "data/foncier.gpkg")` —
   simulate an execution without firing the configured webhook,
5. propose a config diff to the user.

No writes, no outbound calls.

### “Catalog watch” (cron + MCP HTTP)

A scheduled task calls `refresh_worldwide_catalog()` over SSE, parses
the response, and opens a GitHub issue when a published FR dataset has
a `last_modified` newer than the catalog's `revision_token`.

## See also

- [ExtensionHub](./extension-hub) — how third-party MCP tools are loaded.
- [Architecture](./architecture) — the role of `GISPulseApp`.
- [CLI ↔ portal](./symmetry) — why the MCP dry-run must produce the
  same result as `gispulse triggers run --dry-run`.

## Code references

- [`gispulse.adapters.mcp.server`](https://github.com/imagodata/gispulse/blob/main/src/gispulse/adapters/mcp/server.py)
- [`gispulse.adapters.mcp.workdir`](https://github.com/imagodata/gispulse/blob/main/src/gispulse/adapters/mcp/workdir.py)
- EPIC [#206](https://github.com/imagodata/gispulse/issues/206), issues
  [#202](https://github.com/imagodata/gispulse/issues/202)–
  [#205](https://github.com/imagodata/gispulse/issues/205)
