"""Spatial clustering capabilities for GISPulse.

Three algorithms, all building on scikit-learn:

- :class:`DBSCANClusterCapability`      — density-based, discovers arbitrary
  shapes, tags noise points separately. Best for point clouds with varying
  densities and where the number of clusters is unknown.
- :class:`KMeansClusterCapability`      — centroid-based, requires *k* upfront.
  Fastest on very large point sets, always produces *k* clusters of comparable
  size (Voronoi cells).
- :class:`HDBSCANClusterCapability`     — hierarchical density-based. Adapts
  to varying density without the ``eps`` tuning DBSCAN needs; recommended
  default for exploratory analysis.

All three operate on the centroids of input geometries (so they work for
points, lines and polygons alike). They add a ``cluster`` column to the
output GeoDataFrame. ``-1`` marks noise points (DBSCAN/HDBSCAN).
"""

from __future__ import annotations


import geopandas as gpd
import numpy as np

from gispulse.capabilities.base import Capability
from gispulse.capabilities.registry import register


def _coords_from_gdf(
    gdf: gpd.GeoDataFrame,
    crs_meters: str = "EPSG:3857",
) -> "np.ndarray":
    """Return a Nx2 array of projected centroid coordinates.

    Reprojects the input to *crs_meters* so metric-based distance parameters
    (``eps`` in meters for DBSCAN, typically) are interpreted correctly.
    """
    if gdf.empty:
        return np.empty((0, 2), dtype=float)
    work = gdf.to_crs(crs_meters) if gdf.crs is not None else gdf
    pts = work.geometry.centroid
    return np.column_stack([pts.x.to_numpy(), pts.y.to_numpy()])


@register
class DBSCANClusterCapability(Capability):
    """Density-based spatial clustering (DBSCAN)."""

    name = "cluster_dbscan"
    description = (
        "DBSCAN — density-based spatial clustering on geometry centroids. "
        "Adds a 'cluster' column (-1 = noise)."
    )

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        eps: float = 100.0,
        min_samples: int = 5,
        cluster_col: str = "cluster",
        crs_meters: str = "EPSG:3857",
        **_,
    ) -> gpd.GeoDataFrame:
        """
        Args:
            gdf:         Input GeoDataFrame. Non-point geometries are
                         summarised by their centroid.
            eps:         Max distance (in units of *crs_meters*) between two
                         samples to be considered neighbors. Default 100 m.
            min_samples: Min number of samples in a neighborhood to form a
                         core point. Lower values → more clusters & more noise
                         flagged as cluster ``-1``.
            cluster_col: Output column name.
            crs_meters:  Metric CRS used for *eps*. EPSG:3857 is a reasonable
                         default worldwide; prefer EPSG:2154 in France.

        Returns:
            Copy of *gdf* with an added integer *cluster_col*.
        """
        from sklearn.cluster import DBSCAN

        if gdf.empty:
            out = gdf.copy()
            out[cluster_col] = np.array([], dtype=np.int64)
            return out
        if eps <= 0:
            raise ValueError("eps must be > 0.")
        if min_samples < 1:
            raise ValueError("min_samples must be >= 1.")

        coords = _coords_from_gdf(gdf, crs_meters)
        labels = DBSCAN(eps=eps, min_samples=min_samples).fit_predict(coords)
        out = gdf.copy()
        out[cluster_col] = labels.astype(np.int64)
        return out

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "eps": {
                    "type": "number",
                    "minimum": 0,
                    "default": 100.0,
                    "description": "Neighborhood radius in units of crs_meters.",
                },
                "min_samples": {
                    "type": "integer",
                    "minimum": 1,
                    "default": 5,
                    "description": "Minimum samples in a core neighborhood.",
                },
                "cluster_col": {
                    "type": "string",
                    "default": "cluster",
                    "description": "Output column name.",
                },
                "crs_meters": {
                    "type": "string",
                    "default": "EPSG:3857",
                    "description": "Metric CRS used for eps.",
                },
            },
        }


@register
class KMeansClusterCapability(Capability):
    """Centroid-based clustering (K-Means)."""

    name = "cluster_kmeans"
    description = (
        "K-Means — partition geometry centroids into k clusters. "
        "Adds 'cluster' and distance-to-centroid columns."
    )

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        k: int = 5,
        cluster_col: str = "cluster",
        distance_col: str | None = "cluster_dist",
        random_state: int = 42,
        crs_meters: str = "EPSG:3857",
        **_,
    ) -> gpd.GeoDataFrame:
        """
        Args:
            gdf:           Input GeoDataFrame.
            k:             Number of clusters (> 0). Capped at len(gdf).
            cluster_col:   Output cluster id column name.
            distance_col:  When set, store the distance (in crs_meters) from
                           each feature to its cluster centroid. Set to None
                           to skip.
            random_state:  Reproducibility seed.
            crs_meters:    Metric CRS for distance computation.
        """
        from sklearn.cluster import KMeans

        if gdf.empty:
            out = gdf.copy()
            out[cluster_col] = np.array([], dtype=np.int64)
            if distance_col:
                out[distance_col] = np.array([], dtype=float)
            return out
        if k < 1:
            raise ValueError("k must be >= 1.")

        coords = _coords_from_gdf(gdf, crs_meters)
        effective_k = min(k, len(coords))
        km = KMeans(
            n_clusters=effective_k,
            random_state=random_state,
            n_init="auto",
        ).fit(coords)

        out = gdf.copy()
        out[cluster_col] = km.labels_.astype(np.int64)
        if distance_col:
            centers = km.cluster_centers_[km.labels_]
            dists = np.linalg.norm(coords - centers, axis=1)
            out[distance_col] = dists
        return out

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "k": {
                    "type": "integer",
                    "minimum": 1,
                    "default": 5,
                    "description": "Number of clusters.",
                },
                "cluster_col": {
                    "type": "string",
                    "default": "cluster",
                },
                "distance_col": {
                    "type": ["string", "null"],
                    "default": "cluster_dist",
                    "description": "Distance-to-centroid column. Null = skip.",
                },
                "random_state": {
                    "type": "integer",
                    "default": 42,
                },
                "crs_meters": {
                    "type": "string",
                    "default": "EPSG:3857",
                },
            },
            "required": ["k"],
        }


@register
class HDBSCANClusterCapability(Capability):
    """Hierarchical density-based clustering (HDBSCAN)."""

    name = "cluster_hdbscan"
    description = (
        "HDBSCAN — hierarchical density clustering. "
        "Adapts to varying densities without eps tuning. "
        "Adds 'cluster' (-1=noise) and 'cluster_probability' columns."
    )

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        min_cluster_size: int = 5,
        min_samples: int | None = None,
        cluster_selection_epsilon: float = 0.0,
        cluster_col: str = "cluster",
        probability_col: str | None = "cluster_probability",
        crs_meters: str = "EPSG:3857",
        **_,
    ) -> gpd.GeoDataFrame:
        """
        Args:
            gdf:                        Input GeoDataFrame.
            min_cluster_size:           Smallest group size for a valid cluster.
            min_samples:                Core sample density. Defaults to
                                        *min_cluster_size* when None.
            cluster_selection_epsilon:  Distance threshold below which clusters
                                        are not split further.
            cluster_col:                Output cluster id column.
            probability_col:            When set, store each point's
                                        probability of belonging to its
                                        assigned cluster. Set to None to skip.
            crs_meters:                 Metric CRS used for distances.
        """
        from sklearn.cluster import HDBSCAN

        if gdf.empty:
            out = gdf.copy()
            out[cluster_col] = np.array([], dtype=np.int64)
            if probability_col:
                out[probability_col] = np.array([], dtype=float)
            return out
        if min_cluster_size < 2:
            raise ValueError("min_cluster_size must be >= 2.")

        coords = _coords_from_gdf(gdf, crs_meters)
        model = HDBSCAN(
            min_cluster_size=int(min_cluster_size),
            min_samples=min_samples,
            cluster_selection_epsilon=float(cluster_selection_epsilon),
        ).fit(coords)

        out = gdf.copy()
        out[cluster_col] = model.labels_.astype(np.int64)
        if probability_col:
            out[probability_col] = model.probabilities_.astype(float)
        return out

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "min_cluster_size": {
                    "type": "integer",
                    "minimum": 2,
                    "default": 5,
                },
                "min_samples": {
                    "type": ["integer", "null"],
                    "description": "Defaults to min_cluster_size when null.",
                },
                "cluster_selection_epsilon": {
                    "type": "number",
                    "minimum": 0,
                    "default": 0.0,
                },
                "cluster_col": {"type": "string", "default": "cluster"},
                "probability_col": {
                    "type": ["string", "null"],
                    "default": "cluster_probability",
                },
                "crs_meters": {
                    "type": "string",
                    "default": "EPSG:3857",
                },
            },
        }
