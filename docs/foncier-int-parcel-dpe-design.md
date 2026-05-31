# Design aval: `int_parcel_dpe`

Statut: design uniquement, a appliquer plus tard cote `gispulse-foncier`.

## Objectif

Construire une table intermediaire `int_parcel_dpe` qui rattache les DPE ADEME
aux parcelles cadastrales par point-in-parcel, sans inference adresse ni fallback
centroide.

## Entrees attendues

- DPE logements existants et neufs issus de `gispulse-src-dpe`, materialises en
  tables raw/staging separees puis reunies avec un champ `dpe_dataset`.
- Parcelles mono-departement avec `id_parcelle`, `code_departement`,
  `code_commune`, `geom_valid`.
- Les coordonnees DPE BAN sont les champs Lambert-93
  `coordonnee_cartographique_x_ban` et `coordonnee_cartographique_y_ban`.

## Filtre qualite

Ne garder que les DPE dont:

```sql
statut_geocodage = 'adresse géocodée ban à l''adresse'
```

Le staging foncier doit conserver l'Unicode exact pour ce filtre, ou exposer
une colonne normalisee documentee et testee.

## Jointure spatiale

Creer le point DPE en EPSG:2154:

```sql
ST_SetSRID(
  ST_MakePoint(
    coordonnee_cartographique_x_ban,
    coordonnee_cartographique_y_ban
  ),
  2154
) AS geom
```

Jointure cible:

```sql
ST_Within(dpe.geom, parcel.geom_valid)
```

La jointure doit rester mono-departement:

```sql
dpe.code_departement_ban = parcel.code_departement
```

## Sortie minimale

- `id_parcelle`
- `numero_dpe`
- `dpe_dataset` (`logements-existants` ou `logements-neufs`)
- `date_etablissement_dpe`
- `date_derniere_modification_dpe`
- `etiquette_dpe`
- `etiquette_ges`
- `conso_5_usages_par_m2_ep`
- `emission_ges_5_usages_par_m2`
- `surface_habitable_logement`
- `type_batiment`
- `code_insee_ban`
- `identifiant_ban`
- `statut_geocodage`
- `geom`

## Garde-fous

- Rejeter les lignes sans coordonnees X/Y ou avec statut de geocodage different.
- Ne pas faire de matching adresse dans ce modele; garder les non-matches pour
  un rapport d'audit separe.
- Si un point tombe dans plusieurs parcelles, produire un compteur d'ambiguite
  et selectionner une parcelle uniquement dans un modele aval explicitement
  dedie a l'arbitrage.
- Tester au moins un departement avec `ST_Within`, le filtre qualite exact, et
  l'absence de cross-departement.
