# Codebase Structure

```
gispulse/
├── cli.py                    # Typer CLI entry point (gispulse command)
├── core/
│   ├── models.py             # Core types: Dataset, Layer, Job, Artifact, Rule, Trigger, Scenario
│   └── logging.py            # structlog config
├── capabilities/
│   ├── base.py               # Base capability class
│   ├── vector.py             # Buffer, intersects, filter capabilities
│   ├── raster.py             # Raster capabilities
│   ├── network.py            # Network capabilities
│   ├── postgis_sql.py        # PostGIS SQL capabilities
│   └── registry.py           # Capability registry
├── rules/
│   ├── engine.py             # Rules engine (sequential execution)
│   ├── loader.py             # JSON rules loader
│   ├── predicates.py         # Rule predicates
│   └── validation.py         # Rule validation
├── orchestration/
│   ├── runner.py             # Job runner
│   └── scenario_runner.py    # Scenario runner
├── persistence/
│   ├── duckdb_engine.py      # DuckDB session engine (Phase 1 primary)
│   ├── gpkg.py               # GPKG adapter
│   ├── postgis.py            # PostGIS adapter (Phase 3)
│   ├── io.py                 # Multi-format vector I/O
│   ├── raster_io.py          # Raster I/O
│   ├── repository.py         # Repository ABC + InMemoryRepository
│   └── sqlite_repository.py  # SQLite-backed repository (Phase 2)
├── adapters/
│   ├── http/                 # FastAPI facade (Phase 2)
│   ├── mcp/                  # FastMCP facade (parking lot)
│   └── esb/                  # ESB/triggers (parking lot)
├── tests/
│   ├── unit/                 # Unit tests
│   └── integration/          # Integration tests
├── docs/                     # Specs, ADRs, plans
├── pyproject.toml            # Project config
└── Makefile                  # Dev commands
```
