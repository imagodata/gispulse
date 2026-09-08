"""Acquire a bounded GRB bundle and partition corridors; dry-run by default."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from gispulse.adapters.ogc.wfs_fetcher import WfsFetcher
from gispulse.adapters.rest.offset_pages import OffsetPagination
from gispulse.capabilities.vector.polygon_partition import partition_polygons
from gispulse.core.io.geoparquet import write_geoparquet
from gispulse_src_grb.source import GrbSource

_ENTRIES = (
    "grb-wegbaan-vl",
    "grb-wegopdeling-vl",
    "grb-wegsegment-vl",
    "grb-wegknoop-vl",
    "grb-kunstwerk-vl",
)


def prepare_grb(
    *,
    bbox: tuple,
    output: Path,
    write: bool = False,
    page_size: int = 2000,
    max_pages: int = 1000,
    max_features: int = 1_000_000,
    area_tolerance_m2: float = 1e-6,
    length_tolerance_m: float = 1e-6,
) -> dict:
    """Publish raw layers plus unclassified candidate faces, never a costing layer.

    Tolerances account for numerical residuals only; no snapping or simplification.
    Limits apply per layer. The bbox is in the native EPSG:31370 CRS.
    """
    if (
        len(bbox) != 4
        or not all(math.isfinite(v) for v in bbox)
        or bbox[0] >= bbox[2]
        or bbox[1] >= bbox[3]
    ):
        raise ValueError("GRB_EXTENT_INVALID: ordered finite EPSG:31370 bbox required")
    if any(not math.isfinite(v) or v < 0 for v in (area_tolerance_m2, length_tolerance_m)):
        raise ValueError("GRB_TOLERANCE_INVALID: nonnegative finite tolerances required")
    if output.exists():
        raise ValueError("GRB_OUTPUT_EXISTS: choose a fresh output directory")
    source = GrbSource()
    accesses = []
    for entry in _ENTRIES:
        access = source.access_for(entry)
        access.params["pagination"].update(
            page_size=page_size, max_pages=max_pages, max_features=max_features
        )
        OffsetPagination.from_params(access.params["pagination"])
        accesses.append((entry, access))
    report = {
        "status": "planned",
        "bbox_epsg31370": list(bbox),
        "entries": list(_ENTRIES),
        "output": str(output),
        "page_size": page_size,
        "max_pages": max_pages,
        "max_features": max_features,
        "area_tolerance_m2": area_tolerance_m2,
        "length_tolerance_m": length_tolerance_m,
        "ready_for_costing": False,
    }
    if not write:
        return report
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".grb-", dir=output.parent))
    try:
        frames, layers = {}, []
        for entry, access in accesses:
            result = WfsFetcher().fetch(access, extent=bbox)
            frames[entry] = result.data
            # GeoJSON empties omit property schemas; restore typed empty source IDs for partitioning.
            if result.data.empty:
                for column in ("OIDN", "UIDN"):
                    result.data[column] = []
            filename = entry + ".geoparquet"
            write_geoparquet(result.data, str(staging / filename), compression="zstd")
            layers.append(
                {
                    "entry": entry,
                    "file": filename,
                    "endpoint": access.endpoint,
                    "source": "GRB",
                    "spatial_filter": "native_intersects",
                    **result.metadata,
                }
            )
        faces, diagnostics = partition_polygons(
            frames["grb-wegbaan-vl"],
            frames["grb-wegopdeling-vl"],
            polygon_id="OIDN",
            boundary_id="OIDN",
            coverage_bounds=bbox,
            area_tolerance_m2=area_tolerance_m2,
            length_tolerance_m=length_tolerance_m,
        )
        write_geoparquet(faces, str(staging / "candidate_faces.geoparquet"), compression="zstd")
        write_geoparquet(
            diagnostics, str(staging / "partition_diagnostics.geoparquet"), compression="zstd"
        )
        hashes = {}
        for file in sorted(staging.glob("*.geoparquet")):
            with file.open("rb") as stream:
                hashes[file.name] = hashlib.file_digest(stream, "sha256").hexdigest()
        report.update(
            status="prepared_candidates",
            layers=layers,
            sha256=hashes,
            fetched_at=datetime.now(UTC).isoformat(),
            candidate_faces=len(faces),
            topology=diagnostics.status.value_counts().sort_index().to_dict(),
        )
        (staging / "report.json").write_text(json.dumps(report, indent=2) + "\n")
        if output.exists():
            raise ValueError("GRB_OUTPUT_EXISTS: output appeared during acquisition")
        staging.rename(output)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bbox", nargs=4, type=float, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--page-size", type=int, default=2000)
    parser.add_argument("--max-pages", type=int, default=1000)
    parser.add_argument("--max-features", type=int, default=1_000_000)
    parser.add_argument("--area-tolerance-m2", type=float, default=1e-6)
    parser.add_argument("--length-tolerance-m", type=float, default=1e-6)
    args = parser.parse_args()
    try:
        report = prepare_grb(
            bbox=tuple(args.bbox),
            output=args.output,
            write=args.write,
            page_size=args.page_size,
            max_pages=args.max_pages,
            max_features=args.max_features,
            area_tolerance_m2=args.area_tolerance_m2,
            length_tolerance_m=args.length_tolerance_m,
        )
    except Exception as exc:
        print(
            json.dumps(
                {
                    "status": "error",
                    "code": str(exc).split(":", 1)[0],
                    "context": str(exc),
                    "recovery": "inspect source, bounds and topology; retry to a fresh directory",
                }
            )
        )
        return 1
    print(json.dumps(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
