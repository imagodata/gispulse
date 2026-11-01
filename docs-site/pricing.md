---
title: Pricing
description: Comparer les tiers GISPulse — Community (gratuit), Pro, Team et Enterprise. Modèle open-core AGPL-3.0.
---

# Pricing


GISPulse est open-source (AGPL-3.0) avec un modèle open-core. **Les 144 capabilities sont gratuites** (Community) ; Pro, c'est l'exploitation : PostGIS, triggers cluster, orchestration, monitoring.

## Offre Early Adopter

::: tip Offre limitee — 50 clients
Les 50 premiers clients Pro beneficient d'un tarif **49 EUR/mois** garanti 24 mois (puis bascule vers le tarif standard ou resiliation). Essai gratuit 30 jours.

[Souscrire / nous contacter](mailto:contact@gispulse.dev)
:::

---

## Tiers

<div class="pricing-grid">

<div class="pricing-card">
<span class="badge-community">Community</span>

### Gratuit

<div class="price">0 €<small>/mois</small></div>

Licence AGPL-3.0. Open-source.

**Inclus :**
- Moteur DuckDB complet
- Les 144 capabilities (vecteur, attributs, classification, stats, topologie, réseau, raster, 3D)
- 22 sources de données FR/BE et 20+ workflows métier
- CLI — toutes les commandes
- Portal single-user
- Python SDK, serveur MCP
- Plugin QGIS
- Triggers locaux (5 actifs max)
- Docker
- Datasets locaux illimités

**Limites :**
- 1 utilisateur
- Pas de clé API REST
- Pas de PostGIS persistant

[Commencer gratuitement](/getting-started/installation)

</div>

<div class="pricing-card featured">
<span class="badge-pro">Pro</span>

### 79 €/mois

<div class="price">79 €<small>/mois</small></div>

790 €/an (2 mois offerts)

**Tout Community, plus :**
- PostGIS persistant
- Mode hybride DuckDB + PostGIS
- Triggers ESB
- Exécuteur DAG
- Node editor visuel
- Monitoring / métriques
- Pipelines cron
- Quotas de publication de cartes publiques (à venir)
- 5 clés API
- 50 datasets

[Commencer l'essai 30 jours](mailto:contact@gispulse.dev)

</div>

<div class="pricing-card">
<span class="badge-team">Team</span>

### 299 €/mois

<div class="price">299 €<small>/mois</small></div>

2 990 €/an (2 mois offerts)

**Tout Pro, plus :**
- RBAC (rôles et permissions)
- Multi-projets
- Support prioritaire 48h
- 2 instances
- 20 clés API
- Datasets illimités

[Contacter](mailto:contact@gispulse.dev)

</div>

<div class="pricing-card">
<span class="badge-enterprise">Enterprise</span>

### Sur devis

<div class="price">À partir de<small> 1 490 €/mois</small></div>

**Tout Team, plus :**
- SSO SAML / OIDC
- Clustering horizontal
- White-label
- SLA 4h garanti
- Capabilities custom
- Instances illimitées
- Clés API illimitées
- Datasets illimités

[Nous contacter](mailto:sales@gispulse.dev)

</div>

</div>

---

## Tableau comparatif détaillé

| Fonctionnalité | Community | Pro | Team | Enterprise |
|----------------|:---------:|:---:|:----:|:----------:|
| **Moteur** | | | | |
| DuckDB (local) | oui | oui | oui | oui |
| PostGIS persistant | — | oui | oui | oui |
| Mode hybride | — | oui | oui | oui |
| **Interfaces** | | | | |
| CLI | oui | oui | oui | oui |
| API REST | — | oui | oui | oui |
| Portal web | single-user | oui | oui | oui |
| Python SDK | oui | oui | oui | oui |
| Plugin QGIS | oui | oui | oui | oui |
| **Capabilities** | | | | |
| 144 capabilities (vecteur, attributs, classification, stats, topologie, réseau, raster, 3D pointcloud) | oui | oui | oui | oui |
| Raster (`rasterio`, 6 caps) | oui | oui | oui | oui |
| Réseau (`networkx`, routage, résilience) | oui | oui | oui | oui |
| `postgis_sql` (SQL paramétré) | — | oui | oui | oui |
| Capabilities custom (entry-points) | — | — | — | oui |
| **Orchestration** | | | | |
| Exécuteur DAG | — | oui | oui | oui |
| Pipelines cron | — | oui | oui | oui |
| Triggers locaux (5 actifs max) | oui | oui | oui | oui |
| Triggers ESB (cluster, pg_notify) | — | oui | oui | oui |
| Monitoring / métriques | — | oui | oui | oui |
| Visual node editor | — | oui | oui | oui |
| **Collaboration** | | | | |
| Multi-utilisateurs | — | — | oui | oui |
| RBAC | — | — | oui | oui |
| Multi-projets | — | — | oui | oui |
| **Sécurité & SLA** | | | | |
| SSO SAML / OIDC | — | — | — | oui |
| SLA 4h | — | — | — | oui |
| White-label | — | — | — | oui |
| Support | communauté | communauté | 48h | 4h |
| **Limites** | | | | |
| Utilisateurs | 1 | 1 | illimité | illimité |
| Clés API | 0 | 5 | 20 | illimité |
| Datasets | illimité (local) | 50 | illimité | illimité |
| Instances | 1 | 1 | 2 | illimité |

---

## FAQ

### GISPulse Community est-il vraiment gratuit ?

Oui, sans limite de temps et sans limite sur les données locales. Les 144 capabilities (dont raster et réseau), les 22 sources FR/BE et les 20+ workflows métier sont inclus. Le code est ouvert sous AGPL-3.0 — vous pouvez le modifier et le redistribuer selon les termes de la licence. Le tier Pro ajoute l'exploitation : PostGIS, triggers cluster, exécuteur DAG, éditeur visuel, monitoring, pipelines cron.

### Quelle est la différence AGPL-3.0 et la licence Pro ?

Community (AGPL-3.0) : si vous modifiez GISPulse et le distribuez (y compris en SaaS), vous devez publier vos modifications. Pro/Team/Enterprise : licence commerciale permettant un usage propriétaire sans obligation de publication.

### Puis-je passer de Community à Pro sans perdre mes données ?

Oui. Les datasets locaux et les règles JSON sont compatibles avec tous les tiers. La migration vers PostGIS est assistée par l'outil `gispulse portal`.

### Y a-t-il un essai gratuit pour Pro ?

Oui, 30 jours d'essai sans carte bancaire. Contactez [contact@gispulse.dev](mailto:contact@gispulse.dev).

### Qu'est-ce que l'offre Early Adopter ?

Les 50 premiers clients Pro bénéficient d'un tarif de lancement à 49 €/mois garanti 24 mois. Au-delà des 24 mois : basculement au tarif standard (79 €/mois) ou résiliation libre. [Réserver une place](mailto:contact@gispulse.dev).

### Mon organisation utilise GISPulse en SaaS interne — quel tier ?

Cela dépend du nombre d'utilisateurs et de si vous exposez GISPulse à l'extérieur. Contactez-nous pour un audit de votre cas d'usage : [contact@gispulse.dev](mailto:contact@gispulse.dev).

### Proposez-vous des réductions pour ONGs / secteur public ?

Oui, sur demande. Nous sommes sensibles aux projets d'intérêt général géospatial.

### L'Enterprise inclut-il un déploiement on-premise ?

Oui. Enterprise est conçu pour les déploiements on-premise, air-gapped et multi-tenant.
