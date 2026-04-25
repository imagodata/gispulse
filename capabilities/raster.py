"""
Raster capabilities for GISPulse.

Requires optional dependencies:
    - rasterstats  (ZonalStatsCapability)
    - rasterio     (ChangeDetectionCapability, RasterClipCapability,
                    NdviCapability, RasterReprojectCapability,
                    RasterMergeCapability)
    - numpy        (ChangeDetectionCapability, NdviCapability)

All capabilities except basic Community ones require tier="pro".
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import geopandas as gpd

from capabilities.base import Capability
from capabilities.registry import register
from persistence.tier import check_tier


@register
class ZonalStatsCapability(Capability):
    """Calcule des statistiques d'un raster pour chaque polygone d'une couche vecteur."""

    name = "zonal_stats"
    description = "Computes raster statistics (min/max/mean/std/sum/count) for each polygon."

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        raster_path: str = "",
        stats: list[str] | None = None,
        prefix: str = "rs_",
        nodata: float | None = None,
        band: int = 1,
        **_,
    ) -> gpd.GeoDataFrame:
        """
        Args:
            gdf:         Couche vecteur (polygones).
            raster_path: Chemin vers le fichier raster (.tif, .img, …).
            stats:       Liste de stats à calculer. Défaut: ["min","max","mean","std","count"].
            prefix:      Préfixe des colonnes résultantes.
            nodata:      Valeur nodata à ignorer (surcharge la valeur du raster).
            band:        Index de bande (1-based, conforme GDAL).
        """
        check_tier("pro")

        try:
            from rasterstats import zonal_stats
        except ImportError as exc:
            raise ImportError(
                "ZonalStatsCapability requires 'rasterstats'. "
                "Install with: pip install rasterstats"
            ) from exc

        if not raster_path:
            raise ValueError("ZonalStatsCapability requires 'raster_path'.")
        if not Path(raster_path).exists():
            raise FileNotFoundError(f"Raster not found: {raster_path}")

        stats = stats or ["min", "max", "mean", "std", "count"]

        kwargs: dict[str, Any] = {"stats": stats, "prefix": prefix, "band": band}
        if nodata is not None:
            kwargs["nodata"] = nodata

        results = zonal_stats(
            vectors=gdf.geometry,
            raster=raster_path,
            **kwargs,
        )

        result_df = gpd.GeoDataFrame(results, geometry=gdf.geometry.values, crs=gdf.crs)
        # Réintègre les colonnes attributaires de l'input
        for col in gdf.columns:
            if col not in result_df.columns:
                result_df[col] = gdf[col].values
        return result_df.reset_index(drop=True)

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "raster_path": {"type": "string", "description": "Path to the raster file."},
                "stats": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": ["min", "max", "mean", "std", "count", "sum", "median", "range"],
                    },
                    "default": ["min", "max", "mean", "std", "count"],
                },
                "prefix": {"type": "string", "default": "rs_"},
                "band": {"type": "integer", "default": 1, "minimum": 1},
            },
            "required": ["raster_path"],
        }


@register
class RasterClipCapability(Capability):
    """Découpe un raster sur l'emprise d'un layer vecteur (mask rasterio)."""

    name = "raster_clip"
    description = "Clips a raster to the extent of a vector layer using rasterio.mask."

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        raster_path: str = "",
        output_path: str = "",
        all_touched: bool = False,
        crop: bool = True,
        nodata: float | None = None,
        **_,
    ) -> gpd.GeoDataFrame:
        """
        Args:
            gdf:          Couche vecteur utilisée comme masque (polygones/multipolygones).
            raster_path:  Chemin vers le raster source.
            output_path:  Chemin du raster découpé en sortie (.tif).
            all_touched:  Si True, inclut toutes les cellules touchant le masque.
            crop:         Si True, recadre l'étendue du raster sur le masque.
            nodata:       Valeur nodata pour les zones masquées (défaut: valeur du raster).

        Returns:
            GeoDataFrame avec le bounding box du raster découpé (1 feature).
        """
        check_tier("pro")

        try:
            import rasterio
            from rasterio.mask import mask as rio_mask
        except ImportError as exc:
            raise ImportError(
                "RasterClipCapability requires 'rasterio'. "
                "Install with: pip install rasterio"
            ) from exc

        if not raster_path:
            raise ValueError("RasterClipCapability requires 'raster_path'.")
        if not output_path:
            raise ValueError("RasterClipCapability requires 'output_path'.")
        if not Path(raster_path).exists():
            raise FileNotFoundError(f"Raster not found: {raster_path}")
        if gdf.empty:
            raise ValueError("RasterClipCapability requires a non-empty vector layer.")

        # Reprojeter le masque dans le CRS du raster si nécessaire
        with rasterio.open(raster_path) as src:
            raster_crs = src.crs
            if gdf.crs is not None and gdf.crs.to_epsg() != raster_crs.to_epsg():
                gdf = gdf.to_crs(raster_crs.to_string())

            shapes = [geom.__geo_interface__ for geom in gdf.geometry if geom is not None]
            if not shapes:
                raise ValueError("No valid geometries in the input layer.")

            kwargs: dict[str, Any] = {
                "all_touched": all_touched,
                "crop": crop,
                "filled": True,
            }
            if nodata is not None:
                kwargs["nodata"] = nodata

            clipped, out_transform = rio_mask(src, shapes, **kwargs)
            out_meta = src.meta.copy()
            out_meta.update(
                {
                    "driver": "GTiff",
                    "height": clipped.shape[1],
                    "width": clipped.shape[2],
                    "transform": out_transform,
                }
            )
            if nodata is not None:
                out_meta["nodata"] = nodata

        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with rasterio.open(output_path, "w", **out_meta) as dst:
            dst.write(clipped)

        # Retourne le bounding box du raster découpé comme GeoDataFrame
        from shapely.geometry import box as shapely_box
        from rasterio.transform import array_bounds

        bounds = array_bounds(clipped.shape[1], clipped.shape[2], out_transform)
        bbox = shapely_box(*bounds)
        crs_str = (
            f"EPSG:{raster_crs.to_epsg()}" if raster_crs.to_epsg() else str(raster_crs)
        )
        return gpd.GeoDataFrame(
            [{"output_path": output_path, "geometry": bbox}],
            crs=crs_str,
        )

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "raster_path": {"type": "string", "description": "Path to the source raster."},
                "output_path": {"type": "string", "description": "Path for the clipped raster output."},
                "all_touched": {"type": "boolean", "default": False},
                "crop": {"type": "boolean", "default": True},
                "nodata": {"type": ["number", "null"], "description": "Nodata value for masked areas."},
            },
            "required": ["raster_path", "output_path"],
        }


@register
class NdviCapability(Capability):
    """Calcule le NDVI depuis deux bandes (Rouge et NIR) d'un raster multibande."""

    name = "ndvi"
    description = "Computes NDVI = (NIR - RED) / (NIR + RED) from a multi-band raster."

    def execute(
        self,
        gdf: gpd.GeoDataFrame,  # non utilisé — requis par la signature Capability
        raster_path: str = "",
        output_path: str = "",
        red_band: int = 3,
        nir_band: int = 4,
        **_,
    ) -> gpd.GeoDataFrame:
        """
        Args:
            gdf:          Non utilisé (placeholder — Capability attend un GDF en entrée).
            raster_path:  Chemin vers le raster multibande source.
            output_path:  Chemin du raster NDVI en sortie (.tif, 1 bande float32).
            red_band:     Index de la bande Rouge (1-based). Défaut: 3 (Sentinel-2 B4).
            nir_band:     Index de la bande NIR (1-based). Défaut: 4 (Sentinel-2 B8).

        Returns:
            GeoDataFrame avec le bounding box du raster NDVI (1 feature) et
            les colonnes ``ndvi_min``, ``ndvi_max``, ``ndvi_mean``.
        """
        check_tier("pro")

        try:
            import numpy as np
            import rasterio
        except ImportError as exc:
            raise ImportError(
                "NdviCapability requires 'rasterio' and 'numpy'. "
                "Install with: pip install rasterio numpy"
            ) from exc

        if not raster_path:
            raise ValueError("NdviCapability requires 'raster_path'.")
        if not output_path:
            raise ValueError("NdviCapability requires 'output_path'.")
        if not Path(raster_path).exists():
            raise FileNotFoundError(f"Raster not found: {raster_path}")

        with rasterio.open(raster_path) as src:
            if red_band > src.count or nir_band > src.count:
                raise ValueError(
                    f"raster_path has {src.count} bands but red_band={red_band} "
                    f"and nir_band={nir_band} were requested."
                )
            red = src.read(red_band).astype(np.float32)
            nir = src.read(nir_band).astype(np.float32)
            nodata = src.nodata
            transform = src.transform
            crs = src.crs
            meta = src.meta.copy()

        # Masque nodata
        valid = np.ones(red.shape, dtype=bool)
        if nodata is not None:
            valid &= (red != nodata) & (nir != nodata)

        # NDVI — évite la division par zéro
        denom = nir + red
        denom_safe = np.where(denom == 0, np.nan, denom)
        ndvi = np.where(valid, (nir - red) / denom_safe, np.nan)

        # Écriture du raster NDVI
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        meta.update(
            {
                "count": 1,
                "dtype": "float32",
                "nodata": np.nan,
            }
        )
        with rasterio.open(output_path, "w", **meta) as dst:
            dst.write(ndvi.astype(np.float32), 1)

        # Statistiques globales (hors nodata)
        valid_ndvi = ndvi[~np.isnan(ndvi)]
        ndvi_min = float(valid_ndvi.min()) if valid_ndvi.size else float("nan")
        ndvi_max = float(valid_ndvi.max()) if valid_ndvi.size else float("nan")
        ndvi_mean = float(valid_ndvi.mean()) if valid_ndvi.size else float("nan")

        from shapely.geometry import box as shapely_box
        from rasterio.transform import array_bounds

        bounds = array_bounds(ndvi.shape[0], ndvi.shape[1], transform)
        bbox = shapely_box(*bounds)
        crs_str = f"EPSG:{crs.to_epsg()}" if crs.to_epsg() else str(crs)
        return gpd.GeoDataFrame(
            [
                {
                    "output_path": output_path,
                    "ndvi_min": ndvi_min,
                    "ndvi_max": ndvi_max,
                    "ndvi_mean": ndvi_mean,
                    "geometry": bbox,
                }
            ],
            crs=crs_str,
        )

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "raster_path": {"type": "string", "description": "Path to the multi-band raster."},
                "output_path": {"type": "string", "description": "Path for the NDVI output raster."},
                "red_band": {
                    "type": "integer",
                    "default": 3,
                    "minimum": 1,
                    "description": "Index of the Red band (1-based).",
                },
                "nir_band": {
                    "type": "integer",
                    "default": 4,
                    "minimum": 1,
                    "description": "Index of the NIR band (1-based).",
                },
            },
            "required": ["raster_path", "output_path"],
        }


@register
class RasterReprojectCapability(Capability):
    """Reprojette un raster vers un CRS cible avec rasterio.warp."""

    name = "raster_reproject"
    description = "Reprojects a raster to a target CRS using rasterio.warp.reproject."

    def execute(
        self,
        gdf: gpd.GeoDataFrame,  # non utilisé — requis par la signature Capability
        raster_path: str = "",
        output_path: str = "",
        target_crs: str = "EPSG:4326",
        resampling: str = "nearest",
        **_,
    ) -> gpd.GeoDataFrame:
        """
        Args:
            gdf:          Non utilisé (placeholder).
            raster_path:  Chemin vers le raster source.
            output_path:  Chemin du raster reprojeté en sortie.
            target_crs:   CRS cible (ex: "EPSG:2154", "EPSG:32631").
            resampling:   Méthode de rééchantillonnage: nearest, bilinear, cubic,
                          lanczos, average, mode. Défaut: nearest.

        Returns:
            GeoDataFrame avec le bounding box du raster reprojeté (1 feature).
        """
        check_tier("pro")

        try:
            import rasterio
            from rasterio.crs import CRS
            from rasterio.warp import (
                Resampling,
                calculate_default_transform,
                reproject,
            )
        except ImportError as exc:
            raise ImportError(
                "RasterReprojectCapability requires 'rasterio'. "
                "Install with: pip install rasterio"
            ) from exc

        if not raster_path:
            raise ValueError("RasterReprojectCapability requires 'raster_path'.")
        if not output_path:
            raise ValueError("RasterReprojectCapability requires 'output_path'.")
        if not Path(raster_path).exists():
            raise FileNotFoundError(f"Raster not found: {raster_path}")

        # Résolution du nom de méthode de rééchantillonnage
        resampling_map: dict[str, Resampling] = {
            "nearest": Resampling.nearest,
            "bilinear": Resampling.bilinear,
            "cubic": Resampling.cubic,
            "lanczos": Resampling.lanczos,
            "average": Resampling.average,
            "mode": Resampling.mode,
        }
        if resampling not in resampling_map:
            raise ValueError(
                f"Unknown resampling method: {resampling!r}. "
                f"Valid values: {list(resampling_map)}"
            )
        resampling_algo = resampling_map[resampling]
        dst_crs = CRS.from_string(target_crs)

        with rasterio.open(raster_path) as src:
            transform, width, height = calculate_default_transform(
                src.crs, dst_crs, src.width, src.height, *src.bounds
            )
            meta = src.meta.copy()
            meta.update(
                {
                    "crs": dst_crs,
                    "transform": transform,
                    "width": width,
                    "height": height,
                }
            )

            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            with rasterio.open(output_path, "w", **meta) as dst:
                for band_idx in range(1, src.count + 1):
                    reproject(
                        source=rasterio.band(src, band_idx),
                        destination=rasterio.band(dst, band_idx),
                        src_transform=src.transform,
                        src_crs=src.crs,
                        dst_transform=transform,
                        dst_crs=dst_crs,
                        resampling=resampling_algo,
                    )

        from shapely.geometry import box as shapely_box
        from rasterio.transform import array_bounds

        bounds = array_bounds(height, width, transform)
        bbox = shapely_box(*bounds)
        epsg = dst_crs.to_epsg()
        crs_str = f"EPSG:{epsg}" if epsg else str(dst_crs)
        return gpd.GeoDataFrame(
            [{"output_path": output_path, "target_crs": target_crs, "geometry": bbox}],
            crs=crs_str,
        )

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "raster_path": {"type": "string", "description": "Path to the source raster."},
                "output_path": {"type": "string", "description": "Path for the reprojected raster."},
                "target_crs": {
                    "type": "string",
                    "default": "EPSG:4326",
                    "description": "Target CRS string, e.g. 'EPSG:2154'.",
                },
                "resampling": {
                    "type": "string",
                    "default": "nearest",
                    "enum": ["nearest", "bilinear", "cubic", "lanczos", "average", "mode"],
                    "description": "Resampling algorithm.",
                },
            },
            "required": ["raster_path", "output_path"],
        }


@register
class RasterMergeCapability(Capability):
    """Fusionne plusieurs rasters en un seul avec rasterio.merge."""

    name = "raster_merge"
    description = "Merges multiple rasters into a single raster using rasterio.merge."

    def execute(
        self,
        gdf: gpd.GeoDataFrame,  # non utilisé — requis par la signature Capability
        raster_paths: list[str] | None = None,
        output_path: str = "",
        method: str = "first",
        nodata: float | None = None,
        **_,
    ) -> gpd.GeoDataFrame:
        """
        Args:
            gdf:           Non utilisé (placeholder).
            raster_paths:  Liste des chemins des rasters à fusionner (au moins 2).
            output_path:   Chemin du raster fusionné en sortie.
            method:        Stratégie de fusion : first, last, min, max, sum, count.
                           Défaut: first (premier raster non-nodata au pixel).
            nodata:        Valeur nodata commune (surcharge les valeurs individuelles).

        Returns:
            GeoDataFrame avec le bounding box du raster fusionné (1 feature) et
            les colonnes ``n_sources`` et ``output_path``.
        """
        check_tier("pro")

        try:
            import rasterio
            from rasterio.merge import merge as rio_merge
        except ImportError as exc:
            raise ImportError(
                "RasterMergeCapability requires 'rasterio'. "
                "Install with: pip install rasterio"
            ) from exc

        raster_paths = raster_paths or []
        if len(raster_paths) < 2:
            raise ValueError(
                "RasterMergeCapability requires at least 2 raster paths in 'raster_paths'."
            )
        if not output_path:
            raise ValueError("RasterMergeCapability requires 'output_path'.")

        valid_methods = ("first", "last", "min", "max", "sum", "count")
        if method not in valid_methods:
            raise ValueError(
                f"Unknown merge method: {method!r}. Valid values: {list(valid_methods)}"
            )

        for p in raster_paths:
            if not Path(p).exists():
                raise FileNotFoundError(f"Raster not found: {p}")

        src_files = [rasterio.open(p) for p in raster_paths]
        try:
            merge_kwargs: dict[str, Any] = {"method": method}
            if nodata is not None:
                merge_kwargs["nodata"] = nodata

            merged, out_transform = rio_merge(src_files, **merge_kwargs)
            meta = src_files[0].meta.copy()
            meta.update(
                {
                    "driver": "GTiff",
                    "height": merged.shape[1],
                    "width": merged.shape[2],
                    "transform": out_transform,
                }
            )
            if nodata is not None:
                meta["nodata"] = nodata

            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            with rasterio.open(output_path, "w", **meta) as dst:
                dst.write(merged)

            crs = src_files[0].crs
        finally:
            for f in src_files:
                f.close()

        from shapely.geometry import box as shapely_box
        from rasterio.transform import array_bounds

        bounds = array_bounds(merged.shape[1], merged.shape[2], out_transform)
        bbox = shapely_box(*bounds)
        epsg = crs.to_epsg()
        crs_str = f"EPSG:{epsg}" if epsg else str(crs)
        return gpd.GeoDataFrame(
            [
                {
                    "output_path": output_path,
                    "n_sources": len(raster_paths),
                    "geometry": bbox,
                }
            ],
            crs=crs_str,
        )

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "raster_paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 2,
                    "description": "Ordered list of raster paths to merge.",
                },
                "output_path": {"type": "string", "description": "Path for the merged raster."},
                "method": {
                    "type": "string",
                    "default": "first",
                    "enum": ["first", "last", "min", "max", "sum", "count"],
                    "description": "Pixel merge strategy.",
                },
                "nodata": {
                    "type": ["number", "null"],
                    "description": "Common nodata value for all sources.",
                },
            },
            "required": ["raster_paths", "output_path"],
        }


@register
class ChangeDetectionCapability(Capability):
    """Détecte les zones de changement entre deux rasters (diff > seuil → polygones)."""

    name = "change_detection"
    description = "Detects changed areas between two rasters and returns them as vector polygons."

    def execute(
        self,
        gdf: gpd.GeoDataFrame,  # non utilisé — requis par la signature Capability
        raster_before: str = "",
        raster_after: str = "",
        threshold: float = 0.0,
        band: int = 1,
        output_crs: str | None = None,
        **_,
    ) -> gpd.GeoDataFrame:
        """
        Args:
            gdf:           Non utilisé (placeholder — Capability attend un GDF en entrée).
            raster_before: Chemin vers le raster T1.
            raster_after:  Chemin vers le raster T2.
            threshold:     Valeur absolue minimale du diff pour être considéré comme changement.
            band:          Index de bande (1-based).
            output_crs:    CRS du GeoDataFrame résultat (défaut: CRS du raster).

        Returns:
            GeoDataFrame de polygones représentant les zones de changement,
            avec une colonne `diff_mean` (valeur moyenne du diff dans chaque polygone).
        """
        check_tier("pro")

        try:
            import numpy as np
            import rasterio
            from rasterio.features import shapes
        except ImportError as exc:
            raise ImportError(
                "ChangeDetectionCapability requires 'rasterio' and 'numpy'. "
                "Install with: pip install rasterio numpy"
            ) from exc

        for p, label in [(raster_before, "raster_before"), (raster_after, "raster_after")]:
            if not p:
                raise ValueError(f"ChangeDetectionCapability requires '{label}'.")
            if not Path(p).exists():
                raise FileNotFoundError(f"Raster not found: {p}")

        with rasterio.open(raster_before) as src_b:
            arr_before = src_b.read(band).astype(np.float32)
            nodata_b = src_b.nodata
            transform = src_b.transform
            crs = src_b.crs

        with rasterio.open(raster_after) as src_a:
            arr_after = src_a.read(band).astype(np.float32)
            nodata_a = src_a.nodata

        # Masque nodata
        mask = np.ones(arr_before.shape, dtype=bool)
        if nodata_b is not None:
            mask &= arr_before != nodata_b
        if nodata_a is not None:
            mask &= arr_after != nodata_a

        diff = np.abs(arr_after - arr_before)
        changed = (diff > threshold) & mask
        changed_uint8 = changed.astype(np.uint8)

        # Vectorisation des zones de changement
        geoms = []
        diff_means = []
        for geom, val in shapes(changed_uint8, mask=changed_uint8, transform=transform):
            if val == 1:
                from shapely.geometry import shape
                poly = shape(geom)
                # Calcule le diff moyen dans ce polygone via mask rasterio
                from rasterio.features import rasterize
                tmp_mask = rasterize(
                    [(geom, 1)],
                    out_shape=arr_before.shape,
                    transform=transform,
                    dtype=np.uint8,
                )
                vals = diff[tmp_mask == 1]
                geoms.append(poly)
                diff_means.append(float(vals.mean()) if len(vals) else 0.0)

        result = gpd.GeoDataFrame(
            {"diff_mean": diff_means},
            geometry=geoms,
            crs=crs.to_epsg() and f"EPSG:{crs.to_epsg()}" or str(crs),
        )
        if output_crs:
            result = result.to_crs(output_crs)
        return result.reset_index(drop=True)

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "raster_before": {"type": "string", "description": "Path to T1 raster."},
                "raster_after": {"type": "string", "description": "Path to T2 raster."},
                "threshold": {
                    "type": "number",
                    "default": 0.0,
                    "description": "Min absolute diff to flag as change.",
                },
                "band": {"type": "integer", "default": 1, "minimum": 1},
                "output_crs": {
                    "type": ["string", "null"],
                    "description": "Output CRS, e.g. 'EPSG:2154'.",
                },
            },
            "required": ["raster_before", "raster_after"],
        }
