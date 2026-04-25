# Suggested Commands

## Installation
```bash
pip install -e ".[dev]"       # Install with dev dependencies
make install                   # Same via Makefile
```

## Testing
```bash
make test                      # Run all tests
make test-unit                 # Unit tests only
make test-integration          # Integration tests only
python -m pytest tests/ -v     # Direct pytest
python -m pytest tests/unit/test_capabilities.py -v  # Single file
```

## Linting & Formatting
```bash
make lint                      # ruff check .
make format                    # ruff format .
ruff check . --fix             # Auto-fix lint issues
```

## Running
```bash
gispulse run input.gpkg --rules rules.json -o output.gpkg
gispulse layers input.gpkg
gispulse capabilities
```

## Cleanup
```bash
make clean                     # Remove __pycache__, .pytest_cache, .egg-info
```

## Git
```bash
git status
git log --oneline -10
git diff
```
