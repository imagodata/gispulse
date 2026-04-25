---
title: Contribuer
description: Guide de contribution au projet open-source GISPulse — environnement, structure, style, tests, PR.
---

# Contribuer a GISPulse


GISPulse est un projet open-source sous licence AGPL-3.0. Les contributions sont les bienvenues : code, documentation, bug reports, idees.

---

## Environnement de developpement

### Prerequis

- Python 3.10+
- Git
- Docker (optionnel, pour PostGIS)

### Installation

```bash
# Cloner le depot
git clone https://github.com/gispulse/gispulse.git
cd gispulse

# Creer un environnement virtuel
python -m venv .venv
source .venv/bin/activate

# Installer en mode developpement avec toutes les dependances
pip install -e ".[dev,raster,network,postgis]"
```

### PostGIS local (optionnel)

```bash
docker run -d \
  --name gispulse-postgres \
  -e POSTGRES_USER=gispulse \
  -e POSTGRES_PASSWORD=secret \
  -e POSTGRES_DB=gispulse \
  -p 5432:5432 \
  postgis/postgis:16-3.4
```

```bash
# .env
GISPULSE_DSN=postgresql://gispulse:secret@localhost:5432/gispulse
```

### Verifier l'installation

```bash
# Lancer les tests
pytest

# Verifier le CLI
gispulse --version
gispulse capabilities
```

---

## Structure du projet

```
gispulse/
├── core/               # Types fondamentaux (Dataset, Layer, Job, Artifact)
├── capabilities/       # Capabilities (buffer, filter, clip, etc.)
│   ├── base.py         # Classe abstraite Capability
│   ├── registry.py     # Registre des capabilities
│   └── ...             # Implementations
├── rules/              # Parseur et validateur de regles JSON
├── orchestration/      # Executeur DAG, pipeline, cron
├── persistence/        # Adapters DuckDB, PostGIS, GPKG
├── adapters/           # Facades CLI, API REST, SDK
├── tests/              # Tests unitaires et d'integration
├── docs-site/          # Documentation VitePress
└── plugins/            # Plugins QGIS, ArcGIS
```

### Principes architecturaux

- **Architecture modulaire** : core -> capabilities -> rules -> orchestration -> persistence -> adapters
- **Pas de dependance circulaire** : les couches internes n'importent jamais les couches externes
- **Core types** : `Dataset`, `Layer`, `Job`, `Artifact`, `Scenario`, `Rule`, `Trigger`
- **Capabilities auto-enregistrees** via le decorateur `@register`

---

## Ajouter une capability

### 1. Creer le fichier

```bash
touch capabilities/ma_capability.py
```

### 2. Implementer la classe

```python
from capabilities.base import Capability
from capabilities.registry import register

@register
class MaCapability(Capability):
    name = "ma_capability"
    description = "Description claire de ce que fait la capability"
    schema = {
        "type": "object",
        "properties": {
            "parametre": {
                "type": "number",
                "default": 1.0,
                "description": "Description du parametre"
            }
        }
    }

    def execute(self, gdf, config, **kwargs):
        parametre = config.get("parametre", 1.0)
        # Logique spatiale ici
        return gdf
```

### 3. Ajouter les tests

```python
# tests/test_ma_capability.py
import geopandas as gpd
from shapely.geometry import Point
from capabilities.ma_capability import MaCapability

def test_ma_capability_basic():
    gdf = gpd.GeoDataFrame(
        {"id": [1, 2]},
        geometry=[Point(0, 0), Point(1, 1)],
        crs="EPSG:4326"
    )
    cap = MaCapability()
    result = cap.execute(gdf, {"parametre": 2.0})
    assert len(result) == 2
    assert result.crs == gdf.crs
```

### 4. Documenter

Ajoutez une section dans `docs-site/guide/capabilities.md` avec :
- Description
- Moteurs supportes
- Exemple JSON
- Tableau des parametres

---

## Style de code

### Regles generales

- **PEP 8** : `black` pour le formatage, `ruff` pour le linting
- **Type hints** : obligatoires sur toutes les fonctions publiques
- **Docstrings** : format Google pour les classes et fonctions publiques
- **Python 3.10+** : utilisez `match/case`, `X | Y` pour les unions de types

### Exemple

```python
def buffer_geometry(
    gdf: gpd.GeoDataFrame,
    distance: float,
    crs_meters: str = "EPSG:3857",
) -> gpd.GeoDataFrame:
    """Applique un buffer metrique sur un GeoDataFrame.

    Args:
        gdf: GeoDataFrame source.
        distance: Distance du buffer en metres.
        crs_meters: CRS metrique pour la projection intermediaire.

    Returns:
        GeoDataFrame avec les geometries bufferisees.

    Raises:
        ValueError: Si la distance est negative et le GDF contient des points.
    """
    ...
```

### Outils

```bash
# Formatage
black .

# Linting
ruff check .

# Type checking
mypy core/ capabilities/
```

---

## Tests

### Lancer les tests

```bash
# Tous les tests
pytest

# Avec couverture
pytest --cov=core --cov=capabilities --cov-report=html

# Un fichier specifique
pytest tests/test_buffer.py -v

# Uniquement les tests unitaires (sans PostGIS)
pytest -m "not postgis"
```

### Conventions

- Fichiers : `tests/test_<module>.py`
- Fonctions : `test_<methode>_<scenario>()`
- Utilisez des fixtures pour les GeoDataFrames de test
- Les tests PostGIS sont marques `@pytest.mark.postgis` et necessitent une base locale
- Pas de donnees reelles dans les tests : generez des geometries synthetiques

::: warning
Les tests doivent passer **sans PostGIS** par defaut. Les tests PostGIS sont executes separement en CI.
:::

---

## Processus de pull request

### 1. Creer une branche

```bash
git checkout -b feat/ma-feature
# ou
git checkout -b fix/mon-bug
```

### Conventions de nommage

- `feat/` : nouvelle fonctionnalite
- `fix/` : correction de bug
- `docs/` : documentation
- `refactor/` : refactoring sans changement fonctionnel
- `test/` : ajout ou modification de tests

### 2. Developper et tester

```bash
# Ecrire le code et les tests
pytest
black .
ruff check .
```

### 3. Committer

```bash
git add <fichiers>
git commit -m "feat: description courte du changement"
```

Format de message : `<type>: <description>` (convention [Conventional Commits](https://www.conventionalcommits.org/)).

### 4. Ouvrir la PR

- Ciblez la branche `main`
- Decrivez le changement et le contexte
- Referencez l'issue associee (`Closes #42`)
- Les tests CI doivent passer

### 5. Review

Un mainteneur relira votre PR. Attendez-vous a :
- Des retours sur le style et l'architecture
- Des demandes de tests supplementaires
- Des suggestions d'optimisation

---

## Reporter un bug

Ouvrez une issue sur [GitHub Issues](https://github.com/gispulse/gispulse/issues) avec :

1. **Version** de GISPulse (`gispulse --version`)
2. **Systeme** : OS, Python, versions des dependances
3. **Etapes de reproduction** : commandes exactes, fichier de regles, donnees minimales
4. **Comportement attendu** vs **comportement observe**
5. **Logs** : sortie complete avec `--verbose`

::: tip
Un bug reproductible avec un fichier de regles minimal et des donnees synthetiques sera traite en priorite.
:::

---

## Code de conduite

Le projet suit le [Contributor Covenant v2.1](https://www.contributor-covenant.org/version/2/1/code_of_conduct/). En resume :

- Soyez respectueux et constructif
- Pas de harcelement ni de discrimination
- Privilegiez la collaboration technique

Les violations peuvent etre reportees a [conduct@gispulse.dev](mailto:conduct@gispulse.dev).

---

## Communication

| Canal | Usage |
|-------|-------|
| [GitHub Discussions](https://github.com/gispulse/gispulse/discussions) | Questions techniques, propositions d'architecture |
| [GitHub Issues](https://github.com/gispulse/gispulse/issues) | Bugs et feature requests |
| Pull Requests | Code reviews |
| [contact@gispulse.dev](mailto:contact@gispulse.dev) | Partenariats et questions commerciales |
