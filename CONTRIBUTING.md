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

## Code of Conduct

Ce projet adopte un code de conduite base sur le respect mutuel et la collaboration constructive. Tout comportement abusif, discriminatoire ou harcelant ne sera pas tolere.

Signalez tout probleme via les issues GitHub ou par email aux mainteneurs.

## Questions

Ouvrez une issue GitHub pour toute question technique ou suggestion.
