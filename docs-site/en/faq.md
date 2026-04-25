---
title: FAQ
description: Frequently asked questions about GISPulse — the declarative geospatial engine.
---

# Frequently Asked Questions

## General

### What is GISPulse?

GISPulse is a declarative geospatial engine. You describe spatial processing pipelines as JSON rules, and GISPulse executes them on your data. It handles format conversion, CRS reprojection, spatial operations, and output generation — all without writing code.

Think of it as **what dbt is to data engineering, but for GIS**: reproducible, versionable, auditable spatial workflows.

### What does "rules-as-config" mean?

Instead of writing Python scripts or building visual workflows in a GUI, you define your spatial processing as a JSON file. Each rule specifies a capability (e.g., `buffer`, `spatial_join`, `filter`), its parameters, and its inputs. The engine reads the file and executes the pipeline.

This approach brings several advantages:
- **Reproducibility** — same input + same rules = same output, every time
- **Version control** — your entire pipeline is a JSON file under Git
- **Readability** — GIS analysts can understand and modify rules without knowing Python
- **Portability** — the same rules run on DuckDB locally or PostGIS in production

### How does GISPulse differ from FME?

| | GISPulse | FME |
|---|---|---|
| **Model** | Open source (AGPL-3.0) | Proprietary, 5–15k EUR/year |
| **Approach** | Declarative JSON rules | Visual GUI workbenches |
| **Portability** | Embedded DuckDB, zero install | Requires FME Desktop/Server |
| **API** | REST API + Python SDK included | Paid add-ons |
| **Version control** | Native JSON under Git | XML export, not Git-friendly |
| **Vendor lock-in** | None | High |

FME excels at complex visual ETL with hundreds of transformers. GISPulse targets teams that want to automate, version, and deploy spatial processing in modern CI/CD pipelines.

### How does GISPulse differ from QGIS Processing?

QGIS Processing is excellent for interactive GIS work but is not designed for headless, server-side, or API-driven execution. GISPulse complements QGIS: you can use the QGIS plugin to explore data interactively, then run the same rules headlessly in a pipeline via CLI or API.

### How does GISPulse differ from raw PostGIS?

PostGIS is the spatial engine that powers GISPulse in persistent mode. But instead of writing and maintaining raw SQL, you define rules in JSON. GISPulse generates the optimized SQL, handles CRS transformations, manages sessions, and provides a REST API on top. You get PostGIS power without PostGIS complexity.

---

## Formats & Data

### What formats does GISPulse support?

GISPulse supports 13+ spatial formats via PyOGRIO/GDAL:

- **Vector:** GPKG, GeoJSON, Shapefile, GeoParquet, FlatGeobuf, CSV+WKT, KML
- **Raster:** GeoTIFF (Pro)
- **Services:** WFS, OGC API Features, PostGIS
- **Tiles:** PMTiles, MVT

The I/O layer auto-detects formats. No prior conversion needed.

### Can I use GISPulse without PostGIS?

Yes. Portable mode uses an embedded DuckDB engine that requires zero installation. It works entirely in memory with local files. PostGIS is only needed for persistent mode (triggers, scheduling, multi-user).

### What is the dual engine architecture?

GISPulse abstracts the spatial engine behind an `ExecutionStrategy` interface:

- **DuckDB engine** — embedded, serverless, portable. Ideal for local processing, CI/CD, batch jobs.
- **PostGIS engine** — persistent, server-based. Ideal for real-time triggers, continuous pipelines, multi-user access.

The same JSON rules run on either engine. You choose the engine at execution time with `--engine duckdb` or `--engine postgis`.

---

## Production & Deployment

### Is GISPulse production-ready?

Yes. **GISPulse v1.1.1** is the current stable release: 117 capabilities, 3,600+ tests, multi-backend DuckDB / PostGIS engine, Prometheus metrics, RBAC, SSO (OIDC / SAML), audit logging and S3 storage. The CLI, REST API, Python SDK, QGIS plugin, ArcGIS add-in and Tauri desktop client are all shipped.

| Component | Status |
|-----------|--------|
| Engine (DuckDB / PostGIS / GPKG portable) | **Stable — v1.1.1** |
| CLI | **Stable** |
| REST API + Python SDK | **Stable** |
| 117 capabilities (vector, attributes, classification, stats, topology, 3D pointcloud, raster, network, PostGIS SQL) | **Stable** |
| QGIS plugin / ArcGIS add-in / Tauri desktop | **Stable** |
| Web Portal (single-user Community, multi-user Pro / Team / Enterprise) | **Stable** |
| Visual node editor | **Beta** |

### How do I deploy GISPulse?

Four deployment modes:

1. **pip install** — `pip install gispulse` for CLI and SDK usage
2. **Docker** — `docker-compose` with Caddy, Prometheus, Grafana
3. **Kubernetes** — Helm chart for scalable deployments
4. **Desktop** — Tauri app for standalone usage

### Can I migrate from FME?

There is no automated FME-to-GISPulse migration tool. However, most FME workbenches can be expressed as GISPulse rules:

1. Identify the transformers used in your FME workflow
2. Map each transformer to a GISPulse capability (buffer, spatial_join, filter, etc.)
3. Write the equivalent JSON rules file
4. Test with `gispulse run --engine duckdb`

For complex migrations, [contact us](mailto:contact@gispulse.dev) for assistance.

---

## Extensibility

### Can I create custom capabilities?

Yes (Enterprise tier). GISPulse uses a capability registry with auto-discovery. You implement a Python class following the capability interface, register it, and it becomes available in your rules. Custom capabilities benefit from the same engine abstraction, session management, and API exposure as built-in ones.

### Can I use GISPulse as a library?

Yes. The Python SDK provides programmatic access to all GISPulse features:

```python
from gispulse import GISPulse

gp = GISPulse()
result = gp.run("rules.json", engine="duckdb")
```

You can also use individual capabilities directly, build custom pipelines, or integrate GISPulse into existing Python applications.

---

## Security & Licensing

### How is data security handled?

- **Portable mode:** all data stays local. No network calls, no cloud dependencies.
- **Persistent mode:** data lives in your PostGIS instance. GISPulse never transmits data externally.
- **Enterprise:** SSO (SAML/OIDC), RBAC, audit logging, encrypted connections, on-premise deployment.

### What does the AGPL-3.0 license mean for me?

If you use GISPulse internally (no distribution), AGPL-3.0 imposes no obligations. If you modify GISPulse and distribute it or offer it as SaaS, you must publish your modifications under the same license. Pro/Team/Enterprise tiers include a commercial license that removes this obligation.

---

## Support

### Where can I get help?

- **Community:** [GitHub Discussions](https://github.com/imagodata/gispulse/discussions) for questions and ideas
- **Issues:** [GitHub Issues](https://github.com/imagodata/gispulse/issues) for bug reports
- **Pro support:** email support included with Pro tier
- **Enterprise support:** dedicated support with 4h SLA
- **Contact:** [contact@gispulse.dev](mailto:contact@gispulse.dev)
