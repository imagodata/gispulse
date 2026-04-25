# Task Completion Checklist

After completing any code task:

1. **Lint:** `ruff check .` — fix any issues
2. **Format:** `ruff format .` — ensure consistent formatting
3. **Tests:** `make test-unit` — run unit tests (integration if touching persistence/adapters)
4. **Verify:** `git diff` — review changes before committing
5. **No scope creep:** Only change what was asked. No extra refactoring, no speculative abstractions.
