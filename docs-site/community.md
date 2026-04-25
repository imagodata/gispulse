---
title: Communaute
description: Rejoindre la communaute GISPulse — GitHub, contributions, roadmap, contact et support.
---

# Communaute


GISPulse est un projet open-source construit par et pour les professionnels du geospatial. Rejoignez la communaute.

---

## Liens principaux

| Ressource | Lien |
|-----------|------|
| Code source | [github.com/gispulse/gispulse](https://github.com/gispulse/gispulse) |
| Discussions | [GitHub Discussions](https://github.com/gispulse/gispulse/discussions) |
| Issues & bugs | [GitHub Issues](https://github.com/gispulse/gispulse/issues) |
| Documentation | [gispulse.dev](https://gispulse.dev) |
| Blog | [gispulse.dev/blog](/blog/gispulse-vs-fme) |
| Changelog | [gispulse.dev/changelog](/changelog) |
| Contact | [contact@gispulse.dev](mailto:contact@gispulse.dev) |

---

## Participer

### Reporter un bug

Ouvrez une [issue](https://github.com/gispulse/gispulse/issues) avec :

1. Version de GISPulse (`gispulse --version`)
2. Etapes de reproduction
3. Comportement attendu vs observe
4. Logs complets (`--verbose`)

### Proposer une fonctionnalite

Ouvrez une [discussion](https://github.com/gispulse/gispulse/discussions) dans la categorie "Ideas". Decrivez :

- Le cas d'usage concret
- Le comportement souhaite
- Les alternatives envisagees

### Contribuer du code

Le guide complet est disponible ici : [Contribuer a GISPulse](/contributing).

En resume :

1. Forkez le depot
2. Creez une branche (`feat/`, `fix/`, `docs/`)
3. Ecrivez du code avec tests et type hints
4. Ouvrez une pull request vers `main`

::: tip Premier contribution ?
Cherchez les issues etiquetees [`good first issue`](https://github.com/gispulse/gispulse/issues?q=label%3A%22good+first+issue%22) — elles sont concues pour accueillir les nouveaux contributeurs.
:::

---

## Roadmap

GISPulse suit une roadmap publique orientee par les besoins terrain des professionnels GIS.

### Vision a long terme

Devenir le moteur geospatial de reference pour les pipelines declaratifs — le **dbt du geospatial**.

### Axes en cours

| Axe | Statut | Horizon |
|-----|--------|---------|
| Moteur DuckDB + PostGIS + GPKG portable | Livre | v1.0 |
| 117 capabilities (vecteur, attributs, classification, stats, topologie, 3D pointcloud, raster, reseau, PostGIS SQL) | Livre | v1.1 |
| CLI / API REST / SDK Python | Livre | v1.0 |
| Plugin QGIS / Add-in ArcGIS / Desktop Tauri | Livre | v1.0 |
| Templates de pipelines metier (21 presets) | Livre | v1.0 |
| 6 scenarios playground interactifs | Livre | v1.1 |
| RBAC, SSO (OIDC/SAML), audit log, S3 storage | Livre | v1.0 |
| Visual node editor | Beta | v1.2 |
| Ingestion WFS / OGC API Features | Planifie | v1.3 |
| Exposition MVT / STAC | Planifie | v1.3 |
| Marketplace de capabilities | Planifie | v2.0 |

::: info
La roadmap est indicative. Les priorites evoluent selon les retours de la communaute et les besoins des utilisateurs Pro/Team/Enterprise.
:::

---

## Support

| Tier | Canal | Delai |
|------|-------|-------|
| Community | GitHub Discussions / Issues | Communaute (best effort) |
| Pro | GitHub Discussions / Issues | Communaute (best effort) |
| Team | Support prioritaire par email | 48h garanti |
| Enterprise | Support dedie | SLA 4h garanti |

Pour le support commercial : [contact@gispulse.dev](mailto:contact@gispulse.dev).

---

## Sponsors & soutien

GISPulse est un projet independant. Si vous utilisez GISPulse en production et souhaitez soutenir son developpement :

- **Tier Pro/Team/Enterprise** : votre abonnement finance directement le developpement
- **Sponsor GitHub** : [Sponsoriser sur GitHub](https://github.com/sponsors/gispulse)
- **Contribution code** : chaque PR compte
- **Bouche-a-oreille** : parlez de GISPulse a vos collegues GIS

::: tip Organisations publiques
Nous proposons des tarifs adaptes pour les collectivites territoriales, EPCI, syndicats mixtes et organismes de recherche. Contactez [contact@gispulse.dev](mailto:contact@gispulse.dev).
:::

---

## Regles de la communaute

Le projet suit le [Contributor Covenant v2.1](https://www.contributor-covenant.org/version/2/1/code_of_conduct/). Les principes fondamentaux :

- **Respect** : traitez chaque membre avec courtoisie
- **Constructivite** : critiquez les idees, pas les personnes
- **Inclusion** : le geospatial est un domaine vaste — accueillez les profils divers
- **Transparence** : les decisions techniques sont documentees publiquement

Les violations peuvent etre reportees a [conduct@gispulse.dev](mailto:conduct@gispulse.dev).

---

## A propos

GISPulse est ne du constat que les professionnels GIS passent trop de temps a re-ecrire les memes traitements spatiaux dans des scripts fragiles. Le projet propose une alternative : des regles declaratives, versionnables, portables et reproductibles.

Le coeur du moteur est et restera open-source sous licence AGPL-3.0.
