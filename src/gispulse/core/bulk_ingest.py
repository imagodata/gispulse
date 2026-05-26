"""N3 bulk ingest S3 layout helpers.

The helpers in this module are intentionally pure: they only turn a
declarative source partition into stable Garage/S3 keys and manifest records.
The actual runner and network materialisation live elsewhere.
"""

from __future__ import annotations

from typing import Literal

from gispulse.core.config import settings

BULK_RAW_PREFIX = "raw"
BULK_STAGE_PREFIX = "stage"

BulkS3Kind = Literal["raw", "stage"]

__all__ = [
    "BULK_RAW_PREFIX",
    "BULK_STAGE_PREFIX",
    "BulkS3Kind",
    "bulk_ingest_manifest_record",
    "bulk_s3_key",
    "bulk_s3_uri",
    "normalize_bulk_department",
]


def _clean_segment(value: object, *, label: str) -> str:
    segment = str(value).strip()
    if (
        not segment
        or segment in {".", ".."}
        or "/" in segment
        or "\\" in segment
        or "//" in segment
        or ".." in segment
    ):
        raise ValueError(f"Invalid {label} path segment: {value!r}")
    return segment


def normalize_bulk_department(departement: object | None) -> str:
    """Return the canonical department scope used in bulk S3 keys."""
    if departement is None:
        return "national"

    value = str(departement).strip().upper()
    if value == "NATIONAL":
        return "national"
    if value in {"2A", "2B"}:
        return value
    if value.isdigit():
        return value.zfill(2) if len(value) <= 2 else value
    return _clean_segment(value, label="departement")


def _scope_segments(
    *, departement: object | None, partition: object | None
) -> tuple[str, ...]:
    dept = normalize_bulk_department(departement)
    if dept == "national":
        segments = ["national"]
    else:
        segments = [f"departement={dept}"]
    if partition is not None:
        part = _clean_segment(partition, label="partition")
        segments.append(f"partition={part}")
    return tuple(segments)


def _scope_value(*, departement: object | None, partition: object | None) -> str:
    return "/".join(_scope_segments(departement=departement, partition=partition))


def bulk_s3_key(
    *,
    kind: BulkS3Kind,
    source: object,
    entry: object,
    departement: object | None,
    revision: object,
    filename: object,
    partition: object | None = None,
) -> str:
    """Build a stable N3 raw/stage S3 object key.

    Layout:

    ``<raw|stage>/<source>/<entry>/millesime=<revision>/<scope>/<filename>``

    where ``scope`` is either ``departement=<dept>`` (plus optional
    ``partition=<partition>``) or ``national`` for national-only tables.
    """
    clean_kind = _clean_segment(kind, label="kind")
    if clean_kind not in {BULK_RAW_PREFIX, BULK_STAGE_PREFIX}:
        raise ValueError(
            f"Invalid bulk S3 kind: {kind!r}; expected 'raw' or 'stage'"
        )
    clean_source = _clean_segment(source, label="source")
    clean_entry = _clean_segment(entry, label="entry")
    clean_revision = _clean_segment(revision, label="revision")
    clean_filename = _clean_segment(filename, label="filename")

    segments = [
        clean_kind,
        clean_source,
        clean_entry,
        f"millesime={clean_revision}",
        *_scope_segments(departement=departement, partition=partition),
        clean_filename,
    ]
    return "/".join(segments)


def bulk_s3_uri(*, key: str, bucket: object | None = None) -> str:
    """Resolve a bulk S3 key into an ``s3://`` URI."""
    clean_key = str(key).strip().lstrip("/")
    if not clean_key or ".." in clean_key or "//" in clean_key:
        raise ValueError(f"Invalid bulk S3 key: {key!r}")
    bucket_name = _clean_segment(bucket or settings.s3.bucket, label="bucket")
    return f"s3://{bucket_name}/{clean_key}"


def bulk_ingest_manifest_record(
    *,
    source: object,
    entry: object,
    departement: object | None,
    revision: object,
    raw_filename: object,
    stage_filename: object,
    partition: object | None = None,
    bucket: object | None = None,
    row_count: int | None = None,
    status: str = "pending",
) -> dict[str, object]:
    """Build the manifest record emitted by a future N3 bulk ingest runner."""
    clean_source = _clean_segment(source, label="source")
    clean_entry = _clean_segment(entry, label="entry")
    clean_revision = _clean_segment(revision, label="revision")
    clean_status = _clean_segment(status, label="status")

    raw_key = bulk_s3_key(
        kind=BULK_RAW_PREFIX,
        source=clean_source,
        entry=clean_entry,
        departement=departement,
        revision=clean_revision,
        partition=partition,
        filename=raw_filename,
    )
    stage_key = bulk_s3_key(
        kind=BULK_STAGE_PREFIX,
        source=clean_source,
        entry=clean_entry,
        departement=departement,
        revision=clean_revision,
        partition=partition,
        filename=stage_filename,
    )
    return {
        "source": clean_source,
        "entry": clean_entry,
        "scope": _scope_value(departement=departement, partition=partition),
        "revision": clean_revision,
        "raw_s3_uri": bulk_s3_uri(key=raw_key, bucket=bucket),
        "stage_s3_uri": bulk_s3_uri(key=stage_key, bucket=bucket),
        "row_count": row_count,
        "status": clean_status,
    }
