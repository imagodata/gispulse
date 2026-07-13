"""Spatial clustering capabilities for GISPulse.

Five algorithms, all building on scikit-learn:

- :class:`DBSCANClusterCapability`      — density-based, discovers arbitrary
  shapes, tags noise points separately. Best for point clouds with varying
  densities and where the number of clusters is unknown.
- :class:`KMeansClusterCapability`      — centroid-based, requires *k* upfront.
  Fastest on very large point sets, always produces *k* clusters of comparable
  size (Voronoi cells).
- :class:`HDBSCANClusterCapability`     — hierarchical density-based. Adapts
  to varying density without the ``eps`` tuning DBSCAN needs; recommended
  default for exploratory analysis.
- :class:`NetworkDBSCANClusterCapability` — DBSCAN where distance is measured
  as the **shortest path along a line network** rather than straight-line
  (capability H). Points snapped to the nearest network node; ``eps_m`` is a
  distance along edges. Two points close as the crow flies but far along the
  network do **not** end up in the same cluster.
- :class:`STDBSCANClusterCapability` — spatio-temporal DBSCAN (capability J):
  clusters points that are close **both** in space (``eps_m``) and in time
  (``eps_time`` seconds, on a datetime column).

The first three operate on the centroids of input geometries (so they work
for points, lines and polygons alike). They add a ``cluster`` column to the
output GeoDataFrame. ``-1`` marks noise points (DBSCAN/HDBSCAN).
"""

from __future__ import annotations


import geopandas as gpd
import numpy as np
import pandas as pd

from gispulse.capabilities.base import Capability
from gispulse.capabilities.registry import register
from gispulse.core.crs import is_angular
from gispulse.core.network_graph_handle import NetworkGraph


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


@register
class NetworkDBSCANClusterCapability(Capability):
    """Network-constrained DBSCAN (shortest-path metric, capability H)."""

    name = "cluster_network_dbscan"
    description = (
        "Network-constrained DBSCAN — clusters points by shortest-path "
        "distance along a line network instead of straight-line distance. "
        "Points are snapped to the nearest network node; eps_m is measured "
        "along edges. Adds a 'cluster' column (-1 = noise). Pass the network "
        "as ref_layer."
    )

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        ref_gdf: gpd.GeoDataFrame | None = None,
        eps_m: float = 100.0,
        min_samples: int = 5,
        cluster_col: str = "cluster",
        weight_col: str | None = None,
        crs_meters: str = "EPSG:3857",
        **_,
    ) -> gpd.GeoDataFrame:
        """
        Args:
            gdf:         Point layer to cluster (non-point geometries use their
                         centroid).
            ref_gdf:     Line-network layer, injected via ``ref_layer``. The
                         clustering metric is the shortest-path distance over
                         this network.
            eps_m:       Neighborhood radius **along the network**, in units of
                         *crs_meters* (meters) — or of *weight_col* when set.
                         Two points are neighbors when their snapped nodes are
                         within this network distance. Default 100.
            min_samples: Min samples in a neighborhood to form a core point.
            cluster_col: Output column name.
            weight_col:  Edge-weight column on the network; defaults to metric
                         edge length. When set, *eps_m* is in its units.
            crs_meters:  Metric CRS used for snapping and edge lengths.
                         EPSG:3857 worldwide default; prefer EPSG:2154 in
                         France.

        Returns:
            Copy of *gdf* (in its original CRS) with an added integer
            *cluster_col* (``-1`` = noise / unreachable).
        """
        from sklearn.cluster import DBSCAN

        if gdf.empty:
            out = gdf.copy()
            out[cluster_col] = np.array([], dtype=np.int64)
            return out
        if ref_gdf is None or ref_gdf.empty:
            raise ValueError(
                "cluster_network_dbscan requires a line-network layer "
                "(inject via ref_layer)."
            )
        if eps_m <= 0:
            raise ValueError("eps_m must be > 0.")
        if min_samples < 1:
            raise ValueError("min_samples must be >= 1.")

        try:
            import networkx as nx
        except ImportError as exc:
            raise ImportError(
                "cluster_network_dbscan requires 'networkx'. "
                "Install with: pip install networkx"
            ) from exc

        # Reproject to a metric CRS so eps_m and edge weights are in meters.
        network_m = ref_gdf.to_crs(crs_meters) if is_angular(ref_gdf) else ref_gdf
        points_m = gdf.to_crs(crs_meters) if is_angular(gdf) else gdf

        graph = NetworkGraph.from_lines(network_m, weight_col)
        if len(graph) == 0:
            raise ValueError(
                "cluster_network_dbscan: the network produced an empty graph."
            )

        # Snap each point centroid to its nearest network node (O(log n)).
        node_of_point: list[int] = []
        for geom in points_m.geometry:
            if geom is None or geom.is_empty:
                node_of_point.append(-1)
                continue
            pt = geom if geom.geom_type == "Point" else geom.centroid
            node_of_point.append(graph.nearest_node(pt))

        n = len(node_of_point)
        big = float(eps_m) * 2.0 + 1.0  # sentinel > eps → never a neighbor

        # One bounded Dijkstra per *distinct* snapped node (cutoff eps_m),
        # then fill the point×point precomputed distance matrix by lookup —
        # points sharing a node reuse the same single-source result.
        G = graph.graph
        lengths_by_node: dict[int, dict[int, float]] = {}
        for nid in set(node_of_point):
            if nid < 0:
                continue
            lengths_by_node[nid] = nx.single_source_dijkstra_path_length(
                G, nid, cutoff=float(eps_m), weight="weight"
            )

        dist = np.full((n, n), big, dtype=float)
        for i in range(n):
            ni = node_of_point[i]
            dist[i, i] = 0.0
            if ni < 0:
                continue
            li = lengths_by_node.get(ni, {})
            for j in range(n):
                if i == j:
                    continue
                d = li.get(node_of_point[j])
                if d is not None:
                    dist[i, j] = d
        # Symmetrize defensively (graph is undirected → distances are
        # symmetric; this guards against cutoff edge effects).
        dist = np.minimum(dist, dist.T)

        labels = DBSCAN(
            eps=float(eps_m), min_samples=min_samples, metric="precomputed"
        ).fit_predict(dist)

        out = gdf.copy()
        out[cluster_col] = labels.astype(np.int64)
        return out

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "ref_layer": {
                    "type": ["string", "null"],
                    "description": "Line-network layer used as the routing graph.",
                },
                "eps_m": {
                    "type": "number",
                    "minimum": 0,
                    "default": 100.0,
                    "description": "Neighborhood radius along the network (crs_meters units, or weight_col units).",
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
                "weight_col": {
                    "type": ["string", "null"],
                    "description": "Edge-weight column; defaults to metric edge length.",
                },
                "crs_meters": {
                    "type": "string",
                    "default": "EPSG:3857",
                    "description": "Metric CRS for snapping and edge lengths.",
                },
            },
        }


@register
class STDBSCANClusterCapability(Capability):
    """Spatio-temporal DBSCAN (ST-DBSCAN, capability J)."""

    name = "cluster_st_dbscan"
    description = (
        "ST-DBSCAN — density-based clustering with a spatial threshold (eps_m) "
        "AND a temporal threshold (eps_time, seconds) on a datetime column. "
        "Two points cluster together only when they are close in space and "
        "in time. Adds a 'cluster' column (-1 = noise)."
    )

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        time_col: str = "timestamp",
        eps_m: float = 100.0,
        eps_time: float = 3600.0,
        min_samples: int = 5,
        cluster_col: str = "cluster",
        crs_meters: str = "EPSG:3857",
        **_,
    ) -> gpd.GeoDataFrame:
        """
        Args:
            gdf:         Point layer (non-point geometries use their centroid).
            time_col:    Datetime column. Parsed with ``pandas.to_datetime``;
                         unparseable / missing values become noise.
            eps_m:       Spatial neighborhood radius in *crs_meters* units.
            eps_time:    Temporal neighborhood in **seconds**. Two points are
                         neighbors only when both ``|Δt| <= eps_time`` and the
                         metric distance ``<= eps_m`` hold.
            min_samples: Min samples in a neighborhood to form a core point.
            cluster_col: Output column name.
            crs_meters:  Metric CRS used for *eps_m*.

        Returns:
            Copy of *gdf* with an added integer *cluster_col* (``-1`` = noise).
        """
        from sklearn.cluster import DBSCAN

        if gdf.empty:
            out = gdf.copy()
            out[cluster_col] = np.array([], dtype=np.int64)
            return out
        if time_col not in gdf.columns:
            raise ValueError(f"cluster_st_dbscan: time_col {time_col!r} not in gdf.")
        if eps_m <= 0:
            raise ValueError("eps_m must be > 0.")
        if eps_time <= 0:
            raise ValueError("eps_time must be > 0.")
        if min_samples < 1:
            raise ValueError("min_samples must be >= 1.")

        coords = _coords_from_gdf(gdf, crs_meters)

        t = pd.to_datetime(gdf[time_col], errors="coerce")
        na = t.isna().to_numpy()
        # Force a second resolution regardless of the parsed unit (pandas may
        # yield datetime64[ns] or [us]); NaT is masked out below.
        secs = t.to_numpy().astype("datetime64[s]").astype("int64").astype(float)

        # Spatial distance matrix (euclidean on projected centroids).
        diff = coords[:, None, :] - coords[None, :, :]
        dist_sp = np.sqrt((diff * diff).sum(axis=2))

        # Temporal gate: pairs outside eps_time (or involving a NaT) get a
        # sentinel distance > eps_m so DBSCAN never treats them as neighbors.
        dt = np.abs(secs[:, None] - secs[None, :])
        within_time = dt <= float(eps_time)
        within_time[na, :] = False
        within_time[:, na] = False

        big = float(eps_m) * 2.0 + 1.0
        dist = np.where(within_time, dist_sp, big)
        np.fill_diagonal(dist, 0.0)

        labels = DBSCAN(
            eps=float(eps_m), min_samples=min_samples, metric="precomputed"
        ).fit_predict(dist)

        out = gdf.copy()
        out[cluster_col] = labels.astype(np.int64)
        return out

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "time_col": {
                    "type": "string",
                    "default": "timestamp",
                    "description": "Datetime column for the temporal dimension.",
                },
                "eps_m": {
                    "type": "number",
                    "minimum": 0,
                    "default": 100.0,
                    "description": "Spatial radius in crs_meters units.",
                },
                "eps_time": {
                    "type": "number",
                    "minimum": 0,
                    "default": 3600.0,
                    "description": "Temporal radius in seconds.",
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
                    "description": "Metric CRS used for eps_m.",
                },
            },
            "required": ["time_col"],
        }


# ---------------------------------------------------------------------------
# Balanced (size-bounded) K-Means
# ---------------------------------------------------------------------------
#
# Pure-numpy port of a size-constrained partitioner: deterministic k-means
# (k-means++ init + Lloyd), then a split/merge rebalance that forces every
# cluster into ``[min_size, max_size]``. Faithful to the source algorithm
# (``partition_sites``); here it operates directly on the projected centroids
# returned by :func:`_coords_from_gdf` rather than on domain objects.


def _kmeans_plusplus_init(
    points: np.ndarray, k: int, rng: np.random.Generator
) -> np.ndarray:
    """k-means++ seeding — return the row indices of *k* initial centers."""
    n = points.shape[0]
    first = int(rng.integers(n))
    centers = [first]
    closest_sq = np.sum((points - points[first]) ** 2, axis=1)
    for _ in range(1, k):
        total = float(closest_sq.sum())
        if total <= 0.0:
            # All points coincide with an existing center: fill arbitrarily
            # but stably.
            remaining = [i for i in range(n) if i not in centers]
            centers.append(remaining[0] if remaining else centers[-1])
            continue
        probs = closest_sq / total
        nxt = int(rng.choice(n, p=probs))
        centers.append(nxt)
        dist_sq = np.sum((points - points[nxt]) ** 2, axis=1)
        closest_sq = np.minimum(closest_sq, dist_sq)
    return np.array(centers, dtype=int)


def _lloyd(
    points: np.ndarray, k: int, rng: np.random.Generator, iters: int = 100
) -> np.ndarray:
    """Lloyd's k-means; one label per point. Centers seeded with k-means++."""
    n = points.shape[0]
    if k >= n:
        return np.arange(n, dtype=int)
    centers = points[_kmeans_plusplus_init(points, k, rng)].copy()
    labels = np.zeros(n, dtype=int)
    for i in range(iters):
        # Assign to the nearest center (deterministic tie-break via argmin).
        d = np.sum((points[:, None, :] - centers[None, :, :]) ** 2, axis=2)
        new_labels = np.argmin(d, axis=1)
        if np.array_equal(new_labels, labels) and i > 0:
            break
        labels = new_labels
        for c in range(k):
            members = points[labels == c]
            if len(members) > 0:
                centers[c] = members.mean(axis=0)
            # Empty cluster: keep the old center (stable; resolved on rebalance).
    return labels


def _choose_k(n: int, min_size: int, max_size: int) -> int:
    """k so the mean cluster size lands mid-``[min_size, max_size]``, bounded feasible."""
    if n <= max_size:
        return 1
    k_lo = -(-n // max_size)  # ceil(n / max_size): lower bound honouring max
    k_hi = max(k_lo, n // min_size)  # floor(n / min_size): upper bound honouring min
    target = (min_size + max_size) / 2.0
    k = round(n / target)
    return int(min(max(k, k_lo), k_hi))


def _compact_relabel(labels: np.ndarray) -> np.ndarray:
    """Renumber 0..K-1 deterministically (clusters by size desc, then id)."""
    uniq = sorted(
        np.unique(labels).tolist(), key=lambda c: (-int((labels == c).sum()), c)
    )
    remap = {old: new for new, old in enumerate(uniq)}
    return np.array([remap[int(c)] for c in labels], dtype=int)


def _rebalance(
    points: np.ndarray,
    labels: np.ndarray,
    min_size: int,
    max_size: int,
    rng: np.random.Generator,
    max_passes: int = 50,
) -> np.ndarray:
    """Force every cluster into ``[min_size, max_size]`` by split (2-means) / merge.

    Irreducible remainder tolerated: with ``n < min_size`` the min bound cannot
    be met (a single cluster remains).
    """
    labels = labels.copy()
    for _ in range(max_passes):
        sizes = {c: int((labels == c).sum()) for c in np.unique(labels)}
        # 1) Split the largest cluster above max_size.
        over = [c for c, s in sizes.items() if s > max_size]
        if over:
            c = max(over, key=lambda c: sizes[c])
            idx = np.where(labels == c)[0]
            sub = _lloyd(points[idx], 2, rng)
            new_id = (labels.max() + 1) if len(labels) else 0
            labels[idx[sub == 1]] = new_id
            continue
        # 2) Merge a below-min cluster into the nearest centroid (with margin).
        under = [c for c, s in sizes.items() if s < min_size]
        if under and len(sizes) > 1:
            c = min(under, key=lambda c: sizes[c])
            idx = np.where(labels == c)[0]
            centroid = points[idx].mean(axis=0)
            others = [o for o in sizes if o != c]
            # Target: the nearest that stays within max_size after absorption;
            # failing that, the nearest outright (re-split next pass). Explicit
            # loop (no closure capturing the loop var, cf. ruff B023).
            best_o = others[0]
            best_score: tuple[int, float] | None = None
            for o in others:
                cent_o = points[labels == o].mean(axis=0)
                dist_sq = float(np.sum((cent_o - centroid) ** 2))
                overflow = 1 if sizes[o] + sizes[c] > max_size else 0
                score = (overflow, dist_sq)
                if best_score is None or score < best_score:
                    best_score = score
                    best_o = o
            labels[idx] = best_o
            continue
        break
    return _compact_relabel(labels)


@register
class BalancedKMeansClusterCapability(Capability):
    """Size-balanced K-Means — clusters bounded to ``[min_size, max_size]``."""

    name = "cluster_balanced_kmeans"
    description = (
        "Balanced K-Means — partition geometry centroids into clusters whose "
        "size is bounded in [min_size, max_size]. Deterministic k-means++ / "
        "Lloyd followed by a split/merge rebalance. Unlike 'cluster_kmeans' it "
        "enforces per-cluster size bounds; k is chosen automatically from "
        "min_size/max_size. When the split/merge rebalance cannot meet the "
        "bounds — infeasible for the point count (e.g. min_size == max_size not "
        "dividing n) or a cluster of excess co-located/duplicate points that "
        "cannot be split — it raises a ValueError listing the out-of-bounds "
        "clusters, rather than returning an out-of-contract result silently. "
        "Adds a 'cluster' column."
    )

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        min_size: int = 20,
        max_size: int = 50,
        cluster_col: str = "cluster",
        random_state: int = 42,
        crs_meters: str = "EPSG:3857",
        **_,
    ) -> gpd.GeoDataFrame:
        """
        Args:
            gdf:          Input GeoDataFrame (non-point geometries use their
                          centroid).
            min_size:     Lower bound on cluster size (>= 1). A leftover
                          cluster below this is tolerated only when
                          ``len(gdf) < min_size`` (nothing to merge into).
            max_size:     Upper bound on cluster size (>= min_size).
            cluster_col:  Output cluster id column name.
            random_state: Reproducibility seed (k-means++ init and splits).
            crs_meters:   Metric CRS used for the centroid geometry.

        Returns:
            Copy of *gdf* with an added integer *cluster_col*, renumbered
            0..K-1 by descending cluster size then id (deterministic). Every
            cluster satisfies ``[min_size, max_size]``, except the single
            leftover cluster tolerated when ``len(gdf) < min_size``.

        Raises:
            ValueError: ``min_size < 1`` or ``min_size > max_size``; or the
                split/merge rebalance cannot enforce ``[min_size, max_size]``
                for this point set — infeasible bounds for the point count
                (e.g. ``min_size == max_size`` not dividing n) or a cluster of
                excess co-located/duplicate points that cannot be split. The
                error lists the out-of-bounds clusters and their sizes with a
                recovery hint; the bound guarantee is never violated silently.
        """
        if min_size < 1:
            raise ValueError("min_size must be >= 1.")
        if min_size > max_size:
            raise ValueError(f"min_size ({min_size}) > max_size ({max_size}).")

        if gdf.empty:
            out = gdf.copy()
            out[cluster_col] = np.array([], dtype=np.int64)
            return out

        coords = _coords_from_gdf(gdf, crs_meters)
        n = coords.shape[0]
        rng = np.random.default_rng(random_state)

        k = _choose_k(n, min_size, max_size)
        labels = _lloyd(coords, k, rng) if k > 1 else np.zeros(n, dtype=int)
        labels = _rebalance(coords, labels, min_size, max_size, rng)

        # Enforce the hard [min_size, max_size] guarantee at the boundary (AX
        # #5 validate before returning, #4 machine-readable error). The
        # split/merge rebalance cannot always converge — infeasible bounds for
        # this n (e.g. min_size == max_size not dividing n), or a cluster of
        # excess co-located/duplicate points that k-means++ cannot split (the
        # 2-means split of coincident points is a no-op) — and would otherwise
        # return an out-of-contract result silently. The only excused
        # violation is the documented irreducible remainder: with
        # n < min_size there is nothing to merge into, so a single undersized
        # cluster is tolerated.
        sizes = {int(c): int((labels == c).sum()) for c in np.unique(labels)}
        excused = n < min_size and len(sizes) == 1
        if not excused:
            offending = {
                c: s for c, s in sizes.items() if s < min_size or s > max_size
            }
            if offending:
                raise ValueError(
                    "cluster_balanced_kmeans: cannot enforce cluster size "
                    f"bounds [{min_size}, {max_size}] for n={n} points — "
                    f"out-of-bounds clusters (cluster_id: size): {offending}. "
                    "The bounds are infeasible for this point set: either "
                    "min_size == max_size does not divide n, or a cluster holds "
                    "more than max_size co-located/duplicate points that cannot "
                    "be split. Recovery: increase max_size, reduce min_size, "
                    "widen the [min_size, max_size] range, or de-duplicate the "
                    "co-located points."
                )

        out = gdf.copy()
        out[cluster_col] = labels.astype(np.int64)
        return out

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "min_size": {
                    "type": "integer",
                    "minimum": 1,
                    "default": 20,
                    "description": "Lower bound on cluster size.",
                },
                "max_size": {
                    "type": "integer",
                    "minimum": 1,
                    "default": 50,
                    "description": "Upper bound on cluster size (>= min_size).",
                },
                "cluster_col": {
                    "type": "string",
                    "default": "cluster",
                    "description": "Output column name.",
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
        }
