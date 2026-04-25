---
title: Contributing
description: How to contribute to GISPulse — development setup, code style, testing, PR process.
---

# Contributing to GISPulse

Thank you for your interest in contributing to GISPulse. This guide covers everything you need to get started.

## Development Setup

### Prerequisites

- Python 3.10+
- Git
- Docker (optional, for PostGIS testing)
- Node.js 18+ (for portal development)

### Clone and install

```bash
git clone https://github.com/imagodata/gispulse.git
cd gispulse

# Create a virtual environment
python -m venv .venv
source .venv/bin/activate

# Install with all development dependencies
pip install -e ".[dev,all]"

# Verify installation
gispulse doctor
```

### Running PostGIS locally (optional)

```bash
docker compose up -d postgres
```

This starts a PostGIS instance for integration testing. Not required for DuckDB-only development.

---

## Project Structure

```
gispulse/
├── gispulse/              # Core Python package
│   ├── core/              # Engine, session, job runner
│   ├── capabilities/      # Spatial capabilities (buffer, clip, etc.)
│   ├── rules/             # Rules-as-config engine
│   ├── orchestration/     # DAG executor, triggers, ESB
│   ├── persistence/       # PostGIS, SpatiaLite adapters
│   ├── adapters/          # I/O, format adapters
│   ├── api/               # FastAPI REST API
│   └── cli.py             # Typer CLI entry point
├── portal/                # React 19 web portal
├── sdk/                   # Python SDK (httpx + pydantic)
├── clients/               # QGIS plugin, ArcGIS add-in, Tauri desktop
├── tests/                 # Unit and integration tests
├── deploy/                # Docker, Caddy, monitoring
└── docs-site/             # VitePress documentation
```

---

## Adding a Capability

Capabilities are the building blocks of GISPulse. Each capability implements a single spatial operation.

### 1. Create the capability class

```python
# gispulse/capabilities/my_capability.py
from gispulse.capabilities.base import BaseCapability

class MyCapability(BaseCapability):
    """Short description of what this capability does."""

    name = "my_capability"

    def execute(self, input_layer, params: dict, session) -> str:
        """Execute the capability and return the output layer name."""
        # Your spatial logic here
        ...
        return output_layer_name
```

### 2. Register it

The capability registry uses auto-discovery. Place your file in `gispulse/capabilities/` and it will be automatically detected.

### 3. Write tests

```python
# tests/capabilities/test_my_capability.py
def test_my_capability_basic():
    # Test with sample data
    ...

def test_my_capability_edge_cases():
    # Test empty input, invalid CRS, etc.
    ...
```

### 4. Document it

Add the capability to the documentation with parameters, examples, and supported engines.

---

## Code Style

GISPulse follows strict code quality standards:

- **PEP 8** — enforced via linting
- **Type hints** — required on all public functions and methods
- **Docstrings** — required on all public classes and functions
- **Max line length** — 120 characters
- **Import ordering** — stdlib, third-party, local (isort-compatible)

### Example

```python
from typing import Optional

from gispulse.core.types import Dataset, Layer


def compute_buffer(
    layer: Layer,
    distance: float,
    crs: Optional[str] = None,
) -> Layer:
    """Compute a buffer around all features in the layer.

    Args:
        layer: Input vector layer.
        distance: Buffer distance in meters.
        crs: Optional target CRS. If provided, the layer is
            reprojected before buffering.

    Returns:
        A new Layer with buffered geometries.
    """
    ...
```

---

## Testing

### Running tests

```bash
# All tests
pytest

# Unit tests only
pytest tests/unit/

# Integration tests (requires PostGIS)
pytest tests/integration/

# With coverage
pytest --cov=gispulse --cov-report=html

# Specific test file
pytest tests/capabilities/test_buffer.py -v
```

### Test conventions

- Test files are in `tests/`, mirroring the source structure
- Use `pytest` fixtures for session setup and sample data
- Integration tests that require PostGIS are marked with `@pytest.mark.integration`
- Async tests use `@pytest.mark.asyncio`

---

## Pull Request Process

### 1. Open an issue first

For anything beyond a trivial fix, open an issue to discuss the approach before writing code. This avoids wasted effort and ensures alignment with the project direction.

### 2. Create a branch

```bash
git checkout -b feature/my-feature
# or
git checkout -b fix/my-bugfix
```

### 3. Make your changes

- Follow the code style guidelines above
- Add or update tests
- Update documentation if needed

### 4. Run checks locally

```bash
# Tests
pytest

# Type checking (if configured)
mypy gispulse/
```

### 5. Submit the PR

- Write a clear title and description
- Reference the related issue (`Fixes #123`)
- Describe what changed and why
- Include any testing notes

### 6. Review

A maintainer will review your PR. Address feedback promptly. Once approved, a maintainer will merge.

---

## Reporting Issues

Use [GitHub Issues](https://github.com/imagodata/gispulse/issues) to report bugs or request features.

### Bug reports should include

- GISPulse version (`gispulse --version`)
- Python version
- Operating system
- Steps to reproduce
- Expected vs actual behavior
- Sample data or rules file (if applicable)

### Feature requests should include

- Use case description
- Expected behavior
- Why existing capabilities do not cover the need

---

## Code of Conduct

GISPulse is committed to providing a welcoming and inclusive experience for everyone. We expect all contributors to:

- Be respectful and constructive in discussions
- Welcome newcomers and help them get started
- Focus on the technical merits of contributions
- Accept constructive criticism gracefully

Harassment, discrimination, and hostile behavior are not tolerated.

---

## Communication

- **GitHub Discussions** — questions, ideas, show-and-tell
- **GitHub Issues** — bugs and feature requests
- **Email** — [contact@gispulse.dev](mailto:contact@gispulse.dev) for private matters

We look forward to your contributions.
