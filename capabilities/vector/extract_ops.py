from __future__ import annotations


import geopandas as gpd

from capabilities.base import Capability
from capabilities.registry import register


# ---------------------------------------------------------------------------
# Vertex / segment manipulation (QGIS processing analogues)
# ---------------------------------------------------------------------------


@register
class ExtractVerticesCapability(Capability):
    """Extracts every vertex of every geometry as a Point layer."""

    name = "extract_vertices"
    description = (
        "Produces a Point GeoDataFrame with one row per vertex of the input "
        "geometries. Adds vertex_index (per-feature) and global_index columns."
    )

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        keep_attrs: bool = True,
        **_,
    ) -> gpd.GeoDataFrame:
        """
        Args:
            gdf:        Input GeoDataFrame.
            keep_attrs: Copy the source feature attributes onto each vertex.
        """
        from shapely.geometry import Point

        if gdf.empty:
            return gpd.GeoDataFrame(geometry=[], crs=gdf.crs)

        rows: list[dict] = []
        global_idx = 0
        for _, row in gdf.iterrows():
            geom = row.geometry
            if geom is None or geom.is_empty:
                continue
            for vi, coord in enumerate(_iter_coords(geom)):
                base = row.to_dict() if keep_attrs else {}
                base["geometry"] = Point(coord[0], coord[1])
                base["vertex_index"] = vi
                base["global_index"] = global_idx
                rows.append(base)
                global_idx += 1

        if not rows:
            return gpd.GeoDataFrame(geometry=[], crs=gdf.crs)
        return gpd.GeoDataFrame(rows, crs=gdf.crs)

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "keep_attrs": {
                    "type": "boolean",
                    "default": True,
                    "description": "Carry source attributes onto each vertex.",
                },
            },
        }


@register
class ExtractSegmentsCapability(Capability):
    """Extracts each consecutive vertex pair as a 2-point LineString."""

    name = "extract_segments"
    description = (
        "Splits every LineString / polygon boundary into its individual "
        "2-point segments. Adds segment_index and length columns."
    )

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        crs_meters: str | None = "EPSG:3857",
        keep_attrs: bool = True,
        **_,
    ) -> gpd.GeoDataFrame:
        """
        Args:
            gdf:        Input GeoDataFrame.
            crs_meters: Metric CRS used to compute segment lengths. When
                        None, lengths are in native CRS units.
            keep_attrs: Propagate parent attributes onto each segment.
        """
        from shapely.geometry import LineString

        if gdf.empty:
            return gpd.GeoDataFrame(geometry=[], crs=gdf.crs)

        rows: list[dict] = []
        for _, row in gdf.iterrows():
            geom = row.geometry
            if geom is None or geom.is_empty:
                continue
            coords = list(_iter_coords(geom))
            for si in range(len(coords) - 1):
                base = row.to_dict() if keep_attrs else {}
                seg = LineString([coords[si], coords[si + 1]])
                base["geometry"] = seg
                base["segment_index"] = si
                rows.append(base)

        if not rows:
            return gpd.GeoDataFrame(geometry=[], crs=gdf.crs)

        result = gpd.GeoDataFrame(rows, crs=gdf.crs)
        if crs_meters and result.crs is not None:
            metric = result.to_crs(crs_meters)
            result["length"] = metric.geometry.length
        else:
            result["length"] = result.geometry.length
        return result

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "crs_meters": {
                    "type": ["string", "null"],
                    "default": "EPSG:3857",
                    "description": "Metric CRS for length computation.",
                },
                "keep_attrs": {
                    "type": "boolean",
                    "default": True,
                },
            },
        }


@register
class DensifyVerticesCapability(Capability):
    """Adds vertices along each segment at a fixed distance or interval count."""

    name = "densify_vertices"
    description = (
        "Inserts additional vertices along each segment — either at a "
        "fixed spacing (max_distance) or by splitting each segment into "
        "n equal parts."
    )

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        max_distance: float | None = None,
        n_vertices_per_segment: int | None = None,
        crs_meters: str = "EPSG:3857",
        **_,
    ) -> gpd.GeoDataFrame:
        """
        Args:
            gdf:                    Input GeoDataFrame.
            max_distance:           If set, ensures no segment is longer than
                                    this value (in *crs_meters* units).
            n_vertices_per_segment: Alternative: split each segment into
                                    N equal parts (N >= 1).
            crs_meters:             Metric CRS used for distance.

        Exactly one of *max_distance* or *n_vertices_per_segment* must be set.
        """
        from shapely import segmentize

        if gdf.empty:
            return gdf.copy()

        if (max_distance is None) == (n_vertices_per_segment is None):
            raise ValueError(
                "Provide exactly one of 'max_distance' or 'n_vertices_per_segment'."
            )

        original_crs = gdf.crs
        work = gdf.to_crs(crs_meters) if original_crs is not None else gdf.copy()

        if max_distance is not None:
            if max_distance <= 0:
                raise ValueError("max_distance must be > 0.")
            work["geometry"] = [segmentize(g, max_distance) for g in work.geometry]
        else:
            if n_vertices_per_segment < 1:
                raise ValueError("n_vertices_per_segment must be >= 1.")
            # Derive a per-geometry tolerance by dividing each segment's length.
            new_geoms = []
            for g in work.geometry:
                if g is None or g.is_empty:
                    new_geoms.append(g)
                    continue
                # Use length of the smallest segment / N as the max_distance.
                coords = list(_iter_coords(g))
                if len(coords) < 2:
                    new_geoms.append(g)
                    continue
                seg_lengths = [
                    ((coords[i + 1][0] - coords[i][0]) ** 2
                     + (coords[i + 1][1] - coords[i][1]) ** 2) ** 0.5
                    for i in range(len(coords) - 1)
                ]
                if not seg_lengths:
                    new_geoms.append(g)
                    continue
                step = min(seg_lengths) / n_vertices_per_segment
                new_geoms.append(segmentize(g, max(step, 1e-9)))
            work["geometry"] = new_geoms

        return work.to_crs(original_crs) if original_crs is not None else work

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "max_distance": {
                    "type": ["number", "null"],
                    "description": "Max segment length (crs_meters).",
                },
                "n_vertices_per_segment": {
                    "type": ["integer", "null"],
                    "description": "Number of equal parts per segment.",
                },
                "crs_meters": {
                    "type": "string",
                    "default": "EPSG:3857",
                },
            },
        }


def _iter_coords(geom):
    """Iterate over all (x, y, ...) vertex coordinates of a geometry."""
    gt = geom.geom_type
    if gt == "Point":
        yield (geom.x, geom.y)
    elif gt in ("LineString", "LinearRing"):
        yield from geom.coords
    elif gt == "Polygon":
        yield from geom.exterior.coords
        for ring in geom.interiors:
            yield from ring.coords
    elif gt.startswith("Multi") or gt == "GeometryCollection":
        for part in geom.geoms:
            yield from _iter_coords(part)


