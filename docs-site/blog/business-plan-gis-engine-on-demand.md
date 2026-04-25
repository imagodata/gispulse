---
title: "GISPulse Business Plan — GIS Engine on Demand"
date: 2026-04-09
description: "Plan business detaille : analyse de marche, strategie open-source, adoption, integration MCP, projections financieres"
tags: [business, strategy, open-source, mcp, market-analysis]
---

# GISPulse Business Plan — GIS Engine on Demand

## Positionnement

**GISPulse = le moteur de regles spatiales programmable, open-source, MCP-native.**

Differenciateurs uniques (aucun concurrent ne combine les 4) :

- **Rules-as-config** : JSON declaratif, versionnable, testable en CI
- **Triggers spatiaux evenementiels** : declenchement sur geometrie, pas sur SQL
- **Mode portable <-> persistant** : GPKG/SpatiaLite <-> PostGIS sans friction
- **Facade MCP** : premier moteur GIS pilotable par IA

---

## 1. Analyse de marche

### 1.1 Taille du marche (TAM / SAM / SOM)

| Niveau | Valeur | Perimetre |
|--------|--------|-----------|
| **TAM** | 14-17 Mds USD | Marche GIS global (CAGR 13.7%) |
| **SAM** | 2.2-3.5 Mds USD | ETL spatial + outils dev + middleware |
| **SOM** | 8-15M EUR / 3 ans | France + Europe francophone, PME/collectivites |

Le marche global GIS etait estime a 14.1 milliards USD en 2023 (MarketsandMarkets), avec une croissance projetant 28.1 milliards USD en 2028. Les segments porteurs : cloud GIS (38% de la croissance), LBS, defense/gouvernement, urban planning.

Le sous-segment open-source avec support commercial represente environ 15-20% du SAM, soit 350-700M USD, domine par des services autour de PostGIS, QGIS, GeoServer.

Un SOM de 8-15M EUR sur 3 ans est defendable si la traction PLU/FTTH se confirme. Hypothese : 150-300 clients Pro/Team en 2027 generent entre 1.4M et 10.7M EUR ARR selon mix tarifaire.

### 1.2 Concurrence — Matrice de positionnement

| Concurrent | Prix | Rules Engine | Mode Portable | MCP | Open-Source |
|------------|------|-------------|---------------|-----|-------------|
| **FME** | 1500-40K USD/an | Workflow visuel | Non | Non | Non |
| **CARTO** | 500-5000 USD/mois | Non | Non | Non | Non |
| **PostGIS** | Gratuit | Non | Non | Non | Oui |
| **DuckDB Spatial** | Gratuit | Non | Oui (embarque) | Non | Oui |
| **Apache Sedona** | Gratuit | Non | Non | Non | Oui |
| **WherobotsDB** | 200+ USD/mois | Non | Non | Non | Non |
| **GeoServer** | Gratuit | Non | Non | Non | Oui |
| **GDAL + scripts** | Gratuit | Non | N/A | Non | Oui |
| **GISPulse** | 0-1490 EUR/mois | **Oui (JSON)** | **Oui** | **Oui** | **Oui (AGPL)** |

### 1.3 Analyse detaillee des concurrents

#### FME (Safe Software)

- Leader inconteste ETL geospatial depuis 30 ans
- 450+ formats supportes, ~25 000 organisations
- Pricing : Desktop 1 500-3 000 USD/an, Server 10 000-40 000 USD/an, Flow cloud 3 000-8 000 USD/mois
- Faiblesses : prix prohibitif PME, interface vieillissante, pas de rules-as-config, vendor lock-in, pas de MCP
- **Gap vs GISPulse** : pas de triggers spatiaux evenementiels, pas de moteur de regles declaratif JSON, pas de facade MCP

#### CARTO

- Plateforme cloud analytics spatiale SaaS
- Integration BigQuery/Snowflake, deck.gl natif
- Pricing : Team ~500 USD/mois, Enterprise 2 000-5 000 USD/mois
- Faiblesses : cloud-only, tres cher, pas open-source, pas de rules engine
- **Gap vs GISPulse** : vise les CDO grandes entreprises, pas les PME/collectivites

#### DuckDB Spatial

- SQL analytique ultra-rapide, lecture directe GeoParquet/FlatGeobuf
- Plutot une dependance potentielle qu'un concurrent
- Faiblesses : pas de moteur de regles, pas de triggers, pas de mode server

#### GDAL/OGR + scripts Python

- Le concurrent invisible : 80% des data engineers GIS bricolent leurs pipelines avec GDAL + scripts
- Zero orchestration, zero rules engine, maintenance ad-hoc
- **GISPulse remplace directement ces scripts ad-hoc par des regles declaratives**

#### PostGIS seul

- Moteur spatial de reference, gratuit
- Aucun rules engine, aucune orchestration, aucune facade API
- GISPulse s'appuie dessus et l'augmente — positionnement complementaire

### 1.4 Tendances 2025-2026

**Cloud-Native Geospatial** : COG, GeoParquet, FlatGeobuf, STAC sont les standards de facto. AWS, Google, Microsoft ont lance des services geospatiaux natifs. La demande en outils capables de consommer ces formats directement explose.

**MCP / AI Integration** : le Model Context Protocol (Anthropic, 2024) cree un vecteur d'integration inedit pour les outils GIS. Tres peu d'outils GIS l'ont encore fait. GISPulse est early-mover avec une fenetre d'opportunite de 12-18 mois.

**GeoParquet & Spatial SQL Renaissance** : GeoParquet 1.0 (ratifie OGC 2023) est le format d'echange analytique standard. La spatial SQL renaissance alimente la demande pour des moteurs capables de parler SQL spatial nativement.

**Souverainete logicielle** : en Europe, Data Spaces (Gaia-X), Data Act 2025, obligations INSPIRE creent une demande forte en outils open-source souverains. Les collectivites francaises cherchent des alternatives a FME.

**STAC Ecosystem** : STAC s'est impose pour les catalogues raster/satellite. L'integration STAC est un must-have pour le segment earth observation.

### 1.5 Gaps du marche

| Gap | Description | Avantage GISPulse |
|-----|-------------|-------------------|
| **Rules Engine Spatial** | Aucun outil ne propose un moteur de regles metier spatiales en JSON, versionnables, testables en CI/CD | Differenciateur central |
| **Mode Portable <-> Persistant** | La transition GPKG <-> PostGIS n'existe nulle part sans scripts manuels | Abstraction unique |
| **Triggers Spatiaux** | Les triggers PostGIS sont couples a la base, non portables, non testables | Triggers declaratifs, observables |
| **Facade MCP pour GIS** | Aucun outil GIS mainstream n'expose ses capacites via MCP | First-mover advantage 12-18 mois |
| **Collectivites post-FME** | Pression budgetaire + souverainete = recherche active d'alternatives open-source | Offre PLU/urbanisme directement adressable |

---

## 2. Strategie Open Source — AGPL Dual-Licence

### 2.1 Modele retenu : type GitLab/Supabase

| Modele | Avantage | Risque | Verdict |
|--------|----------|--------|---------|
| Open Core (type dbt) | Coeur gratuit, features premium | Frustre les contributeurs | Non retenu |
| AGPL dual-licence (type GitLab) | Force les entreprises a payer si pas publication | Clair juridiquement | **Retenu** |
| Cloud-only premium (type PostHog) | Self-host gratuit, managed payant | Trop tot, pas d'infra cloud | Prevu Year 2 |

### 2.2 Feature gating : gratuit vs payant

| Gratuit (AGPL) | Pro (79 EUR/mois) | Team (299 EUR/mois) | Enterprise (>=1490 EUR/mois) |
|----------------|-------------------|---------------------|------------------------------|
| Core engine complet | Templates metier (FTTH, PLU) | RBAC multi-utilisateurs | SSO SAML/OIDC |
| Rules JSON illimitees | Support email 48h | Audit logs | SLA garanti 99.9% |
| CLI + FastAPI | DuckDB engine | Triggers avances | Support dedie |
| Mode portable GPKG | Facade MCP | Cron scheduling | Licence commerciale (non-AGPL) |
| PostGIS basique | Dashboard monitoring | S3/stockage cloud | Deploiement on-premise assiste |
| 1 engine (PostGIS ou SpatiaLite) | Multi-engine | Webhooks | Templates custom |
| Pas de limite de datasets | Export enrichi | API metering | Formation incluse |

**Logique** : le moteur reste 100% open-source et fonctionnel. On monetise la **productivite** (templates, monitoring, multi-engine) et la **gouvernance** (RBAC, audit, SSO).

### 2.3 Community building : stars -> contributors -> enterprise

| Phase | Action | Objectif | Timeline |
|-------|--------|----------|----------|
| Visibilite | README impeccable, badges, GIF demo 30s | 500 stars | M1-M3 |
| Engagement | Issues "good first issue", CONTRIBUTING.md | 10 contributeurs | M3-M6 |
| Credibilite | FOSS4G talk, blog posts, comparatifs | 1500 stars | M6-M9 |
| Conversion | CTA "Book a demo" dans la doc, formulaire Enterprise | 5 leads enterprise | M9-M12 |

### 2.4 Benchmark modeles OSS

- **PostHog** : self-host gratuit, cloud payant. Approche "transparent pricing + public handbook" a copier
- **Supabase** : AGPL + managed cloud. Growth tiree par les devs individuels puis equipes
- **GitLab** : AGPL dual-licence, feature tiers CE/EE. Modele le plus proche. Erreur a eviter : trop de features en EE
- **dbt** : open core, CLI gratuit, Cloud payant. Piege : contributeurs frustrés quand le core stagne

**Regle** : review du gating tous les trimestres. Feature gratuite < 5% usage = reste gratuite. Feature payante qui bloque l'adoption = passe gratuite.

---

## 3. Strategie d'adoption — 3 phases

### Phase 1 : Developer Adoption (M1-M4, avril-juillet 2026)

**Objectif** : les devs GIS peuvent `pip install` et avoir un resultat en 5 minutes.

| Action | Detail | Metrique cible |
|--------|--------|----------------|
| PyPI publish | `pip install gispulse` | 500 downloads/mois M4 |
| CLI ergonomique | `gispulse run rules.json --input data.gpkg --output result.gpkg` | Time-to-value < 5min |
| Doc quickstart | 3 tutoriels : filtrage basique, regles metier, export enrichi | 50 stars GitHub M4 |
| Exemples reproductibles | Repo `gispulse-examples` avec 5 cas concrets | Fork ratio > 10% |
| GitHub README | GIF anime, badges CI/coverage/PyPI, one-liner install | CTR README > 30% |

**Flywheel** : pip install -> quickstart 5min -> "ca marche" -> star GitHub -> tweet -> nouveau dev

### Phase 2 : Team Adoption (M4-M8, juillet-novembre 2026)

**Objectif** : les equipes SIG adoptent via le plugin QGIS et les templates metier.

| Action | Detail | Metrique cible |
|--------|--------|----------------|
| QGIS Plugin Repo | Plugin stable, 1 clic install | 200 installs M8 |
| Template FTTH | Pipeline NRO -> SRO -> PBO -> PTO | 20 equipes telecom testent |
| Template PLU/CNIG | Conformite CNIG, export reglementaire | 15 collectivites testent |
| Onboarding guide | "De QGIS a GISPulse en 15 minutes" | Conversion > 40% |
| Discord/forum | Support communautaire, channels metier | 100 membres actifs |

**Flywheel** : template metier -> equipe adopte -> feedback -> template ameliore -> nouvelle equipe

**Le QGIS plugin est le cheval de Troie.** 95% des SIG-istes utilisent QGIS. Si on est dans leur toolbar, on est dans leur workflow. Zero friction : install, pointer GPKG, choisir template, run.

### Phase 3 : Enterprise (M8-M14, novembre 2026 - mai 2027)

| Action | Detail | Metrique cible |
|--------|--------|----------------|
| SSO SAML/OIDC | Integration AD/Azure/Okta | 3 contrats enterprise |
| Audit logs | Tracabilite complete | Requis secteur public |
| SLA | 99.9% uptime, support 4h | Requis production |
| Licence commerciale | Alternative AGPL pour integration propriétaire | 2 licences vendues |
| Case studies | 2-3 temoignages clients publies | Credibilite pipeline |

### Metriques cles par phase

| Metrique | Phase 1 (M4) | Phase 2 (M8) | Phase 3 (M14) |
|----------|-------------|-------------|---------------|
| GitHub stars | 200 | 800 | 2 000 |
| PyPI downloads/mois | 500 | 2 000 | 5 000 |
| QGIS installs | - | 200 | 600 |
| Contributeurs | 3 | 10 | 20 |
| MRR | 0 EUR | 1 500 EUR | 8 000 EUR |
| Clients payants | 0 | 5-10 Pro | 15 Pro + 3 Team + 1 Enterprise |
| Discord membres | 30 | 100 | 300 |

---

## 4. Integration MCP — Levier strategique

### 4.1 Positionnement : premier moteur GIS MCP-native

Le MCP (Model Context Protocol) permet a un LLM d'interagir directement avec le moteur GIS. Personne ne fait ca dans le geospatial en 2026.

**La proposition** : "Demande a ton agent IA d'analyser tes donnees spatiales. GISPulse comprend."

### 4.2 Cas d'usage concrets

| Cas d'usage | Persona | Valeur |
|-------------|---------|--------|
| "Verifie la conformite CNIG de ce PLU" | Urbaniste | Gagne 2h de travail |
| "Genere le rapport FTTH pour le NRO Bordeaux-Nord" | Technicien telecom | Automatisation complete |
| "Quelles parcelles a 500m de la riviere sont en zone inondable ?" | Analyste risques | Requete en langage naturel |
| "Applique les regles du template X sur mon dataset Y" | Dev GIS | Raccourci CLI |
| "Cree un pipeline pour mes 15 couches GPKG" | Data engineer | Orchestration complexe |

### 4.3 Ecosysteme cible

| Plateforme | Integration | Priorite |
|------------|-------------|----------|
| Claude Desktop / Claude Code | MCP natif, premier client | P0 — deja compatible |
| Cursor / Windsurf | MCP via config, devs GIS | P1 |
| Agents autonomes (CrewAI, LangGraph) | MCP comme tool | P1 |
| ChatGPT | Via plugin ou API relay | P2 |
| n8n / Make | Via FastAPI adapter | P2 |

### 4.4 Monetisation du canal MCP — Plan en 3 temps

1. **M1-M6** : MCP gratuit et illimite. Chaque requete MCP = demo gratuite du produit. Objectif = adoption.
2. **M6-M12** : Metering en place (comptage requetes, telemetrie opt-in). MCP reste gratuit sur le tier communautaire.
3. **M12+** : Free tier = 100 requetes MCP/jour. Pro/Team = illimite. Enterprise = metering custom + SLA.

**Pourquoi pas tout de suite** : le marche MCP est naissant. Paywall sur un canal inconnu = tuer l'adoption. On veut devenir le reflexe "MCP + GIS = GISPulse" avant de monetiser.

---

## 5. Go-to-Market

### 5.1 Canaux par priorite

| # | Canal | Action | Timeline | Impact |
|---|-------|--------|----------|--------|
| 1 | **PyPI** | Package publie, keywords GIS | Mai 2026 | Decouverte devs Python |
| 2 | **GitHub** | Repo public, issues templates, releases | Actif | Credibilite + contributions |
| 3 | **QGIS Plugin Repo** | Plugin stable v1.0 | Juillet 2026 | Adoption equipes SIG |
| 4 | **Discord** | Serveur communautaire, channels metier | Mai 2026 | Retention + feedback |
| 5 | **Blog/Dev.to** | Articles techniques, comparatifs | Bi-mensuel juin+ | SEO + credibilite |
| 6 | **GeoDataDays** | Talk/demo | Septembre 2026 | Visibilite France |
| 7 | **FOSS4G** | Talk/workshop | 2027 | Visibilite mondiale |
| 8 | **LinkedIn** | Posts reguliers, audience GIS francophone | Hebdomadaire | Leads enterprise |

### 5.2 Content marketing — 5 articles prioritaires

1. **"GISPulse vs FME : l'alternative open-source pour le traitement spatial"** — SEO killer
2. **"Automatiser la conformite CNIG/PLU avec des regles JSON"** — Cas d'usage urbanisme
3. **"FTTH : de la conception NRO au PTO avec GISPulse"** — Cas d'usage telecom
4. **"Le premier moteur GIS pilotable par IA (MCP)"** — Positionnement innovation
5. **"Migrer de scripts Python ad-hoc vers des rules-as-config"** — Conversion devs existants

### 5.3 Early adopters — 50 premiers clients cibles

| Segment | Cible | Profil | Canal |
|---------|-------|--------|-------|
| Devs GIS freelance | 15 | Python + QGIS, automatisation | PyPI + blog + Discord |
| Bureaux d'etudes telecom | 10 | Deploiement FTTH, FME ou scripts | Template FTTH + demo |
| Collectivites territoriales | 10 | PLU/CNIG, budget contraint | Template PLU + GeoDataDays |
| Startups GeoAI | 5 | Agents IA + donnees spatiales | MCP + GitHub + LinkedIn |
| Labs/recherche | 5 | Reproductibilite, open-source | PyPI + publications |
| Operateurs infra (eau, energie) | 5 | Pipelines reseau | Demo directe + partenaires |

### 5.4 Partenariats strategiques

| Partenaire | Type | Valeur | Action |
|------------|------|--------|--------|
| **OSGeo** | Communaute | Label "Community Project", credibilite | Candidature M4 |
| **Anthropic** | Ecosysteme MCP | Premier GIS dans le MCP directory | Listing M5 |
| **Camptocamp/Oslandia** | Integrateurs | Revente + support + deploiement | Partenariat M8 |
| **OVH/Scaleway** | Cloud hosting | Offre managed souveraine | Contact M6 |
| **QGIS.org** | Ecosysteme | Visibilite plugin repo, co-marketing | Contribution upstream |
| **Syndicats SIG** (CRAIG, GeoBretagne) | Collectivites | Pilote terrain, credibilisation | Contact M4 |

---

## 6. Segments cibles — Analyse detaillee

### Bureaux d'etudes GIS

- **Potentiel** : eleve. ~3 500 structures en France, equipes 2-15 personnes
- **Douleur** : scripts GDAL non maintenus, FME trop cher, pas de rules engine partageable
- **Budget outils** : 2 000-15 000 EUR/an
- **Cycle de vente** : 2-6 semaines, decision technique
- **Priorite** : R1-R2, segment d'adoption initiale

### Collectivites territoriales

- **Potentiel** : moyen-eleve, cycle long. ~5 200 entites avec SIG actif
- **Douleur** : FME couteux, dependance editeur, conformite RGPD/souverainete
- **Budget DSI/SIG** : 50 000-500 000 EUR/an
- **Cycle de vente** : 3-18 mois (marches publics, UGAP)
- **Blocage** : necessite reference UGAP ou accord-cadre + collectivite pilote
- **Priorite** : R3-R4, forte valeur mais cycle long

### Operateurs FTTH / Telecoms

- **Potentiel** : eleve et differencie. ~180 operateurs FTTH actifs en France
- **Douleur** : pipelines validation FTTH fragiles, regles dispersees dans des scripts
- **Valeur GISPulse** : rules engine JSON + triggers = automatisation validation FTTH
- **Priorite** : R2-R3, ticket moyen plus important (Enterprise/Team)

### Urbanisme / PLU / SCOT

- **Potentiel** : moyen, specifique France. ~1 200 EPCI competents PLU
- **Douleur** : validation conformite CNIG manuelle et chronophage
- **Valeur GISPulse** : plugin validation PLU-CNIG (regles JSON, rapport conformite)
- **Priorite** : R3, necessite partenariat DDT pilote

### Data Engineers geospatiaux

- **Potentiel** : croissant. GeoAI, pipelines ML avec features spatiales, DuckDB/GeoParquet
- **Douleur** : pas d'outil intermediaire entre "script GDAL" et "PostGIS full stack"
- **Valeur GISPulse** : facade MCP + mode portable DuckDB/GPKG, distribution PyPI
- **Priorite** : R2, PyPI + doc API Python = unlockers

---

## 7. Pricing — Benchmark concurrentiel

| Produit | Tier | Prix | Mode |
|---------|------|------|------|
| FME Desktop | Licence annuelle | 1 500-3 000 USD/an | Desktop |
| FME Flow | Cloud managed | 3 000-8 000 USD/mois | Cloud |
| FME Server | Self-hosted | 10 000-40 000 USD/an | On-premise |
| CARTO | Team | ~500 USD/mois | SaaS |
| CARTO | Enterprise | 2 000-5 000 USD/mois | SaaS |
| WherobotsDB | Pro | ~200 USD/mois | Cloud |
| Mapbox | Pay-per-use | 5 USD/1 000 map loads | SaaS |
| Felt | Pro | ~10 USD/mois/user | SaaS |
| **GISPulse** | **Pro** | **79 EUR/mois** | Self-hosted/SaaS |
| **GISPulse** | **Team** | **299 EUR/mois** | Self-hosted/SaaS |
| **GISPulse** | **Enterprise** | **Sur devis (>=1490 EUR)** | On-premise + support |

**Positionnement tarifaire** :

- Pro 79 EUR/mois = ~5% du cout FME Desktop annuel
- Team 299 EUR/mois (~3 600 EUR/an) = 3-10x moins cher que FME Server
- vs CARTO : 5-15x moins cher

**Recommandation** : ajouter un tier **"Collectivite/Administration"** a 150-200 EUR/mois (facturation annuelle, clause souverainete on-premise) pour s'aligner avec les processus d'achat publics francais.

---

## 8. Projections financieres

### 8.1 Year 1-3

| Metrique | Y1 (fin mars 2027) | Y2 (fin mars 2028) | Y3 (fin mars 2029) |
|----------|---------------------|---------------------|---------------------|
| Users actifs gratuits | 800 | 3 000 | 10 000 |
| Clients Pro | 20 | 60 | 150 |
| Clients Team | 4 | 15 | 40 |
| Clients Enterprise | 1 | 5 | 15 |
| **MRR** | 4 266 EUR | 17 000 EUR | 50 000 EUR |
| **ARR** | **~50K EUR** | **~200K EUR** | **~600K EUR** |
| GitHub stars | 800 | 2 000 | 5 000 |
| PyPI downloads/mois | 2 000 | 5 000 | 15 000 |

### 8.2 Detail Year 1 par trimestre

| Trimestre | Users gratuits | Pro | Team | Enterprise | MRR | ARR |
|-----------|---------------|-----|------|------------|-----|-----|
| Q1 (avr-jun 2026) | 50 | 0 | 0 | 0 | 0 EUR | 0 EUR |
| Q2 (jul-sep 2026) | 200 | 5 | 0 | 0 | 395 EUR | 4 740 EUR |
| Q3 (oct-dec 2026) | 500 | 12 | 2 | 0 | 1 546 EUR | 18 552 EUR |
| Q4 (jan-mar 2027) | 800 | 20 | 4 | 1 | 4 266 EUR | 51 192 EUR |

### 8.3 Unit economics

| Metrique | Pro | Team | Enterprise |
|----------|-----|------|------------|
| CAC | 50-100 EUR | 300-500 EUR | 2 000-5 000 EUR |
| Duree moyenne | 20 mois | 30 mois | 36 mois |
| LTV | 1 580 EUR | 8 970 EUR | 53 640 EUR |
| **LTV/CAC** | **16-32x** | **18-30x** | **11-27x** |
| Churn mensuel | 5% | 2% | 2% |

### 8.4 Structure de couts

| Poste | M1-M6 | M7-M12 | M13-M18 |
|-------|-------|--------|---------|
| Dev (1 fondateur) | 0 EUR (equity) | 0 EUR (equity) | 3 000 EUR |
| Infra (CI, hosting, PostGIS) | 50 EUR | 150 EUR | 400 EUR |
| Outils (GitHub, monitoring) | 30 EUR | 50 EUR | 100 EUR |
| Marketing (events, content) | 0 EUR | 200 EUR | 500 EUR |
| **Total** | **80 EUR** | **400 EUR** | **4 000 EUR** |

### 8.5 Break-even

- **Operationnel** (hors salaire fondateur) : **M8-M10** (MRR > 400 EUR/mois)
- **Reel** (avec salaire) : **M18-M22** (MRR > 4 000 EUR/mois, soit ~25 Pro + 5 Team)

---

## 9. Risques et mitigations

| Risque | Probabilite | Impact | Mitigation |
|--------|-------------|--------|------------|
| Adoption trop lente | Moyen | Haut | Doubler sur QGIS plugin (plus fort volume) |
| FME lance offre similaire | Faible | Haut | MCP + open-source = pas de lock-in possible |
| Templates trop niche | Moyen | Moyen | FTTH (marche large) + core generique |
| MCP reste marginal | Faible | Moyen | FastAPI reste le canal principal, MCP = bonus |
| Solo-dev = bus factor 1 | **Haut** | **Critique** | Documenter, automatiser, recruter contributeur M6 |
| Cycle vente collectivites trop long | Moyen | Moyen | Focus bureaux d'etudes d'abord, collectivites en parallele |
| Concurrence cloud (AWS/Google) | Faible | Moyen | Open-source + souverainete = avantage en Europe |

---

## 10. Top 10 Actions immediates

| # | Action | Deadline | Impact |
|---|--------|----------|--------|
| 1 | PyPI package publiable et teste | Mai 2026 | Debloque toute la Phase 1 |
| 2 | README GitHub avec GIF demo 30s + one-liner | Mai 2026 | Premiere impression = adoption |
| 3 | Quickstart doc "5 minutes to first result" | Mai 2026 | Conversion visiteur -> user |
| 4 | Discord server + channels metier | Mai 2026 | Feedback loop + communaute |
| 5 | Listing MCP ecosystem Anthropic | Mai 2026 | Positionnement "GIS + AI" |
| 6 | Template FTTH fonctionnel | Juin 2026 | Premier use case monetisable |
| 7 | Article "GISPulse vs FME" | Juin 2026 | SEO + positionnement |
| 8 | Soumission talk GeoDataDays | Juin 2026 | Visibilite evenementielle |
| 9 | QGIS Plugin v1.0 stable | Juillet 2026 | Cheval de Troie adoption |
| 10 | Template PLU/CNIG | Juillet 2026 | Deuxieme vertical |

---

## 11. Deep Dive — Pricing

### 11.1 Benchmark SaaS B2B dev tools open-source

| Produit | Free Tier | Pro/Paid | Enterprise | Modele |
|---------|-----------|----------|------------|--------|
| **PostHog** | Self-host illimite, cloud 1M events/mois | ~$0.00045/event ($450/mois pour 10M) | SSO, SLA, sur devis | Usage-based |
| **Supabase** | 500 MB DB, 2 projets | $25/mois/projet + usage compute | $599/mois flat | Projet-based + usage |
| **GitLab** | Self-host illimite, cloud 5 users | Premium $29/user/mois | Ultimate $99/user/mois | Per-seat |
| **Grafana Cloud** | 10k metrics, 50 GB logs | Usage-based ~$8/1000 series | $299+/mois + devis | Usage-based |
| **HashiCorp** | BSL (ex-MPL) | $0.05-$0.08/heure/node | Sur devis | Per-node/cluster |
| **Elastic** | SSPL self-host | Cloud $16+/mois/GB | Sur devis | Usage-based |
| **MongoDB Atlas** | M0, 512 MB | Serverless $0.10/M reads | On-premise $20k-$80k/an | Freemium + usage |
| **dbt Cloud** | 1 seat, 1 projet | $50/seat/mois | $200-$500/seat/mois | Per-seat |
| **Airbyte Cloud** | Credits offerts | $2.50/credit (~1M records) | Sur devis | Usage-based |

**Enseignements cles** :
- PostHog a abandonne le per-seat : trop de friction pour les devs
- Le per-seat (GitLab, dbt) fonctionne pour les outils collaboratifs, pas pour un moteur de traitement
- L'usage-based (Airbyte, Grafana) est conceptuellement correct pour un moteur ETL mais cree du "meter anxiety"
- Le flat rate + feature gates (Supabase) est le meilleur compromis pour un produit naissant

### 11.2 Pricing GIS detaille

| Produit | Tier | Prix detaille | Notes |
|---------|------|---------------|-------|
| **FME Form (Desktop)** | Licence flottante | $3,500-$4,500/an | Perpetuel dispo ~$2,800 + 20% maintenance/an |
| **FME Form Essentials** | Usage limite | ~$1,200/an | Version light |
| **FME Flow (Server)** | 1 engine | $12,000-$15,000/an | Chaque engine +$3,500/an |
| **FME Flow** | Collectivites | $30k-$80k/an (3-8 engines) | Budget typique metropoles FR |
| **FME Flow Hosted** | Cloud | $0.40-$0.80/heure processing | Usage-based |
| **CARTO Builder** | Self-service | $199-$399/mois/user | Analytics inclus |
| **CARTO Enterprise** | Sur devis | $30k-$150k/an | BigQuery/Snowflake integration |
| **Mapbox Maps** | Pay-per-use | $0.50/1000 map loads (web) | Free: 50k loads/mois |
| **Mapbox Geocoding** | Pay-per-use | $0.75/1000 requests | Free: 100k/mois |
| **WherobotsDB** | WCU | $0.20-$0.50/WCU/heure | Modele Databricks-like |
| **Felt** | Pro/Team | $10-$15/user/mois | Feature gates collaboration |

### 11.3 Strategies de pricing pour l'adoption

**Free tier genereux (recommande)** : le produit complet avec limites de volume. Adoption maximale, zero friction. Les devs evaluent la valeur reelle avant de payer.

**Reverse trial** : 30 jours Pro par defaut, puis downgrade. Taux de conversion superieur car les users vivent la valeur complete. A envisager en v2.

**Pour un moteur de traitement spatial** : le per-seat est anti-naturel (1 dev deploie pour toute une organisation). Le flat rate + feature gates est optimal.

**Pricing psychologique** : 79 EUR vs 99 EUR — en B2B les acheteurs arrondissent. 99 EUR est plus standard et s'aligne avec $99 USD. Le 299 EUR n'a pas de valeur psychologique forte — 249 EUR ou 349 EUR seraient plus tranchants.

**Discount annuel standard** : 20% (2 mois offerts).
- Pro : 99 EUR/mois -> 950 EUR/an (~79 EUR/mois equiv)
- Team : 349 EUR/mois -> 3,350 EUR/an (~279 EUR/mois equiv)

### 11.4 Pricing collectivites francaises

**Seuils marches publics (2024)** :
- < 40,000 EUR HT : achat direct, bon de commande, zero friction administrative
- 40,000 - 214,000 EUR HT : procedure adaptee (MAPA)
- > 214,000 EUR HT : procedure formalisee (AO ouvert, JOUE)

**Implication** : Team a 349 EUR/mois = 4,188 EUR/an = achat direct sans mise en concurrence. Enterprise a 15,000 EUR/an = sous le seuil MAPA.

**Ce que paient les collectivites en GIS** :
- FME Flow : 40k-80k EUR/an pour les metropoles
- ESRI ArcGIS : 50k-200k EUR/an selon licences/extensions
- Support QGIS/GeoServer (Camptocamp, Oslandia, 3liz) : 10k-30k EUR/an
- MapServer support mutualise : 5k-15k EUR/an

**UGAP** : etre reference permet aux collectivites d'acheter sans mise en concurrence. Process 6-18 mois. A initier en parallele.

**Souverainete** : circulaire Cloud au Centre 2021, doctrine "Cloud de confiance". L'AGPL repond exactement a ce besoin (code auditable par nature). Argument commercial fort.

### 11.5 Grille tarifaire revisee (recommandation)

| Tier | Prix | Volume | Features | Cible |
|------|------|--------|----------|-------|
| **Community** | Gratuit (AGPL self-host) | Illimite | Core complet, CLI, FastAPI | Devs, contrib |
| **Developer Cloud** | 0 EUR/mois | 50k features/jour, 10 jobs | Tout Community en cloud | Evaluation |
| **Pro** | **99 EUR/mois** (950 EUR/an) | 1M features/jour, 50 jobs | Templates, MCP, DuckDB, support J+2 | Indep, startups, BE < 10 pers |
| **Team** | **349 EUR/mois** (3,350 EUR/an) | 10M features/jour, 200 jobs | SSO, RBAC, audit, 5 membres, support J+1 | PME, agences GIS |
| **Enterprise** | **Sur devis** (min 15,000 EUR/an) | Illimite | On-premise, SLA 99.5%, support 4h, formation | Metropoles, ETI, ministeres |

---

## 12. Deep Dive — AGPL Dual-Licence

### 12.1 Mecanisme exact de l'AGPL-3.0

La GPL oblige a partager le code source si vous **distribuez** le logiciel. L'AGPL-3.0 ajoute l'**article 13** : l'interaction par reseau compte comme distribution.

**Scenarios concrets** :

| Scenario | Obligation AGPL |
|----------|-----------------|
| Entreprise utilise GISPulse self-host, sans modification, en interne | Aucune obligation |
| Entreprise integre GISPulse dans son SaaS et l'expose a ses clients | Doit publier le code source complet sous AGPL |
| Entreprise modifie GISPulse, usage interne uniquement, pas d'API exposee | Pas d'obligation |
| Entreprise expose GISPulse en API interne entre equipes | Zone grise — genere souvent l'achat de licence par prudence |
| Grande entreprise refuse categoriquement l'AGPL (Google, banques) | Achete la licence commerciale |

### 12.2 Benchmark legal

**MongoDB (SSPL, ex-AGPL)** : a quitte l'AGPL en 2018 car AWS lancait DocumentDB sans contribuer ni payer. L'AGPL ne les y obligeait pas (pas de modification, pas de distribution). Le SSPL oblige a open-sourcer TOUT le stack si on offre le logiciel as-a-service. L'OSI a refuse de certifier le SSPL comme open-source.

**Grafana (AGPL v3 depuis 2021)** : migration Apache 2.0 -> AGPL pour se proteger des cloud providers. Peu de backlash car : communaute fidele, self-hosting reste libre, plugins enterprise restent proprietary. C'est exactement le modele a suivre.

**MinIO (AGPL-3.0)** : communication tres claire sur les obligations. Page licence qui explique les scenarios. GISPulse devrait avoir une page similaire.

**Supabase (Apache 2.0 core)** : evite l'AGPL pour maximiser l'adoption. Business model repose sur le cloud managed. Ne fonctionne que si on a les ressources pour etre le meilleur operateur.

**Elastic (SSPL)** : conflit AWS -> abandon Apache 2.0. Retour partiel AGPL en 2024 pour regagner la confiance communautaire. Montre que la credibilite OSI est un asset.

**HashiCorp (BSL)** : passage BSL en 2023 a provoque un backlash massif (fork OpenTofu). La licence est un asset strategique fragile.

### 12.3 Risques AGPL

**Entreprises qui refusent** : Google (politique interne connue), Apple (App Store incompatible GPL), banques (zero AGPL/GPL en prod). Impact faible pour GISPulse — ces personas ne sont pas la cible primaire.

**Contributeurs** : l'AGPL n'est pas un repoussoir pour les contributeurs individuels. Peut l'etre pour les contributions enterprise. En pratique, Grafana/MinIO/Nextcloud ont des ecosystemes sains sous AGPL.

**Plugins/extensions** : un plugin qui importe des modules Python GISPulse est probablement soumis a l'AGPL. Un plugin via API REST/MCP ne l'est probablement pas. **Solution** : definir une Plugin API Exception explicite.

**Fork hostile** : l'AGPL ne protege pas completement contre un hyperscaler qui opererait GISPulse sans modification. Risque negligeable au stade actuel.

### 12.4 CLA (Contributor License Agreement)

**Pourquoi c'est necessaire** : pour vendre une licence commerciale a cote de l'AGPL, il faut detenir les droits sur tout le code. Sans CLA, chaque contributeur detient le copyright sur son code et bloque le dual-licensing.

**Recommandation** : Apache ICLA (+ ECLA pour les entreprises), heberge sur CLA-assistant.io.

- Le contributeur conserve son copyright (moins rebutant que la cession totale)
- Automatisable via GitHub Actions
- Standard reconnu par les legals corporate
- Implementation : fichier CLA.md + CLA-assistant bot + workflow GitHub

### 12.5 Verdict AGPL

**Garder AGPL-3.0 + Plugin API Exception.**

Actions :
1. Ajouter une Plugin API Exception dans LICENSE : plugins via API REST ou MCP ne sont pas des oeuvres derivees
2. Implementer CLA avec CLA-assistant.io
3. Creer une page /licence claire avec scenarios (style MinIO)
4. Ne pas passer au SSPL ni a une licence custom — la credibilite OSI n'en vaut pas le cout

---

## 13. Deep Dive — Integration MCP

### 13.1 Etat de l'ecosysteme MCP (avril 2026)

| Indicateur | Valeur |
|------------|--------|
| Serveurs MCP publics | 500-1000+ |
| Domaines couverts | Dev tools, databases, APIs, fichiers — **zero GIS** |
| Maturite protocole | Stable, spec v1.0+, SDK Python/TypeScript/Java |
| Adoption enterprise | Debut, principalement dev tools |

**Clients MCP** : Claude Desktop (reference), Claude Code, Cursor, Windsurf, VS Code (Copilot), Continue.dev, Zed.

**Protocoles concurrents** : OpenAI function calling (proprietaire, pas interoperable), LangChain tools (framework-specific). OpenAI Agents SDK supporte MCP directement = validation du standard. MCP est le standard gagnant.

### 13.2 Architecture MCP cible

**Tools (operations)** :

| Categorie | Tool | Priorite | Existe |
|-----------|------|----------|--------|
| Discovery | `list_capabilities` | P0 | Oui |
| Discovery | `get_capability_info` | P0 | Oui |
| Discovery | `list_datasets` | P0 | Oui |
| Discovery | `describe_layer` (schema, CRS, bbox, stats) | P0 | Non |
| Discovery | `preview_layer` (N features en GeoJSON) | P1 | Non |
| Data | `load_gpkg` | P0 | Oui |
| Data | `load_file` (GeoJSON, Shapefile, GeoParquet) | P1 | Non |
| Data | `connect_postgis` | P1 | Non |
| Data | `export_result` (GPKG, GeoJSON, GeoParquet) | P1 | Non |
| Rules | `create_rule` / `list_rules` / `validate_rule` / `delete_rule` | P0 | Oui |
| Rules | `generate_rule` (depuis description naturelle) | P2 | Non |
| Execution | `run_job` | P0 | Oui |
| Execution | `run_rule_preview` (dry-run sur N features) | P1 | Non |
| Execution | `get_job_status` | P1 | Non |
| Spatial | `execute_sql` (DuckDB spatial, read-only) | P1 | Non |
| Spatial | `validate_geometries` | P1 | Non |
| Templates | `list_templates` / `apply_template` | P1 | Non |
| Triggers | `list_triggers` / `create_trigger` | P2 | Non |

**Resources (donnees lisibles par le LLM)** :

| URI | Contenu | Priorite |
|-----|---------|----------|
| `gispulse://capabilities` | JSON capabilities | P0 (existe) |
| `gispulse://rules` | JSON rules session | P0 (existe) |
| `gispulse://datasets` | Liste datasets charges | P1 |
| `gispulse://datasets/{id}/schema` | Schema (colonnes, types, CRS) | P1 |
| `gispulse://templates` | Catalogue templates metier | P2 |

**Prompts MCP** :

| Prompt | Description | Priorite |
|--------|-------------|----------|
| `explore_dataset` | Charge, decris, montre les stats | P1 |
| `build_pipeline` | Cree une chaine de regles pour un objectif | P2 |
| `validate_data` | Verifie qualite (geometries, CRS, nulls) | P2 |

**Transport** : stdio pour le lancement (90% des clients MCP), SSE pour le mode serveur en v1.1.

### 13.3 Cas d'usage MCP detailles

**Exploration de donnees** :
```
User: "J'ai un GPKG avec des donnees de reseau, montre-moi ce qu'il contient"
LLM -> [load_gpkg] -> {3 layers: cables, noeuds, zones}
LLM -> [describe_layer cables] -> {12340 features, EPSG:2154, columns...}
LLM: "3 couches. 'cables' a 12 340 entites en Lambert 93..."
```

**Execution de regles** :
```
User: "Buffer 50m autour des cables, filtre ceux en statut actif"
LLM -> [create_rule buffer] -> [create_rule filter] -> [run_job]
LLM: "8 200 cables actifs avec buffer de 50m."
```

**Debug spatial** :
```
User: "Mon job echoue sur certaines geometries"
LLM -> [validate_geometries] -> {200 invalides: 150 auto-intersections, 50 vertex dupliques}
LLM: "200 invalides. Veux-tu une regle de correction automatique ?"
```

### 13.4 Monetisation MCP

| Tier | Limites MCP |
|------|-------------|
| Community | 100 tool calls/jour, DuckDB only, datasets locaux |
| Pro | Illimite, PostGIS, templates, export |
| Team | Illimite, multi-user, triggers MCP |

**Le MCP est un canal d'acquisition, pas un produit.** Le LLM devient le commercial :
1. User installe `pip install gispulse[mcp]` (gratuit)
2. Configure dans Claude Desktop
3. Explore, cree des regles -> hook
4. Veut PostGIS/templates/triggers -> paywall Pro
5. Message : "Cette fonctionnalite necessite GISPulse Pro"

### 13.5 Securite MCP (P0)

Le serveur MCP actuel n'a aucune restriction de path. Actions requises :
1. Whitelist de directories autorises (`GISPULSE_MCP_ALLOWED_PATHS`)
2. Pas d'execution SQL arbitraire en mode MCP (ou mode read-only)
3. Rate limiting cote serveur MCP
4. Logging de chaque tool call pour audit

### 13.6 Roadmap MCP

| Phase | Timeline | Contenu |
|-------|----------|---------|
| **v1.0** | Mai 2026 | Tools actuels + `describe_layer` + `preview_layer` + sandboxing + metering |
| **v1.1** | Juin 2026 | `load_file` multi-format, `export_result`, `execute_sql` read-only, SSE |
| **v1.2** | Juillet 2026 | Prompts MCP, resources dynamiques, templates |
| **v2.0** | Sept 2026 | Sampling (generate_rule), triggers MCP, PostGIS live, streaming |

---

## 14. Deep Dive — Go-to-Market

### 14.1 Lancement PyPI (mai 2026)

**Experience "5 minutes"** :
```bash
pip install gispulse                          # 30s
gispulse init my-project && cd my-project     # 10s — cree rules.json, sample.gpkg
gispulse run --rules rules.json --input sample.gpkg --output result.gpkg  # 20s
gispulse serve                                # optionnel — portal web localhost:8765
gispulse mcp                                  # optionnel — serveur MCP pour Claude
```

**Regle d'or** : si ca ne fonctionne pas en 5 minutes sans Docker, sans PostGIS, sans rien configurer, c'est mort.

**Launch day plan** :

| Timing | Canal | Action |
|--------|-------|--------|
| J-7 | Twitter/X | Teaser "Spatial rules-as-config is coming" |
| J-3 | Blog | Article "Why we built GISPulse" |
| J0 08:00 | PyPI | `twine upload` tag v1.0.0 |
| J0 09:00 | GitHub | Release v1.0.0 avec release notes |
| J0 10:00 | Hacker News | "Show HN: GISPulse — Rules-as-config spatial processing (Python)" |
| J0 10:30 | Reddit r/gis | Adapter vocabulaire au public GIS |
| J0 10:30 | Reddit r/python | Focus Python, pas GIS |
| J0 11:00 | LinkedIn | Post personnel + page ImagoData |
| J0 12:00 | Twitter/X | Thread 5 tweets avec demo GIF |
| J0 14:00 | Dev.to | Article technique |
| J+1 | Discord | Lancer premiers canaux |

**Product Hunt** : non pertinent pour le lancement (pas de UI sexy). A reconsiderer en v2 avec le portal web.

### 14.2 QGIS Plugin — Cheval de Troie

**Architecture** : thin client PyQt5 -> `gispulse` engine (le package Python).

**Integration QGIS** :
- **Processing Provider** (P0) : chaque capability = 1 algorithme Processing. Decouverte via la toolbox, chainable dans Model Builder.
- **Dock Widget** (P1) : panel lateral "GISPulse Rules"
- **Menu + Toolbar** (P0) : 3-4 boutons (Run, Rules, Datasets)

**Distribution** : QGIS Plugin Repo officiel (review 1-2 semaines), GitHub releases en parallele.

**Funnel de conversion** :
```
Install plugin gratuit -> capabilities basiques (buffer, filter, clip)
  -> Veut PostGIS / templates / triggers
  -> "Pro feature. 30 jours d'essai gratuit."
  -> Upgrade
```

### 14.3 Content marketing — 10 articles planifies

| # | Titre | Audience | SEO Keywords | Priorite |
|---|-------|----------|-------------|----------|
| 1 | "Why we built a rules-as-config engine for geospatial" | Devs, HN | geospatial rules engine | P0 launch |
| 2 | "Getting started with GISPulse in 5 minutes" | Nouveaux users | python gis library | P0 |
| 3 | "DuckDB for geospatial: the portable spatial database" | Data devs | duckdb spatial | P1 |
| 4 | "Building an MCP server for spatial data analysis" | Devs AI/LLM | mcp server python | P1 |
| 5 | "500 lines of Python -> 20 lines of JSON" | GIS analysts | spatial etl open source | P1 |
| 6 | "PostGIS triggers meets business rules" | Data engineers | postgis automation | P2 |
| 7 | "QGIS + GISPulse: automate your spatial workflows" | QGIS users | qgis plugin development | P2 |
| 8 | "Validate PLU/CNIG data automatically" | Urbanistes FR | validation cnig plu | P2 |
| 9 | "GeoParquet + DuckDB + GISPulse: modern geo stack" | Data engineers | geoparquet processing | P3 |
| 10 | "FTTH network planning with declarative rules" | Telecoms | ftth network planning gis | P3 |

**Video** : la demo MCP (Claude Desktop + GISPulse = conversation spatiale) est le hero content viral. 5-8 min, un LLM qui analyse des donnees spatiales ca n'existe nulle part.

### 14.4 Evenements

| Evenement | Date | Format | Priorite |
|-----------|------|--------|----------|
| **GeoDataDays** | Sept/Oct 2026 | Talk 20min + stand | P0 France |
| **PyConFR** | Oct 2026 | Talk 30min | P1 |
| **FOSS4G** | 2027 | Workshop 2h | P1 mondial |
| **QGIS User Conference** | 2027 | Talk + demo plugin | P1 |
| **SotM France** | 2026/2027 | Lightning talk | P2 |

### 14.5 Partenariats concrets

| Partenaire | Profil | Approche | Timing |
|------------|--------|----------|--------|
| **Oslandia** | Expert PostGIS, contrib QGIS core | Co-dev plugin QGIS | Juin 2026 |
| **Camptocamp** | Integrateur, clients collectivites | Revendeur Pro/Team | Sept 2026 |
| **Makina Corpus** | Django GIS | Integration Django + GISPulse | Sept 2026 |
| **Alkante** | Geomatique Bretagne | Demo PLU/CNIG | Oct 2026 |
| **Scaleway** | Cloud marketplace | Image Docker GISPulse | Q4 2026 |
| **OVH** | PostGIS managed | Template Terraform | Q4 2026 |

**Oslandia est le partenaire #1.** Au coeur de l'ecosysteme PostGIS/QGIS francais. Un tweet @Oslandia vaut plus que 10 posts LinkedIn.

**OSGeo** : Community Project realiste a 6 mois (100+ stars, quelques contributeurs). AGPL est compatible.

**Anthropic MCP Ecosystem** : soumettre PR au repo `modelcontextprotocol/servers`. Gratuit, visibilite immediate.

### 14.6 Metriques et funnel

```
Visiteur site/GitHub (1000/mois)
  |  20% -> Star GitHub
  |  10% -> pip install (100/mois)
  |
pip install
  |  50% -> premier run (50/mois)
  |  30% -> abandon
  |
User actif (50/mois)
  |  30% -> teste Pro trial 30j (15/mois)
  |  60% -> reste Community
  |
Trial Pro
  |  33% -> convertit Pro (5/mois)
  |  67% -> retourne Community
  |
Pro cumule -> MRR cible M6 : 20 x 99 = 1,980 EUR
```

**Outils** : Plausible (analytics GDPR, 9 EUR/mois), PostHog self-hosted (funnel, gratuit), pypistats.org, GitHub Insights, Sentry (errors, gratuit), Buttondown (newsletter, gratuit < 1000).

---

## Sources

- MarketsandMarkets, "GIS Market — Global Forecast to 2028" (2023)
- Grand View Research, "Geographic Information System Market Size Report" (2024)
- OGC Cloud-Native Geospatial Forum — cloudnativegeo.org
- DuckDB Spatial v1.0 release notes
- GeoParquet 1.0 specification — geoparquet.org
- Safe Software FME pricing — safe.com/pricing
- CARTO pricing — carto.com/pricing
- WherobotsDB pricing — wherobots.com/pricing
- ANCT — Rapport observatoire SIG des collectivites 2024
- CNIG — Standard PLU dematerialise v3.0
- Arcep — Observatoire tres haut debit Q4 2025
- Model Context Protocol spec — modelcontextprotocol.io
