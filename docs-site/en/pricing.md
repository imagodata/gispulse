---
title: Pricing
description: Compare GISPulse tiers — Community (free), Pro, Team and Enterprise. Open-core model AGPL-3.0.
---

# Pricing

GISPulse is open-source (AGPL-3.0) with an open-core model. The engine core is free forever.

## Early Adopter Offer

::: tip Limited offer — 50 customers
The first 50 Pro customers get a **49 EUR/month** rate guaranteed for 24 months (then switches to the standard rate or cancellation). 30-day free trial.

[Subscribe / contact us](mailto:contact@gispulse.dev)
:::

---

## Tiers

<div class="pricing-grid">

<div class="pricing-card">
<span class="badge-community">Community</span>

### Free

<div class="price">0 EUR<small>/month</small></div>

AGPL-3.0 license. Open-source.

**Included:**
- Full DuckDB engine
- CLI — all commands
- Single-user portal
- Python SDK
- QGIS Plugin
- Docker
- Unlimited local datasets

**Limits:**
- 1 user
- No REST API key
- No persistent PostGIS

[Get started for free](/getting-started/installation)

</div>

<div class="pricing-card featured">
<span class="badge-pro">Pro</span>

### 79 EUR/month

<div class="price">79 EUR<small>/month</small></div>

790 EUR/year (2 months free)

**Everything in Community, plus:**
- Persistent PostGIS
- Hybrid mode DuckDB + PostGIS
- ESB Triggers
- DAG executor
- Visual node editor
- Monitoring / metrics
- Cron pipelines
- Raster capabilities
- Network capabilities
- 5 API keys
- 50 datasets

[Start 30-day trial](mailto:contact@gispulse.dev)

</div>

<div class="pricing-card">
<span class="badge-team">Team</span>

### 299 EUR/month

<div class="price">299 EUR<small>/month</small></div>

2,990 EUR/year (2 months free)

**Everything in Pro, plus:**
- RBAC (roles and permissions)
- Multi-project
- Priority support 48h
- 2 instances
- 20 API keys
- Unlimited datasets

[Contact us](mailto:contact@gispulse.dev)

</div>

<div class="pricing-card">
<span class="badge-enterprise">Enterprise</span>

### Custom pricing

<div class="price">Starting at<small> 1,490 EUR/month</small></div>

**Everything in Team, plus:**
- SSO SAML / OIDC
- Horizontal clustering
- White-label
- 4h guaranteed SLA
- Custom capabilities
- Unlimited instances
- Unlimited API keys
- Unlimited datasets

[Contact us](mailto:sales@gispulse.dev)

</div>

</div>

---

## Detailed Comparison Table

| Feature | Community | Pro | Team | Enterprise |
|---------|:---------:|:---:|:----:|:----------:|
| **Engine** | | | | |
| DuckDB (local) | yes | yes | yes | yes |
| Persistent PostGIS | — | yes | yes | yes |
| Hybrid mode | — | yes | yes | yes |
| **Interfaces** | | | | |
| CLI | yes | yes | yes | yes |
| REST API | — | yes | yes | yes |
| Web portal | single-user | yes | yes | yes |
| Python SDK | yes | yes | yes | yes |
| QGIS Plugin | yes | yes | yes | yes |
| ArcGIS Add-in | yes | yes | yes | yes |
| Tauri Desktop | yes | yes | yes | yes |
| **Capabilities** | | | | |
| 104 Community capabilities (vector, attributes, classification, stats, topology, 3D pointcloud) | yes | yes | yes | yes |
| Raster (`rasterio`, 6 caps) | — | yes | yes | yes |
| Network (`networkx`, 6 caps) | — | yes | yes | yes |
| `postgis_sql` (parameterised SQL) | — | yes | yes | yes |
| Custom capabilities (entry-points) | — | — | — | yes |
| **Orchestration** | | | | |
| DAG executor | — | yes | yes | yes |
| Cron pipelines | — | yes | yes | yes |
| ESB Triggers | — | yes | yes | yes |
| Visual node editor | — | yes | yes | yes |
| **Collaboration** | | | | |
| Multi-user | — | — | yes | yes |
| RBAC | — | — | yes | yes |
| Multi-project | — | — | yes | yes |
| **Security & SLA** | | | | |
| SSO SAML / OIDC | — | — | — | yes |
| 4h SLA | — | — | — | yes |
| White-label | — | — | — | yes |
| Support | community | community | 48h | 4h |
| **Limits** | | | | |
| Users | 1 | 1 | unlimited | unlimited |
| API keys | 0 | 5 | 20 | unlimited |
| Datasets | unlimited (local) | 50 | unlimited | unlimited |
| Instances | 1 | 1 | 2 | unlimited |

---

## FAQ

### Is GISPulse Community really free?

Yes, with no time limit and no limit on local data. The code is open under AGPL-3.0 — you can modify and redistribute it under the terms of the license.

### What is the difference between AGPL-3.0 and the Pro license?

Community (AGPL-3.0): if you modify GISPulse and distribute it (including as SaaS), you must publish your modifications. Pro/Team/Enterprise: commercial license allowing proprietary use without publication obligation.

### Can I upgrade from Community to Pro without losing my data?

Yes. Local datasets and JSON rules are compatible with all tiers. Migration to PostGIS is assisted by the `gispulse portal` tool.

### Is there a free trial for Pro?

Yes, 30 days trial without credit card. Contact [contact@gispulse.dev](mailto:contact@gispulse.dev).

### What is the Early Adopter offer?

The first 50 Pro customers get a launch rate of 49 EUR/month guaranteed for 24 months. After 24 months: switch to the standard rate (79 EUR/month) or free cancellation. [Reserve a spot](mailto:contact@gispulse.dev).

### My organization uses GISPulse as internal SaaS — which tier?

It depends on the number of users and whether you expose GISPulse externally. Contact us for an audit of your use case: [contact@gispulse.dev](mailto:contact@gispulse.dev).

### Do you offer discounts for NGOs / public sector?

Yes, upon request. We are committed to geospatial projects serving the public interest.

### Does Enterprise include on-premise deployment?

Yes. Enterprise is designed for on-premise, air-gapped and multi-tenant deployments.
