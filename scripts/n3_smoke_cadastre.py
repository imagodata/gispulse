"""Isolated N3 bulk -> Garage smoke for cadastre Etalab GeoJSON-gzip.

This smoke writes to the configured Garage bucket under an isolated prefix.
It targets one cadastre bulk entry and one department; it is not a national
orchestration runner.

Typical run:

    uv run --env-file .env python scripts/n3_smoke_cadastre.py

Useful overrides:

    N3_CADASTRE_SMOKE_BUCKET=gispulse
    N3_CADASTRE_SMOKE_PREFIX=smoke-n3/
    N3_CADASTRE_SMOKE_DEPARTMENT=63
    N3_CADASTRE_SMOKE_ENTRY=parcelles_bulk
    N3_CADASTRE_SMOKE_REVISION=cadastre-smoke

Credentials are read only through settings.s3 / environment variables and are
never logged by this script.
"""

from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from gispulse.core.bulk_runner import BulkIngestRunner
from gispulse.core.config import settings
from gispulse.core.plugin_model import AccessProtocol
from gispulse.persistence.duckdb_engine import DuckDBSession
from scripts.n3_smoke import (
    N3SmokeReport,
    _head_size,
    _manifest_uri,
    _normalise_prefix,
    _read_stage_row_count,
    _validate_isolation,
    make_s3_client,
)

LOGGER = logging.getLogger("n3_smoke_cadastre")
DEFAULT_ENTRY = "parcelles_bulk"
DEFAULT_DEPARTMENT = "63"
DEFAULT_PREFIX = "smoke-n3/"
DEFAULT_REVISION = "cadastre-smoke"


@dataclass(frozen=True)
class N3CadastreSmokeConfig:
    bucket: str
    prefix: str
    entry: str = DEFAULT_ENTRY
    department: str = DEFAULT_DEPARTMENT
    revision: str = DEFAULT_REVISION

    @classmethod
    def from_env(cls) -> "N3CadastreSmokeConfig":
        return cls(
            bucket=os.environ.get(
                "N3_CADASTRE_SMOKE_BUCKET",
                os.environ.get("N3_SMOKE_BUCKET", settings.s3.bucket),
            ).strip()
            or settings.s3.bucket,
            prefix=_normalise_prefix(
                os.environ.get(
                    "N3_CADASTRE_SMOKE_PREFIX",
                    os.environ.get("N3_SMOKE_PREFIX", DEFAULT_PREFIX),
                )
            ),
            entry=os.environ.get("N3_CADASTRE_SMOKE_ENTRY", DEFAULT_ENTRY).strip()
            or DEFAULT_ENTRY,
            department=os.environ.get(
                "N3_CADASTRE_SMOKE_DEPARTMENT",
                os.environ.get(
                    "N3_CADASTRE_SMOKE_DEPT",
                    os.environ.get("N3_SMOKE_DEPARTMENT", DEFAULT_DEPARTMENT),
                ),
            ).strip()
            or DEFAULT_DEPARTMENT,
            revision=os.environ.get(
                "N3_CADASTRE_SMOKE_REVISION",
                os.environ.get("N3_SMOKE_REVISION", DEFAULT_REVISION),
            ).strip()
            or DEFAULT_REVISION,
        )


def run_smoke(
    config: N3CadastreSmokeConfig,
    *,
    source_loader: Callable[[], Any] | None = None,
    runner_factory: Callable[..., BulkIngestRunner] = BulkIngestRunner,
    s3_client_factory: Callable[[], Any] | None = None,
    duckdb_session_factory: Callable[[], Any] = DuckDBSession,
) -> N3SmokeReport:
    """Run the cadastre smoke and verify the stage parquet through DuckDB/S3."""
    source_loader = source_loader or load_source
    s3_client_factory = s3_client_factory or make_s3_client
    prefix = _normalise_prefix(config.prefix)
    _validate_isolation(config.bucket, prefix)

    LOGGER.info(
        "starting cadastre N3 smoke: entry=%s department=%s bucket=%s prefix=%s "
        "revision=%s",
        config.entry,
        config.department,
        config.bucket,
        prefix,
        config.revision,
    )
    source = source_loader()
    _validate_cadastre_entry(source, config.entry)

    runner = runner_factory(
        bucket=config.bucket,
        key_prefix=prefix,
        write_table_raw=True,
    )
    result = runner.run_entry(
        source,
        config.entry,
        departement=config.department,
        revision=config.revision,
    )

    raw_uri = _manifest_uri(result.manifest, "raw_s3_uri")
    stage_uri = _manifest_uri(result.manifest, "stage_s3_uri")
    s3 = s3_client_factory()
    raw_size = _head_size(s3, raw_uri, expected_bucket=config.bucket)
    stage_size = _head_size(s3, stage_uri, expected_bucket=config.bucket)
    row_count = _read_stage_row_count(stage_uri, duckdb_session_factory)

    LOGGER.info("raw object: %s (%s bytes)", raw_uri, raw_size)
    LOGGER.info("stage object: %s (%s bytes)", stage_uri, stage_size)
    LOGGER.info("stage rows: %s", row_count)
    return N3SmokeReport(
        raw_s3_uri=raw_uri,
        stage_s3_uri=stage_uri,
        raw_size_bytes=raw_size,
        stage_size_bytes=stage_size,
        stage_row_count=row_count,
    )


def load_source() -> Any:
    """Load the bundled cadastre source plugin for direct checkout smokes."""
    plugin_path = (
        Path(__file__).resolve().parents[1] / "plugins" / "gispulse-src-cadastre"
    )
    if plugin_path.is_dir() and str(plugin_path) not in sys.path:
        sys.path.insert(0, str(plugin_path))

    from gispulse_src_cadastre.source import CadastreSource

    return CadastreSource()


def _validate_cadastre_entry(source: Any, entry_id: str) -> None:
    entry = next((item for item in source.catalog() if item.id == entry_id), None)
    source_name = getattr(source, "name", "")
    if source_name != "cadastre":
        raise ValueError(f"unsupported source {source_name!r}; expected 'cadastre'")
    if entry is None:
        raise KeyError(f"cadastre: unknown entry {entry_id!r}")
    if entry.access.protocol is not AccessProtocol.DOWNLOAD:
        raise ValueError(f"cadastre:{entry_id} is not a DOWNLOAD source")
    if not str(entry.access.endpoint).lower().endswith(".json.gz"):
        raise ValueError(f"cadastre:{entry_id} is not a GeoJSON gzip bulk source")


def main() -> int:
    level = os.environ.get("N3_SMOKE_LOG_LEVEL", "INFO").upper()
    logging.basicConfig(level=level, format="%(levelname)s %(message)s")
    try:
        run_smoke(N3CadastreSmokeConfig.from_env())
    except Exception:
        LOGGER.exception("cadastre N3 smoke failed")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
