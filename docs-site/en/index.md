---
layout: home
title: GISPulse — The Declarative Geospatial Engine
titleTemplate: false
description: "Rules-as-config for your spatial data. What dbt is to data, GISPulse is to GIS. Open source AGPL-3.0, multi-engine, 16+ formats."

head:
  - - meta
    - name: keywords
      content: "geospatial engine, rules-as-config, spatial ETL, PostGIS, DuckDB, GeoJSON, GPKG, FME alternative, spatial workflow, open source GIS"
  - - meta
    - property: og:title
      content: "GISPulse — The Declarative Geospatial Engine"
  - - meta
    - property: og:description
      content: "Rules-as-config for your spatial data. What dbt is to data, GISPulse is to GIS. Open source AGPL-3.0."
  - - meta
    - property: og:type
      content: website
  - - meta
    - name: twitter:title
      content: "GISPulse — The Declarative Geospatial Engine"
  - - meta
    - name: twitter:description
      content: "Rules-as-config for your spatial data. Open source, multi-engine, 16+ formats."

hero:
  name: GISPulse
  text: The Declarative Geospatial Engine
  tagline: "Rules-as-config for your spatial data. What dbt is to data, GISPulse is to GIS. v2.0.0 available — 118 capabilities."
  image:
    light: /logo.svg
    dark: /logo-dark.svg
    alt: GISPulse
  actions:
    - theme: brand
      text: Discover the project
      link: /guide/rules
    - theme: alt
      text: Contact us
      link: /en/community

features:
  - icon: "📋"
    title: Declarative
    details: "JSON rules, not code. Reproducible, Git-versionable, readable by your GIS team without knowing Python."
    link: /guide/rules
    linkText: Write rules

  - icon: "⚙️"
    title: Multi-engine
    details: "Embedded DuckDB for portable mode (no install needed), PostGIS for production, or both in hybrid mode."
    link: /guide/engines
    linkText: Choose your engine

  - icon: "🗂️"
    title: 16+ formats
    details: "GPKG, GeoJSON, Shapefile, GeoParquet, CSV+WKT, FlatGeobuf, WFS, GeoTIFF, PMTiles, PostGIS... no prior conversion needed."
    link: /guide/formats
    linkText: Supported formats

  - icon: "🔌"
    title: Embeddable
    details: "CLI for scripts and CI/CD. Built-in REST API. Python SDK. QGIS and ArcGIS plugins. You choose the entry point."
    link: /api/sdk
    linkText: Integrations

  - icon: "🧭"
    title: End-to-end walkthroughs
    details: "Three concrete scenarios QGIS save → trigger fires → portal action: building classification, isochrone recompute, audit log. Zero GIS-client plugin needed."
    link: /en/guide/walkthroughs/parcels
    linkText: Parcels · Isochrone · Audit

  - icon: "🔓"
    title: Open Source AGPL-3.0
    details: "Open, auditable, contributable source code. No vendor lock-in. Self-hosted on your infrastructure or in your cloud."

  - icon: "🏢"
    title: Enterprise-ready
    details: "RBAC, audit log, SSO (OIDC/SAML), S3/object storage, Prometheus monitoring. Support and SLA available."
    link: /pricing
    linkText: Enterprise Plans
---

<!-- ─────────────────────── COMING SOON BANNER ─────────────────────── -->

<section class="gp-section gp-coming-soon-banner">
<div class="gp-coming-soon">
  <span class="gp-coming-soon-badge">v2.0.0</span>
  <p>GISPulse v2.0.0 is available. 118 capabilities (vector, attributes, classification, spatial statistics, clustering, 3D pointcloud, raster, network, PostGIS SQL), 3,600+ tests, multi-backend DuckDB/PostGIS engine, Prometheus metrics, ExtensionHub with the data-packs regime, worldwide geo aggregator, and MCP server.</p>
  <a href="/gispulse/en/getting-started/installation" class="gp-coming-soon-cta">Install now</a>
</div>
</section>

<!-- ─────────────────────── SOCIAL PROOF ─────────────────────── -->

<section class="gp-section gp-social-proof">
<div class="gp-badges-row">
  <span class="gp-badge gp-badge-license">Open source AGPL-3.0</span>
  <span class="gp-badge gp-badge-formats">16+ formats</span>
  <span class="gp-badge gp-badge-tests">3,600+ tests</span>
  <span class="gp-badge gp-badge-deploy">4 deployment modes</span>
  <span class="gp-badge gp-badge-python">Python 3.10+</span>
</div>
<div class="gp-stats-row">
  <div class="gp-stat">
    <span class="gp-stat-value">118</span>
    <span class="gp-stat-label">spatial capabilities</span>
  </div>
  <div class="gp-stat">
    <span class="gp-stat-value">3</span>
    <span class="gp-stat-label">engines (DuckDB / PostGIS / Hybrid)</span>
  </div>
  <div class="gp-stat">
    <span class="gp-stat-value">1</span>
    <span class="gp-stat-label">JSON file to describe it all</span>
  </div>
  <div class="gp-stat">
    <span class="gp-stat-value">0</span>
    <span class="gp-stat-label">server install in portable mode</span>
  </div>
</div>
</section>

<!-- ─────────────────────── PROBLEM / SOLUTION ─────────────────────── -->

<section class="gp-section gp-problem-solution">

## The problem with current tools

<div class="gp-ps-grid">
<div class="gp-ps-pain">
<h3>What you deal with today</h3>
<ul>
<li><strong>FME</strong> — 5,000 to 15,000 EUR/year per seat. Perpetual licenses deprecated in 2025. Total vendor lock-in.</li>
<li><strong>QGIS Processing</strong> — Excellent for interactive use, but not headless, no API, no server mode or scheduling.</li>
<li><strong>PostGIS alone</strong> — Powerful engine, but raw SQL to maintain, no native rule versioning, steep learning curve.</li>
<li><strong>GeoPandas scripts</strong> — Flexible, but imperative: every change requires a developer, impossible to audit by a non-dev.</li>
</ul>
</div>
<div class="gp-ps-solution">
<h3>What GISPulse brings</h3>
<ul>
<li><strong>Declarative JSON rules</strong> that your GIS team can read, modify and version without writing a single line of Python.</li>
<li>A <strong>portable engine</strong> — no database required. Run a processing job on any machine in 60 seconds.</li>
<li>The <strong>power of PostGIS</strong> when you need it — same rules, same file, just a different engine.</li>
<li>A <strong>REST API and Python SDK</strong> included — integrate GISPulse into your existing pipelines, ETLs, and apps.</li>
</ul>
</div>
</div>

<div class="gp-price-compare">
<span class="gp-price-fme">FME: 5,000–15,000 EUR/year</span>
<span class="gp-price-arrow">→</span>
<span class="gp-price-gp">GISPulse Community: <strong>free</strong></span>
</div>

</section>

<!-- ─────────────────────── CODE SNIPPET ─────────────────────── -->

<section class="gp-section gp-code-hero">

## A spatial pipeline in 5 lines

<div class="gp-code-tabs">
<div class="gp-code-panel">

Identify parcels in flood zones, compute their area, and export statistics by municipality — all in a single rules file:

```json
[
  {
    "name": "parcelles_a_risque",
    "capability": "spatial_join",
    "params": {
      "input": "parcelles.gpkg",
      "ref_layer": "zones_inondables.gpkg",
      "predicate": "intersects",
      "columns": ["niveau_risque"]
    }
  },
  {
    "name": "surface_par_commune",
    "capability": "spatial_aggregate",
    "params": {
      "input": "parcelles_a_risque",
      "ref_layer": "communes.gpkg",
      "predicate": "within",
      "agg": { "parcelle_id": "count", "surface_m2": "sum" }
    }
  }
]
```

</div>
<div class="gp-code-cli">

```bash
# Portable mode — no database required
gispulse run rules.json --engine duckdb

# API mode
curl -X POST http://localhost:8000/jobs \
  -d @rules.json

# SDK mode
from gispulse import GISPulse
gp = GISPulse()
result = gp.run("rules.json")
```

</div>
</div>

<div class="gp-code-caption">
Same rules file, three execution modes: local CLI, REST API, Python SDK.
</div>

</section>

<!-- ─────────────────────── HOW IT WORKS ─────────────────────── -->

<section class="gp-section gp-how-it-works">

## Three steps, zero friction

<div class="gp-steps">
<div class="gp-step">
<div class="gp-step-number">1</div>
<div class="gp-step-content">
<h3>Import your data</h3>
<p>GPKG, GeoJSON, Shapefile, GeoParquet, CSV, WFS... GISPulse reads everything, no prior conversion needed. The engine automatically adapts the schema.</p>
</div>
</div>
<div class="gp-step">
<div class="gp-step-number">2</div>
<div class="gp-step-content">
<h3>Declare your rules</h3>
<p>A JSON file describes the complete pipeline: capabilities to apply, parameters, execution order. Versionable under Git.</p>
</div>
</div>
<div class="gp-step">
<div class="gp-step-number">3</div>
<div class="gp-step-content">
<h3>Export in the same format</h3>
<p>The result is written in the original format. No database to manage, no conversion. Your files remain your files.</p>
</div>
</div>
</div>

</section>

<!-- ─────────────────────── CAPABILITIES ─────────────────────── -->

<section class="gp-section gp-capabilities-showcase">

## 118 capabilities, ready to use

<div class="gp-cap-grid">
<div class="gp-cap-group">
<h3>Vector — geometry</h3>
<ul>
<li><strong>buffer</strong>, <strong>clip</strong>, <strong>union</strong>, <strong>dissolve</strong>, <strong>centroid</strong>, <strong>reproject</strong></li>
<li><strong>convex_hull</strong>, <strong>concave_hull</strong>, <strong>alpha_shape</strong>, <strong>voronoi_polygons</strong>, <strong>delaunay_triangulation</strong></li>
<li><strong>simplify</strong>, <strong>chaikin_smooth</strong>, <strong>offset_curve</strong>, <strong>line_merge</strong>, <strong>line_substring</strong></li>
</ul>
</div>
<div class="gp-cap-group">
<h3>Cross-layer analysis & overlay</h3>
<ul>
<li><strong>spatial_join</strong>, <strong>intersects</strong>, <strong>spatial_aggregate</strong>, <strong>filter</strong>, <strong>nearest_neighbor</strong></li>
<li><strong>overlay_intersection</strong>, <strong>overlay_union</strong>, <strong>erase</strong>, <strong>merge_layers</strong>, <strong>classify_by_ring</strong></li>
</ul>
</div>
<div class="gp-cap-group">
<h3>Attributes & reshape</h3>
<ul>
<li><strong>add_field</strong>, <strong>drop_field</strong>, <strong>rename_field</strong>, <strong>cast_field</strong>, <strong>attribute_join</strong></li>
<li><strong>case_when</strong>, <strong>coalesce_fields</strong>, <strong>lookup_table</strong>, <strong>pivot</strong>, <strong>unpivot</strong></li>
<li><strong>sort</strong>, <strong>deduplicate</strong>, <strong>random_sample</strong>, <strong>top_n</strong></li>
</ul>
</div>
<div class="gp-cap-group">
<h3>Classification & styling</h3>
<ul>
<li><strong>classify</strong> (jenks / quantile / std_dev / pretty), <strong>classify_categorical</strong></li>
<li><strong>head_tail_breaks</strong> (Jiang 2013), <strong>normalize</strong> (log1p / minmax / zscore)</li>
<li><strong>choropleth</strong>, <strong>bivariate_choropleth</strong>, <strong>continuous_ramp</strong>, <strong>graduated_size</strong></li>
</ul>
</div>
<div class="gp-cap-group">
<h3>Spatial statistics & clustering</h3>
<ul>
<li><strong>morans_i</strong>, <strong>getis_ord_g</strong>, <strong>spatial_weights</strong>, <strong>kde_heatmap</strong></li>
<li><strong>cluster_kmeans</strong>, <strong>cluster_dbscan</strong>, <strong>cluster_hdbscan</strong></li>
<li><strong>grid_create</strong>, <strong>hexgrid_create</strong></li>
</ul>
</div>
<div class="gp-cap-group">
<h3>Topology & validation</h3>
<ul>
<li><strong>topology_check</strong>, <strong>duplicate_geometry</strong>, <strong>attribute_validation</strong>, <strong>completeness_check</strong></li>
<li><strong>polygon_fix_gaps</strong>, <strong>polygon_fix_overlaps</strong>, <strong>polygon_remove_slivers</strong></li>
<li><strong>network_snap_endpoints</strong>, <strong>network_node_lines</strong>, <strong>network_remove_pseudo_nodes</strong></li>
</ul>
</div>
<div class="gp-cap-group">
<h3>3D pointcloud</h3>
<ul>
<li><strong>pointcloud_load_las</strong> — LAS / LAZ to GeoDataFrame</li>
<li><strong>pointcloud_filter_classification</strong> — ASPRS codes</li>
<li><strong>pointcloud_zonal_height</strong> — building / canopy heights</li>
<li><strong>pointcloud_grid_summary</strong> — Z statistics per grid cell</li>
</ul>
</div>
<div class="gp-cap-group">
<h3>Raster & network (Pro)</h3>
<ul>
<li><strong>zonal_stats</strong>, <strong>raster_clip</strong>, <strong>raster_reproject</strong>, <strong>ndvi</strong>, <strong>change_detection</strong></li>
<li><strong>shortest_path</strong>, <strong>isochrone</strong>, <strong>od_matrix</strong>, <strong>network_allocation</strong>, <strong>connectivity_check</strong></li>
<li><strong>postgis_sql</strong> — parameterized SQL query on PostGIS</li>
</ul>
</div>
</div>

<div class="gp-cap-footer">
<a href="/gispulse/en/guide/capabilities">See the full catalogue of 118 capabilities &rarr;</a>
</div>

</section>

<!-- ─────────────────────── DUAL MODE ─────────────────────── -->

<section class="gp-section gp-portable-vs-persistent">

## Portable or persistent — same rules

<div class="gp-dual-mode">
<div class="gp-mode-card">
<div class="gp-mode-icon">&#128193;</div>
<h3>Portable mode</h3>
<p>No database required. GISPulse spins up a temporary DuckDB engine in memory, executes your rules on your files, and writes the result in the original format.</p>
<div class="gp-mode-flow">GPKG in &rarr; DuckDB (memory) &rarr; GPKG out</div>
<ul>
<li>Zero server installation</li>
<li>Works offline and in CI/CD</li>
<li>Output format = input format</li>
<li>Ideal for scripts, one-shots, batch ETL</li>
</ul>
</div>
<div class="gp-mode-card">
<div class="gp-mode-icon">&#128215;</div>
<h3>Persistent mode</h3>
<p>Connect PostGIS. The same rules become real-time triggers, continuous pipelines, spatial materialized views.</p>
<div class="gp-mode-flow">PostGIS &harr; active triggers &harr; live results</div>
<ul>
<li>Real-time spatial triggers</li>
<li>Continuous pipelines with scheduling</li>
<li>Multi-user with RBAC</li>
<li>Ideal for servers, APIs, dashboards</li>
</ul>
</div>
</div>

</section>

<!-- ─────────────────────── PRICING PREVIEW ─────────────────────── -->

<section class="gp-section gp-pricing-preview">

## Pricing

<div class="gp-pricing-coming-soon">
<p>Community (free, AGPL-3.0), Pro and Enterprise plans are available — see the <a href="/gispulse/en/pricing">pricing page</a>.</p>
<p>Interested in volume licensing or early-adopter pricing? <a href="mailto:contact@gispulse.dev">Contact us</a>.</p>
</div>

</section>

<!-- ─────────────────────── COMPARISON ─────────────────────── -->

<section class="gp-section gp-comparison">

## GISPulse vs the alternatives

| Criteria | **GISPulse** | FME | QGIS Processing | PostGIS alone |
|---|---|---|---|---|
| **Price** | Free (AGPL) | 5–15k EUR/year | Free | Free |
| **Declarative** | Native JSON | Visual GUI | GUI + scripts | Raw SQL |
| **Portable mode** | Embedded DuckDB | No | Desktop interface | No |
| **Server mode** | PostGIS / API | FME Flow ($$) | No | Yes |
| **CLI / headless** | Yes | Partial | No | psql only |
| **REST API** | Included | Paid | No | Not native |
| **Versionable (Git)** | Native (JSON) | XML export | Non-standard | SQL migrations |
| **Python SDK** | Included | Paid | PyQGIS scripts | psycopg2/SQLAlchemy |
| **QGIS Plugin** | Yes | Yes | Native | No |
| **Cloud-native** | S3, Docker, K8s | FME Cloud ($$) | No | Self-managed |

<div class="gp-comparison-note">
FME remains an excellent tool for complex graphical workflows. GISPulse targets teams that want to <strong>automate, version, and deploy</strong> their spatial processing in a modern pipeline.
<a href="/gispulse/en/blog/gispulse-vs-fme">Detailed comparison &rarr;</a>
</div>

</section>


<!-- ─────────────────────── FOOTER CTA ─────────────────────── -->

<section class="gp-footer-cta-big">

<h2>GISPulse v2.0.0 is available</h2>
<p>Install GISPulse and run your first spatial pipeline in 5 minutes.</p>

<div class="gp-footer-cta-actions">
  <a href="/gispulse/en/getting-started/installation" class="gp-btn-primary">Install GISPulse</a>
  <a href="/gispulse/en/getting-started/quickstart" class="gp-btn-secondary">Quickstart</a>
</div>

<div class="gp-footer-meta">
AGPL-3.0 &nbsp;&middot;&nbsp; Python 3.10+ &nbsp;&middot;&nbsp; DuckDB 1.x / PostGIS 3.x
</div>

</section>
