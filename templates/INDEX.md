# GISPulse — Catalogue des presets

Bibliothèque de workflows métier prêts à l'emploi. Chaque preset est un fichier JSON directement consommable par le runner GISPulse.

**Référence conceptuelle** : [docs/KNOWLEDGE_BASE.md](../docs/KNOWLEDGE_BASE.md)
**Format des rules** : [docs/RULES_GUIDE.md](../docs/RULES_GUIDE.md)
**Triggers** : [docs/TRIGGERS_GUIDE.md](../docs/TRIGGERS_GUIDE.md)

---

## Presets par domaine

### Préparation / Nettoyage topologique

| Preset | Format | Complexité | Notes |
|--------|--------|------------|-------|
| [cadastre_topology_cleanup.json](cadastre_topology_cleanup.json) | v2 | ★★ | Nettoyage coverage polygonale : make_valid → snap_to_grid → polygon_snap_borders → polygon_fix_gaps → polygon_fix_overlaps → polygon_remove_slivers. Data prep avant analyse stricte |
| [reseau_topology_prep.json](reseau_topology_prep.json) | v2 | ★★ | Nettoyage graphe linéaire : network_snap_endpoints → extend_dangles → node_lines → remove_duplicates → connectivity_check → remove_pseudo_nodes. Prérequis `shortest_path`/`isochrone` |

### Télécom / FTTH

| Preset | Format | Complexité | Notes |
|--------|--------|------------|-------|
| [ftth_network_analysis.json](ftth_network_analysis.json) | v1 | ★★ | Import reseau + isochrones NRO + stats par ID_NRO + taux couverture. **⚠ utilise `network_check` → corriger en `connectivity_check`** |
| [ftth_demande_hotspots.json](ftth_demande_hotspots.json) | v2 | ★★★ | Hotspots de demande FTTH par HDBSCAN + concave_hull par grappe + distance NRO la plus proche. Priorisation déploiement |

### Urbanisme / PLU / ZAN

| Preset | Format | Complexité | Notes |
|--------|--------|------------|-------|
| [validation_plu_cnig.json](validation_plu_cnig.json) | v1 | ★★ | Topologie + attributs CNIG + complétude zonage |
| [urbanisme_zan_bilan.json](urbanisme_zan_bilan.json) | v2 | ★★★ | Bilan ZAN OCS GE T1/T2, ventilation par catégorie, trajectoire 2031 |
| [urbanisme_permis_conformite.json](urbanisme_permis_conformite.json) | v2 | ★★ | Instruction PC : zonage + servitudes + ABF + risques + Natura 2000 |

### Risques naturels

| Preset | Format | Complexité | Notes |
|--------|--------|------------|-------|
| [risques_ppri_exposition.json](risques_ppri_exposition.json) | v2 | ★★★ | Bâti + pop + ERP sensibles exposés aux PPRI, avec trigger DML auto-flag |

### Environnement / Biodiversité / Eau

| Preset | Format | Complexité | Notes |
|--------|--------|------------|-------|
| [environmental_monitoring.json](environmental_monitoring.json) | v1 | ★★★ | NDVI Sentinel-2 + zonal_stats + détection changement (nécessite plugin raster_calculate) |
| [environnement_tvb_ruptures.json](environnement_tvb_ruptures.json) | v2 | ★★★ | Trame Verte et Bleue : corridors + ruptures infrastructures + connectivity_check |
| [environnement_iota_loi_eau.json](environnement_iota_loi_eau.json) | v2 | ★★ | Pré-diagnostic IOTA : zones humides + masses d'eau + SAGE + Natura 2000 |

### Mobilité / Accessibilité

| Preset | Format | Complexité | Notes |
|--------|--------|------------|-------|
| [mobilite_accessibilite_services.json](mobilite_accessibilite_services.json) | v2 | ★★★ | Isochrones 15/30 min + pop INSEE + services BPE + zones blanches (PRO) |

### Foncier / DVF / Cadastre

| Preset | Format | Complexité | Notes |
|--------|--------|------------|-------|
| [foncier_dvf_marche.json](foncier_dvf_marche.json) | v1 | ★★ | Analyse marché DVF : prix médian/moyen/m², tension marché par IRIS |
| [foncier_parcelles_vacantes.json](foncier_parcelles_vacantes.json) | v2 | ★★★ | Détection friches : cadastre × MAJIC × PLU × DVF, score de priorité |

### Agriculture

| Preset | Format | Complexité | Notes |
|--------|--------|------------|-------|
| [agriculture_rpg_bcae.json](agriculture_rpg_bcae.json) | v2 | ★★★ | Contrôle conformité BCAE : bandes tampons BCAE 1, zones humides BCAE 9, haies BCAE 8 |

### Énergie

| Preset | Format | Complexité | Notes |
|--------|--------|------------|-------|
| [energie_solaire_toiture.json](energie_solaire_toiture.json) | v2 | ★★★★ | Potentiel solaire PV toiture : irradiation × pente × orientation × puissance × ABF (PRO + rasters requis) |

### Sécurité civile / SDIS

| Preset | Format | Complexité | Notes |
|--------|--------|------------|-------|
| [securite_sdis_couverture.json](securite_sdis_couverture.json) | v2 | ★★★ | SDACR : isochrones C1 10min / C2 20min + zones blanches + routes inondables (PRO) |
| [securite_sdis_thiessen.json](securite_sdis_thiessen.json) | v2 | ★★ | Zones de premier appel Voronoï autour des CIS + pop INSEE + ERP sensibles + niveau de tension effectif |

### Santé publique

| Preset | Format | Complexité | Notes |
|--------|--------|------------|-------|
| [sante_apl_deserts.json](sante_apl_deserts.json) | v2 | ★★★★ | APL méthode DREES : offre ETP / pop dans isochrone 20 min + double peine précarité (PRO) |

### Bâtiment / Rénovation

| Preset | Format | Complexité | Notes |
|--------|--------|------------|-------|
| [batiment_dpe_priorisation.json](batiment_dpe_priorisation.json) | v2 | ★★★ | Passoires thermiques BDNB × précarité FILOSOFI × OPAH/PIG × copropriétés fragiles |

### Retail / Géomarketing

| Preset | Format | Complexité | Notes |
|--------|--------|------------|-------|
| [retail_huff_chalandise.json](retail_huff_chalandise.json) | v2 | ★★★★ | Modèle Huff : parts de marché par IRIS + whitespace + potentiel CA zones blanches (PRO) |

---

## Usage

### CLI

```bash
# Preset v1 (rules flat)
gispulse run input.gpkg \
  --rules templates/foncier_dvf_marche.json \
  -o output/dvf_analyse.gpkg \
  --layer dvf

# Preset v2 (pipeline avec ref_layers)
gispulse run parcelles.gpkg \
  --rules templates/foncier_parcelles_vacantes.json \
  --ref-source majic:data/fichiers_fonciers.gpkg \
  --ref-source plu_zonage:data/plu.gpkg \
  --ref-source dvf:data/dvf.gpkg \
  --ref-source zones_enjeux:data/zac.gpkg \
  -o output/friches.gpkg
```

### API HTTP

```bash
curl -X POST http://localhost:8000/pipelines/run \
  -H "Content-Type: application/json" \
  -d @templates/urbanisme_zan_bilan.json
```

### Tiers et pré-requis

- **Community (default)** : tous les presets non marqués `requires_pro`
- **PRO** : presets nécessitant `isochrone`, `network_allocation`, `zonal_stats`, plugins raster
- **Plugins additionnels** : indiqués dans le champ `requires_plugins` du JSON

---

## Index par primitive

Pour retrouver rapidement un preset utilisant une capability précise :

| Capability | Presets |
|------------|---------|
| `filter` | tous |
| `buffer` | environnement_tvb_ruptures, agriculture_rpg_bcae, environnement_iota_loi_eau |
| `spatial_join` | risques_ppri, urbanisme_zan_bilan, foncier_*, agriculture_rpg_bcae, sante_apl_deserts, batiment_dpe, mobilite_accessibilite, retail_huff, urbanisme_permis |
| `intersects` | environnement_tvb_ruptures, agriculture_rpg_bcae, environnement_iota_loi_eau |
| `clip` | urbanisme_zan_bilan, environnement_iota_loi_eau |
| `dissolve` | environnement_tvb_ruptures |
| `centroid` | retail_huff_chalandise |
| `area_length` | urbanisme_zan_bilan, risques_ppri, urbanisme_permis, foncier_parcelles_vacantes, environnement_iota_loi_eau, batiment_dpe, energie_solaire_toiture |
| `calculate` | tous |
| `spatial_aggregate` | urbanisme_zan_bilan, risques_ppri, mobilite_accessibilite, foncier_dvf, sante_apl_deserts, retail_huff, batiment_dpe, environnement_tvb_ruptures, agriculture_rpg_bcae |
| `reproject` | risques_ppri, agriculture_rpg_bcae, energie_solaire_toiture, securite_sdis, mobilite_accessibilite, urbanisme_permis, batiment_dpe |
| `isochrone` | mobilite_accessibilite_services, securite_sdis_couverture, sante_apl_deserts, retail_huff_chalandise |
| `connectivity_check` | environnement_tvb_ruptures, ftth_network_analysis, reseau_topology_prep |
| `zonal_stats` | energie_solaire_toiture, environmental_monitoring |
| `topology_check` / `attribute_validation` / `completeness_check` | validation_plu_cnig, cadastre_topology_cleanup |
| `make_valid` / `snap_to_grid` | cadastre_topology_cleanup, reseau_topology_prep |
| `polygon_fix_gaps` / `polygon_fix_overlaps` / `polygon_remove_slivers` / `polygon_snap_borders` | cadastre_topology_cleanup |
| `network_snap_endpoints` / `network_extend_dangles` / `network_node_lines` / `network_remove_duplicates` / `network_remove_pseudo_nodes` | reseau_topology_prep |
| `cluster_hdbscan` | ftth_demande_hotspots |
| `concave_hull` | ftth_demande_hotspots |
| `nearest_neighbor` | ftth_demande_hotspots |
| `voronoi_polygons` | securite_sdis_thiessen |

---

## Contribution

Pour ajouter un nouveau preset :

1. Créer `templates/<domaine>_<nom>.json` (kebab_case, sans espaces).
2. Respecter la structure v1 (rules array) ou v2 (PipelineSpec) documentée dans [RULES_GUIDE.md](../docs/RULES_GUIDE.md).
3. Utiliser uniquement les capabilities du registre (voir [KNOWLEDGE_BASE.md §2](../docs/KNOWLEDGE_BASE.md#2-capabilities--référence-complète)).
4. Documenter les `ref_layers` attendus en entête (`inputs_notes`).
5. Ajouter une entrée dans la table de ce fichier.
6. Tester avec un dataset d'échantillon (idéalement dans `examples/`).
