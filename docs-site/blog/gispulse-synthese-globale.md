# GISPulse - Synthese Globale & Positionnement Marche

**Date :** Avril 2026
**Editeur :** ImagoData (contact@imagodata.com)
**Licence :** AGPL-3.0-or-later + Dual licence commerciale
**Version :** 1.0.0
**Site :** gispulse.io

---

## 1. Executive Summary

GISPulse est un **moteur geospatial modulaire open-source** qui execute des regles metier, des traitements spatiaux et des triggers sur des datasets geographiques, independamment du client d'entree.

**Proposition de valeur unique :** "Definissez vos traitements spatiaux en JSON, pas en code" - une approche **rules-as-config** pour l'automatisation geospatiale.

**Double mode d'exploitation :**
- **Mode portable** : GPKG/SpatiaLite <-> moteur DuckDB temporaire <-> GPKG enrichi (zero infrastructure)
- **Mode persistant** : PostGIS central, regles et pipelines actifs en continu (production)

**Facades d'acces multiples :** FastAPI (HTTP/REST), FastMCP (Model Context Protocol pour IA), CLI, QGIS Plugin (prevu).

---

## 2. Architecture Technique

### Stack technologique

| Couche | Technologie | Role |
|--------|-------------|------|
| **Moteur spatial** | PostGIS / DuckDB Spatial | Execution SQL spatiale |
| **Portable** | SpatiaLite / GPKG | Sessions fichier, zero serveur |
| **API HTTP** | FastAPI + Uvicorn | Facade REST/JSON |
| **Facade IA** | FastMCP | Acces Model Context Protocol |
| **Orchestration** | Python asyncio + worker pool | Pipeline, jobs, triggers |
| **Cache/Queue** | Redis (optionnel) | Job queue, metering, cache |
| **Stockage** | S3/MinIO (optionnel) | Artifacts, exports |
| **Auth** | OIDC/SSO + RBAC | Securite multi-tenant |
| **Billing** | Stripe | Abonnements Pro/Enterprise |

### Architecture modulaire

```
core/           Types fondamentaux (dataset, layer, job, rule, trigger)
capabilities/   Moteurs de traitement (vector, raster, network, validation)
rules/          Evaluation des predicats, execution des operations
orchestration/  Runner, scheduler, worker, metering
persistence/    DuckDB engine, SpatiaLite session, PostGIS
adapters/       HTTP (FastAPI), MCP (FastMCP), Billing (Stripe)
```

### Dependances principales

- Python 3.10+ (compatibilite 3.10 / 3.11 / 3.12)
- GeoPandas, Shapely 2, DuckDB 1.x, pyogrio, pyproj
- Extensions optionnelles : PostGIS (SQLAlchemy/GeoAlchemy2), raster (rasterio), network (NetworkX), Redis, S3, SSO, Billing

---

## 3. Marche GIS - Taille & Tendances

### Chiffres cles 2025-2026

| Segment | Valeur 2025 | Projection | CAGR |
|---------|-------------|------------|------|
| **Logiciels GIS** | ~14,6 Mrd$ | 31,8 Mrd$ (2031) | ~13,9% |
| **Analytics geospatial** | ~102-104 Mrd$ | 209-310 Mrd$ (2029-2034) | 12,9-19% |
| **Startups geospatiales** | 11,8 Mrd$ (financement cumule, 203 startups) | - | - |

### Moteurs de croissance

1. **Integration IA/LLM** avec les workflows geospatiaux - agents GIS autonomes, analyse spatiale pilotee par LLM
2. **Villes intelligentes et urbanisme** - budgets publics en hausse
3. **Climat, energie, gestion des risques** - nouveaux verticaux (ex: Felt leve 15M$ sur ce segment)
4. **Migration cloud** - passage du desktop-first a l'API-first
5. **Overture Maps** (Meta, Microsoft, AWS, TomTom) - legitimisation des donnees ouvertes en production
6. **Standards cloud-natifs** - GeoParquet, PMTiles, STAC, COG deviennent les standards de facto

### Concentration du marche

Esri detient environ **45% du marche** des logiciels GIS. Le reste est tres fragmente entre stacks open-source, fournisseurs cloud, et SaaS verticaux. Cette fragmentation est une **opportunite** pour un outil federateur comme GISPulse.

---

## 4. Paysage Concurrentiel

### 4.1 Concurrents Open-Source

| Outil | Langage | Forces | Faiblesses vs GISPulse |
|-------|---------|--------|----------------------|
| **GeoServer** | Java | Plus grand serveur GIS OSS ; full OGC (WMS/WFS/WCS) ; FAO, NASA, World Bank | Lourd ; pas de moteur de regles ; pas de mode portable ; publication seule |
| **pygeoapi** | Python | Leger ; OGC API complet (Features, Processes, Tiles) ; impl. ref. OGC | Pas de regles/triggers ; pas de traitement metier |
| **QGIS Server** | C++ | Compatibilite projets QGIS ; WMS/WFS/OGC API | Pas de regles-as-config ; pas API-first ; concurrence limitee en prod |
| **MapServer** | C | Performance rendering maximale ; NASA | Pas de WFS-T ; pas de processing ; niche |
| **Martin** | Rust | Leader perf tuiles vectorielles 2025 (2-3x plus rapide) ; MapLibre | Tuiles uniquement ; pas de logique metier |
| **pg_tileserv** | Go | Ultra-leger PostGIS -> MVT ; zero config | PostGIS-only ; pas de cache ; pas de regles |
| **DuckDB Spatial** | C++ | In-process ; GeoParquet natif ; spatial joins optimises v1.3 | Pas de regles ; pas de triggers ; pas de serving |
| **Apache Sedona** | Java/Scala | Distribue Spark/Flink ; 50M downloads ; 300+ fonctions | Overkill single-node ; pas de mode portable ; complexe |
| **ZOO-Project** | C/Python | OGC API Processes ; chaining geoprocessing | Niche ; complexe ; pas rules-as-config |
| **GeoKettle** | Java | ETL spatial open-source (ex-concurrent de FME) | **Abandonne depuis 2017** - gap non comble |

### 4.2 Concurrents Commerciaux

| Outil | Type | Pricing | Forces | Faiblesses vs GISPulse |
|-------|------|---------|--------|----------------------|
| **Esri ArcGIS** | Proprietaire | 7 500-500 000+$/an | ~45% PDM ; suite complete ; defense/gov | Tres cher ; vendor lock-in ; architecture ancienne |
| **Safe Software FME** | Proprietaire | 3 000-5 000$/an (desktop) ; serveur sur devis | Standard de facto ETL spatial ; 350+ connecteurs ; 200K users | Visual-only ; pas developer-friendly ; cher ; pas API-first |
| **1Spatial 1Integrate** | Proprietaire | Enterprise (sur devis) | Moteur de regles brevete ; validation donnees spatiales ; 3D | **Concurrent le plus direct** ; mais validation seule, pas processing ; pas d'OSS ; pas portable |
| **CARTO** | SaaS | Hybride fixe+conso | Cloud-native ; integrations Snowflake/BQ/Databricks ; MCP Server 2025 | Cher a l'echelle ; pas de mode offline/portable ; pas de regles-as-config |
| **Mapbox** | SaaS | Free tier + pay-as-you-go | Meilleure visu vectorielle ; navigation ; MCP Server 2025 | Rendu/API seulement ; pas de processing ; complementaire |
| **Felt** | SaaS | Team/Enterprise | UX moderne ; climat/energie ; pricing transparent | Pas de moteur de traitement ; pas de regles/triggers |
| **Precisely** | Enterprise | Sur devis | 400+ datasets ; geo-addressing ; enrichissement | Enrichissement donnees, pas processing ; ferme |
| **Snowflake Spatial** | Cloud | Consommation | 60+ fonctions spatiales ; H3 natif ; zero infra ; GeoParquet | Pas d'indexation spatiale mature comme PostGIS ; cher a l'echelle ; pas de triggers |
| **BigQuery GIS** | Cloud | Consommation | Serverless ; echelle massive ; S2 indexing | Pas PostGIS-compatible ; pas de H3 ; exploratory cher |
| **Databricks Spatial** | Cloud | Consommation | H3 natif ; Sedona/Mosaic ; Spark integration | Geospatial = capacite secondaire ; setup complexe |

### 4.3 Matrice de differentiation

| Dimension | GeoServer | FME | 1Integrate | DuckDB | PostGIS brut | CARTO | **GISPulse** |
|-----------|-----------|-----|------------|--------|-------------|-------|-------------|
| Rules-as-config | Non | Visual | Oui (validation) | Non | Non | Non | **Oui** |
| Triggers/events | Non | Limite | Non | Non | SQL triggers | Non | **Oui** |
| Mode portable (GPKG) | Non | Oui (format) | Non | Oui (fichier) | Non | Non | **Oui** |
| PostGIS-natif | Plugin | Connecteur | Connecteur | Read-only | Natif | Non | **Natif** |
| API-first / dev-centric | Partiel | Non | Non | Oui | Partiel | Oui | **Oui** |
| Open-source | Oui | Non | Non | Oui | Oui | Non | **Oui (AGPL)** |
| Facade MCP (IA) | Non | Non | Non | Non | Non | Oui (2025) | **Oui** |
| Formats cloud-natifs | Limite | Oui | Non | Oui | Via outils | Oui | **Roadmap** |
| AI agent ready | Non | Non | Non | Non | Non | Oui | **Oui (MCP)** |
| Mode dual portable/persistant | Non | Non | Non | Non | Non | Non | **Oui** |

**Conclusion :** Aucun concurrent open-source ne combine les 4 piliers : rules-as-config + triggers + dual-mode portable/persistant + facade MCP. Le concurrent le plus proche (1Integrate) est commercial, validation-only, et ArcGIS-centrique.

---

## 5. Positionnement Strategique

### 5.1 Niche cible

**GISPulse se positionne sur le gap entre :**
- Les **serveurs de publication** (GeoServer, pygeoapi) qui exposent mais ne traitent pas
- Les **ETL visuels** (FME) qui sont chers et developer-hostile
- Les **bases spatiales** (PostGIS, DuckDB) qui sont puissantes mais sans logique metier
- Les **plateformes cloud** (CARTO, Snowflake) qui sont non portables et couteuses

### 5.2 Segments de marche adressables

| Segment | Besoin | GISPulse fit |
|---------|--------|-------------|
| **Bureaux d'etudes geo** | Automatiser traitements recurrents sur GPKG | Mode portable + regles JSON |
| **Collectivites** | Valider PLU/CNIG, conformite INSPIRE | Templates de regles metier |
| **Operateurs FTTH** | Validation topologique, pipeline terrain | Template FTTH + triggers |
| **Startups data/geo** | Moteur spatial API-first pour produit SaaS | PostGIS + FastAPI + MCP |
| **Equipes data engineering** | ETL spatial developer-friendly | Rules-as-config vs FME visual |
| **Projets IA geospatiale** | Moteur d'execution derriere agents LLM | Facade MCP native |

### 5.3 Modele economique

| Tier | Prix | Contenu |
|------|------|---------|
| **Community** | Gratuit (AGPL) | Core engine, CLI, mode portable, API locale |
| **Pro** | 79 EUR/mois (790 EUR/an) | RBAC, Redis job queue, S3 storage, cron scheduler, audit logging |
| **Team** | 299 EUR/mois | Multi-tenant, metering, support prioritaire |
| **Enterprise** | Sur devis (>= 1490 EUR) | SSO OIDC, Stripe billing, Terraform deploy, SLA |

**Strategie d'acquisition :**
- QGIS Plugin = cheval de Troie (3M+ utilisateurs QGIS)
- PyPI = canal developer principal
- Open governance prevue a 1000+ stars GitHub
- Cible : premiers 50 early adopters via GeoDataDays et communaute QGIS

---

## 6. Standards & Tendances Technologiques

### Standards emergents (2025-2026)

| Standard | Statut | Pertinence GISPulse |
|----------|--------|-------------------|
| **OGC API Features** | Adopte (GeoServer, pygeoapi, QGIS Server) | Exposition des resultats traites - **roadmap R8** |
| **STAC** | Standard communautaire OGC (oct 2025) | Catalogage assets spatiaux - **roadmap R8** |
| **GeoParquet 2.0** | Natif Apache Parquet + Iceberg 3 (2025) | Output cloud-native du mode portable - **opportunite** |
| **PMTiles** | Production (Overture Maps, Azure Maps) | Distribution tuiles sans serveur - **complementaire** |
| **COG** | Standard OGC (v1.0 juil 2023) | Sortie raster cloud-optimisee - **roadmap R10** |
| **MCP** | Adoption rapide (CARTO, Mapbox, Wherobots) | Facade IA deja implementee - **avantage** |

### Vague IA geospatiale

- **GIS Copilot** : 86% de reussite sur 100+ taches spatiales multi-etapes ; reduit le temps de ~1h45 a ~27min
- **Serveurs MCP geospatiaux en multiplication** : CARTO MCP, Mapbox MCP, PostGIS MCP, GDAL MCP, GeoServer MCP, Wherobots MCP
- **GISPulse advantage** : Architecture MCP-native depuis le debut, pas un ajout posterieur

### Ecosysteme PostGIS

- **985+ entreprises** utilisent PostGIS (Landbase 2025)
- PostgreSQL = **BDD #1 mondiale** (StackOverflow 2025 : 55,6% des developpeurs)
- PostGIS valide comme choix d'architecture moteur pour GISPulse
- DuckDB Spatial = complement ideal pour le mode portable (analytics in-process, GeoParquet natif)

---

## 7. Forces (Pros)

### Avantages competitifs

1. **Niche unique non occupee** : aucun outil OSS ne combine rules-as-config + triggers + dual-mode + MCP
2. **Mode portable sans infrastructure** : DuckDB/SpatiaLite -> GPKG, zero serveur requis (unique sur le marche)
3. **PostGIS-natif** : s'appuie sur la BDD spatiale #1 mondiale (985+ entreprises, 55,6% adoption PostgreSQL)
4. **MCP-native des le depart** : pret pour la vague agents IA geospatiaux 2025-2026
5. **Architecture modulaire propre** : core/capabilities/rules/orchestration/persistence/adapters bien separes
6. **Stack Python moderne** : ecosysteme riche (GeoPandas, Shapely 2, DuckDB), developpeurs accessibles
7. **Modele AGPL + dual licence** : communaute libre + revenus commerciaux viables
8. **Gap GeoKettle** : le marche ETL spatial OSS est orphelin depuis 2017, GISPulse comble ce vide
9. **Cible developpeurs vs FME visual** : positionnement anti-FME explicite pour equipes techniques
10. **Templates metier** : FTTH, PLU/CNIG = revenus directs sur verticaux reglementaires francais

### Atouts techniques

- Streaming chunke, LRU cache, worker pool, pagination + simplification
- 863 tests unitaires passants
- React 19 + TypeScript 5.9 + MapLibre + xyflow (DAG editor) en frontend
- CI/CD, Docker, Terraform Hetzner/DO prets

---

## 8. Faiblesses (Cons)

### Risques et dette technique

1. **Securite non production-ready** : 18 vulnerabilites identifiees dont 4 critiques (SQL injection, SSRF, endpoint /sql/execute sans auth) - **sprint R1 en cours**
2. **Score QA : 5,5/10** : 149 assertions silencieuses, zero test sur core/filter/ (7 modules), bridge.py, operation_executor.py
3. **Cross-layer rules non fonctionnel** : le differenciateur cle (rules-as-config) ne supporte pas encore les operations cross-layer/spatial join en JSON - **le produit ne delivre pas encore sa promesse**
4. **God files** : portal_router.py (1218 lignes, 6 responsabilites), vector.py (1130 lignes)
5. **Violations architecturales** : MetricsCollector (adapters->core), spatialite_session->rules, double PredicateEvaluator
6. **Double bootstrap FastAPI** : app.py vs portal_app.py non fusionne
7. **Frontend dette** : 2 viewers paralleles (deck.gl + MapLibre), 16 Zustand stores
8. **Pas encore sur PyPI** : distribution non effective (prevu sprint R4, mai 2026)
9. **Pas de plugin QGIS** : le "cheval de Troie" n'existe pas encore (prevu R4)
10. **Zero client payant** : modele economique non valide en reel
11. **Equipe tres reduite** : projet porte par un developpeur principal
12. **Formats cloud-natifs non supportes** : GeoParquet, PMTiles, COG en roadmap seulement (R8/R10)
13. **Raster immature** : numpy non importe dans raster_io.py -> NameError potentiel

### Risques strategiques

- **Time-to-market** : les grands acteurs (CARTO, Mapbox, Wherobots) ajoutent des facades MCP rapidement
- **Adoption** : marche GIS conservateur, les collectivites et bureaux d'etudes changent lentement
- **Concurrence indirecte** : DuckDB Spatial + scripts Python custom = alternative "good enough" pour beaucoup de cas
- **1Integrate** : si 1Spatial lance un tier gratuit ou OSS, menace directe
- **Scalabilite equipe** : projet ambitieux pour une equipe reduite

---

## 9. Roadmap (Avril - Aout 2026)

| Sprint | Dates | Focus | Statut |
|--------|-------|-------|--------|
| **R1** Bunker | 7-20 avr | Fix 3 P0 secu + 4 P1 (auth bypass, SQL injection) | **En cours** |
| **R2** Plomberie | 21 avr - 4 mai | Redis lifecycle, race conditions, tests | Planifie |
| **R3** Caisse | 5-18 mai | Login SSO, Billing UI, Admin panel | Planifie |
| **R4** Vitrine | 19 mai - 1 juin | **PyPI, QGIS Plugin Repo, landing, CI** | Planifie |
| **R5** Premier client | 2-15 juin | Onboarding, settings, docs, health | Planifie |
| **R6** FTTH | 16-29 juin | Validation topologique + template FTTH | Planifie |
| **R7** PLU/CNIG | 30 juin - 13 juil | Validation PLU CNIG (30 regles) | Planifie |
| **R8** OGC/STAC | 14-27 juil | OGC API Features, STAC, GeoParquet | Planifie |
| **R9** Traction | 28 juil - 10 aout | Blog SEO, Discord, GeoDataDays | Planifie |
| **R10** Hardening | 11-24 aout | Load testing, raster, autoscale, backup | Planifie |

**Jalons cles :**
- Mai 2026 : premier package PyPI public
- Juin 2026 : premier client payant cible
- Juillet 2026 : conformite OGC API
- GeoDataDays 2026 : presentation/demo publique

---

## 10. Analyse SWOT

| | Positif | Negatif |
|---|---------|---------|
| **Interne** | **Forces** | **Faiblesses** |
| | Niche unique rules-as-config + dual mode | Securite non prod-ready (18 vulns) |
| | PostGIS-natif + MCP-native | Cross-layer rules non fonctionnel |
| | AGPL + modele dual licence viable | Zero client payant / adoption non validee |
| | Architecture modulaire propre | Equipe reduite (1 dev principal) |
| | Stack Python moderne accessible | Pas encore distribue (PyPI mai 2026) |
| **Externe** | **Opportunites** | **Menaces** |
| | Gap GeoKettle (ETL spatial OSS orphelin) | CARTO/Mapbox ajoutent MCP rapidement |
| | Vague IA/MCP geospatiale 2025-2026 | Marche GIS conservateur (adoption lente) |
| | GeoParquet/STAC deviennent standards | DuckDB + scripts = "good enough" pour certains |
| | FTTH/PLU = verticaux reglementaires France | 1Integrate pourrait ouvrir un tier gratuit |
| | PostgreSQL #1 BDD mondiale | FME ameliore son API (Gartner Niche Player 2025) |
| | 3M+ utilisateurs QGIS (canal plugin) | Cloud warehouses integrent plus de spatial |

---

## 11. Comparaison Detaillee - GISPulse vs Alternatives Cles

### GISPulse vs FME (Safe Software)

| Critere | GISPulse | FME |
|---------|----------|-----|
| Approche | Code/JSON-first (rules-as-config) | Visual-first (drag-and-drop) |
| Cible | Developpeurs, equipes techniques | GIS analysts, non-developpeurs |
| Prix | Gratuit (Community) / 79 EUR/mois (Pro) | ~3 000-5 000 EUR/an desktop ; serveur sur devis |
| Open-source | Oui (AGPL) | Non |
| API-first | Oui (FastAPI natif) | Non (API ajoutee apres) |
| Connecteurs | PostGIS, DuckDB, GPKG, SpatiaLite | 350+ formats |
| IA/MCP | Oui (facade MCP native) | Non |
| Maturite | Pre-production (v1.0, avril 2026) | 25+ ans, 200K+ utilisateurs |
| Mode portable | Oui (GPKG/DuckDB, zero serveur) | Oui (desktop) mais pas de mode dual |

### GISPulse vs 1Integrate (1Spatial)

| Critere | GISPulse | 1Integrate |
|---------|----------|------------|
| Type | OSS + dual licence | Proprietaire enterprise |
| Regles | Processing + validation | Validation seule |
| Triggers/events | Oui | Non |
| Mode portable | Oui (GPKG) | Non |
| PostGIS-natif | Oui | Via connecteur |
| Prix | Gratuit / 79 EUR/mois | Enterprise (milliers EUR/an) |
| 3D | Non (roadmap) | Oui (v3.0) |
| Maturite | Pre-production | Production, gov UK/EU |

### GISPulse vs DuckDB Spatial

| Critere | GISPulse | DuckDB Spatial |
|---------|----------|----------------|
| Nature | Moteur de traitement avec regles | Base analytique in-process |
| Regles/triggers | Oui | Non |
| API REST | Oui (FastAPI) | Non (library) |
| GeoParquet | Roadmap (R8) | Natif |
| Mode serveur | Oui (PostGIS) | Non (in-process) |
| Relation | **Utilise DuckDB comme moteur portable** | Complementaire |

---

## 12. Metriques Techniques Actuelles

| Metrique | Valeur |
|----------|--------|
| **Lignes de code backend** | ~15 000+ LOC Python |
| **Lignes de code frontend** | ~33 600 LOC (React 19 + TS 5.9) |
| **Tests** | 863 pass, 0 fail |
| **Couverture estimee** | ~60% (zones mortes identifiees) |
| **Modules** | 6 couches (core, capabilities, rules, orchestration, persistence, adapters) |
| **Dependances core** | 7 (GeoPandas, Shapely, DuckDB, pyogrio, pyproj, typer, structlog) |
| **Extensions optionnelles** | 10 (postgis, api, mcp, raster, network, redis, s3, scheduling, sso, billing) |
| **Score audit global** | 5,5/10 (secu 3/10, tests 6/10, archi 7/10, perf 7,5/10, frontend 7/10) |
| **Issues GitHub** | ~53 issues planifiees (R1-R10) |

---

## 13. Opportunites Strategiques Identifiees

1. **Combler le gap GeoKettle** : le marche ETL spatial open-source est orphelin depuis 2017. GISPulse peut capturer cette audience.

2. **Facade MCP = positionnement IA** : etre le moteur d'execution derriere les agents IA geospatiaux (tendance 2025-2026 majeure).

3. **QGIS Plugin comme canal d'acquisition** : 3M+ utilisateurs QGIS = marche captif pour un plugin qui pousse vers GISPulse Pro.

4. **Verticaux reglementaires francais** : PLU/CNIG (30 regles de validation), FTTH (validation topologique) = pain points identifies avec budget.

5. **GeoParquet comme output natif** : transformer le mode portable pour produire des artifacts cloud-natifs (GeoParquet au lieu de GPKG seul).

6. **Anti-FME pour developpeurs** : positionnement explicite "FME costs 5000 EUR/yr and is visual-only, GISPulse is free and code-first".

7. **Cloud-Native Geo gap** : 45% des pros geospatiaux seniors ont besoin d'aide pour la transition CNG (source: CNG Conference 2025). GISPulse peut etre le pont.

---

## 14. Conclusion & Recommandation

**GISPulse occupe une niche strategique reelle** : aucun outil open-source ne combine moteur de regles spatial + triggers + mode dual portable/persistant + facade MCP. Le marche GIS croit a ~14% par an, la vague IA/MCP geospatiale accelere, et le gap GeoKettle (ETL spatial OSS) reste non comble depuis 2017.

**Les risques sont significatifs mais geres** : le sprint R1 (securite) est en cours, la roadmap R1-R10 couvre les 5 prochains mois avec des jalons clairs. Le principal risque est l'execution avec une equipe reduite sur un scope ambitieux.

**Verdict : produit a fort potentiel dans une niche non contestee, en phase pre-production.** La cle est d'atteindre PyPI + premier client payant d'ici juin 2026 pour valider le modele.

---

*Sources : Fortune Business Insights, Mordor Intelligence, MarketsandMarkets, Landbase 2025, StackOverflow 2025, Cloud-Native Geo Forum, OGC, Tracxn, audits internes GISPulse (avril 2026).*
