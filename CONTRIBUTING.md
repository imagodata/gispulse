# Contributing to GISPulse

Merci de votre interet pour GISPulse. Ce guide decrit comment contribuer au projet.

## Comment contribuer

1. **Fork** le depot sur GitHub
2. **Creez une branche** depuis `main` : `git checkout -b feature/ma-contribution`
3. **Implementez** vos changements en suivant les conventions ci-dessous
4. **Testez** : assurez-vous que `pytest` passe sans regression
5. **Soumettez une Pull Request** vers `main` avec une description claire

## Style guide

- **Python 3.10+** requis
- **PEP 8** — applique via `ruff` (config dans `pyproject.toml`)
- **Type hints** obligatoires sur toutes les signatures publiques
- **Docstrings** pour les modules, classes et fonctions publiques
- **Tests** requis pour tout nouveau code ou correction de bug (`tests/`)
- Ligne max : 100 caracteres (config ruff)

## Architecture

Le projet suit une architecture modulaire : `core`, `capabilities`, `rules`, `orchestration`, `persistence`, `adapters`. Respectez la separation des couches — le core ne doit jamais importer depuis les adapters.

Consultez `docs/ARCHITECTURE.md` pour les details.

## Tests

```bash
pip install -e ".[dev]"
pytest
```

Les tests doivent passer avant soumission. Les PRs avec tests en echec ne seront pas mergees.

## Contributor License Agreement (CLA)

By submitting a pull request, you agree that your contributions will be licensed under AGPL-3.0-or-later, the same license that covers the project. You certify that you have the right to submit the contribution under these terms.

## Developer Certificate of Origin (DCO)

GISPulse uses the **DCO** (instead of a CLA) to keep contributor friction low.
Every commit you push must carry a `Signed-off-by` trailer certifying that
you have the right to submit it under the project's licence. The full text
is at https://developercertificate.org/.

In practice, just add `-s` when you commit :

```bash
git commit -s -m "fix: my contribution"
```

This appends a line like :

```
Signed-off-by: Your Name <your@email.com>
```

CI checks every commit on every PR and rejects unsigned commits. To
backfill a missing trailer on an existing PR :

```bash
git rebase --signoff @{upstream}
git push --force-with-lease
```

By signing off, you certify that your contribution is licensed under
**AGPL-3.0-or-later** (the project's open-source licence) AND grant
ImagoData the right to dual-license the project commercially under
[LICENSE-COMMERCIAL.md](LICENSE-COMMERCIAL.md). This is what makes
the dual-licence model viable without a separate CLA signing flow.

## Where to file what

| What | Where |
|---|---|
| Bug report | [GitHub Issues](https://github.com/imagodata/gispulse/issues) (use the bug template) |
| Feature request | [GitHub Issues](https://github.com/imagodata/gispulse/issues) (use the feature template) |
| Question / discussion | [GitHub Discussions](https://github.com/imagodata/gispulse/discussions) |
| Security finding | **security@imagodata.com** — see [SECURITY.md](SECURITY.md) |
| Commercial / Enterprise | **sales@imagodata.com** — see [LICENSE-COMMERCIAL.md](LICENSE-COMMERCIAL.md) |

## Code of Conduct

This project adopts the [Contributor Covenant 2.1](https://www.contributor-covenant.org/version/2/1/code_of_conduct/).
See [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md). Violations can be reported
to **conduct@imagodata.com**.

## Scope of contributions

`gispulse` is the **AGPL OSS engine**. Some features live in the private
`gispulse-enterprise` companion package (Stripe billing, OIDC SSO, RBAC
admin, premium connectors). Contributions to those modules cannot be
accepted in this repo — please open an issue describing the integration
need and we will route it appropriately.

What we welcome here :

- New capabilities (spatial operations, classification, analytics)
- New persistence backends or formats
- New adapters (HTTP, MCP, OGC, ESB, CLI)
- Performance, accessibility, internationalisation
- Tests and documentation
- Examples and template plugins under `plugins/`
