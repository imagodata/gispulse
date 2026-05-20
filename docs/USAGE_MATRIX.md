# Matrice Usages × Scénarios

Comment les utilisateurs cibles consomment GISPulse au quotidien : qui, pour quoi faire, via quel canal, et quel preset comme point de départ.

Complète [INTEGRATION_MATRIX.md](INTEGRATION_MATRIX.md) (échange de données) et [`templates/INDEX.md`](../templates/INDEX.md) (catalogue des 21 presets).

> **2026-05-20 — manifeste ELT `version: 3` (ADR 0005)** : pour les personas « pipeline-as-code » (Géomaticien collectivité, Chef de projet ZAN, Bureau d'études environnement, Géomaticien SDIS, Analyste foncier), le manifeste v3 unifie sources / triggers / modèles dans **un seul fichier déclaratif**. Voir [`docs-site/guide/elt-manifest.md`](../docs-site/guide/elt-manifest.md) et le guide de migration [`elt-migration.md`](../docs-site/guide/elt-migration.md). Les formats v1 / v2 restent supportés (dépréciés v1.10.1 → supprimés v2.0.0).

Légende :
- **Tier** : C = Community (AGPL, gratuit), P = Pro (paid features : isochrone réseau, raster lourd, ESB triggers)
- **Canal** : CLI, Portal (SPA web), QGIS (plugin), ArcGIS (OGC API), MVT (web map), Webhook (sortie événementielle), SDK (Python/JS)
- **Boucle** : One-shot (1×), Planifié (cron), DML (déclencheur sur changement)

---

## 1. Matrice Persona × Scénario × Canal

Quatorze personas représentatifs des deals déjà signés ou en discussion. Le canal recommandé est celui qui ferme la boucle le plus vite — pas forcément le seul.

> **Caveat parité 2026-05-03 (errata)** — l'audit initial a corrigé son verdict après vérification : l'activation du change-log depuis le portail (`POST /datasets/{id}/enable_tracking`) est shippée depuis v1.5.x avec auto-register `WatcherRegistry`. Donc la boucle "DML" portail-only **fonctionne déjà bout-en-bout** pour les 14 personas. Les 3 P0 restants ([`CLI_PORTAL_PARITY_AUDIT.md`](CLI_PORTAL_PARITY_AUDIT.md)) sont des features de debug / observabilité (changelog inspection, dashboard watchers, import triggers.yaml), pas des bloquants fonctionnels.

| Persona | Pain quotidien | Preset(s) | Canal principal | Boucle | Tier |
|---|---|---|---|---|---|
| **Géomaticien collectivité (DDT, EPCI)** | Instruction PC, conformité PLU, livrables CNIG | `validation_plu_cnig`, `urbanisme_permis_conformite` | QGIS plugin (drag GPKG) | Manuel | C |
| **Chef de projet ZAN** | Bilan trajectoire artificialisation T1/T2/2031 | `urbanisme_zan_bilan` | Portal + export GPKG vers QGIS | Planifié (annuel) | C |
| **Opérateur FTTH (BE déploiement)** | Priorisation NRO, hotspots demande, taux de couverture | `ftth_demande_hotspots`, `ftth_network_analysis` | CLI batch + MVT pour dashboard | Planifié (hebdo) | P (isochrone) |
| **Géomaticien SDIS** | SDACR, zones blanches secours, Voronoi CIS | `securite_sdis_couverture`, `securite_sdis_thiessen` | QGIS plugin + webhook DML | DML (nouvelle ZA) | P (isochrone) |
| **Analyste foncier (EPF, promoteur)** | Détection friches, marché DVF par IRIS | `foncier_parcelles_vacantes`, `foncier_dvf_marche` | Portal + SDK Python | Planifié (mensuel) | C |
| **Bureau d'études environnement** | TVB ruptures corridors, IOTA loi sur l'eau | `environnement_tvb_ruptures`, `environnement_iota_loi_eau` | CLI + GPKG sortie pour QGIS | One-shot (étude) | C |
| **Agent État (préfecture risques)** | Bâti / pop / ERP exposés PPRI, alertes nouvelles autorisations | `risques_ppri_exposition` | Webhook + dashboard MapLibre | DML (nouveau permis) | C |
| **Géomaticien santé publique (ARS)** | Déserts médicaux APL méthode DREES | `sante_apl_deserts` | Portal + export FileGDB ArcGIS | Planifié (trimestriel) | P (isochrone) |
| **Agriculteur / OPA / DRAAF** | Contrôle conformité BCAE 1/8/9 | `agriculture_rpg_bcae` | CLI + GPKG vers Telepac/QGIS | One-shot (avant déclaration PAC) | C |
| **Bureau d'études énergie** | Potentiel solaire toiture par bâtiment | `energie_solaire_toiture` | SDK Python (notebook) | One-shot | P (raster) |
| **Mobilité / AOM** | Accessibilité 15/30 min services BPE, zones blanches | `mobilite_accessibilite_services` | Portal + MVT public | Planifié | P (isochrone) |
| **Service rénovation énergétique (ANAH/PIG)** | Priorisation passoires DPE × précarité | `batiment_dpe_priorisation` | Portal + export Excel/GPKG | Planifié (annuel) | C |
| **Géomarketing retail** | Modèle Huff, parts de marché, whitespace | `retail_huff_chalandise` | SDK Python + dashboard interne | One-shot (étude implantation) | P (isochrone) |
| **Topographe / cadastreur** | Nettoyage géométrique avant analyse stricte | `cadastre_topology_cleanup`, `reseau_topology_prep` | CLI pre-flight + QGIS | Manuel (data prep) | C |

---

## 2. Matrice Scénario × Capabilities × Tier × Sortie

Inverse de la matrice 1 : pour chaque preset, qu'est-ce qui le rend possible et comment il se diffuse.

| Preset | Capabilities clés | Tier | Sortie | Trigger DML pertinent |
|---|---|---|---|---|
| `cadastre_topology_cleanup` | `make_valid`, `polygon_fix_*`, `polygon_remove_slivers` | C | GPKG nettoyé | non (data prep) |
| `reseau_topology_prep` | `network_snap_endpoints`, `extend_dangles`, `connectivity_check` | C | GPKG graphe | non (data prep) |
| `validation_plu_cnig` | `topology_check`, `attribute_validation`, `completeness_check` | C | rapport JSON + GPKG | sur INSERT zonage |
| `urbanisme_zan_bilan` | `clip`, `spatial_aggregate`, `area_length` | C | GPKG ventilation T1/T2 | sur ENTER zone artificialisée |
| `urbanisme_permis_conformite` | `spatial_join`, `intersects`, `area_length` | C | rapport conformité | sur INSERT permis |
| `risques_ppri_exposition` | `spatial_join`, `spatial_aggregate`, `reproject` | C | GPKG bâti exposé + alerte webhook | ✅ DML INSERT permis OU UPDATE PPRI |
| `environnement_tvb_ruptures` | `buffer`, `intersects`, `dissolve`, `connectivity_check` | C | GPKG corridors + ruptures | sur INSERT infrastructure |
| `environnement_iota_loi_eau` | `buffer`, `intersects`, `clip`, `area_length` | C | rapport IOTA | sur INSERT projet |
| `mobilite_accessibilite_services` | `isochrone`, `spatial_aggregate` | P | MVT zones blanches | planifié |
| `foncier_dvf_marche` | `spatial_aggregate` (médiane/m²) | C | GPKG par IRIS | planifié (DVF semestriel) |
| `foncier_parcelles_vacantes` | `spatial_join` cadastre×MAJIC×PLU×DVF, score | C | GPKG friches scorées | planifié + DML mutation MAJIC |
| `agriculture_rpg_bcae` | `buffer`, `intersects`, `spatial_join` | C | rapport conformité BCAE | one-shot annuel |
| `energie_solaire_toiture` | `zonal_stats` raster MNS, `area_length` | P | GPKG potentiel kWh/an/bât | one-shot |
| `securite_sdis_couverture` | `isochrone` 10/20 min, zones blanches | P | GPKG couverture + MVT | sur INSERT route inondable |
| `securite_sdis_thiessen` | `voronoi_polygons`, `spatial_aggregate` | C | GPKG zones premier appel | sur UPDATE CIS (ouverture/fermeture) |
| `sante_apl_deserts` | `isochrone` 20 min, agrégation ETP/pop | P | GPKG APL par commune | planifié |
| `batiment_dpe_priorisation` | `spatial_join` BDNB×FILOSOFI×OPAH | C | GPKG priorisation | planifié |
| `retail_huff_chalandise` | `isochrone`, modèle Huff, `centroid` | P | GPKG parts de marché par IRIS | one-shot étude |
| `ftth_network_analysis` | `connectivity_check`, isochrone NRO, `spatial_aggregate` | P | GPKG taux couverture par NRO | planifié |
| `ftth_demande_hotspots` | `cluster_hdbscan`, `concave_hull`, `nearest_neighbor` | C | GPKG hotspots scorés | planifié |
| `environmental_monitoring` | `zonal_stats` NDVI Sentinel-2, détection changement | P (raster) | GPKG diff temporel | planifié saison |

---

## 3. Matrice Canal × Boucle CDC

Pour chaque canal d'export, quel type de feedback loop GISPulse ferme. Réf : [`docs/INTEGRATION_MATRIX.md`](INTEGRATION_MATRIX.md).

| Canal | Latence | Cible | Boucle qu'il ferme |
|---|---|---|---|
| **CLI** (`gispulse run`) | secondes-minutes | terminal, scripts shell, CI | "j'ai un nouveau jeu de données → je veux le rapport tout de suite" |
| **CLI** (`gispulse watch`) | 100 ms (poll GPKG) | watcher local | "le GPKG bouge → re-évalue les triggers, write-back via DML" |
| **Portal SPA** (gispulse-portal) | quasi temps réel (WS) | navigateur, pas d'install | "métier sans QGIS veut explorer / lancer un preset / voir une carte" |
| **QGIS plugin** (v1.4) | UI interactive | bureau analyste | "je travaille déjà dans QGIS → j'attache un GPKG, je stream les résultats" |
| **OGC API Features / WFS** | pull HTTP | QGIS, ArcGIS, MapLibre | "je connecte un client GIS standard à un dataset GISPulse" |
| **MVT + TileJSON** | tile cache | dashboard web public | "je publie une couche scoree pour des dizaines / centaines de vues" |
| **Webhook (`POST /…`)** | <1 s sur DML | n8n, Zapier, ArcGIS GeoEvent, Slack/Teams | "un permis touche un PPRI → l'agent reçoit l'alerte avant le café" |
| **WebSocket `/ws/events`** | <100 ms | MapLibre live, deck.gl | "tableau de bord temps réel pour PCO / dispatch / supervision" |
| **SDK Python** (`pip install gispulse`) | API HTTP | Jupyter, pipelines internes, intégration métier | "je scripte une étude, je benchmark plusieurs scénarios" |

---

## 4. Quatre scénarios bout-en-bout

Pour la communication big-launch et les démos, quatre histoires complètes qui combinent matrices 1 + 2 + 3.

### A. Alerte PPRI temps réel (préfecture risques)

1. Dataset `permis_construire` connecté en GPKG (ou PostGIS Pro).
2. Preset `risques_ppri_exposition` chargé en pipeline.
3. Trigger DML : `INSERT permis WHERE intersects(ppri_zone)`.
4. Action : webhook → Teams de l'instructeur + entrée GeoEvent ArcGIS.
5. Latence cible : < 2 s entre le `INSERT` et l'alerte. Boucle : DML.

### B. Hotspots demande FTTH (BE déploiement)

1. Dataset hebdo `demandes_eligibilite.csv` extrait du SI commercial.
2. Preset `ftth_demande_hotspots` lancé via cron `gispulse run --once`.
3. Sortie : GPKG hotspots + MVT publié sur portal interne.
4. Vue MapLibre dans le dashboard SI commercial.
5. Boucle : Planifié.

### C. SDACR Voronoi → isochrones (SDIS)

1. Pre-flight : `reseau_topology_prep` sur le graphe routier (data prep).
2. Preset `securite_sdis_thiessen` (théorique Community).
3. Si tier Pro disponible : `securite_sdis_couverture` (réseau réel).
4. Comparaison Voronoi vs réseau dans QGIS plugin.
5. Boucle : One-shot puis DML sur `UPDATE cis` (ouverture/fermeture caserne).

### D. Bilan ZAN annuel (chef de projet EPCI)

1. Données OCS GE T1 et T2 chargées dans le portal.
2. Preset `urbanisme_zan_bilan` lancé en mode planifié (annuel).
3. Sortie : GPKG ventilation + rapport. Trajectoire 2031 calculée.
4. Diffusion : QGIS plugin pour les techniciens, MVT pour le dashboard élu.
5. Boucle : Planifié.

---

## Voir aussi

- [`templates/INDEX.md`](../templates/INDEX.md) — catalogue détaillé des 21 presets, format JSON
- [`docs/INTEGRATION_MATRIX.md`](INTEGRATION_MATRIX.md) — matrice clients GIS × modes d'échange
- [`docs/CLI_PORTAL_PARITY_AUDIT.md`](CLI_PORTAL_PARITY_AUDIT.md) — état de la dette de symétrie CLI ↔ Portal (5 P0 ouverts au 2026-05-03)
- [`docs/TRIGGERS_GUIDE.md`](TRIGGERS_GUIDE.md) — sémantique des triggers DML / contrat webhook
- [`docs-site/guide/walkthroughs/`](../docs-site/guide/walkthroughs/) — tutoriels pas-à-pas (audit, isochrone, parcels)
