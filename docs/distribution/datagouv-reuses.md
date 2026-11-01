# Fiches « réutilisation » data.gouv.fr — plugins sources GISPulse

Ce document contient 13 fiches prêtes à coller dans le formulaire « Publier une
réutilisation » de data.gouv.fr (une par plugin source français de GISPulse), un
tableau récapitulatif des jeux de données liés, et un volet Belgique.
Rédigé le 2026-11-01. Slugs et identifiants résolus avec l'API publique
`https://www.data.gouv.fr/api/1/` ce jour-là.

## Procédure

1. **[COMPTE PROPRIÉTAIRE]** Se connecter sur data.gouv.fr avec un compte
   utilisateur (ou, mieux, avec une organisation « Imagodata » créée et certifiée
   pour que la réutilisation soit affichée au nom de l'organisation ; champ
   `organization` de l'API).
2. Ouvrir le formulaire : `https://www.data.gouv.fr/admin/reuses/new` (l'ancien
   chemin `/fr/admin/reuse/new/` redirige vers la connexion puis vers ce
   formulaire ; vérifié par `curl` : redirection vers
   `/login?next=%2Fadmin%2Freuses%2Fnew`).
3. Remplir les champs avec la fiche : **Titre**, **Type**, **Thématique**,
   **Description**, **URL**, **Tags**, puis lier les **jeux de données** (et
   services de données) listés dans la fiche en les cherchant par leur titre.
4. Publier. Les réutilisations sont publiques ; elles peuvent être modifiées après
   coup.

Champs obligatoires côté API (`Reuse (write)` du Swagger) : `title`,
`description`, `type`, `topic`, `url`. Le champ `topic` (thématique) est donc
requis en plus de ce qui était prévu ; la valeur proposée est dans chaque fiche.

Types de réutilisation disponibles (`/api/1/reuses/types/`) : `api`,
`application`, `idea`, `news_article`, `paper`, `post`, `visualization`,
`hardware` (« Objet connecté »). **Toutes les fiches utilisent `application`**
(« Application »), qui est le plus juste pour un outil logiciel.

Les réutilisations peuvent aussi lier des **services de données** (champ
`dataservices` de l'API) : c'est le seul lien possible pour GPU et SUP, faute de
jeu national.

Ligne d'installation commune (le plugin est dans le dépôt, pas dans le wheel
`gispulse`) :

```bash
pipx install gispulse
pipx inject gispulse "gispulse-src-<nom> @ git+https://github.com/imagodata/gispulse@v2.6.0#subdirectory=plugins/gispulse-src-<nom>"
```

Équivalent `pip` : `pip install "gispulse-src-<nom> @ git+https://github.com/imagodata/gispulse@v2.6.0#subdirectory=plugins/gispulse-src-<nom>"`
(motif identique à celui des notes de version).

URL : les pages `https://imagodata.github.io/gispulse/plugins/...` existent pour
`src-cadastre`, `src-dvf`, `src-gpu` et `src-ign` (HTTP 200 vérifié). Pour les
neuf autres plugins il n'y a pas de page dans `docs-site/plugins/` : la fiche
pointe vers le dossier du plugin sur GitHub.

---

## 1. BAN — `gispulse-src-ban`

- **Titre** (93 car.) : GISPulse — BAN : adresses géocodées prêtes à croiser avec bâtiments et cadastre (open source)
- **Type** : Application (`application`)
- **Thématique** : `housing_and_development`
- **URL** : https://github.com/imagodata/gispulse/tree/main/plugins/gispulse-src-ban  (dossier du plugin ; pas de page docs-site)
- **Tags** : ban, adresse, geocodage, open-source, duckdb, sig, python
- **Jeux de données / services à lier** :
  - `base-adresse-nationale` (id `5530fbacc751df5ff937dddb`) — Base Adresse Nationale (producteur : Base Adresse Nationale) — jeu de données
  - `api-geoplateforme-geocodage` (id `691c3f1bf99cf33e91bfb7d0`) — API Géoplateforme - Géocodage (IGN) — service de données (API)
- **Description** (630 car., à coller) :

````markdown
GISPulse (SIG open source, DuckDB) : le plugin **gispulse-src-ban** interroge le géocodeur de la Géoplateforme (recherche et reverse sur la Base Adresse Nationale) et ingère les exports CSV par département (`adresses-{departement}.csv.gz`). Les identifiants BAN servent de clé de jointure vers le RNB, la BDNB ou le cadastre.

Templates : `ftth_demande_hotspots` (hotspots de demande FTTH).

```
pipx install gispulse
pipx inject gispulse "gispulse-src-ban @ git+https://github.com/imagodata/gispulse@v2.6.0#subdirectory=plugins/gispulse-src-ban"
```
Docs : https://github.com/imagodata/gispulse/tree/main/plugins/gispulse-src-ban
````

## 2. BDNB — `gispulse-src-bdnb`

- **Titre** (85 car.) : GISPulse — BDNB : parc de bâtiments et passoires thermiques, prêts à croiser (DuckDB)
- **Type** : Application (`application`)
- **Thématique** : `housing_and_development`
- **URL** : https://github.com/imagodata/gispulse/tree/main/plugins/gispulse-src-bdnb  (dossier du plugin ; pas de page docs-site)
- **Tags** : bdnb, batiment, dpe, renovation-energetique, open-source, duckdb, sig
- **Jeux de données / services à lier** :
  - `base-de-donnees-nationale-des-batiments` (id `61dc7157488f8cdb4283e3c3`) — Base de données nationale des bâtiments (CSTB) — jeu de données
- **Description** (635 car., à coller) :

````markdown
GISPulse (SIG open source, DuckDB) : le plugin **gispulse-src-bdnb** télécharge les archives départementales de la BDNB (millésime courant, GeoPackage des bâtiments et tables CSV `batiment_groupe`) et les expose en tables DuckDB, jointes aux autres sources par identifiant de bâtiment.

Templates : `batiment_dpe_priorisation` (priorisation rénovation énergétique), `energie_solaire_toiture`.

```
pipx install gispulse
pipx inject gispulse "gispulse-src-bdnb @ git+https://github.com/imagodata/gispulse@v2.6.0#subdirectory=plugins/gispulse-src-bdnb"
```
Docs : https://github.com/imagodata/gispulse/tree/main/plugins/gispulse-src-bdnb
````

## 3. BODACC — `gispulse-src-bodacc`

- **Titre** (94 car.) : GISPulse — BODACC : annonces légales d'entreprises exploitables en SIG et en SQL (open source)
- **Type** : Application (`application`)
- **Thématique** : `economy_and_business`
- **URL** : https://github.com/imagodata/gispulse/tree/main/plugins/gispulse-src-bodacc  (dossier du plugin ; pas de page docs-site)
- **Tags** : bodacc, entreprises, annonces-legales, dila, open-source, sql, duckdb
- **Jeux de données / services à lier** :
  - `bodacc` (id `559395f1c751df0f51a453b9`) — BODACC (Premier ministre / DILA) — jeu de données
  - `api-bulletin-officiel-des-annonces-civiles-et-commerciales-bodacc` (id `672cf66ea47b8d4c15d13d5b`) — API BODACC (DILA) — service de données (API)
- **Description** (708 car., à coller) :

````markdown
GISPulse (SIG open source, DuckDB) : le plugin **gispulse-src-bodacc** interroge l'API Opendatasoft du BODACC (annonces commerciales, ventes et cessions, procédures collectives, dépôts de comptes, immatriculations, radiations) page par page, avec filtres par registre, famille et date, pour croiser les annonces avec des données territoriales.

Templates : aucun template embarqué à ce jour (source disponible via le catalogue et les pipelines personnalisés).

```
pipx install gispulse
pipx inject gispulse "gispulse-src-bodacc @ git+https://github.com/imagodata/gispulse@v2.6.0#subdirectory=plugins/gispulse-src-bodacc"
```
Docs : https://github.com/imagodata/gispulse/tree/main/plugins/gispulse-src-bodacc
````

## 4. Cadastre — `gispulse-src-cadastre`

- **Titre** (89 car.) : GISPulse — Cadastre : parcelles à nettoyer, croiser avec DVF et PLU (DuckDB, open source)
- **Type** : Application (`application`)
- **Thématique** : `housing_and_development`
- **URL** : https://imagodata.github.io/gispulse/plugins/src-cadastre
- **Tags** : cadastre, parcelle, parcellaire-express, topologie, open-source, duckdb, sig
- **Jeux de données / services à lier** :
  - `cadastre` (id `59b0020ec751df07d5f13bcf`) — Cadastre (data.gouv.fr / Etalab) — jeu de données
  - `parcellaire-express-pci` (id `61489f2f2de14040f348fa0b`) — Parcellaire Express (PCI) (IGN) — jeu de données
  - `api-geoplateforme-diffusion-dobjets-wfs` (id `691c3d09ef6f52a7d97d632b`) — API Géoplateforme - Diffusion d'objets WFS (IGN) — service de données (API)
- **Note** : Le plugin lit le WFS Parcellaire Express de l'IGN et le cadastre Etalab ; le jeu `cadastre` (data.gouv.fr) est celui dont l'API de revision du plugin interroge les métadonnées.
- **Description** (691 car., à coller) :

````markdown
GISPulse (SIG open source, DuckDB) : le plugin **gispulse-src-cadastre** lit les parcelles, communes et bâtiments du Parcellaire Express (WFS de la Géoplateforme) et le cadastre Etalab (GeoJSON par département). Le template de nettoyage répare géométries, gaps, chevauchements et slivers avant toute analyse stricte.

Templates : `cadastre_topology_cleanup` (nettoyage topologique de la couverture), `foncier_parcelles_vacantes`, `risques_ppri_exposition`.

```
pipx install gispulse
pipx inject gispulse "gispulse-src-cadastre @ git+https://github.com/imagodata/gispulse@v2.6.0#subdirectory=plugins/gispulse-src-cadastre"
```
Docs : https://imagodata.github.io/gispulse/plugins/src-cadastre
````

## 5. DVF — `gispulse-src-dvf`

- **Titre** (78 car.) : GISPulse — DVF prêt à croiser avec le cadastre et le PLU (DuckDB, open source)
- **Type** : Application (`application`)
- **Thématique** : `housing_and_development`
- **URL** : https://imagodata.github.io/gispulse/plugins/src-dvf
- **Tags** : dvf, valeurs-foncieres, immobilier, cadastre, plu, open-source, duckdb
- **Jeux de données / services à lier** :
  - `demandes-de-valeurs-foncieres` (id `5c4ae55a634f4117716d5656`) — Demandes de valeurs foncières (Ministères économiques et financiers / DGFiP) — jeu de données
  - `demandes-de-valeurs-foncieres-geolocalisees` (id `5cc1b94a634f4165e96436c1`) — Demandes de valeurs foncières géolocalisées (Etalab, fichiers réellement lus) — jeu de données
- **Note** : Le plugin lit les fichiers de `files.data.gouv.fr/geo-dvf`, rattachés au jeu « géolocalisées » (producteur affiché : data.gouv.fr/Etalab). Le jeu amont est celui de la DGFiP (Ministères économiques et financiers). Lier les deux.
- **Description** (660 car., à coller) :

````markdown
GISPulse (SIG open source, DuckDB) : le plugin **gispulse-src-dvf** scanne en DuckDB les fichiers DVF géolocalisés d'Etalab (CSV par département ou national, fenêtre glissante de 5 ans) avec filtre d'emprise, et reconstitue section et numéro de plan depuis `id_parcelle` pour la jointure cadastrale.

Templates : `foncier_dvf_marche` (prix médians et tension par IRIS), `foncier_parcelles_vacantes` (friches : cadastre × MAJIC × PLU × DVF).

```
pipx install gispulse
pipx inject gispulse "gispulse-src-dvf @ git+https://github.com/imagodata/gispulse@v2.6.0#subdirectory=plugins/gispulse-src-dvf"
```
Docs : https://imagodata.github.io/gispulse/plugins/src-dvf
````

## 6. Géorisques — `gispulse-src-georisques`

- **Titre** (89 car.) : GISPulse — Géorisques : risques par commune et par point, croisés avec le bâti et le PPRI
- **Type** : Application (`application`)
- **Thématique** : `environment_and_energy`
- **URL** : https://github.com/imagodata/gispulse/tree/main/plugins/gispulse-src-georisques  (dossier du plugin ; pas de page docs-site)
- **Tags** : georisques, risques-naturels, ppri, inondation, argiles, open-source, duckdb
- **Jeux de données / services à lier** :
  - `base-nationale-de-gestion-assistee-des-procedures-administratives-relatives-aux-risques-gaspar` (id `536995eea3a729239d20486b`) — GASPAR (Ministère de la Transition écologique) — jeu de données
  - `aleas-retraits-et-gonflements-des-argiles-en-france-metropolitaine-hors-ville-de-paris` (id `66631f6c5ff38efa62d385ab`) — Aléas retrait-gonflement des argiles (BRGM) — jeu de données
  - `zonage-en-potentiel-radon-2` (id `683e3b0b4914b4ba0d2f175a`) — Zonage en potentiel radon (BRGM) — jeu de données
  - `territoire-a-risque-dinondation-tri-du-sig-directive-inondation-france-metropolitaine-rapportage-2020-241` (id `666028132e577b5a3e9fc8f5`) — TRI Directive inondation, France métropolitaine, rapportage 2020 (BRGM) — jeu de données
  - `obligations-reglementaires-liees-aux-parcelles-cadastrales-secteurs-dinformation-sur-les-sols-sis` (id `66602812be9c808ce59fc8f6`) — Secteurs d'information sur les sols - SIS (BRGM) — jeu de données
  - `api-georisques` (id `672cf9b96136ac3468829100`) — API Géorisques (BRGM) — service de données (API)
- **Note** : Le zonage sismique et la RGA ponctuelle sont lus via l'API Géorisques ; aucun jeu national officiel du zonage sismique n'a été trouvé sur data.gouv.fr (seuls des extraits départementaux). Le service de données API Géorisques couvre ces appels.
- **Description** (691 car., à coller) :

````markdown
GISPulse (SIG open source, DuckDB) : le plugin **gispulse-src-georisques** interroge l'API Géorisques (risques GASPAR, radon, zonage sismique, retrait-gonflement des argiles, TRI, sites et sols pollués) et ingère les exports en masse (RGA, TRI 2020, SIS, procédures GASPAR). Les signaux sont bruts ; les règles métier se posent dans les pipelines.

Templates : `risques_ppri_exposition` (bâti, population et ERP exposés aux PPRI).

```
pipx install gispulse
pipx inject gispulse "gispulse-src-georisques @ git+https://github.com/imagodata/gispulse@v2.6.0#subdirectory=plugins/gispulse-src-georisques"
```
Docs : https://github.com/imagodata/gispulse/tree/main/plugins/gispulse-src-georisques
````

## 7. GPU — `gispulse-src-gpu`

- **Titre** (86 car.) : GISPulse — GPU : zonages PLU et prescriptions en SIG, avec contrôle CNIG (open source)
- **Type** : Application (`application`)
- **Thématique** : `housing_and_development`
- **URL** : https://imagodata.github.io/gispulse/plugins/src-gpu
- **Tags** : plu, urbanisme, gpu, cnig, zonage, open-source, duckdb
- **Jeux de données / services à lier** :
  - `api-carto-module-geoportail-de-lurbanisme-gpu` (id `672cf67520c9ae9747b4015c`) — API Carto - module Géoportail de l'Urbanisme (IGN) — service de données (API)
  - `api-geoplateforme-diffusion-dobjets-wfs` (id `691c3d09ef6f52a7d97d632b`) — API Géoplateforme - Diffusion d'objets WFS (IGN) — service de données (API)
- **Note** : Aucun jeu de données national du Géoportail de l'Urbanisme n'a été trouvé sur data.gouv.fr (les résultats sont des extraits départementaux ou régionaux publiés par des DDT et DREAL). Seuls les services de données IGN sont liés ; ne pas lier de jeu départemental à titre de source officielle.
- **Description** (674 car., à coller) :

````markdown
GISPulse (SIG open source, DuckDB) : le plugin **gispulse-src-gpu** lit les couches du Géoportail de l'Urbanisme via le WFS de la Géoplateforme (zones U/AU/A/N, documents d'urbanisme, secteurs de carte communale, prescriptions et informations surfaciques, linéaires et ponctuelles), jointes par `idurba`.

Templates : `validation_plu_cnig` (topologie et attributs CNIG), `urbanisme_zan_bilan`, `urbanisme_permis_conformite`, `foncier_parcelles_vacantes`.

```
pipx install gispulse
pipx inject gispulse "gispulse-src-gpu @ git+https://github.com/imagodata/gispulse@v2.6.0#subdirectory=plugins/gispulse-src-gpu"
```
Docs : https://imagodata.github.io/gispulse/plugins/src-gpu
````

## 8. IGN — `gispulse-src-ign`

- **Titre** (89 car.) : GISPulse — BD TOPO et Admin Express en pipelines SIG reproductibles (DuckDB, open source)
- **Type** : Application (`application`)
- **Thématique** : `open_data_tools`
- **URL** : https://imagodata.github.io/gispulse/plugins/src-ign
- **Tags** : ign, bd-topo, admin-express, wfs, geoplateforme, open-source, duckdb
- **Jeux de données / services à lier** :
  - `bd-topo-r` (id `61489083dc4223219e50cc35`) — BD TOPO® (IGN) — jeu de données
  - `admin-express-admin-express-cog-admin-express-cog-carto-admin-express-cog-carto-pe-admin-express-cog-carto-plus-pe` (id `5808de39c751df1e0679df72`) — ADMIN EXPRESS (IGN) — jeu de données
  - `api-geoplateforme-diffusion-dobjets-wfs` (id `691c3d09ef6f52a7d97d632b`) — API Géoplateforme - Diffusion d'objets WFS (IGN) — service de données (API)
- **Description** (737 car., à coller) :

````markdown
GISPulse (SIG open source, DuckDB) : le plugin **gispulse-src-ign** interroge le WFS de la Géoplateforme pour les bâtiments, tronçons de route et cours d'eau de la BD TOPO v3 et pour les communes, départements et régions d'Admin Express. Ces couches de référence alimentent la plupart des templates (isochrones, zones de couverture, bâti).

Templates : `agriculture_rpg_bcae`, `securite_sdis_couverture`, `energie_solaire_toiture`, `ftth_demande_hotspots`, `retail_huff_chalandise`, `mobilite_accessibilite_services`.

```
pipx install gispulse
pipx inject gispulse "gispulse-src-ign @ git+https://github.com/imagodata/gispulse@v2.6.0#subdirectory=plugins/gispulse-src-ign"
```
Docs : https://imagodata.github.io/gispulse/plugins/src-ign
````

## 9. INSEE — `gispulse-src-insee`

- **Titre** (95 car.) : GISPulse — INSEE : contours IRIS et indicateurs prêts à croiser (population, logement, revenus)
- **Type** : Application (`application`)
- **Thématique** : `society_and_demography`
- **URL** : https://github.com/imagodata/gispulse/tree/main/plugins/gispulse-src-insee  (dossier du plugin ; pas de page docs-site)
- **Tags** : insee, iris, recensement, filosofi, demographie, open-source, duckdb
- **Jeux de données / services à lier** :
  - `iris-ge` (id `61489e33b77d1697d00d2fec`) — Iris...GE (IGN) — jeu de données
  - `contours-iris-r-2` (id `55424b64c751df766fa7b26f`) — Contours... IRIS® (IGN) — jeu de données
  - `population-principaux-indicateurs-iris` (id `6ad16a7506eea90157a47a35`) — Population - Principaux indicateurs - Iris (Insee) — jeu de données
  - `logements-principaux-indicateurs-iris` (id `6ad16a7006eea90157a47a2f`) — Logements - Principaux indicateurs - Iris (Insee) — jeu de données
  - `menages-principaux-indicateurs-iris` (id `6ad16a7206eea90157a47a32`) — Ménages - Principaux indicateurs - Iris (Insee) — jeu de données
  - `population-active-et-chomage-principaux-indicateurs-iris` (id `6ad16a6e06eea90157a47a2c`) — Population active et chômage - Principaux indicateurs - Iris (Insee) — jeu de données
  - `education-scolarisation-principaux-indicateurs-iris` (id `6ad16a6d06eea90157a47a2b`) — Éducation - Scolarisation - Principaux indicateurs - Iris (Insee) — jeu de données
- **Note** : Écart de millésime : le plugin lit les fichiers `base-ic-*-2022` du site insee.fr (RP 2022) et Filosofi IRIS 2021 ; les jeux data.gouv.fr de l'Insee sont la nouvelle série « Principaux indicateurs - Iris » (RP 2023, via l'API Melodi). Ce sont les jeux correspondants (même thème, millésime plus récent), pas les fichiers exacts. Aucun jeu Filosofi IRIS de l'Insee n'a été trouvé sur data.gouv.fr (la source reste insee.fr/fr/statistiques/8229323). À mentionner dans la description si le formulaire l'exige.
- **Description** (804 car., à coller) :

````markdown
GISPulse (SIG open source, DuckDB) : le plugin **gispulse-src-insee** charge les contours IRIS (WFS ou archives IRIS-GE par département) et les tables du recensement et de Filosofi par IRIS (population, logement, ménages, activité, diplômes, revenus). La clé de jointure est `code_iris`, sans aucune géométrie inventée dans les tables.

Templates : `foncier_dvf_marche`, `mobilite_accessibilite_services`, `retail_huff_chalandise`, `sante_apl_deserts`, `securite_sdis_couverture`, `securite_sdis_thiessen`, `urbanisme_zan_bilan`, `batiment_dpe_priorisation`.

```
pipx install gispulse
pipx inject gispulse "gispulse-src-insee @ git+https://github.com/imagodata/gispulse@v2.6.0#subdirectory=plugins/gispulse-src-insee"
```
Docs : https://github.com/imagodata/gispulse/tree/main/plugins/gispulse-src-insee
````

## 10. Loyers — `gispulse-src-loyers`

- **Titre** (77 car.) : GISPulse — Carte des loyers et zonage TLV prêts à croiser avec DVF et le bâti
- **Type** : Application (`application`)
- **Thématique** : `housing_and_development`
- **URL** : https://github.com/imagodata/gispulse/tree/main/plugins/gispulse-src-loyers  (dossier du plugin ; pas de page docs-site)
- **Tags** : loyers, immobilier, tlv, zone-tendue, logement, open-source, duckdb
- **Jeux de données / services à lier** :
  - `carte-des-loyers-indicateurs-de-loyers-dannonce-par-commune-en-2025` (id `693aa2feed1bf4da603faa49`) — Carte des loyers 2025 (Ministère de la Transition écologique) — jeu de données
  - `liste-des-communes-selon-le-zonage-tlv-1` (id `657c88da2947e13be0597058`) — Liste des communes selon le zonage TLV (Ministère de la Transition écologique) — jeu de données
- **Description** (681 car., à coller) :

````markdown
GISPulse (SIG open source, DuckDB) : le plugin **gispulse-src-loyers** charge les indicateurs de loyers d'annonce par commune (appartement, 1-2 pièces, 3 pièces et plus, maison) et le zonage des communes tendues (TLV) publiés sur data.gouv.fr, avec la clé de jointure `INSEE_C`. Les estimations ANIL reposent sur les annonces SeLoger et leboncoin.

Templates : aucun template embarqué à ce jour (source disponible via le catalogue).

```
pipx install gispulse
pipx inject gispulse "gispulse-src-loyers @ git+https://github.com/imagodata/gispulse@v2.6.0#subdirectory=plugins/gispulse-src-loyers"
```
Docs : https://github.com/imagodata/gispulse/tree/main/plugins/gispulse-src-loyers
````

## 11. Nature — `gispulse-src-nature`

- **Titre** (84 car.) : GISPulse — Natura 2000 et ZNIEFF dans des pipelines SIG reproductibles (open source)
- **Type** : Application (`application`)
- **Thématique** : `environment_and_energy`
- **URL** : https://github.com/imagodata/gispulse/tree/main/plugins/gispulse-src-nature  (dossier du plugin ; pas de page docs-site)
- **Tags** : natura2000, znieff, biodiversite, trame-verte-et-bleue, open-source, duckdb, sig
- **Jeux de données / services à lier** :
  - `inpn-donnees-du-programme-natura-2000` (id `559a6bb1c751df57a5390bd3`) — INPN - Natura 2000 (MNHN) — jeu de données
  - `inpn-donnees-du-programme-znieff` (id `559b9561c751df301b390bdc`) — INPN - ZNIEFF (MNHN) — jeu de données
  - `api-carto-module-nature` (id `672cf64bde9cfb952eab5d10`) — API Carto - module Nature (IGN) — service de données (API)
- **Description** (683 car., à coller) :

````markdown
GISPulse (SIG open source, DuckDB) : le plugin **gispulse-src-nature** interroge l'API Carto Nature de l'IGN avec l'emprise courante pour obtenir les sites Natura 2000 (directives Habitats et Oiseaux) et les ZNIEFF de type 1 et 2, utilisés comme réservoirs de biodiversité ou contraintes dans les templates.

Templates : `environnement_tvb_ruptures` (Trame Verte et Bleue), `environnement_iota_loi_eau`, `urbanisme_permis_conformite`.

```
pipx install gispulse
pipx inject gispulse "gispulse-src-nature @ git+https://github.com/imagodata/gispulse@v2.6.0#subdirectory=plugins/gispulse-src-nature"
```
Docs : https://github.com/imagodata/gispulse/tree/main/plugins/gispulse-src-nature
````

## 12. RNB — `gispulse-src-rnb`

- **Titre** (80 car.) : GISPulse — RNB : identité des bâtiments reliée aux adresses BAN et aux parcelles
- **Type** : Application (`application`)
- **Thématique** : `housing_and_development`
- **URL** : https://github.com/imagodata/gispulse/tree/main/plugins/gispulse-src-rnb  (dossier du plugin ; pas de page docs-site)
- **Tags** : rnb, batiment, identifiant, adresse, ban, open-source, duckdb
- **Jeux de données / services à lier** :
  - `referentiel-national-des-batiments` (id `65a5568dfc88169d0a5416ca`) — Référentiel National des Bâtiments (RNB) — jeu de données
  - `api-rnb` (id `6661e98d206735b6dabb623a`) — API RNB — service de données (API)
- **Description** (627 car., à coller) :

````markdown
GISPulse (SIG open source, DuckDB) : le plugin **gispulse-src-rnb** interroge l'API du Référentiel National des Bâtiments par emprise, par parcelle cadastrale ou par clé d'adresse BAN, avec pagination. L'identifiant `rnb_id` sert de pivot d'identité du bâtiment pour joindre BDNB, cadastre et adresses.

Templates : aucun template embarqué à ce jour (source disponible via le catalogue).

```
pipx install gispulse
pipx inject gispulse "gispulse-src-rnb @ git+https://github.com/imagodata/gispulse@v2.6.0#subdirectory=plugins/gispulse-src-rnb"
```
Docs : https://github.com/imagodata/gispulse/tree/main/plugins/gispulse-src-rnb
````

## 13. SUP — `gispulse-src-sup`

- **Titre** (83 car.) : GISPulse — Servitudes d'utilité publique (SUP) en couches SIG : ABF, PPR, assiettes
- **Type** : Application (`application`)
- **Thématique** : `housing_and_development`
- **URL** : https://github.com/imagodata/gispulse/tree/main/plugins/gispulse-src-sup  (dossier du plugin ; pas de page docs-site)
- **Tags** : servitudes, sup, abf, ppr, urbanisme, open-source, duckdb
- **Jeux de données / services à lier** :
  - `api-carto-module-geoportail-de-lurbanisme-gpu` (id `672cf67520c9ae9747b4015c`) — API Carto - module Géoportail de l'Urbanisme (IGN) — service de données (API)
  - `api-geoplateforme-diffusion-dobjets-wfs` (id `691c3d09ef6f52a7d97d632b`) — API Géoplateforme - Diffusion d'objets WFS (IGN) — service de données (API)
- **Note** : Même constat que pour GPU : pas de jeu national des SUP du GPU sur data.gouv.fr. Un jeu BRGM « Servitude d'Utilité Publique (SUP) » par parcelle existe mais n'est pas consommé par le plugin ; il n'est pas lié.
- **Description** (716 car., à coller) :

````markdown
GISPulse (SIG open source, DuckDB) : le plugin **gispulse-src-sup** lit les servitudes d'utilité publique du Géoportail de l'Urbanisme via le WFS de la Géoplateforme (actes, assiettes et générateurs surfaciques, linéaires, ponctuels), avec deux vues filtrées : abords des monuments historiques (AC1, AC2, AC4) et zonages de plans de prévention des risques (PM1, PM3).

Templates : `urbanisme_permis_conformite` (instruction PC : zonage, servitudes, ABF, risques, Natura 2000).

```
pipx install gispulse
pipx inject gispulse "gispulse-src-sup @ git+https://github.com/imagodata/gispulse@v2.6.0#subdirectory=plugins/gispulse-src-sup"
```
Docs : https://github.com/imagodata/gispulse/tree/main/plugins/gispulse-src-sup
````

## Récapitulatif plugin → jeux de données

| Plugin | Slugs des jeux de données | Services de données (slugs) |
|---|---|---|
| ban | `base-adresse-nationale` | `api-geoplateforme-geocodage` |
| bdnb | `base-de-donnees-nationale-des-batiments` | — |
| bodacc | `bodacc` | `api-bulletin-officiel-des-annonces-civiles-et-commerciales-bodacc` |
| cadastre | `cadastre`, `parcellaire-express-pci` | `api-geoplateforme-diffusion-dobjets-wfs` |
| dvf | `demandes-de-valeurs-foncieres`, `demandes-de-valeurs-foncieres-geolocalisees` | — |
| georisques | `base-nationale-de-gestion-assistee-des-procedures-administratives-relatives-aux-risques-gaspar`, `aleas-retraits-et-gonflements-des-argiles-en-france-metropolitaine-hors-ville-de-paris`, `zonage-en-potentiel-radon-2`, `territoire-a-risque-dinondation-tri-du-sig-directive-inondation-france-metropolitaine-rapportage-2020-241`, `obligations-reglementaires-liees-aux-parcelles-cadastrales-secteurs-dinformation-sur-les-sols-sis` | `api-georisques` |
| gpu | — | `api-carto-module-geoportail-de-lurbanisme-gpu`, `api-geoplateforme-diffusion-dobjets-wfs` |
| ign | `bd-topo-r`, `admin-express-admin-express-cog-admin-express-cog-carto-admin-express-cog-carto-pe-admin-express-cog-carto-plus-pe` | `api-geoplateforme-diffusion-dobjets-wfs` |
| insee | `iris-ge`, `contours-iris-r-2`, `population-principaux-indicateurs-iris`, `logements-principaux-indicateurs-iris`, `menages-principaux-indicateurs-iris`, `population-active-et-chomage-principaux-indicateurs-iris`, `education-scolarisation-principaux-indicateurs-iris` | — |
| loyers | `carte-des-loyers-indicateurs-de-loyers-dannonce-par-commune-en-2025`, `liste-des-communes-selon-le-zonage-tlv-1` | — |
| nature | `inpn-donnees-du-programme-natura-2000`, `inpn-donnees-du-programme-znieff` | `api-carto-module-nature` |
| rnb | `referentiel-national-des-batiments` | `api-rnb` |
| sup | — | `api-carto-module-geoportail-de-lurbanisme-gpu`, `api-geoplateforme-diffusion-dobjets-wfs` |

> Les templates cités dans chaque fiche ont été repérés par les noms de sources
> présents dans `templates/*.json` et `templates/INDEX.md` ; relire le template
> concerné avant publication pour confirmer le câblage exact. Les plugins `loyers`,
> `rnb` et `bodacc` n'ont aucun template embarqué.

## Belgique

Plugins concernés : `gispulse-src-grb` (Flandre), `gispulse-src-picc` (Wallonie),
`gispulse-src-urbis` (Bruxelles), `gispulse-src-statbel-be`, `gispulse-src-gipod-be`.
Constat général : **aucun portail belge n'offre un mécanisme d'inscription de
réutilisations aussi direct que data.gouv.fr**. Seule la Flandre a une procédure
documentée (e-mail). Le reste passe par un contact. Données vérifiées le 2026-11-01 ;
deux portails (data.gouv.be, Statbel) sont protégés par un contrôle anti-robot et
n'ont pas pu être lus.

Texte réutilisable (FR/EN à adapter, ≤ 600 caractères) :

> GISPulse est un moteur de règles SIG open source (AGPL, DuckDB). Ses plugins
> sources belges lisent les données ouvertes régionales (GRB, PICC, UrbIS, GIPOD,
> Statbel) et calculent, par exemple, des traversées de voirie avec niveau relatif
> (sol, tunnel, pont). Installation : `pipx install gispulse`, puis le plugin voulu
> depuis `https://github.com/imagodata/gispulse` (tag v2.6.0). Source ouverte,
> sans client SIG, reproductible en ligne de commande.

| Source | Plugin | Producteur / portail | Mécanisme de référencement des réutilisations | Contact / URL à utiliser |
|---|---|---|---|---|
| GRB (Flandre) | `gispulse-src-grb`, `gispulse-src-gipod-be` (GIPOD) | Digitaal Vlaanderen, Datavindplaats | **Oui, par e-mail.** La section « Toepassingen » de Datavindplaats (`https://www.vlaanderen.be/datavindplaats/toepassingen-op-basis-van-open-data`) liste des applications ; la page `https://www.vlaanderen.be/datavindplaats/uw-data-en-services-hier` indique d'envoyer à `digitaal.vlaanderen@vlaanderen.be` : nom de l'application, courte description, capture d'écran nette, liens vers un ou plusieurs jeux ou services de Datavindplaats. Pour une application **commerciale**, conditions supplémentaires (neutralité commerciale, deux contacts joignables, mise à jour, contrôle annuel, avertissement publié). | E-mail ci-contre. GISPulse est open source (hors cas commercial). À citer : GRB `https://geo.api.vlaanderen.be/GRB/wfs`, GIPOD `https://geo.api.vlaanderen.be/GIPOD/ogc/features/v1`. |
| PICC (Wallonie) | `gispulse-src-picc` | Service public de Wallonie, Géoportail de la Wallonie (`https://geoportail.wallonie.be`) ; données de Wallonie-Bruxelles via ODWB (`https://www.odwb.be`) | **Pas de registre de réutilisations trouvé.** Aucune section « applications » sur le Géoportail ; `/applications` renvoie 404 ; les pages ODWB `/pages/reutilisations` et `/pages/applications` renvoient 404. Existe un « Club des utilisateurs du PICC » (réunion annoncée le 8 décembre 2026 à Namur) : occasion de présenter l'outil. | Page Contact du Géoportail (`https://geoportail.wallonie.be/contact`, annoncée par le site ; l'adresse e-mail n'est pas affichée). Pas d'adresse inventée ici. Source technique utilisée : `https://geoservices.wallonie.be/arcgis/rest/services/TOPOGRAPHIE/PICC_VDIFF/MapServer`. |
| UrbIS (Bruxelles) | `gispulse-src-urbis` | Paradigm, `datastore.brussels` | **Pas de registre de réutilisations trouvé.** La page régionale « valorisation des données régionales » (`https://be.brussels/fr/propos-de-la-region/publications-open-data-statistiques/open-data/valorisation-des-donnees-regionales`) ne présente pas de réutilisations ni de procédure de soumission. | Contact Paradigm : `irisline@paradigm.brussels` (contact général, indiqué sur cette page). Ne pas utiliser `salesinfo@` (commercial). Service utilisé : `https://geoservices-vector.irisnet.be/geoserver/urbisvector/wfs`. |
| Statbel | `gispulse-src-statbel-be` | Statbel (office belge de statistique), `https://statbel.fgov.be/fr/open-data` | **Non vérifié** : le site est derrière un contrôle anti-robot, la page n'a pas pu être lue. Aucune affirmation sur un éventuel mécanisme. | Page Contact `https://statbel.fgov.be/en/contact` (non lisible depuis un script). Écrire via ce formulaire après vérification manuelle. |
| Portail fédéral | tous | `https://data.gov.be` (BOSA) | **Non vérifié** : `https://data.gov.be/en/applications` répond 200 mais est derrière un contrôle anti-robot ; savoir s'il propose une soumission d'application demande une vérification manuelle (navigateur). | À tester à la main ; si une soumission existe, y déposer le même texte une seule fois pour les cinq sources. |

Ordre conseillé : (1) e-mail à Digitaal Vlaanderen (seule procédure documentée) ;
(2) vérification manuelle de `data.gov.be` et de Statbel ; (3) message à Paradigm
et au Géoportail de la Wallonie pour demander s'ils tiennent une liste de
réutilisations, sans promettre d'inscription.
