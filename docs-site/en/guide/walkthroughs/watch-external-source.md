---
title: Walkthrough — Watch an external source
description: Fire an action when a remote data source (the IGN cadastre) publishes a new revision. No local edit, no GIS plugin. Added in v1.7.0.
---

# Walkthrough — Watch an external source

> **The promise**: the IGN publishes a new cadastre vintage → GISPulse detects it on the next poll → your action fires (webhook, log, SQL…). You edit nothing, you open no GIS.

Through v1.6.x a GISPulse trigger reacted to a **local edit** (DML on a GeoPackage, a file diff). v1.7.0 adds a second mode: the `source_changed` trigger, which reacts to the **freshness of a remote source**. It is the "Extract" brick of the ETL platform (EPIC #175).

## What you will see

A trigger watching the **Parcellaire Express cadastre** (IGN). On every new vintage published, GISPulse emits an event and POSTs a webhook — without ever downloading the parcels until you ask for it.

| Before | After a new revision |
|---|---|
| `gispulse watch` running, polling `revision()` at the `frequency` cadence | The revision token changes → `source.changed` event → webhook POST |

## Prerequisites

- `gispulse` ≥ 1.7.0 (`pipx install gispulse`)
- The **`gispulse-src-cadastre`** source plugin: `pip install gispulse-src-cadastre`. It registers the `cadastre://` source queried below. To write your own, see the [source authoring guide](https://github.com/imagodata/gispulse/blob/main/docs/SOURCE_PLUGIN_GUIDE.md).
- An HTTP endpoint to receive the webhook ([webhook.site](https://webhook.site/) for a quick test).

## Setup (≈ 1 minute)

### 1. Confirm the source is discovered

```bash
gispulse marketplace list --kind source
```

`cadastre` must show up as `active`. If it is `locked`, it is a higher-tier plugin; `failed` means a dependency is missing (`gispulse doctor`).

### 2. Write the rules

```yaml
# triggers.yaml
version: 1
gpkg: ./project.gpkg          # required by the v1 schema, even if unused here

triggers:
  - name: refresh_on_new_cadastre
    on:
      source_changed: cadastre://parcelles
      frequency: mensuel       # revision() poll cadence
    actions:
      - type: log_event
      - type: webhook
        url: https://webhook.site/YOUR-UNIQUE-ID

security:
  webhook_allowlist:
    - webhook.site
```

> A `source_changed` trigger declares **no `table`, no `when`, no `predicate`** — it watches a source, not a local layer. The v1 schema still requires a `gpkg:` key (the project database): point it at your project's GeoPackage.

The full, commented example ships in the repo: [`examples/triggers/source_changed_cadastre.yaml`](https://github.com/imagodata/gispulse/blob/main/examples/triggers/source_changed_cadastre.yaml).

### 3. Start the watch loop

```bash
gispulse watch ./project.gpkg --rules triggers.yaml
```

The terminal shows:

```text
[info] source watcher: 1 source trigger wired (cadastre://parcelles, every 24h)
[info] watching… (Ctrl+C to stop)
```

On the first tick the watcher reads the current revision and stores it as the **baseline** — it does not fire. Firings come afterwards, on every token change.

## Testing without waiting for a real vintage

A real cadastre vintage ships once a month — too slow for a demo. Three ways to exercise the loop:

- **Shorten the cadence**: `frequency: temps-reel` polls every 5 minutes.
- **A test source**: write a tiny `gispulse-src-*` whose `revision()` returns the current time — it "changes" on every poll. See the [authoring guide](https://github.com/imagodata/gispulse/blob/main/docs/SOURCE_PLUGIN_GUIDE.md).
- **Inspect without running**: `gispulse triggers validate --config triggers.yaml` confirms the trigger and its source URI are valid.

When the revision changes, the webhook receives:

```json
{
  "event": "source.changed",
  "source": "cadastre://parcelles",
  "revision": "\"a1b2c3-2026-02\"",
  "previous_revision": "\"a1b2c3-2026-01\"",
  "ts": "2026-05-17T09:00:00Z"
}
```

## How it works under the hood

```
gispulse watch
      │
      ▼
SourceWatcherRegistry  ← one _WatchEntry per source_changed trigger
      │  every `frequency` seconds
      ▼
DataSource.revision(entry_id)   ← cheap probe (HTTP HEAD, ETag/vintage token)
      │
      ▼
token differs from the last seen?
      │ yes
      ▼
broadcast("source.changed")  →  TriggerEvaluator  →  ActionDispatcher
```

**A poll, not a download.** `revision()` is deliberately cheap — for the cadastre it is an `HTTP HEAD` against the WFS `GetCapabilities`, reading its `ETag` / `Last-Modified` header. No parcel is pulled until an action explicitly asks for it (a `fetch()` — see the [ETL guide](https://github.com/imagodata/gispulse/blob/main/docs/SOURCE_PLUGIN_GUIDE.md)).

**In-memory baseline.** The last-seen token lives in the `gispulse watch` process. On restart, the first tick re-seeds the baseline — no phantom firing, but a vintage published while the process was down goes unnoticed. Persisting the token is tracked for a later release.

## Honest limitations

- **The baseline token is not persisted** — a change that happened while `gispulse watch` was stopped is not caught up.
- **`revision()` may return `None`** — endpoint unreachable, or no freshness header. The watcher treats "unknown" as "unchanged" and does not fire (no false positive).
- **`frequency` is a cadence, not a guarantee** — `mensuel` polls every 24 h; detection latency is at most one interval.
- **No `predicate` on a source trigger** — it fires on any revision change. Filter in the action if needed.

## See also

- [Source authoring guide](https://github.com/imagodata/gispulse/blob/main/docs/SOURCE_PLUGIN_GUIDE.md) — write a `gispulse-src-*` package
- [Triggers Guide](https://github.com/imagodata/gispulse/blob/main/docs/TRIGGERS_GUIDE.md) — "Source-watched triggers" section
- [GeoJSON CDC walkthrough](./geojson-cdc.md) — the other mode: react to a local file edit
- [`examples/triggers/source_changed_cadastre.yaml`](https://github.com/imagodata/gispulse/blob/main/examples/triggers/source_changed_cadastre.yaml) — the runnable example
