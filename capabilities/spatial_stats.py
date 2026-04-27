"""Spatial statistics capabilities — autocorrelation, hotspots, weights.

Implements the core algorithms from PySAL/esda without the external
dependency: weights are built here in NumPy/SciPy, and Moran's I and
Getis-Ord G* are computed from the formulae directly. This keeps the
capability module installable with only the default GISPulse stack.

Provided capabilities:

- :class:`SpatialWeightsCapability`  — build a row-standardised spatial
  weights matrix and expose its neighbour counts.
- :class:`MoransICapability`         — global Moran's I with pseudo
  p-value via permutation inference.
- :class:`GetisOrdGStarCapability`   — local Gi* (z-scores) identifying
  statistically significant hot/cold spots per feature.
"""

from __future__ import annotations


import geopandas as gpd
import numpy as np
import pandas as pd

from capabilities.base import Capability
from capabilities.registry import register


# ---------------------------------------------------------------------------
# Weights construction
# ---------------------------------------------------------------------------

_WEIGHTS_METHODS = {"queen", "rook", "knn", "distance_band"}


def _build_weights(
    gdf: gpd.GeoDataFrame,
    method: str,
    k: int,
    threshold: float | None,
    crs_meters: str,
) -> tuple[np.ndarray, list[list[int]]]:
    """Return (row-standardised W matrix, neighbour lists) as NumPy structures.

    - ``queen`` / ``rook`` use shapely spatial index and the relevant predicate.
    - ``knn``           uses a KDTree on centroids (in *crs_meters* for metric k).
    - ``distance_band`` keeps all neighbours within *threshold* (crs_meters).
    """
    if method not in _WEIGHTS_METHODS:
        raise ValueError(
            f"Invalid method '{method}'. Expected {sorted(_WEIGHTS_METHODS)}."
        )

    n = len(gdf)
    if n == 0:
        return np.empty((0, 0)), []

    neighbours: list[list[int]] = [[] for _ in range(n)]

    if method in ("queen", "rook"):
        sindex = gdf.sindex
        geoms = list(gdf.geometry)
        for i, gi in enumerate(geoms):
            if gi is None or gi.is_empty:
                continue
            for j in sindex.intersection(gi.bounds):
                if j == i:
                    continue
                gj = geoms[j]
                if gj is None or gj.is_empty:
                    continue
                # Queen = touches or intersects boundary (any shared point);
                # Rook  = shared edge (intersection is a line / has length).
                if not gi.intersects(gj):
                    continue
                if method == "queen":
                    neighbours[i].append(j)
                else:
                    shared = gi.boundary.intersection(gj.boundary)
                    if not shared.is_empty and shared.length > 1e-9:
                        neighbours[i].append(j)

    elif method == "knn":
        from scipy.spatial import cKDTree

        work = gdf.to_crs(crs_meters) if gdf.crs is not None else gdf
        pts = np.column_stack([
            work.geometry.centroid.x.to_numpy(),
            work.geometry.centroid.y.to_numpy(),
        ])
        tree = cKDTree(pts)
        k_eff = min(k, n - 1)
        if k_eff < 1:
            return np.zeros((n, n)), neighbours
        _, idx = tree.query(pts, k=k_eff + 1)
        # idx[i, 0] is i itself; take [1:].
        for i in range(n):
            neighbours[i] = [int(j) for j in idx[i, 1:]]

    else:  # distance_band
        if threshold is None or threshold <= 0:
            raise ValueError(
                "method='distance_band' requires a positive threshold."
            )
        from scipy.spatial import cKDTree

        work = gdf.to_crs(crs_meters) if gdf.crs is not None else gdf
        pts = np.column_stack([
            work.geometry.centroid.x.to_numpy(),
            work.geometry.centroid.y.to_numpy(),
        ])
        tree = cKDTree(pts)
        for i in range(n):
            cand = tree.query_ball_point(pts[i], r=threshold)
            neighbours[i] = [int(j) for j in cand if j != i]

    # Row-standardise
    W = np.zeros((n, n), dtype=float)
    for i, nb in enumerate(neighbours):
        if not nb:
            continue
        w = 1.0 / len(nb)
        for j in nb:
            W[i, j] = w
    return W, neighbours


@register
class SpatialWeightsCapability(Capability):
    """Builds a row-standardised spatial weights matrix — emits neighbour counts."""

    name = "spatial_weights"
    description = (
        "Computes spatial weights (queen / rook / k-NN / distance band) and "
        "attaches a 'n_neighbours' column to the output layer."
    )

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        method: str = "queen",
        k: int = 8,
        threshold: float | None = None,
        crs_meters: str = "EPSG:3857",
        col_prefix: str = "w",
        **_,
    ) -> gpd.GeoDataFrame:
        """
        Args:
            gdf:         Input GeoDataFrame.
            method:      'queen' / 'rook' / 'knn' / 'distance_band'.
            k:           Neighbours per feature for 'knn'.
            threshold:   Distance threshold (crs_meters) for 'distance_band'.
            crs_meters:  Metric CRS used for knn / distance_band.
            col_prefix:  Output column prefix — emits <prefix>_n_neighbours.

        Returns:
            Copy of *gdf* with an integer ``<prefix>_n_neighbours`` column.
        """
        if gdf.empty:
            out = gdf.copy()
            out[f"{col_prefix}_n_neighbours"] = np.array([], dtype=np.int64)
            return out

        _, neighbours = _build_weights(gdf, method, k, threshold, crs_meters)
        out = gdf.copy()
        out[f"{col_prefix}_n_neighbours"] = np.array(
            [len(nb) for nb in neighbours], dtype=np.int64
        )
        return out

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "method": {
                    "type": "string",
                    "enum": sorted(_WEIGHTS_METHODS),
                    "default": "queen",
                },
                "k": {
                    "type": "integer",
                    "minimum": 1,
                    "default": 8,
                    "description": "Neighbours for knn.",
                },
                "threshold": {
                    "type": ["number", "null"],
                    "description": "Distance threshold (crs_meters) for distance_band.",
                },
                "crs_meters": {
                    "type": "string",
                    "default": "EPSG:3857",
                },
                "col_prefix": {
                    "type": "string",
                    "default": "w",
                },
            },
        }


# ---------------------------------------------------------------------------
# Moran's I
# ---------------------------------------------------------------------------


def _moran_i(z: np.ndarray, W: np.ndarray) -> float:
    """Compute Moran's I statistic for a standardised weight matrix.

    I = (n / S0) · (z' W z / z' z)  with z = x - mean(x), S0 = sum(W).
    """
    n = len(z)
    S0 = W.sum()
    if S0 <= 0 or n < 2:
        return float("nan")
    num = z @ W @ z
    den = (z * z).sum()
    if den <= 0:
        return float("nan")
    return (n / S0) * (num / den)


@register
class MoransICapability(Capability):
    """Global Moran's I — spatial autocorrelation of a numeric field."""

    name = "morans_i"
    description = (
        "Computes global Moran's I (autocorrelation) with a pseudo p-value "
        "via random permutations. Returns a single-row summary GeoDataFrame."
    )

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        field: str = "",
        method: str = "queen",
        k: int = 8,
        threshold: float | None = None,
        permutations: int = 999,
        crs_meters: str = "EPSG:3857",
        **_,
    ) -> gpd.GeoDataFrame:
        """
        Args:
            gdf:          Input GeoDataFrame.
            field:        Numeric column on which autocorrelation is tested.
            method:       Spatial weights method (see SpatialWeightsCapability).
            k, threshold: Weights parameters.
            permutations: Number of random permutations (0 disables p-value).
            crs_meters:   Metric CRS for distance-based weights.

        Returns:
            Single-row GeoDataFrame with columns: morans_i, expected_i,
            z_score, p_value, n, n_neighbours_avg, geometry (union of inputs).
        """
        if not field:
            raise ValueError("'field' is required.")
        if field not in gdf.columns:
            raise ValueError(f"Field '{field}' not in GeoDataFrame.")
        if gdf.empty:
            raise ValueError("Cannot compute Moran's I on an empty layer.")

        x = pd.to_numeric(gdf[field], errors="coerce").to_numpy(dtype=float)
        if np.isnan(x).any():
            raise ValueError(
                f"Field '{field}' contains non-numeric or NaN values; "
                "drop or impute them before computing Moran's I."
            )

        W, neighbours = _build_weights(gdf, method, k, threshold, crs_meters)
        z = x - x.mean()
        # P1 (beta-test 2026-04-24): a constant field has zero variance so
        # Moran's I is mathematically undefined. The permutation-based pseudo
        # p-value used to silently land near 0.01 because the NaN-vs-NaN
        # comparison ``np.abs(sim - expected) >= abs(i_stat - expected)``
        # evaluates to False for every simulation — making a constant field
        # look statistically significant. Short-circuit to all-NaN here so
        # downstream visualisations don't paint a false signal.
        if not np.any(z != 0.0):
            return gpd.GeoDataFrame(
                {
                    "morans_i": [float("nan")],
                    "expected_i": [-1.0 / (len(x) - 1)] if len(x) > 1 else [float("nan")],
                    "z_score": [float("nan")],
                    "p_value": [float("nan")],
                    "n": [len(x)],
                    "n_neighbours_avg": [
                        float(np.mean([len(nb) for nb in neighbours]))
                        if neighbours else float("nan")
                    ],
                    "geometry": [gdf.geometry.union_all()],
                },
                crs=gdf.crs,
            )

        i_stat = _moran_i(z, W)
        expected = -1.0 / (len(x) - 1)

        p_value = float("nan")
        z_score = float("nan")
        if permutations > 0:
            rng = np.random.default_rng(42)
            sim = np.empty(permutations, dtype=float)
            for p in range(permutations):
                sim[p] = _moran_i(rng.permutation(z), W)
            # two-sided pseudo p-value
            more_extreme = (np.abs(sim - expected) >= abs(i_stat - expected)).sum()
            p_value = (more_extreme + 1.0) / (permutations + 1.0)
            sim_std = sim.std(ddof=1)
            if sim_std > 0:
                z_score = (i_stat - sim.mean()) / sim_std

        summary = gpd.GeoDataFrame(
            {
                "morans_i": [i_stat],
                "expected_i": [expected],
                "z_score": [z_score],
                "p_value": [p_value],
                "n": [len(x)],
                "n_neighbours_avg": [float(np.mean([len(nb) for nb in neighbours]))],
                "geometry": [gdf.geometry.union_all()],
            },
            crs=gdf.crs,
        )
        return summary

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "field": {"type": "string", "description": "Numeric column."},
                "method": {
                    "type": "string",
                    "enum": sorted(_WEIGHTS_METHODS),
                    "default": "queen",
                },
                "k": {"type": "integer", "minimum": 1, "default": 8},
                "threshold": {"type": ["number", "null"]},
                "permutations": {
                    "type": "integer",
                    "minimum": 0,
                    "default": 999,
                    "description": "Permutations for pseudo p-value (0 disables).",
                },
                "crs_meters": {"type": "string", "default": "EPSG:3857"},
            },
            "required": ["field"],
        }


# ---------------------------------------------------------------------------
# Getis-Ord Gi*
# ---------------------------------------------------------------------------


@register
class GetisOrdGStarCapability(Capability):
    """Local Gi* — identifies statistically significant hot and cold spots."""

    name = "getis_ord_g"
    description = (
        "Computes the Getis-Ord Gi* z-score per feature. "
        "Positive z = hot spot (high values clustered), negative z = cold spot."
    )

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        field: str = "",
        method: str = "queen",
        k: int = 8,
        threshold: float | None = None,
        include_self: bool = True,
        crs_meters: str = "EPSG:3857",
        **_,
    ) -> gpd.GeoDataFrame:
        """
        Args:
            gdf:          Input GeoDataFrame.
            field:        Numeric field analysed.
            method:       Spatial weights method.
            k, threshold: Weights parameters.
            include_self: When True, use Gi* (includes the feature itself);
                          when False, use the classic Gi (excludes self).
            crs_meters:   Metric CRS for distance-based weights.

        Returns:
            Copy of *gdf* with added columns:
              - gi_star (numerator / denominator ratio)
              - z_score (standardized Gi*)
              - p_value (two-sided, normal approximation)
              - hotspot_label  ('hot' | 'cold' | 'not_significant')
        """
        from math import erf, sqrt

        if not field:
            raise ValueError("'field' is required.")
        if field not in gdf.columns:
            raise ValueError(f"Field '{field}' not in GeoDataFrame.")
        if gdf.empty:
            out = gdf.copy()
            for col in ("gi_star", "z_score", "p_value"):
                out[col] = np.array([], dtype=float)
            out["hotspot_label"] = pd.array([], dtype="string")
            return out

        x = pd.to_numeric(gdf[field], errors="coerce").to_numpy(dtype=float)
        if np.isnan(x).any():
            raise ValueError(
                f"Field '{field}' contains non-numeric or NaN values."
            )

        W_std, neighbours = _build_weights(gdf, method, k, threshold, crs_meters)
        # Use binary weights (not standardized) for Gi* so the classic formula
        # gives an interpretable z-score. Build a simple 0/1 W here.
        n = len(x)
        W = np.zeros((n, n), dtype=float)
        for i, nb in enumerate(neighbours):
            for j in nb:
                W[i, j] = 1.0
            if include_self:
                W[i, i] = 1.0

        x_mean = x.mean()
        s2 = ((x - x_mean) ** 2).mean()
        s = np.sqrt(s2) if s2 > 0 else 1.0

        z_scores = np.zeros(n)
        gi_values = np.zeros(n)
        for i in range(n):
            w_row = W[i]
            sum_w = w_row.sum()
            sum_w2 = (w_row ** 2).sum()
            if sum_w <= 0:
                z_scores[i] = np.nan
                gi_values[i] = np.nan
                continue
            numerator = (w_row * x).sum() - x_mean * sum_w
            denominator = s * np.sqrt((n * sum_w2 - sum_w ** 2) / max(n - 1, 1))
            if denominator == 0:
                z_scores[i] = np.nan
            else:
                z_scores[i] = numerator / denominator
            # Gi* as a normalised ratio (for reference)
            wx = (w_row * x).sum()
            gi_values[i] = wx / x.sum() if x.sum() != 0 else np.nan

        # Two-sided p-value from normal approximation
        # p = 2 * (1 - Phi(|z|))
        def _p_from_z(z: float) -> float:
            if np.isnan(z):
                return float("nan")
            return 2.0 * (1.0 - 0.5 * (1.0 + erf(abs(z) / sqrt(2.0))))

        p_vals = np.array([_p_from_z(z) for z in z_scores])
        labels: list[str] = []
        for z, p in zip(z_scores, p_vals):
            if np.isnan(z) or np.isnan(p) or p >= 0.05:
                labels.append("not_significant")
            elif z > 0:
                labels.append("hot")
            else:
                labels.append("cold")

        out = gdf.copy()
        out["gi_star"] = gi_values
        out["z_score"] = z_scores
        out["p_value"] = p_vals
        out["hotspot_label"] = labels
        return out

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "field": {"type": "string"},
                "method": {
                    "type": "string",
                    "enum": sorted(_WEIGHTS_METHODS),
                    "default": "queen",
                },
                "k": {"type": "integer", "minimum": 1, "default": 8},
                "threshold": {"type": ["number", "null"]},
                "include_self": {
                    "type": "boolean",
                    "default": True,
                    "description": "True = Gi* (classic), False = Gi (exclude self).",
                },
                "crs_meters": {"type": "string", "default": "EPSG:3857"},
            },
            "required": ["field"],
        }
