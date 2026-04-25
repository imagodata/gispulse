# gispulse-pro

**GISPulse Pro** is the professional distribution of [GISPulse](https://pypi.org/project/gispulse/), the modular geospatial rules engine.

This meta-package installs GISPulse with all Pro dependencies in a single command.

## What Pro adds over Community

| Feature | Community | Pro |
|---------|-----------|-----|
| DuckDB/SpatiaLite engine | Yes | Yes |
| CLI + API | Yes | Yes |
| Rules & triggers | Yes | Yes |
| PostGIS engine | - | Yes |
| Hybrid engine (DuckDB + PostGIS) | - | Yes |
| S3 artifact storage | - | Yes |
| Redis rate limiting & caching | - | Yes |
| Cron-based scheduling | - | Yes |
| RBAC (role-based access) | - | Yes |
| Audit logging | - | Yes |

## Installation

```bash
pip install gispulse-pro
```

## Activation

Pro features require a tier and license key set via environment variables:

```bash
export GISPULSE_TIER=pro
export GISPULSE_LICENSE_KEY=your-license-key

gispulse serve
```

Or inline:

```bash
GISPULSE_TIER=pro GISPULSE_LICENSE_KEY=your-key gispulse serve
```

Without these variables, GISPulse runs in Community mode (DuckDB only, no Pro features).

## Licensing

GISPulse and GISPulse Pro are licensed under AGPL-3.0-or-later. A commercial dual license is available for organizations that cannot comply with AGPL terms. Contact us for details.

## Links

- [Documentation](https://gispulse.dev)
- [GitHub](https://github.com/gispulse/gispulse)
- [Pricing](https://gispulse.dev/pricing)
- [PyPI — gispulse](https://pypi.org/project/gispulse/)
