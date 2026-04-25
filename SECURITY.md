# Security Policy

We take security seriously. Thank you for helping us keep GISPulse safe.

## Supported versions

| Version | Supported          |
|--------:|:------------------:|
| 1.2.x   | ✅ active          |
| 1.1.x   | ⚠️ critical fixes only (until 2026-10) |
| < 1.1   | ❌ end-of-life     |

## Reporting a vulnerability

**Please do not open a public issue.**

Email security findings to **security@imagodata.com** with :
- A clear description of the issue and its impact.
- Steps to reproduce (PoC welcome but not required).
- Affected version(s) and configuration if relevant.
- Your name / handle if you want public credit in the advisory.

You will get an acknowledgement within **72 hours** and a remediation
plan within **7 days** for confirmed issues.

## Disclosure timeline

- **Day 0** : report received, triage starts.
- **Day 7** : confirmation + severity classification (CVSS 3.1).
- **Day 30 (typical)** : fix released, advisory published.
- **Day 30+** : public credit in `CHANGELOG.md` and GitHub Security Advisory.

For high-severity issues we may ship out-of-band patch releases.

## Scope

- The `gispulse` PyPI package and its source repository.
- The companion `gispulse-portal` web UI.
- The Docker images published under `ghcr.io/imagodata/`.

The private `gispulse-enterprise` package follows a separate, contractual
disclosure process with paying customers.

## Out of scope

- Third-party dependencies (please report upstream).
- Demo / playground deployments at `*.gispulse.dev` (operational, not source).
- Issues that require physical access or social engineering.

## No bug bounty

We do not currently run a paid bug bounty programme. We do publish public
credit and may send swag for high-impact findings.
