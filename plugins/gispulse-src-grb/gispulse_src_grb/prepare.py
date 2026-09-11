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
from gispulse.capabilities.vector.classify_faces import validate_functional_faces
from gispulse.capabilities.vector.classify_grb_faces import classify_grb_faces
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

# GeoJSON empties omit property schemas; the classification reads these codes.
_CLASSIFIED_FIELDS = {
    "grb-wegopdeling-vl": ("TYPE",),
    "grb-wegsegment-vl": ("WS_OIDN", "VERH", "STATUS"),
}


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
    boundary_tolerance_m: float = 1e-3,
    axis_coverage_ratio_min: float = 0.8,
    axis_min_extent_m: float = 5.0,
) -> dict:
    """Publish raw layers, unclassified candidate faces and an evidence-based classification.

    Tolerances account for numerical residuals only; no snapping or simplification.
    Limits apply per layer. The bbox is in the native EPSG:31370 CRS. The
    classification thresholds are explicit and recorded in the report.
    """
    if (
        len(bbox) != 4
        or not all(math.isfinite(v) for v in bbox)
        or bbox[0] >= bbox[2]
        or bbox[1] >= bbox[3]
    ):
        raise ValueError("GRB_EXTENT_INVALID: ordered finite EPSG:31370 bbox required")
    if any(
        not math.isfinite(v) or v < 0
        for v in (area_tolerance_m2, length_tolerance_m, boundary_tolerance_m, axis_min_extent_m)
    ):
        raise ValueError("GRB_TOLERANCE_INVALID: nonnegative finite tolerances required")
    if not math.isfinite(axis_coverage_ratio_min) or not 0.0 <= axis_coverage_ratio_min <= 1.0:
        raise ValueError("GRB_RATIO_INVALID: classification ratio required within [0, 1]")
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
        "boundary_tolerance_m": boundary_tolerance_m,
        "axis_coverage_ratio_min": axis_coverage_ratio_min,
        "axis_min_extent_m": axis_min_extent_m,
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
                for column in ("OIDN", "UIDN", *_CLASSIFIED_FIELDS.get(entry, ())):
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
        # candidate_faces and diagnostics never depended on Wegsegment before
        # classification existed, and still do not: publish them first so a
        # Wegsegment data defect (below) degrades the bundle instead of
        # losing hours of paginated WFS acquisition outright.
        write_geoparquet(faces, str(staging / "candidate_faces.geoparquet"), compression="zstd")
        write_geoparquet(
            diagnostics, str(staging / "partition_diagnostics.geoparquet"), compression="zstd"
        )
        try:
            classified, classification = classify_grb_faces(
                faces,
                frames["grb-wegopdeling-vl"],
                frames["grb-wegsegment-vl"],
                axis_coverage_ratio_min=axis_coverage_ratio_min,
                axis_min_extent_m=axis_min_extent_m,
                boundary_tolerance_m=boundary_tolerance_m,
                length_tolerance_m=length_tolerance_m,
            )
        except ValueError as exc:
            # A single bad Wegsegment record (duplicate/blank ID, invalid
            # geometry, missing required field) must not erase raw layers and
            # candidate_faces that acquisition already paid for. Only the
            # classifier's own GRB_CLASSIFY_* data-validation errors degrade
            # like this: anything else (in particular validate_functional_faces
            # below, or an I/O failure) is a real bug or a real infrastructure
            # failure and must not be silently relabelled as a data defect.
            if not str(exc).startswith("GRB_CLASSIFY_"):
                raise
            classification_report = {"status": "failed", "error": str(exc)}
        else:
            # Self-check, deliberately outside the except above: a mismatch here
            # is a classifier bug, never caught. Its own ready_for_costing column
            # is NOT what gets published below — this chantier's mandate keeps
            # that flag false until axis/structure reconciliation is complete,
            # and a per-face True in the published file would invite a
            # downstream reader to skip straight to costing on this alone.
            validate_functional_faces(classified)
            write_geoparquet(
                classified, str(staging / "classified_faces.geoparquet"), compression="zstd"
            )
            classification_report = classification
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
            classification=classification_report,
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
    parser.add_argument("--boundary-tolerance-m", type=float, default=1e-3)
    parser.add_argument("--axis-coverage-ratio-min", type=float, default=0.8)
    parser.add_argument("--axis-min-extent-m", type=float, default=5.0)
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
            boundary_tolerance_m=args.boundary_tolerance_m,
            axis_coverage_ratio_min=args.axis_coverage_ratio_min,
            axis_min_extent_m=args.axis_min_extent_m,
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
