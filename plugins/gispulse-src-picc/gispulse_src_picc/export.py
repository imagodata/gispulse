"""Bounded local PICC export; dry-run by default, no client classification."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from gispulse.adapters.rest.rest_fetcher import RestGeoJsonFetcher
from gispulse.core.io.geoparquet import write_geoparquet
from gispulse_src_picc.source import PiccSource


def export_picc(
    *,
    bbox: tuple[float, float, float, float],
    output: Path,
    write: bool = False,
    page_size: int = 2000,
    max_pages: int = 1000,
    max_features: int = 1_000_000,
) -> dict:
    """Publish both raw layers only after successful fetch and validation.

    Bounds apply per layer. The report records source counts, checksums and CRS;
    it does not certify a remote snapshot or interpret NIVEAU as ground level.
    """
    if (
        len(bbox) != 4
        or not all(math.isfinite(v) for v in bbox)
        or not -180 <= bbox[0] < bbox[2] <= 180
        or not -90 <= bbox[1] < bbox[3] <= 90
    ):
        raise ValueError("PICC_EXTENT_INVALID: ordered finite WGS84 bbox required")
    if page_size > 2000:
        raise ValueError("PICC_PAGE_SIZE_INVALID: service limit is 2000")
    source = PiccSource()
    accesses = []
    for entry in source.entries():
        access = source.access_for(entry.id)
        access.params["pagination"].update(
            page_size=page_size, max_pages=max_pages, max_features=max_features
        )
        from gispulse.adapters.rest.offset_pages import OffsetPagination

        OffsetPagination.from_params(access.params["pagination"])
        accesses.append((entry, access))
    if output.exists():
        raise ValueError("PICC_OUTPUT_EXISTS: choose a fresh output directory")
    report = {
        "status": "planned",
        "bbox_wgs84": list(bbox),
        "target_crs": "EPSG:31370",
        "page_size": page_size,
        "max_pages": max_pages,
        "max_features": max_features,
        "entries": [e.id for e, _ in accesses],
        "output": str(output),
    }
    if not write:
        return report
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".picc-", dir=output.parent))
    try:
        layers = []
        for entry, access in accesses:
            result = RestGeoJsonFetcher().fetch(access, extent=bbox)
            frame = result.data.to_crs(31370)
            if len(frame):
                allowed = (
                    {"Polygon", "MultiPolygon"}
                    if entry.id == "picc-road-surfaces-wa"
                    else {"LineString", "MultiLineString"}
                )
                if (
                    not {"OBJECTID", "NATUR_DESC", "NIVEAU"}.issubset(frame.columns)
                    or frame.geometry.isna().any()
                    or frame.geometry.is_empty.any()
                    or not frame.geometry.is_valid.all()
                    or not set(frame.geom_type).issubset(allowed)
                ):
                    raise ValueError(
                        f"PICC_LAYER_INVALID: inspect raw geometry/schema of {entry.id}"
                    )
            filename = f"{entry.id}.geoparquet"
            target = staging / filename
            write_geoparquet(frame, str(target), compression="zstd")
            with target.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            layers.append(
                {
                    "entry": entry.id,
                    "file": filename,
                    "sha256": digest,
                    "source": entry.metadata,
                    "columns": list(frame.columns),
                    **result.metadata,
                }
            )
        report.update(
            status="complete", layers=layers, fetched_at=datetime.now(timezone.utc).isoformat()
        )
        (staging / "report.json").write_text(json.dumps(report, indent=2) + "\n")
        if output.exists():
            raise ValueError("PICC_OUTPUT_EXISTS: output appeared during extraction")
        staging.rename(output)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bbox", type=float, nargs=4, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--page-size", type=int, default=2000)
    parser.add_argument("--max-pages", type=int, default=1000)
    parser.add_argument("--max-features", type=int, default=1_000_000)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    try:
        report = export_picc(
            bbox=tuple(args.bbox),
            output=args.output,
            write=args.write,
            page_size=args.page_size,
            max_pages=args.max_pages,
            max_features=args.max_features,
        )
    except Exception as exc:
        print(
            json.dumps(
                {
                    "status": "error",
                    "code": str(exc).split(":", 1)[0],
                    "context": str(exc),
                    "recovery": "inspect source and bounds; retry to a fresh output directory",
                }
            )
        )
        return 1
    print(json.dumps(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
