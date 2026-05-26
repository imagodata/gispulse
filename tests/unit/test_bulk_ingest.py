"""Unit tests for N3 bulk ingest S3 layout helpers."""

from __future__ import annotations

import pytest

from gispulse.core.bulk_ingest import (
    BULK_RAW_PREFIX,
    BULK_STAGE_PREFIX,
    bulk_ingest_manifest_record,
    bulk_s3_key,
)


def test_bulk_s3_key_builds_revisioned_stage_key_for_department_partition() -> None:
    key = bulk_s3_key(
        kind=BULK_STAGE_PREFIX,
        source="gpu",
        entry="gpu_documents_bulk_index",
        departement="69",
        revision="2026-01",
        partition="DU_200046977",
        filename="zone_urba.parquet",
    )

    assert key == (
        "stage/gpu/gpu_documents_bulk_index/millesime=2026-01/"
        "departement=69/partition=DU_200046977/zone_urba.parquet"
    )
    assert ".." not in key
    assert "//" not in key


def test_bulk_s3_key_normalizes_department_codes() -> None:
    key = bulk_s3_key(
        kind=BULK_RAW_PREFIX,
        source="insee",
        entry="iris_bulk",
        departement="2a",
        revision="2026-01-01",
        filename="iris.7z",
    )

    assert key == (
        "raw/insee/iris_bulk/millesime=2026-01-01/"
        "departement=2A/iris.7z"
    )


@pytest.mark.parametrize("unsafe", ["../69", "69/70", "", "."])
def test_bulk_s3_key_rejects_unsafe_path_segments(unsafe: str) -> None:
    with pytest.raises(ValueError):
        bulk_s3_key(
            kind=BULK_STAGE_PREFIX,
            source="georisques",
            entry=unsafe,
            departement="69",
            revision="2026",
            filename="gaspar.parquet",
        )


def test_bulk_ingest_manifest_record_lists_scope_revision_and_uris() -> None:
    record = bulk_ingest_manifest_record(
        bucket="gispulse",
        source="georisques",
        entry="gaspar-bulk",
        departement="national",
        revision="gaspar-2025",
        raw_filename="gaspar.zip",
        stage_filename="gaspar.parquet",
        row_count=42,
        status="success",
    )

    assert record == {
        "source": "georisques",
        "entry": "gaspar-bulk",
        "scope": "national",
        "revision": "gaspar-2025",
        "raw_s3_uri": (
            "s3://gispulse/raw/georisques/gaspar-bulk/"
            "millesime=gaspar-2025/national/gaspar.zip"
        ),
        "stage_s3_uri": (
            "s3://gispulse/stage/georisques/gaspar-bulk/"
            "millesime=gaspar-2025/national/gaspar.parquet"
        ),
        "row_count": 42,
        "status": "success",
    }
