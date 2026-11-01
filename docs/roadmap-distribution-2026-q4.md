# Plan de distribution — 90 jours (T4 2026)

Horizon : 2026-11-01 (J0) à 2027-01-30 (J+90). Milestone GitHub :
« Distribution Q4 2026 » (les issues sont créées séparément par le mainteneur ;
la liste figure plus bas).

## Diagnostic 2026-11-01

Le produit est livré ; la distribution ne l'est pas. Faits constatés :

- **2 étoiles GitHub**, **514 téléchargements PyPI par mois**.
- Le **plugin QGIS n'est pas publié** sur plugins.qgis.org.
- **0 réutilisation** déclarée sur data.gouv.fr, alors que 13 plugins consomment
  des jeux data.gouv.fr (fiches prêtes : [`docs/distribution/datagouv-reuses.md`](distribution/datagouv-reuses.md)).
- **`gispulse.dev` et `demo.gispulse.dev` sont hors ligne.**
- Les **docs annoncent encore la v2.0.0 et 118 capabilities** ; la réalité est la
  **2.5.1 avec 144 capabilities**.
- **Aucun Show HN n'a jamais été posté.**
- Le serveur MCP existe (`gispulse mcp`) mais n'est dans aucun registre
  (manifeste prêt : `server.json`, procédure : [`docs/distribution/mcp-registry.md`](distribution/mcp-registry.md)).

Conclusion : le goulot est la visibilité et la preuve d'usage, pas les
fonctionnalités. Les 90 jours sont consacrés à la distribution et à la mesure.

## Décisions

### Positionnement

- **Accroche (headline)** : données ouvertes FR/BE + workflows métier (PLU/CNIG,
  PPRI, ZAN, DVF × cadastre, SDIS, FTTH). C'est ce que des utilisateurs cherchent
  et ce que les 13 fiches data.gouv.fr rendent trouvable.
- **Différenciateur** : « sauvegarde QGIS → règles ». Enregistrer dans QGIS et
  obtenir un trigger rejouable, sans client SIG. C'est l'argument des vidéos et
  du `gispulse demo`.
- **Vision, pas accroche** : « dbt pour le SIG ». À garder pour la documentation
  d'architecture et les échanges avec la communauté DuckDB, pas pour la page
  d'accueil.

### Tarification

- **Les 144 capabilities sont toutes en Community** (OSS). Pas de capability
  verrouillée pour créer de la friction avant d'avoir des utilisateurs.
- **Pro = opérations** : PostGIS hébergé, `esb_triggers`, quota de publication,
  cron, monitoring, RBAC/SSO. On vend ce qui se paie à l'exploitation, pas
  l'algorithme.
- Suites à donner (issue 2) : mise à jour de `pricing_commercial.yml` côté
  enterprise et du `TierGate` côté portal-pro.

## Les trois phases

### Phase 1 — S1-S2 : Vitrine

Rendre le projet comprenable et installable en cinq minutes.

- README et docs : **un seul pitch**, versions et compteurs corrigés (2.5.1, 144).
- Changement de tarification (voir ci-dessus).
- Commande **`gispulse demo`** : une démonstration complète en moins de 30 s.
- **Plugin QGIS publié sur plugins.qgis.org.**
- **DNS** `gispulse.dev` et `demo.gispulse.dev` rétablis.
- **13 réutilisations data.gouv.fr + volet belge** (fiches prêtes).
- **Registre MCP** officiel, plus Smithery / mcp.so / Glama (selon faisabilité,
  voir `docs/distribution/mcp-registry.md`).
- **Vidéo 60 s** « sauvegarde QGIS → trigger » et **vidéo 90 s** MCP.

### Phase 2 — S3-S8 : Contenu

- **Un article par semaine, « un template = un article »** : ZAN, PLU CNIG, PPRI,
  DVF × cadastre, SDIS, hotspots FTTH. Diffusion : GeoRezo, Geotribu, LinkedIn,
  Mastodon, OSGeo-fr.
- **Communauté DuckDB** : entrée dans awesome-duckdb, billet invité.
- **Terminer l'issue #406** (permalien public + embed) : c'est la seule mécanique
  virale du produit (une carte partagée amène un visiteur au produit).
- **Show HN + r/gis vers S6**, à condition que `gispulse demo` tienne en 30 s.
  Sinon repousser, ne pas lancer sur une démo qui échoue.

### Phase 3 — S9-S12 : Mesure et décision

Objectifs à J+90 :

| Indicateur | Cible |
|---|---|
| Étoiles GitHub | 50 |
| Discussions GitHub | 10 |
| Conversations avec des utilisateurs | 5 |

- **L'offre Pro se décide sur ce que ces cinq conversations demandent**, pas
  avant.
- **Porte de sortie honnête** : moins de 30 étoiles et aucun contact entrant à
  J+90 → GISPulse devient un **outil de cabinet** (modèle services, type
  Oslandia) ; l'OSS reste le levier de crédibilité, sans investissement produit
  supplémentaire pour la distribution.

## Gel jusqu'à la porte J+90

Pas de nouvelle capability, pas de Cocarte, pas de Tauri, pas d'add-in ArcGIS,
pas de marketplace avec partage de revenus. Seules les corrections de bugs et ce
plan avancent.

## Portes (gates)

| Porte | Quand | Condition |
|---|---|---|
| G1 — Vitrine | fin S2 | README unique, `gispulse demo` OK, plugin QGIS en ligne, DNS rétablis, 13 réutilisations et MCP soumis |
| G2 — Lancement | S6 | `gispulse demo` en 30 s ; sinon Show HN reporté |
| G3 — Mesure | S9 | tableau de bord de traction en place et lu chaque mois |
| G4 — Décision | J+90 | 50 étoiles / 10 discussions / 5 conversations, ou sortie « outil de cabinet » |

## Issues (milestone « Distribution Q4 2026 »)

À créer par le mainteneur (rechercher d'abord les doublons avec
`gh issue list --search`) :

1. **Vitrine README/docs** : un seul pitch, versions et compteurs alignés sur 2.5.1 / 144.
2. **Pricing move** + suites enterprise : `pricing_commercial.yml` et `TierGate` de portal-pro.
3. **`gispulse demo`** : démonstration complète en moins de 30 s.
4. **Plugin QGIS sur plugins.qgis.org.**
5. **DNS** `gispulse.dev` + `demo.gispulse.dev`.
6. **Registre MCP** + Smithery / mcp.so / Glama.
7. **Réutilisations data.gouv.fr + volet belge.**
8. **Vidéos** : 60 s « save QGIS → trigger », 90 s MCP.
9. **Série d'articles** : un template = un article, une fois par semaine.
10. **Show HN + r/gis + awesome-duckdb.**
11. **#406 prioritaire** : permalien public + embed.
12. **Tableau de bord de traction** : `scripts/traction_report.py` (étoiles, pypistats,
    téléchargements du plugin QGIS, discussions, contacts entrants), revue mensuelle
    et porte J+90.
13. **Gel des surfaces non livrées** (voir section Gel).

## Livrables déjà prêts dans le dépôt

- `server.json` (manifeste du registre MCP) et `docs/distribution/mcp-registry.md`.
- `docs/distribution/datagouv-reuses.md` : 13 fiches et volet Belgique.
- Dépendance connue : la vérification de propriété PyPI du registre MCP exige la
  ligne `mcp-name: io.github.imagodata/gispulse` dans le README publié sur PyPI,
  donc la **prochaine release** doit la contenir avant `mcp-publisher publish`.
