"""Bounded vector-file materialization helpers."""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Sequence
from pathlib import Path

DEFAULT_VECTOR_BATCH_SIZE = 65_536


class VectorStreamMaterializeError(RuntimeError):
    """Raised when vector streaming materialization cannot be completed."""


class _ArrowBackendUnavailable(RuntimeError):
    pass


def stream_vector_to_parquet(
    src_path: str | Path,
    out_path: str | Path,
    *,
    layer: str | None = None,
    where: str | None = None,
    bbox: Sequence[float] | None = None,
    batch_size: int = DEFAULT_VECTOR_BATCH_SIZE,
    columns: Sequence[str] | None = None,
) -> None:
    """Stream a vector datasource to parquet without loading all features.

    The primary path uses GDAL through ``pyogrio.raw.open_arrow`` and writes
    every Arrow ``RecordBatch`` immediately through ``pyarrow.parquet``. If the
    Arrow Python stack is unavailable, the helper falls back to GDAL's
    ``ogr2ogr -f Parquet`` command when present.
    """

    batch_size = _validate_batch_size(batch_size)
    src = Path(src_path)
    out = Path(out_path)
    normalized_bbox = _normalize_bbox(bbox)
    normalized_columns = list(columns) if columns is not None else None

    out.parent.mkdir(parents=True, exist_ok=True)

    try:
        _stream_with_pyogrio_arrow(
            src,
            out,
            layer=layer,
            where=where,
            bbox=normalized_bbox,
            batch_size=batch_size,
            columns=normalized_columns,
        )
    except _ArrowBackendUnavailable as exc:
        _stream_with_ogr2ogr(
            src,
            out,
            layer=layer,
            where=where,
            bbox=normalized_bbox,
            columns=normalized_columns,
            unavailable_reason=exc,
        )


def _stream_with_pyogrio_arrow(
    src: Path,
    out: Path,
    *,
    layer: str | None,
    where: str | None,
    bbox: tuple[float, float, float, float] | None,
    batch_size: int,
    columns: list[str] | None,
) -> None:
    try:
        import pyarrow.parquet as pq
        from pyogrio.raw import open_arrow
    except ImportError as exc:
        raise _ArrowBackendUnavailable(str(exc)) from exc

    if open_arrow is None:
        raise _ArrowBackendUnavailable("pyogrio.raw.open_arrow is unavailable")

    writer: pq.ParquetWriter | None = None
    try:
        with open_arrow(
            str(src),
            layer=layer,
            where=where,
            bbox=bbox,
            columns=columns,
            batch_size=batch_size,
            use_pyarrow=True,
        ) as source:
            _meta, reader = source
            for batch in reader:
                if writer is None:
                    writer = pq.ParquetWriter(
                        out,
                        batch.schema,
                        compression="zstd",
                        use_dictionary=True,
                    )
                writer.write_batch(batch)
            if writer is None:
                writer = pq.ParquetWriter(
                    out,
                    reader.schema,
                    compression="zstd",
                    use_dictionary=True,
                )
    finally:
        if writer is not None:
            writer.close()


def _stream_with_ogr2ogr(
    src: Path,
    out: Path,
    *,
    layer: str | None,
    where: str | None,
    bbox: tuple[float, float, float, float] | None,
    columns: list[str] | None,
    unavailable_reason: BaseException,
) -> None:
    ogr2ogr = shutil.which("ogr2ogr")
    if ogr2ogr is None:
        raise VectorStreamMaterializeError(
            "stream_vector_to_parquet requires pyogrio.open_arrow + pyarrow, "
            "or an ogr2ogr executable with Parquet support."
        ) from unavailable_reason

    cmd = [ogr2ogr, "-f", "Parquet"]
    if where:
        cmd.extend(["-where", where])
    if bbox is not None:
        cmd.extend(["-spat", *(str(value) for value in bbox)])
    if columns:
        cmd.extend(["-select", ",".join(columns)])
    cmd.extend([str(out), str(src)])
    if layer:
        cmd.append(layer)

    result = subprocess.run(cmd, check=False, capture_output=True, text=True)
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or "ogr2ogr failed"
        raise VectorStreamMaterializeError(message)


def _validate_batch_size(batch_size: int) -> int:
    try:
        value = int(batch_size)
    except (TypeError, ValueError):
        raise ValueError(f"batch_size must be a positive integer: {batch_size!r}") from None
    if value <= 0:
        raise ValueError(f"batch_size must be a positive integer: {batch_size!r}")
    return value


def _normalize_bbox(
    bbox: Sequence[float] | None,
) -> tuple[float, float, float, float] | None:
    if bbox is None:
        return None
    values = tuple(float(value) for value in bbox)
    if len(values) != 4:
        raise ValueError("bbox must contain exactly four values: minx, miny, maxx, maxy")
    return values
