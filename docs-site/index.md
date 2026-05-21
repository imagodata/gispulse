---
layout: home
title: GISPulse — Le moteur geospatial declaratif
titleTemplate: false
description: "Rules-as-config pour vos donnees spatiales. Ce que dbt est a la data, GISPulse l'est au GIS. Open source AGPL-3.0, multi-engine, 16+ formats."

head:
  - - meta
    - name: keywords
      content: "moteur geospatial, rules-as-config, spatial ETL, PostGIS, DuckDB, GeoJSON, GPKG, alternative FME, workflow spatial, open source GIS"
  - - meta
    - property: og:title
      content: "GISPulse — Le moteur geospatial declaratif"
  - - meta
    - property: og:description
      content: "Rules-as-config pour vos donnees spatiales. Ce que dbt est a la data, GISPulse l'est au GIS. Open source AGPL-3.0."
  - - meta
    - property: og:type
      content: website
  - - meta
    - name: twitter:title
      content: "GISPulse — Le moteur geospatial declaratif"
  - - meta
    - name: twitter:description
      content: "Rules-as-config pour vos donnees spatiales. Open source, multi-engine, 16+ formats."

hero:
  name: GISPulse
  text: Le moteur geospatial declaratif
  tagline: "Rules-as-config pour vos donnees spatiales. Ce que dbt est a la data, GISPulse l'est au GIS. v2.0.0 disponible — 118 capabilities."
  image:
    light: /logo.svg
    dark: /logo-dark.svg
    alt: GISPulse
  actions:
    - theme: brand
      text: Decouvrir le projet
      link: /guide/rules
    - theme: alt
      text: Nous contacter
      link: /community

features:
  - icon: "📋"
    title: Declaratif
    details: "Des regles JSON, pas du code. Reproductible, versionnable sous Git, lisible par votre equipe SIG sans connaitre Python."
    link: /guide/rules
    linkText: Ecrire des regles

  - icon: "⚙️"
    title: Multi-engine
    details: "DuckDB embarque pour le mode portable (aucune install), PostGIS pour la production, ou les deux en mode hybride."
    link: /guide/engines
    linkText: Choisir son moteur

  - icon: "🗂️"
    title: 16+ formats
    details: "GPKG, GeoJSON, Shapefile, GeoParquet, CSV+WKT, FlatGeobuf, WFS, GeoTIFF, COG, PostGIS, SpatiaLite... sans conversion prealable."
    link: /guide/formats
    linkText: Formats supportes

  - icon: "🔌"
    title: Embeddable
    details: "CLI pour les scripts et CI/CD. API REST incluse. SDK Python. Plugins QGIS et ArcGIS. Vous choisissez le point d'entree."
    link: /api/sdk
    linkText: Integrations

  - icon: "🧭"
    title: Walkthroughs end-to-end
    details: "Trois scenarios concrets QGIS save → trigger fire → action portail : classification de batiments, recompute d'isochrones, log d'audit. Zero plugin GIS-client."
    link: /guide/walkthroughs/parcels
    linkText: Parcelles · Isochrone · Audit

  - icon: "🔓"
    title: Open Source AGPL-3.0
    details: "Code source ouvert, auditable, contribuable. Aucun vendor lock-in. Self-hosted sur votre infra ou dans votre cloud."

  - icon: "🏢"
    title: Enterprise-ready
    details: "RBAC, audit log, SSO (OIDC/SAML), S3/object storage, monitoring Prometheus. Support et SLA disponibles."
    link: /pricing
    linkText: Plans Enterprise
---

<!-- ─────────────────────── COMING SOON BANNER ─────────────────────── -->

<section class="gp-section gp-coming-soon-banner">
<div class="gp-coming-soon">
  <span class="gp-coming-soon-badge">v2.0.0</span>
  <p>GISPulse v2.0.0 est disponible. 118 capabilities (vecteur, attributs, classification, statistiques spatiales, clustering, 3D pointcloud, raster, reseau, PostGIS SQL), 3 600+ tests, moteur multi-backend DuckDB/PostGIS, Prometheus metrics, ExtensionHub avec régime data-packs, agrégateur géo mondial et serveur MCP.</p>
  <a href="/gispulse/getting-started/installation" class="gp-coming-soon-cta">Installer maintenant</a>
</div>
</section>

<!-- ─────────────────────── SOCIAL PROOF ─────────────────────── -->

<section class="gp-section gp-social-proof">
<div class="gp-badges-row">
  <span class="gp-badge gp-badge-license">Open source AGPL-3.0</span>
  <span class="gp-badge gp-badge-formats">16+ formats</span>
  <span class="gp-badge gp-badge-tests">3 600+ tests</span>
  <span class="gp-badge gp-badge-deploy">4 modes de deploiement</span>
  <span class="gp-badge gp-badge-python">Python 3.10+</span>
</div>
<div class="gp-stats-row">
  <div class="gp-stat">
    <span class="gp-stat-value">118</span>
    <span class="gp-stat-label">capabilities spatiales</span>
  </div>
  <div class="gp-stat">
    <span class="gp-stat-value">3</span>
    <span class="gp-stat-label">moteurs (DuckDB / PostGIS / Hybrid)</span>
  </div>
  <div class="gp-stat">
    <span class="gp-stat-value">1</span>
    <span class="gp-stat-label">fichier JSON pour tout decrire</span>
  </div>
  <div class="gp-stat">
    <span class="gp-stat-value">0</span>
    <span class="gp-stat-label">install serveur en mode portable</span>
  </div>
</div>
</section>

<!-- ─────────────────────── PROBLEM / SOLUTION ─────────────────────── -->

<section class="gp-section gp-problem-solution">

## Le probleme avec les outils actuels

<div class="gp-ps-grid">
<div class="gp-ps-pain">
<h3>Ce que vous subissez aujourd'hui</h3>
<ul>
<li><strong>FME</strong> — 5 000 a 15 000 EUR/an par siege. Licences perpetuelles depreciees en 2025. Vendor lock-in total.</li>
<li><strong>QGIS Processing</strong> — Excellent pour l'interactif, mais pas headless, pas d'API, pas de mode serveur ni de scheduling.</li>
<li><strong>PostGIS seul</strong> — Moteur puissant, mais du SQL brut a maintenir, pas de versioning natif des regles, courbe d'apprentissage steep.</li>
<li><strong>Scripts GeoPandas</strong> — Flexibles, mais imperatifs : chaque changement necessite un dev, impossible a auditer par un non-dev.</li>
</ul>
</div>
<div class="gp-ps-solution">
<h3>Ce que GISPulse apporte</h3>
<ul>
<li>Des <strong>regles JSON declaratives</strong> que votre equipe SIG peut lire, modifier et versionner sans ecrire une ligne de Python.</li>
<li>Un <strong>moteur portable</strong> — aucune base de donnees requise. Lancez un traitement sur n'importe quelle machine en 60 secondes.</li>
<li>La <strong>puissance de PostGIS</strong> quand vous en avez besoin — les memes regles, le meme fichier, juste un moteur different.</li>
<li>Une <strong>API REST et un SDK Python</strong> inclus — integrez GISPulse dans vos pipelines existants, vos ETL, vos apps.</li>
</ul>
</div>
</div>

<div class="gp-price-compare">
<span class="gp-price-fme">FME : 5 000–15 000 EUR/an</span>
<span class="gp-price-arrow">→</span>
<span class="gp-price-gp">GISPulse Community : <strong>gratuit</strong></span>
</div>

</section>

<!-- ─────────────────────── CODE SNIPPET ─────────────────────── -->

<section class="gp-section gp-code-hero">

## Un pipeline spatial en 5 lignes

<div class="gp-code-tabs">
<div class="gp-code-panel">

Identifiez les parcelles en zone inondable, calculez leur surface, et exportez les statistiques par commune — tout dans un fichier de regles :

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
# Mode portable — aucune base requise
gispulse run rules.json --engine duckdb

# Mode API
curl -X POST http://localhost:8000/jobs \
  -d @rules.json

# Mode SDK
from gispulse import GISPulse
gp = GISPulse()
result = gp.run("rules.json")
```

</div>
</div>

<div class="gp-code-caption">
Meme fichier de regles, trois modes d'execution : CLI locale, API REST, SDK Python.
</div>

</section>

<!-- ─────────────────────── HOW IT WORKS ─────────────────────── -->

<section class="gp-section gp-how-it-works">

## Trois etapes, zero friction

<div class="gp-steps">
<div class="gp-step">
<div class="gp-step-number">1</div>
<div class="gp-step-content">
<h3>Importez vos donnees</h3>
<p>GPKG, GeoJSON, Shapefile, GeoParquet, CSV, WFS... GISPulse lit tout, sans conversion prealable. Le moteur adapte automatiquement le schema.</p>
</div>
</div>
<div class="gp-step">
<div class="gp-step-number">2</div>
<div class="gp-step-content">
<h3>Declarez vos regles</h3>
<p>Un fichier JSON decrit le pipeline complet : capabilities a appliquer, parametres, ordre d'execution. Versionnable sous Git.</p>
</div>
</div>
<div class="gp-step">
<div class="gp-step-number">3</div>
<div class="gp-step-content">
<h3>Exportez dans le meme format</h3>
<p>Le resultat est ecrit dans le format d'origine. Pas de base a gerer, pas de conversion. Vos fichiers restent vos fichiers.</p>
</div>
</div>
</div>

</section>

<!-- ─────────────────────── CAPABILITIES ─────────────────────── -->

<section class="gp-section gp-capabilities-showcase">

## 118 capabilities, pretes a l'emploi

<div class="gp-cap-grid">
<div class="gp-cap-group">
<h3>Vecteur — geometrie</h3>
<ul>
<li><strong>buffer</strong>, <strong>clip</strong>, <strong>union</strong>, <strong>dissolve</strong>, <strong>centroid</strong>, <strong>reproject</strong></li>
<li><strong>convex_hull</strong>, <strong>concave_hull</strong>, <strong>alpha_shape</strong>, <strong>voronoi_polygons</strong>, <strong>delaunay_triangulation</strong></li>
<li><strong>simplify</strong>, <strong>chaikin_smooth</strong>, <strong>offset_curve</strong>, <strong>line_merge</strong>, <strong>line_substring</strong></li>
</ul>
</div>
<div class="gp-cap-group">
<h3>Analyse inter-couches & overlay</h3>
<ul>
<li><strong>spatial_join</strong>, <strong>intersects</strong>, <strong>spatial_aggregate</strong>, <strong>filter</strong>, <strong>nearest_neighbor</strong></li>
<li><strong>overlay_intersection</strong>, <strong>overlay_union</strong>, <strong>erase</strong>, <strong>merge_layers</strong>, <strong>classify_by_ring</strong></li>
</ul>
</div>
<div class="gp-cap-group">
<h3>Attributs & reshape</h3>
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
<h3>Statistiques spatiales & clustering</h3>
<ul>
<li><strong>morans_i</strong>, <strong>getis_ord_g</strong>, <strong>spatial_weights</strong>, <strong>kde_heatmap</strong></li>
<li><strong>cluster_kmeans</strong>, <strong>cluster_dbscan</strong>, <strong>cluster_hdbscan</strong></li>
<li><strong>grid_create</strong>, <strong>hexgrid_create</strong></li>
</ul>
</div>
<div class="gp-cap-group">
<h3>Topologie & validation</h3>
<ul>
<li><strong>topology_check</strong>, <strong>duplicate_geometry</strong>, <strong>attribute_validation</strong>, <strong>completeness_check</strong></li>
<li><strong>polygon_fix_gaps</strong>, <strong>polygon_fix_overlaps</strong>, <strong>polygon_remove_slivers</strong></li>
<li><strong>network_snap_endpoints</strong>, <strong>network_node_lines</strong>, <strong>network_remove_pseudo_nodes</strong></li>
</ul>
</div>
<div class="gp-cap-group">
<h3>3D pointcloud</h3>
<ul>
<li><strong>pointcloud_load_las</strong> — LAS / LAZ vers GeoDataFrame</li>
<li><strong>pointcloud_filter_classification</strong> — codes ASPRS</li>
<li><strong>pointcloud_zonal_height</strong> — hauteur batiments / canopée</li>
<li><strong>pointcloud_grid_summary</strong> — statistiques Z par grille</li>
</ul>
</div>
<div class="gp-cap-group">
<h3>Raster & reseau (Pro)</h3>
<ul>
<li><strong>zonal_stats</strong>, <strong>raster_clip</strong>, <strong>raster_reproject</strong>, <strong>ndvi</strong>, <strong>change_detection</strong></li>
<li><strong>shortest_path</strong>, <strong>isochrone</strong>, <strong>od_matrix</strong>, <strong>network_allocation</strong>, <strong>connectivity_check</strong></li>
<li><strong>postgis_sql</strong> — requete SQL parametree sur PostGIS</li>
</ul>
</div>
</div>

<div class="gp-cap-footer">
<a href="/gispulse/guide/capabilities">Voir le catalogue complet des 118 capabilities &rarr;</a>
</div>

</section>

<!-- ─────────────────────── DUAL MODE ─────────────────────── -->

<section class="gp-section gp-portable-vs-persistent">

## Portable ou persistant — memes regles

<div class="gp-dual-mode">
<div class="gp-mode-card">
<div class="gp-mode-icon">&#128193;</div>
<h3>Mode portable</h3>
<p>Aucune base de donnees requise. GISPulse monte un moteur DuckDB temporaire en memoire, execute vos regles sur vos fichiers, et ecrit le resultat dans le format d'origine.</p>
<div class="gp-mode-flow">GPKG in &rarr; DuckDB (memoire) &rarr; GPKG out</div>
<ul>
<li>Zero installation serveur</li>
<li>Fonctionne hors-ligne et en CI/CD</li>
<li>Format de sortie = format d'entree</li>
<li>Ideal pour scripts, one-shots, ETL batch</li>
</ul>
</div>
<div class="gp-mode-card">
<div class="gp-mode-icon">&#128215;</div>
<h3>Mode persistant</h3>
<p>Branchez PostGIS. Les memes regles deviennent des triggers temps-reel, des pipelines continus, des vues materialisees spatiales.</p>
<div class="gp-mode-flow">PostGIS &harr; triggers actifs &harr; resultats live</div>
<ul>
<li>Triggers spatiaux temps-reel</li>
<li>Pipelines continus avec scheduling</li>
<li>Multi-utilisateurs avec RBAC</li>
<li>Ideal pour serveurs, APIs, dashboards</li>
</ul>
</div>
</div>

</section>

<!-- ─────────────────────── PRICING PREVIEW ─────────────────────── -->

<section class="gp-section gp-pricing-preview">

## Tarification

<div class="gp-pricing-coming-soon">
<p>Les plans Community (gratuit, AGPL-3.0), Pro et Enterprise sont disponibles — voir la <a href="/gispulse/pricing">page tarifs</a>.</p>
<p>Besoin d'un licensing volume ou d'un tarif early-adopter ? <a href="mailto:contact@gispulse.dev">Contactez-nous</a>.</p>
</div>

</section>

<!-- ─────────────────────── COMPARISON ─────────────────────── -->

<section class="gp-section gp-comparison">

## GISPulse vs les alternatives

| Critere | **GISPulse** | FME | QGIS Processing | PostGIS seul |
|---|---|---|---|---|
| **Prix** | Gratuit (AGPL) | 5–15k EUR/an | Gratuit | Gratuit |
| **Declaratif** | JSON natif | GUI visuel | GUI + scripts | SQL brut |
| **Mode portable** | DuckDB embarque | Non | Interface desktop | Non |
| **Mode serveur** | PostGIS / API | FME Flow ($$) | Non | Oui |
| **CLI / headless** | Oui | Partiel | Non | psql uniquement |
| **API REST** | Incluse | Payante | Non | Non natif |
| **Versionnable (Git)** | Natif (JSON) | Export XML | Non standard | Migrations SQL |
| **SDK Python** | Inclus | Payant | Scripts PyQGIS | psycopg2/SQLAlchemy |
| **Plugin QGIS** | Oui | Oui | Natif | Non |
| **Cloud-native** | S3, Docker, K8s | FME Cloud ($$) | Non | Self-managed |

<div class="gp-comparison-note">
FME reste un excellent outil pour des workflows graphiques complexes. GISPulse cible les equipes qui veulent <strong>automatiser, versionner et deployer</strong> leurs traitements spatiaux dans un pipeline moderne.
<a href="/gispulse/blog/gispulse-vs-fme">Comparatif detaille &rarr;</a>
</div>

</section>


<!-- ─────────────────────── FOOTER CTA ─────────────────────── -->

<section class="gp-footer-cta-big">

<h2>GISPulse v2.0.0 est disponible</h2>
<p>Installez GISPulse et lancez votre premier pipeline spatial en 5 minutes.</p>

<div class="gp-footer-cta-actions">
  <a href="/gispulse/getting-started/installation" class="gp-btn-primary">Installer GISPulse</a>
  <a href="/gispulse/getting-started/quickstart" class="gp-btn-secondary">Quickstart</a>
</div>

<div class="gp-footer-meta">
AGPL-3.0 &nbsp;·&nbsp; Python 3.10+ &nbsp;·&nbsp; DuckDB 1.x / PostGIS 3.x
</div>

</section>
