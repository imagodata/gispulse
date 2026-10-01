"""Acquire a bounded UrbIS bundle with road-crossings level evidence; dry-run by default."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from shapely.geometry import box

from gispulse.adapters.ogc.wfs_fetcher import WfsFetcher
from gispulse.capabilities.vector.urbis_road_crossings import build_urbis_road_crossings
from gispulse.core.io.geoparquet import write_geoparquet
from gispulse_src_urbis.source import ENTRIES, UrbisSource

# GeoJSON empties omit property schemas; the crossings read these fields.
_FIELDS = {
    "urbis-street-surfaces-bxl": ("INSPIRE_ID", "TYPE", "LVL"),
    "urbis-street-axes-bxl": ("INSPIRE_ID", "TYPE", "LVL", "HIERARCHY"),
    "urbis-bridges-bxl": ("INSPIRE_ID", "TYPE", "LVL"),
    "urbis-tunnels-bxl": ("INSPIRE_ID", "TYPE", "LVL"),
}


def prepare_urbis(
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
    crossing_exclusion_buffer_m: float = 15.0,
) -> dict:
    """Publish the four raw UrbIS layers, then the road-crossings level evidence.

    The bbox is in the native EPSG:31370 CRS; limits apply per layer. A defect
    in the level evidence degrades that artifact alone, never the raw layers.
    """
    if (
        len(bbox) != 4
        or not all(math.isfinite(v) for v in bbox)
        or bbox[0] >= bbox[2]
        or bbox[1] >= bbox[3]
    ):
        raise ValueError("URBIS_EXTENT_INVALID: ordered finite EPSG:31370 bbox required")
    if (
        any(
            not math.isfinite(v) or v < 0
            for v in (area_tolerance_m2, length_tolerance_m, boundary_tolerance_m)
        )
        or not math.isfinite(crossing_exclusion_buffer_m)
        or crossing_exclusion_buffer_m <= 0
    ):
        raise ValueError("URBIS_TOLERANCE_INVALID: nonnegative finite tolerances required")
    if output.exists():
        raise ValueError("URBIS_OUTPUT_EXISTS: choose a fresh output directory")
    source = UrbisSource()
    accesses = []
    for entry in ENTRIES:
        access = source.access_for(entry)
        access.params["pagination"].update(
            page_size=page_size, max_pages=max_pages, max_features=max_features
        )
        accesses.append((entry, access))
    report = {
        "status": "planned",
        "bbox_epsg31370": list(bbox),
        "entries": list(ENTRIES),
        "output": str(output),
        "page_size": page_size,
        "max_pages": max_pages,
        "max_features": max_features,
        "area_tolerance_m2": area_tolerance_m2,
        "length_tolerance_m": length_tolerance_m,
        "boundary_tolerance_m": boundary_tolerance_m,
        "crossing_exclusion_buffer_m": crossing_exclusion_buffer_m,
    }
    if not write:
        return report
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".urbis-", dir=output.parent))
    try:
        frames, layers = {}, []
        for entry, access in accesses:
            result = WfsFetcher().fetch(access, extent=bbox)
            if result.data.empty:
                for column in _FIELDS[entry]:
                    result.data[column] = []
            frames[entry] = result.data
            filename = f"{entry}.geoparquet"
            write_geoparquet(result.data, str(staging / filename), compression="zstd")
            layers.append({"entry": entry, "file": filename, "source": "UrbIS", **result.metadata})
        try:
            crossings, coverage, exclusions, crossings_report = build_urbis_road_crossings(
                frames["urbis-street-surfaces-bxl"],
                frames["urbis-street-axes-bxl"],
                frames["urbis-bridges-bxl"],
                frames["urbis-tunnels-bxl"],
                coverage_area=box(*bbox),
                length_tolerance_m=length_tolerance_m,
                area_tolerance_m2=area_tolerance_m2,
                boundary_tolerance_m=boundary_tolerance_m,
                exclusion_buffer_m=crossing_exclusion_buffer_m,
            )
        except ValueError as exc:
            if not str(exc).startswith("URBIS_CROSSINGS_"):
                raise
            crossings_report = {"status": "failed", "error": str(exc)}
        else:
            for name, frame in [
                ("road_crossings", crossings),
                ("road_crossings_coverage", coverage),
                ("road_crossings_exclusions", exclusions),
            ]:
                write_geoparquet(frame, str(staging / f"{name}.geoparquet"), compression="zstd")
            crossings_report = {"status": "prepared", **crossings_report}
        hashes = {}
        for file in sorted(staging.glob("*.geoparquet")):
            with file.open("rb") as stream:
                hashes[file.name] = hashlib.file_digest(stream, "sha256").hexdigest()
        report.update(
            status="complete",
            layers=layers,
            sha256=hashes,
            fetched_at=datetime.now(UTC).isoformat(),
            road_crossings=crossings_report,
        )
        (staging / "report.json").write_text(json.dumps(report, indent=2) + "\n")
        if output.exists():
            raise ValueError("URBIS_OUTPUT_EXISTS: output appeared during acquisition")
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
    parser.add_argument("--crossing-exclusion-buffer-m", type=float, default=15.0)
    args = parser.parse_args()
    try:
        report = prepare_urbis(
            bbox=tuple(args.bbox),
            output=args.output,
            write=args.write,
            page_size=args.page_size,
            max_pages=args.max_pages,
            max_features=args.max_features,
            crossing_exclusion_buffer_m=args.crossing_exclusion_buffer_m,
        )
    except Exception as exc:  # noqa: BLE001 - CLI boundary
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
