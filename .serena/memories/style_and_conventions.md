# Style & Conventions

- **Python:** 3.10+ minimum
- **Style:** PEP 8, enforced by ruff (line-length=100, target py310)
- **Type hints:** Required on all public functions
- **Imports:** `from __future__ import annotations` at top of each module
- **Naming:** snake_case for functions/variables, PascalCase for classes
- **Core types:** Dataset, Layer, Job, Artifact, Rule, Trigger, Scenario (in `core/models.py`)
- **Logging:** structlog
- **Testing:** pytest, tests in `tests/unit/` and `tests/integration/`
- **No docstrings on obvious code** — only where logic isn't self-evident
- **Architecture:** modulaire — core, capabilities, rules, orchestration, persistence, adapters
- **Dependencies:** minimal in base install; extras via optional groups (postgis, api, mcp, dev)
