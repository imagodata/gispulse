# Plan d'implémentation v2.3 → v2.6 (développement parallèle)

> Document de pilotage — créé le 2026-06-10. Branche d'intégration : `integration/v2.4`.
> Source : tri des issues GitHub + audit technique 2026-06-09 + analyse écosystème 2026-06-09.

## 1. Stratégie de branches

`main` = **production** (PyPI `gispulse 2.2.2`, portail déployé). On ne touche pas `main` tant qu'une release n'est pas prête.

```
main (prod, 2.2.2)
  └─ integration/v2.4   ← branche d'intégration de tout le dev v2.3 → v2.6
       ├─ claude/audit-fixes-2026-06-09   (PR #418 — hardening, à rebaser)
       ├─ fix/scheduler-pipeline-config-wiring (PR #439)
       ├─ feat/<maps-saved>, feat/<publish-multi>, ...
       └─ ...
```

Règles :
- Toutes les feature branches partent de `integration/v2.4` et y reviennent par PR.
- On merge `integration/v2.4 → main` **uniquement aux jalons release** (v2.3.0, puis v2.4.0…).
- `integration/v2.4` n'est pas couverte par le ruleset de `main` → les merges de feature n'y sont pas bloqués par le check obsolète `test (3.10)` (**issue #434**, à corriger en admin avant le merge final `integration → main`).

### PRs déjà retargetées sur `integration/v2.4`
| PR | Sujet | État | Action requise |
|----|-------|------|----------------|
| #418 | Hardening sécu (SQLi/RCE/auth/zip-slip/SSRF) + CSV/XLSX write + COG | **CONFLICTING** | Rebaser sur `integration/v2.4` et résoudre les conflits avant merge |
| #439 | Scheduler exécute réellement `pipeline_config` | MERGEABLE | Review + merge dans `integration/v2.4` |

---

## 2. Tri des issues

### 2.1 Nouvelles issues (créées 2026-06-09) — toutes OUVERTES, à développer

| # | Milestone | Sujet | Couvert par #418 ? | Reste à faire |
|---|-----------|-------|--------------------|---------------|
| #397 | v2.3.0 Hardening | SQLi operation_executor | ✅ (quote idents) | Review/merge #418 |
| #398 | v2.3.0 | RCE /marketplace/install | ✅ (validate_api_key + fail-closed) | Review/merge #418 |
| #399 | v2.3.0 | Routers sans validate_api_key | ✅ (filter/catalog/ogc/marketplace protégés) | Review/merge #418 |
| #400 | v2.3.0 | zip-slip 7z + cap upload + pyjwt | 🟡 partiel (zip-slip + cap faits ; **bumps deps NON**) | Bumper pyjwt 2.13.0 + urllib3 (PR deps séparée) |
| #401 | v2.3.0 | CI SHA-pin actions + perms GITHUB_TOKEN | ❌ | À faire — pin SHA, perms explicites, `pypi-publish@release/v1` figé |
| #402 | v2.3.0 | pg_notify : DELETE non couvert + PK `id` codée | ❌ | À faire — couvrir DELETE, PK paramétrable |
| #403 | v2.3.0 | CDC : PK composites tronquées | ❌ | À faire — `row_pk` multi-colonnes |
| #404 | v2.3.0 | Docs drift 127 caps + retirer « Tauri Stable » | ❌ | À faire — sync compteur caps + README |
| #405 | v2.4.0 Maps & Publish | API saved maps (CRUD) | ❌ | **Dev complet** |
| #406 | v2.4.0 | Publication token + read-only serveur | ❌ | **Dev complet** |
| #407 | v2.4.0 | Tuiles MVT | ❌ | **Dev complet** |
| #408 | v2.4.0 | Stats de vues (tier payant) | ❌ | **Dev complet** |
| #409 | v2.4.0 | **EPIC** éditeur web mapping | ❌ | Chapeau de #405-#408 + portal #133-#138 |
| #410 | v2.5.0 I/O & Caps | write_vector CSV + XLSX | 🟡 (CSV/TSV/XLSX WKT faits dans #418) | Vérifier portail + XLSX styling, fermer si #418 suffit |
| #411 | v2.5.0 | publish() multi-cibles postgis/s3/datamart | ❌ | **Dev complet** (lié #340) |
| #412 | v2.5.0 | PMTiles + COG first-class | 🟡 (COG fait dans #418) | **PMTiles** reste à dev |
| #413 | v2.5.0 | geocode/reverse_geocode BAN | ❌ | **Dev complet** (ROI max) |
| #414 | v2.5.0 | interpolate_idw / interpolate_tin | ❌ | **Dev complet** |
| #415 | v2.5.0 | Raster lot 1 (pont vecteur↔raster + 3 STAC) | ❌ | **Dev complet** (lié #166) |
| #416 | v2.6.0 Marketplace | Entitlements packs payants Ed25519 | ❌ | **Dev complet** (lié enterprise #643-#646) |
| #417 | v2.6.0 | Registre marketplace hébergé | ❌ | **Dev complet** (lié #215) |

### 2.2 Anciennes issues — décisions de tri

**Fermées (périmées / superseded) le 2026-06-10 :**
- **#160** — bench perf « HN body credibility » : livrable obsolète (lancement HN passé). Rouvrir ciblé si besoin.
- **#196** — packages pilotes vagues 2-3 : vague 2 (urbanisme) absorbée par data-packs réglementaires #265 ; vague 3 `gispulse-permis` a son propre repo.

**Gardées (toujours pertinentes) — reliées à la nouvelle roadmap :**
| # | Sujet | Décision |
|---|-------|----------|
| #159 | DXF read-only CDC adapter | Garder — niche, backlog I/O |
| #166 | `lidar_fetch` IGN LiDAR HD | Garder — **alimente raster lot 2** (suite de #415) |
| #167 | `export_3dtiles` py3dtiles | Garder — format de sortie universel, backlog |
| #215-222 | EPIC marketplace de templates (v1.10.0) | Garder — **complémentaire de #417** (registre hébergé), non dupliqué |
| #243 / #249 | ELT model/staging DSL + matérialisation | Garder — EPIC ouverte pour continuation Python |
| #265 / #272 | Data-packs réglementaires + harnais QA | Garder — track actif, bloque #196 (fermée) |
| #306 | `gispulse-src-osm` Overpass | Garder — connecteur réel v1.10.0 |
| #336 | WfsFetcher 400 GPU (startIndex sans sortBy) | Garder — **bug réel**, candidat quick-fix dans un sprint v2.x |
| #339 | Converger l'ingestion sur DataSource (deprecate catalog/providers) | Garder — dette d'archi, post plan provider A-K |
| #340 | Object storage S3 (Garage) + DuckDB in-place | Garder — **socle de #411** (publish s3://) et raster lot 2 |
| #434 | Ruleset `main` exige `test (3.10)` obsolète | Garder — **blocage admin** du merge final integration→main |
| #437 | DX : consommer un plugin/capability non découvrable | Garder — relié à #404 (docs) |

### 2.3 Cross-repo (rappel, hors ce repo)
- **portal v2.1.0** : EPIC #138 + #133-#137 (UI saved maps, viewer public+embed, MVT, démo read-only, CVE/CI). Front parallèle de v2.4.0.
- **portal-pro v1.3.0** : #11-#12 (brancher Pricing/Subscription, upsell TierGate).
- **enterprise v1.6.0** : #643-#646 (Stripe BillingProvider, délivrance licence Ed25519, sessions PostGIS quota-ées, SKUs+rev-share). **Bloque #416**.

---

## 3. Plan d'implémentation — par milestone

### Sprint 1 — v2.3.0 Hardening (clôture du déjà-commencé)
**Objectif** : sécuriser la surface avant d'ouvrir les fonctions publiques (cartes publiées). Pré-requis dur : #399 avant toute exposition publique.

1. **Rebaser PR #418** sur `integration/v2.4`, résoudre conflits, review, merge. Ferme #397/#398/#399 + parts de #400/#410/#412.
2. **#400 deps** : PR séparée — `pyjwt → 2.13.0`, `urllib3`, JS `react-router ≥7.15`, `fast-xml-parser`, sortir `shadcn` des deps prod (cross-repo portal).
3. **#401 CI** : SHA-pin de toutes les actions, `permissions:` explicites dans `ci.yml`/`dco.yml`, figer `pypi-publish`.
4. **#402 pg_notify** : ajouter le cas `DELETE` au trigger, rendre la colonne PK paramétrable (pas `id` en dur).
5. **#403 CDC** : `row_pk` doit concaténer toutes les colonnes de PK composite.
6. **#404 docs** : régénérer le compteur de capabilities (127), retirer « Tauri Stable » du README.

*Estimation : ~1 semaine. Dépendances : aucune externe. Cible release : merge `integration → main` → tag 2.3.0.*

### Sprint 2 — Billing E2E (cross-repo, débloque v2.6.0)
**Hors ce repo** mais à séquencer en parallèle : enterprise #643-#646 + portal-pro #11-#12.
**Blocage humain** : clé Ed25519 de production (délivrance licence #644 / #416).

### Sprint 3 — v2.4.0 Maps & Publish (EPIC #409) — cœur du parallèle
**Objectif** : socle « éditeur web mapping + publication » = différenciateur produit (modèle Felt). Backend ici, front sur portal v2.1.0.

- **#405 API saved maps** : modèle `SavedMap` (composition couches + styles QML/SLD + vue + filtres), CRUD router `saved_maps_router.py`, persistence (table `gp_saved_maps`). Réutilise le styling QML-grade existant.
- **#406 Publication** : token de permalien public + enforcement read-only serveur (réutiliser `ReadOnlyMiddleware`). Quota par tier.
- **#407 MVT** : servir des tuiles vectorielles pour les couches de carte. **Réutiliser l'encodeur MVT à moitié écrit** (`examples_router.py:500`) → le compléter et le généraliser.
- **#408 Stats de vues** : compteur de vues sur cartes publiées, gated tier payant.

*Estimation : ~2 semaines backend. Dépendances : #399 (auth) mergé. Front portal #133-#138 en parallèle.*

### Sprint 4 — v2.5.0 I/O & Capabilities
**Objectif** : combler les trous d'écriture P0 et les capabilities à fort ROI.

- **#410 CSV/XLSX** : vérifier que #418 couvre (CSV/TSV/XLSX WKT) ; ajouter XLSX si manquant, valider le chemin export portail, **fermer**.
- **#411 publish() multi-cibles** : router `publish()` vers `postgis://` (`to_postgis` existe), `s3://` (boto3 présent — lié #340), `datamart://` (DuckDB-file kind=duckdb existe). Aujourd'hui mono-cible `geonode://`.
- **#412 PMTiles** : finir l'encodeur MVT (cf. #407) → empaqueter en PMTiles. COG déjà fait (#418).
- **#413 geocode BAN** : capabilities `geocode` / `reverse_geocode` sur `data.geopf.fr/geocodage`. **ROI max** (zéro existant, DVF/PLU/foncier en dépendent).
- **#414 interpolation** : `interpolate_idw` / `interpolate_tin` (scipy/gdal).
- **#415 raster lot 1** : pont vecteur↔raster sans archi nouvelle — `raster_sample_points`, `polygonize`, `slope/aspect/hillshade` (gdal.DEMProcessing), `elevation_profile`, `rasterize`, `zonal_stats v2`, sortie COG + 3 entrées STAC (Cop-DEM GLO-30, ESA WorldCover, NASADEM).

*Estimation : ~2-3 semaines. #413 et #415 sont les quick-wins ROI à prioriser.*

### Sprint 5 — v2.6.0 Marketplace commerciale
**Objectif** : monétisation packs (modèle 80/20). **Dépend de Sprint 2 (billing) + clé Ed25519 prod.**

- **#416 Entitlements** : ajouter `extra.packs` au payload de licence Ed25519 + vérification dans `ExtensionHub` (gate pack payant). Lié enterprise #643-#646.
- **#417 Registre hébergé** : API de fiches packs/plugins au-delà du `registry.json` statique. Complémentaire de l'EPIC templates #215-222.

*Estimation : ~2 semaines. Blocage : billing E2E + clé Ed25519.*

---

## 4. Séquencement global & dépendances

```
S1 Hardening (#397-404, #418)  ──┐
                                  ├─► merge integration→main → tag 2.3.0
S2 Billing E2E (enterprise/pro) ──┘   (puis continue sur integration/v2.4)
        │
        ▼
S3 Maps & Publish (#405-409) ║ portal #133-138   → tag 2.4.0
        │
        ▼
S4 I/O & Caps (#410-415)                          → tag 2.5.0
        │
        ▼
S5 Marketplace (#416-417)  ◄── dépend S2 + clé Ed25519 → tag 2.6.0
```

**Chemin critique** : #399 (auth) → publication publique #406. Billing (S2) → marketplace (S5).

## 5. Blocages humains à lever
1. **Clé Ed25519 de production** + Trusted Publisher PyPI — bloque #416 / #644 / délivrance licence.
2. **Ruleset `main` (#434)** — corriger `test (3.10)` → `test (3.11)` en admin avant le 1er merge `integration → main`.
3. **NPM_TOKEN** (portal) — publication wheel SPA, lié à v2.1.0.
4. **Décision pricing/SKU** (#646) — rev-share packs marketplace.
