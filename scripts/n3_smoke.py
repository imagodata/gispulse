"""Isolated N3 bulk -> Garage smoke for a CSV TABLE_FILE source.

Runbook VPS, without touching /opt/gispulse-core-beta:

1. Create an isolated checkout, for example:
       rm -rf /tmp/n3-smoke
       git clone <gispulse-core-repo-url> /tmp/n3-smoke
       cd /tmp/n3-smoke
       git checkout feat/bulkify-wfs-sources

2. Create a dedicated venv and install only this checkout:
       python3 -m venv .venv
       . .venv/bin/activate
       python -m pip install -U pip
       python -m pip install -e ".[s3]" -e plugins/gispulse-src-georisques

3. Source the Garage env used by the deployed settings.s3 path. Do not print it:
       set -a
       . /path/to/gispulse-garage.env
       set +a

4. Run the smoke in the prod bucket but under an isolated prefix:
       export N3_SMOKE_BUCKET="${GISPULSE_S3_BUCKET:-gispulse}"
       export N3_SMOKE_PREFIX="smoke-n3/"
       export N3_SMOKE_DEPARTMENT="63"
       python scripts/n3_smoke.py

5. Check the logs for the raw/stage S3 paths, object sizes, and stage row count.

6. Cleanup after inspection, first dry-run then real delete:
       export AWS_ACCESS_KEY_ID="$GISPULSE_S3_ACCESS_KEY"
       export AWS_SECRET_ACCESS_KEY="$GISPULSE_S3_SECRET_KEY"
       export AWS_DEFAULT_REGION="${GISPULSE_S3_REGION:-us-east-1}"
       aws --endpoint-url "$GISPULSE_S3_ENDPOINT" \
         s3 rm "s3://${N3_SMOKE_BUCKET:-gispulse}/${N3_SMOKE_PREFIX:-smoke-n3/}" \
         --recursive --dryrun
       aws --endpoint-url "$GISPULSE_S3_ENDPOINT" \
         s3 rm "s3://${N3_SMOKE_BUCKET:-gispulse}/${N3_SMOKE_PREFIX:-smoke-n3/}" \
         --recursive

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
from urllib.parse import urlsplit

from gispulse.core.bulk_runner import BulkIngestRunner
from gispulse.core.config import settings
from gispulse.core.plugin_model import AccessProtocol
from gispulse.persistence.duckdb_engine import DuckDBSession

LOGGER = logging.getLogger("n3_smoke")
DEFAULT_SOURCE = "georisques"
DEFAULT_ENTRY = "sis-bulk"
DEFAULT_DEPARTMENT = "63"
DEFAULT_PREFIX = "smoke-n3/"
DEFAULT_REVISION = "smoke-n3"


@dataclass(frozen=True)
class N3SmokeConfig:
    bucket: str
    prefix: str
    source: str = DEFAULT_SOURCE
    entry: str = DEFAULT_ENTRY
    department: str = DEFAULT_DEPARTMENT
    revision: str = DEFAULT_REVISION

    @classmethod
    def from_env(cls) -> "N3SmokeConfig":
        return cls(
            bucket=os.environ.get("N3_SMOKE_BUCKET", settings.s3.bucket).strip()
            or settings.s3.bucket,
            prefix=_normalise_prefix(
                os.environ.get("N3_SMOKE_PREFIX", DEFAULT_PREFIX)
            ),
            source=os.environ.get("N3_SMOKE_SOURCE", DEFAULT_SOURCE).strip()
            or DEFAULT_SOURCE,
            entry=os.environ.get("N3_SMOKE_ENTRY", DEFAULT_ENTRY).strip()
            or DEFAULT_ENTRY,
            department=os.environ.get(
                "N3_SMOKE_DEPARTMENT",
                os.environ.get("N3_SMOKE_DEPT", DEFAULT_DEPARTMENT),
            ).strip()
            or DEFAULT_DEPARTMENT,
            revision=os.environ.get("N3_SMOKE_REVISION", DEFAULT_REVISION).strip()
            or DEFAULT_REVISION,
        )


@dataclass(frozen=True)
class N3SmokeReport:
    raw_s3_uri: str
    stage_s3_uri: str
    raw_size_bytes: int
    stage_size_bytes: int
    stage_row_count: int


def run_smoke(
    config: N3SmokeConfig,
    *,
    source_loader: Callable[[str], Any] | None = None,
    runner_factory: Callable[..., BulkIngestRunner] = BulkIngestRunner,
    s3_client_factory: Callable[[], Any] | None = None,
    duckdb_session_factory: Callable[[], Any] = DuckDBSession,
) -> N3SmokeReport:
    """Run the N3 smoke and verify the stage parquet through DuckDB/S3."""
    source_loader = source_loader or load_source
    s3_client_factory = s3_client_factory or make_s3_client
    prefix = _normalise_prefix(config.prefix)
    _validate_isolation(config.bucket, prefix)

    LOGGER.info(
        "starting N3 smoke: source=%s:%s department=%s bucket=%s prefix=%s revision=%s",
        config.source,
        config.entry,
        config.department,
        config.bucket,
        prefix,
        config.revision,
    )
    source = source_loader(config.source)
    _validate_smoke_entry(source, config.entry)

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


def load_source(source_name: str) -> Any:
    """Load the source used by this smoke.

    The VPS runbook installs the Géorisques plugin in the venv. For direct
    repo checkouts, we also add the bundled plugin path as a convenience.
    """
    if source_name != DEFAULT_SOURCE:
        raise ValueError(
            f"unsupported N3 smoke source {source_name!r}; expected {DEFAULT_SOURCE!r}"
        )

    plugin_path = (
        Path(__file__).resolve().parents[1] / "plugins" / "gispulse-src-georisques"
    )
    if plugin_path.is_dir() and str(plugin_path) not in sys.path:
        sys.path.insert(0, str(plugin_path))

    from gispulse_src_georisques.source import GeorisquesSource

    return GeorisquesSource()


def make_s3_client() -> Any:
    """Create a Garage-compatible S3 client from settings.s3."""
    if not settings.s3.endpoint:
        raise RuntimeError("GISPULSE_S3_ENDPOINT must be set for the N3 smoke")

    try:
        import boto3
        from botocore.config import Config as BotoConfig
    except ImportError as exc:
        raise RuntimeError("boto3 is required; install with pip install -e '.[s3]'") from exc

    from gispulse.persistence.storage import _make_s3_boto_config

    return boto3.client(
        "s3",
        endpoint_url=settings.s3.endpoint,
        aws_access_key_id=settings.s3.access_key or None,
        aws_secret_access_key=settings.s3.secret_key or None,
        region_name=settings.s3.region,
        config=_make_s3_boto_config(BotoConfig),
    )


def _validate_smoke_entry(source: Any, entry_id: str) -> None:
    entry = next((item for item in source.catalog() if item.id == entry_id), None)
    if entry is None:
        raise KeyError(f"{source.name}: unknown entry {entry_id!r}")
    if entry.access.protocol is not AccessProtocol.TABLE_FILE:
        raise ValueError(f"{source.name}:{entry_id} is not a TABLE_FILE source")
    table_format = str(
        entry.access.params.get("table_format")
        or entry.metadata.get("data_format")
        or ""
    ).lower()
    if table_format != "csv":
        raise ValueError(f"{source.name}:{entry_id} is not a CSV TABLE_FILE source")


def _validate_isolation(bucket: str, prefix: str) -> None:
    configured_bucket = settings.s3.bucket
    if not prefix and bucket == configured_bucket:
        raise RuntimeError(
            "refusing to run without N3_SMOKE_PREFIX in the configured S3 bucket"
        )


def _normalise_prefix(prefix: str) -> str:
    clean = prefix.strip().replace("\\", "/").strip("/")
    return f"{clean}/" if clean else ""


def _manifest_uri(manifest: dict[str, object], key: str) -> str:
    value = str(manifest.get(key, "")).strip()
    if not value:
        raise RuntimeError(f"bulk runner manifest is missing {key}")
    return value


def _head_size(s3_client: Any, uri: str, *, expected_bucket: str) -> int:
    bucket, key = _split_s3_uri(uri)
    if bucket != expected_bucket:
        raise RuntimeError(
            f"manifest URI bucket {bucket!r} does not match expected {expected_bucket!r}"
        )
    response = s3_client.head_object(Bucket=bucket, Key=key)
    return int(response["ContentLength"])


def _read_stage_row_count(
    stage_uri: str,
    duckdb_session_factory: Callable[[], Any],
) -> int:
    sql = f"SELECT count(*) FROM read_parquet({_sql_literal(stage_uri)})"
    with duckdb_session_factory() as session:
        row = session.conn.execute(sql).fetchone()
    return int(row[0])


def _split_s3_uri(uri: str) -> tuple[str, str]:
    parsed = urlsplit(uri)
    if parsed.scheme != "s3" or not parsed.netloc:
        raise ValueError(f"expected an s3:// URI, got {uri!r}")
    key = parsed.path.lstrip("/")
    if not key:
        raise ValueError(f"expected a non-empty S3 key in {uri!r}")
    return parsed.netloc, key


def _sql_literal(value: object) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def main() -> int:
    level = os.environ.get("N3_SMOKE_LOG_LEVEL", "INFO").upper()
    logging.basicConfig(level=level, format="%(levelname)s %(message)s")
    try:
        run_smoke(N3SmokeConfig.from_env())
    except Exception:
        LOGGER.exception("N3 smoke failed")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
