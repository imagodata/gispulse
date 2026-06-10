"""DuckDB resource-limit helpers.

Opt-in bounding of memory, temp storage and thread count for DuckDB
connections opened by GISPulse.  All settings are *optional*: an
unconfigured field means "leave DuckDB's own defaults untouched",
which preserves the current behaviour for existing installs.

Typical usage::

    from gispulse.persistence.duckdb_limits import configure_duckdb_limits
    conn = duckdb.connect(":memory:")
    configure_duckdb_limits(conn)          # reads settings.jobs automatically

**Error boundaries:**

- An invalid ``GISPULSE_DUCKDB_THREADS`` env var (non-integer) raises a
  ``pydantic.ValidationError`` at settings construction time — by design,
  matching the existing behaviour of ``GISPULSE_DUCKDB_THRESHOLD`` and
  ``GISPULSE_JOB_TIMEOUT``.  Fix the env var and restart.
- A limit value accepted by the config but rejected by DuckDB at ``SET``
  time (e.g. an unrecognised memory string) is logged at WARNING level and
  skipped; the session remains fully usable.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from gispulse.core.logging import get_logger

log = get_logger(__name__)


@dataclass(frozen=True)
class DuckDBLimitSettings:
    """Resource limits to apply to a DuckDB connection.

    Empty / zero / None fields mean "not configured" — the corresponding
    ``SET`` statement is skipped entirely.

    Attributes:
        memory_limit:   DuckDB memory_limit string, e.g. ``"4GB"``.
                        Empty string → not applied.
        temp_directory: Directory for DuckDB spill files.
                        ``None`` → not applied.
        threads:        Number of DuckDB worker threads.
                        ``0`` or negative → not applied.
    """

    memory_limit: str
    temp_directory: Path | None
    threads: int


def _sql_literal(value: str) -> str:
    """Return *value* as a single-quoted SQL string literal with escaped quotes."""
    return "'" + str(value).replace("'", "''") + "'"


def configure_duckdb_limits(
    conn: Any,
    limits: DuckDBLimitSettings | None = None,
) -> DuckDBLimitSettings:
    """Apply resource limits to an open DuckDB connection.

    Parameters
    ----------
    conn:
        An open ``duckdb.DuckDBPyConnection`` (or any object with an
        ``.execute(sql)`` method).
    limits:
        Settings to apply.  If ``None``, resolved automatically from
        ``settings.jobs`` via :func:`_limits_from_settings`.

    Returns
    -------
    DuckDBLimitSettings
        The resolved (and applied) settings — useful for logging.

    Notes
    -----
    Each ``SET`` is best-effort: a failure is logged at WARNING level and
    the session continues.  This mirrors the ``_load_extension`` pattern in
    ``persistence/duckdb_engine.py`` so a misconfigured limit never kills
    an ingest run.
    """
    if limits is None:
        limits = _limits_from_settings()

    if limits.memory_limit:
        _try_set(conn, f"SET memory_limit = {_sql_literal(limits.memory_limit)}", "memory_limit")

    if limits.temp_directory is not None:
        try:
            limits.temp_directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            log.warning(
                "duckdb_limits_temp_dir_mkdir_failed",
                path=str(limits.temp_directory),
                error=str(exc),
            )
        _try_set(
            conn,
            f"SET temp_directory = {_sql_literal(str(limits.temp_directory))}",
            "temp_directory",
        )

    if limits.threads >= 1:
        _try_set(conn, f"SET threads = {limits.threads}", "threads")

    return limits


def _try_set(conn: Any, sql: str, setting: str) -> None:
    try:
        conn.execute(sql)
        log.debug("duckdb_limit_applied", setting=setting)
    except Exception as exc:
        log.warning(
            "duckdb_limit_set_failed",
            setting=setting,
            error=str(exc),
        )


def _limits_from_settings() -> DuckDBLimitSettings:
    """Build :class:`DuckDBLimitSettings` from ``settings.jobs``."""
    from gispulse.core.config import settings

    jobs = settings.jobs
    raw_temp = getattr(jobs, "duckdb_temp_dir", "").strip()
    temp_directory = Path(raw_temp).expanduser() if raw_temp else None

    try:
        threads = int(getattr(jobs, "duckdb_threads", 0))
    except (ValueError, TypeError) as exc:
        log.warning("duckdb_limits_threads_invalid", error=str(exc))
        threads = 0

    return DuckDBLimitSettings(
        memory_limit=getattr(jobs, "duckdb_memory_limit", "").strip(),
        temp_directory=temp_directory,
        threads=threads,
    )
