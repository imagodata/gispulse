from __future__ import annotations


import geopandas as gpd
import pandas as pd

from capabilities.base import Capability
from capabilities.registry import register



@register
class MergeLayersCapability(Capability):
    """Concatenates the primary layer with one or more reference layers.

    Accepts a list of layers (primary ``gdf`` + ``ref_gdfs``) and stacks their
    features into a single GeoDataFrame. Attributes are preserved; layers with
    different column sets get NaN in missing columns (pandas concat semantics).
    CRS from the primary layer is retained; other layers are reprojected if
    their CRS differs.

    Pipeline plumbing: the orchestrator resolves ``ref_layers`` (list of step
    ids / layer aliases) into ``ref_gdfs`` before calling ``execute``. This is
    the sibling of ``ref_layer``/``ref_gdf`` used by spatial_join, but for the
    N-layer case — e.g. merging concentric isochrones into a single ref layer
    for a downstream ``spatial_aggregate`` with ``agg: min``.

    Example::

        {"ref_layers": ["isochrone_500m", "isochrone_750m",
                        "isochrone_1000m", "isochrone_1500m"]}
    """

    name = "merge_layers"
    description = (
        "Concatenates the primary layer with one or more reference layers "
        "(ref_layers: list) into a single GeoDataFrame."
    )

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        ref_gdfs: list[gpd.GeoDataFrame] | None = None,
        **_,
    ) -> gpd.GeoDataFrame:
        """
        Args:
            gdf:      Primary layer (first in the merge).
            ref_gdfs: Additional layers to concatenate, injected by the
                      executor from the ``ref_layers`` list parameter.

        Returns:
            GeoDataFrame stacking all input layers. CRS follows ``gdf``.
        """
        layers: list[gpd.GeoDataFrame] = [gdf]
        if ref_gdfs:
            target_crs = gdf.crs
            for extra in ref_gdfs:
                if extra is None or len(extra) == 0:
                    continue
                if target_crs is not None and extra.crs is not None and extra.crs != target_crs:
                    extra = extra.to_crs(target_crs)
                layers.append(extra)

        if len(layers) == 1:
            return gdf.copy()

        merged = pd.concat(layers, ignore_index=True)
        return gpd.GeoDataFrame(merged, geometry=gdf.geometry.name, crs=gdf.crs)

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "ref_layers": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Aliases / step ids of additional layers to stack "
                        "onto the primary layer."
                    ),
                },
            },
        }

