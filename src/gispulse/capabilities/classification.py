"""
Classification capabilities for GISPulse.

Groups all classification/choropleth primitives out of ``capabilities.vector``:

- :class:`ClassifyCapability` — bucket a numeric column into N classes
  (``quantile``, ``equal_interval``, ``manual``, ``jenks``, ``pretty``,
  ``std_dev``) with optional color mapping.
- :class:`ChoroplethCapability` — sugar over ``classify`` that additionally
  produces a full LayerStyleDef (graduated renderer) and a legend dict on
  ``gdf.attrs``, so downstream consumers (QML/SLD export, portal legend UI,
  artifact serialization) pick up structured metadata instead of just a
  per-feature color column.
- :class:`ClassifyCategoricalCapability` — unique-value classification for
  string/discrete fields with a qualitative palette (Set1/Set2/Dark2…) and
  optional explicit ``{value: color}`` mapping.
- :class:`NormalizeCapability` — preprocessing for classify: minmax / zscore
  / log / log1p / rank / percent with optional ``denom_field`` for per-area
  or per-population ratios.

The ``jenks`` method uses :mod:`mapclassify` if installed (extra
``gispulse[classification]``) and falls back to a pure-numpy implementation
otherwise so the default GISPulse install keeps Jenks available.

Palette names are resolved through :mod:`capabilities.palettes`, so scenarios
can write ``"palette": "YlOrRd"`` instead of copy-pasting five hex codes.
"""

from __future__ import annotations

import geopandas as gpd
import numpy as np
import pandas as pd

from gispulse.capabilities.base import Capability
from gispulse.capabilities.palettes import list_palettes, resolve_palette
from gispulse.capabilities.registry import register


_CLASSIFY_METHODS = {
    "quantile",
    "equal_interval",
    "manual",
    "jenks",
    "pretty",
    "std_dev",
}


# ── Jenks natural breaks ─────────────────────────────────────────────────

_JENKS_SAMPLE_CAP = 1000  # Pure-numpy fallback degrades past this; sample above.


def _jenks_breaks_numpy(values: np.ndarray, k: int) -> list[float]:
    """Pure numpy Jenks natural breaks (Fisher 1958 / Jenks 1967).

    O(n²k) dynamic programming. For n > ``_JENKS_SAMPLE_CAP`` the input is
    uniformly sampled down to that size — breaks on the subsample are within
    noise of the full-data optimum for the class counts (2..9) we care about.
    """
    data = np.asarray(values, dtype=float)
    data = data[~np.isnan(data)]
    data.sort()
    n = data.size
    if n == 0:
        raise ValueError("jenks: all values are NaN")
    if k >= n:
        return [float(data[0]), *map(float, data), float(data[-1])][: k + 1]
    if n > _JENKS_SAMPLE_CAP:
        idx = np.linspace(0, n - 1, _JENKS_SAMPLE_CAP).astype(int)
        data = data[idx]
        n = data.size

    # mat1[l, j] = index of lower class limit for class j with l elements
    # mat2[l, j] = accumulated variance
    mat1 = np.zeros((n + 1, k + 1), dtype=np.int64)
    mat2 = np.full((n + 1, k + 1), np.inf)
    mat2[0, :] = 0.0
    mat2[1:, 0] = 0.0
    for i in range(1, k + 1):
        mat1[1, i] = 1
        mat2[1, i] = 0.0

    for ll in range(2, n + 1):
        s1 = 0.0
        s2 = 0.0
        w = 0
        for m in range(1, ll + 1):
            i3 = ll - m + 1
            val = data[i3 - 1]
            s2 += val * val
            s1 += val
            w += 1
            variance = s2 - (s1 * s1) / w
            i4 = i3 - 1
            if i4 != 0:
                for j in range(2, k + 1):
                    candidate = variance + mat2[i4, j - 1]
                    if mat2[ll, j] >= candidate:
                        mat1[ll, j] = i3
                        mat2[ll, j] = candidate
        mat1[ll, 1] = 1
        mat2[ll, 1] = s2 - (s1 * s1) / ll if ll > 0 else 0.0

    # Extract the class boundaries. ``mat1[idx, count]`` is the 1-based index of
    # the first element of the upper class — so the upper bound of the lower
    # class is ``data[mat1 - 2]`` (last element just before the split).
    breaks = [0.0] * (k + 1)
    breaks[0] = float(data[0])
    breaks[k] = float(data[-1])
    idx = n
    count = k
    while count >= 2:
        cut = int(mat1[idx, count])
        breaks[count - 1] = float(data[cut - 2])
        idx = cut - 1
        count -= 1
    return breaks


def _jenks_breaks(values: pd.Series, k: int) -> list[float]:
    """Return k+1 Jenks break points. Prefers mapclassify when available."""
    vals = pd.to_numeric(values, errors="coerce").dropna().to_numpy(dtype=float)
    if vals.size == 0:
        raise ValueError("jenks: no numeric values to classify")
    try:
        import mapclassify  # type: ignore

        # mapclassify.NaturalBreaks does k-means with random restarts via
        # np.random, so it is non-deterministic AND leaks into the global
        # numpy RNG. Pin a local seed so breaks are reproducible and the
        # call does not mutate state seen by other tests / pipeline steps.
        state = np.random.get_state()
        try:
            np.random.seed(0)
            nb = mapclassify.NaturalBreaks(vals, k=k)
        finally:
            np.random.set_state(state)
        # mapclassify.bins are k upper bounds; prepend the data min.
        return [float(vals.min()), *[float(b) for b in nb.bins]]
    except ImportError:
        return _jenks_breaks_numpy(vals, k)


# ── Pretty breaks (R base::pretty) ────────────────────────────────────────


def _pretty_breaks(vmin: float, vmax: float, n: int) -> list[float]:
    """R-style `pretty()` breaks.

    Produces "nice" step of the form {1, 2, 5, 10} * 10^k that brackets
    [vmin, vmax] with approximately n intervals. The actual count can differ
    from n by ±1 — this is intentional and matches R/QGIS behaviour.
    """
    if vmin == vmax:
        return [vmin, vmax]
    if n < 1:
        raise ValueError("pretty: n must be >= 1")
    span = vmax - vmin
    # Raw step candidate
    raw_step = span / n
    magnitude = 10 ** np.floor(np.log10(raw_step))
    normalized = raw_step / magnitude
    # Round up to the nearest "nice" mantissa
    if normalized <= 1:
        step = 1 * magnitude
    elif normalized <= 2:
        step = 2 * magnitude
    elif normalized <= 5:
        step = 5 * magnitude
    else:
        step = 10 * magnitude
    start = np.floor(vmin / step) * step
    end = np.ceil(vmax / step) * step
    breaks = np.arange(start, end + step * 0.5, step)
    return [float(b) for b in breaks]


# ── Standard deviation ───────────────────────────────────────────────────


def _stddev_breaks(values: pd.Series, k: int, multiplier: float = 1.0) -> list[float]:
    """Breaks centered on the mean, spaced by ``multiplier`` standard deviations.

    For odd ``k``, produces ``k - 1`` interior breaks symmetrically around μ
    (so the middle class is centered on μ), bracketed by [min, max]. A common
    choice is ``k=5, multiplier=1.0`` → breaks at μ ± 1.5σ, μ ± 0.5σ.
    """
    if k % 2 == 0:
        raise ValueError(f"std_dev: bins must be odd (so one class is centered on μ), got {k}")
    if multiplier <= 0:
        raise ValueError(f"std_dev: multiplier must be > 0, got {multiplier}")
    vals = pd.to_numeric(values, errors="coerce").dropna().to_numpy(dtype=float)
    if vals.size == 0:
        raise ValueError("std_dev: no numeric values to classify")
    mu = float(np.mean(vals))
    sigma = float(np.std(vals))
    vmin = float(np.min(vals))
    vmax = float(np.max(vals))
    if sigma == 0.0:
        return [vmin, vmax]
    # k-1 interior breaks symmetric around μ: offsets (i - k/2) * mult * σ for i=1..k-1
    half = k / 2.0
    interior = [mu + (i - half) * multiplier * sigma for i in range(1, k)]
    lo = min(vmin, interior[0] - multiplier * sigma)
    hi = max(vmax, interior[-1] + multiplier * sigma)
    return [lo, *interior, hi]


# ── Capability ────────────────────────────────────────────────────────────


@register
class ClassifyCapability(Capability):
    """Bucket a numeric column into N classes and optionally map each class to a color.

    Supported methods:

    - ``quantile`` (default): equal-frequency bins (quintiles when ``bins=5``).
      Uses :func:`pandas.qcut` with ``duplicates='drop'`` so constant regions
      don't blow up.
    - ``equal_interval``: equal-width bins between min and max.
    - ``manual``: caller provides explicit ``breaks`` (``bins+1`` values).
    - ``jenks``: natural breaks — minimises within-class variance. Uses
      :mod:`mapclassify` if installed, else a pure-numpy fallback.
    - ``pretty``: R/QGIS-style readable round-number breaks. The effective
      number of classes can differ from ``bins`` by ±1.
    - ``std_dev``: classes of width ``std_multiplier`` σ centered on the mean.
      Requires odd ``bins``.

    Output adds a 1-indexed class column (``class_col``, default ``"class"``)
    and — if ``palette`` is set — a ``color_col`` (default ``"color"``) with
    the hex color for that class. ``palette`` accepts either a named palette
    (``"YlOrRd"``, ``"Viridis"``, …) or an explicit list of hex strings.

    Examples::

        # Quintiles + named palette
        {"field": "price_per_m2", "method": "quantile", "bins": 5,
         "palette": "YlOrRd"}

        # Jenks natural breaks, 7 classes
        {"field": "density", "method": "jenks", "bins": 7,
         "palette": "Viridis"}

        # Diverging anomaly map around the mean
        {"field": "delta_temp", "method": "std_dev", "bins": 5,
         "palette": "RdBu", "std_multiplier": 1.0}
    """

    name = "classify"
    description = (
        "Bucket a numeric column into N classes "
        "(quantile / equal_interval / manual / jenks / pretty / std_dev) "
        "with optional color mapping."
    )

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        field: str | None = None,
        method: str = "quantile",
        bins: int = 5,
        class_col: str = "class",
        color_col: str | None = "color",
        palette: str | list[str] | None = None,
        breaks: list[float] | None = None,
        std_multiplier: float = 1.0,
        **_,
    ) -> gpd.GeoDataFrame:
        if not field:
            raise ValueError("classify: 'field' parameter is required")
        if field not in gdf.columns:
            raise ValueError(f"classify: field '{field}' not in layer columns")
        if method not in _CLASSIFY_METHODS:
            raise ValueError(
                f"classify: method must be one of {sorted(_CLASSIFY_METHODS)}, got '{method}'"
            )
        if bins < 2:
            raise ValueError("classify: bins must be >= 2")
        if method == "manual":
            if not breaks or len(breaks) != bins + 1:
                raise ValueError(
                    f"classify: 'manual' method requires 'breaks' with {bins + 1} values"
                )

        result = gdf.copy()
        values = pd.to_numeric(result[field], errors="coerce")
        effective_bins = bins
        computed_breaks: list[float] | None = None

        if method == "quantile":
            labels, edges = pd.qcut(
                values, q=bins, labels=False, duplicates="drop", retbins=True
            )
            class_series = labels.astype("Int64") + 1
            computed_breaks = [float(e) for e in edges]
            effective_bins = len(computed_breaks) - 1
        elif method == "equal_interval":
            vmin, vmax = float(np.nanmin(values)), float(np.nanmax(values))
            if vmin == vmax:
                class_series = pd.Series(
                    [1 if pd.notna(v) else pd.NA for v in values],
                    index=result.index,
                    dtype="Int64",
                )
                computed_breaks = [vmin, vmax]
                effective_bins = 1
            else:
                edges = np.linspace(vmin, vmax, bins + 1)
                labels = pd.cut(values, bins=edges, labels=False, include_lowest=True)
                class_series = labels.astype("Int64") + 1
                computed_breaks = [float(e) for e in edges]
        elif method == "manual":
            labels = pd.cut(values, bins=breaks, labels=False, include_lowest=True)
            class_series = labels.astype("Int64") + 1
            computed_breaks = list(breaks)  # type: ignore[arg-type]
        elif method == "jenks":
            edges = _jenks_breaks(values, bins)
            # Dedup in case of degenerate data so pd.cut doesn't raise.
            edges = sorted(set(edges))
            effective_bins = len(edges) - 1
            if effective_bins < 1:
                raise ValueError("jenks: could not compute distinct breaks")
            labels = pd.cut(values, bins=edges, labels=False, include_lowest=True)
            class_series = labels.astype("Int64") + 1
            computed_breaks = [float(e) for e in edges]
        elif method == "pretty":
            vmin, vmax = float(np.nanmin(values)), float(np.nanmax(values))
            edges = _pretty_breaks(vmin, vmax, bins)
            edges = sorted(set(edges))
            effective_bins = len(edges) - 1
            if effective_bins < 1:
                effective_bins = 1
                edges = [vmin, vmax]
            labels = pd.cut(values, bins=edges, labels=False, include_lowest=True)
            class_series = labels.astype("Int64") + 1
            computed_breaks = [float(e) for e in edges]
        else:  # std_dev
            edges = _stddev_breaks(values, bins, multiplier=std_multiplier)
            edges = sorted(set(edges))
            effective_bins = len(edges) - 1
            if effective_bins < 1:
                effective_bins = 1
                edges = [float(np.nanmin(values)), float(np.nanmax(values))]
            labels = pd.cut(values, bins=edges, labels=False, include_lowest=True)
            class_series = labels.astype("Int64") + 1
            computed_breaks = [float(e) for e in edges]

        result[class_col] = class_series

        if palette is not None:
            resolved = resolve_palette(palette, effective_bins)
            if color_col and resolved is not None:
                def _to_color(c):
                    if pd.isna(c):
                        return None
                    idx = int(c) - 1
                    if 0 <= idx < len(resolved):
                        return resolved[idx]
                    return None

                result[color_col] = class_series.map(_to_color)

        # Stash classification metadata for downstream capabilities (choropleth,
        # legend artifact, QML export). Kept on ``gdf.attrs`` so the payload
        # survives copy but does not pollute columns / I/O.
        style_meta = {
            "field": field,
            "method": method,
            "bins": effective_bins,
            "class_col": class_col,
        }
        if computed_breaks is not None:
            style_meta["breaks"] = computed_breaks
        if palette is not None:
            style_meta["palette"] = palette
        result.attrs["gispulse_style"] = style_meta

        return result

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "field": {
                    "type": "string",
                    "description": "Numeric column to classify.",
                },
                "method": {
                    "type": "string",
                    "enum": sorted(_CLASSIFY_METHODS),
                    "default": "quantile",
                },
                "bins": {"type": "integer", "minimum": 2, "default": 5},
                "class_col": {"type": "string", "default": "class"},
                "color_col": {"type": ["string", "null"], "default": "color"},
                "palette": {
                    "description": (
                        "Named palette (e.g. 'YlOrRd', 'Viridis', 'RdBu') "
                        "or explicit hex list (length must equal bins)."
                    ),
                    "oneOf": [
                        {"type": "string", "enum": list_palettes()},
                        {"type": "array", "items": {"type": "string"}},
                        {"type": "null"},
                    ],
                },
                "breaks": {
                    "type": ["array", "null"],
                    "items": {"type": "number"},
                    "description": "Explicit breakpoints (bins+1 values). Only for method='manual'.",
                },
                "std_multiplier": {
                    "type": "number",
                    "minimum": 0,
                    "default": 1.0,
                    "description": "Class width in σ units. Only for method='std_dev'.",
                },
            },
            "required": ["field"],
        }


# ── Choropleth sugar ─────────────────────────────────────────────────────


def _infer_geom_type(gdf: gpd.GeoDataFrame) -> str:
    """Best-effort geom type inference for LayerStyleDef symbol building."""
    if gdf.geometry.empty:
        return "polygon"
    # Use the first non-empty geometry's type
    for geom in gdf.geometry:
        if geom is None or geom.is_empty:
            continue
        t = geom.geom_type.lower()
        if "point" in t:
            return "point"
        if "line" in t:
            return "line"
        if "polygon" in t:
            return "polygon"
    return "polygon"


def _default_symbol_for_geom(geom_type: str, color: str) -> dict:
    """Build a minimal symbol dict compatible with persistence.style_converter."""
    if geom_type == "point":
        return {
            "kind": "point", "shape": "circle", "size": 6, "color": color,
            "opacity": 0.9, "strokeColor": "#ffffff", "strokeWidth": 1,
        }
    if geom_type == "line":
        return {
            "kind": "line", "color": color, "width": 2, "opacity": 1.0,
            "cap": "round", "join": "round",
        }
    return {
        "kind": "fill", "color": color, "opacity": 0.7,
        "strokeColor": "#ffffff", "strokeWidth": 0.5,
    }


def build_legend(
    gdf: gpd.GeoDataFrame,
    *,
    class_col: str = "class",
    color_col: str | None = "color",
    label_fmt: str | None = None,
) -> dict:
    """Build a structured legend dict from a classified GeoDataFrame.

    Expects the metadata attached by :class:`ClassifyCapability` on
    ``gdf.attrs["gispulse_style"]``. The output shape is designed to feed a
    portal component or a ``GET /jobs/{id}/artifacts/legend`` endpoint:

    .. code-block:: json

        {
          "type": "legend",
          "field": "price_per_m2",
          "method": "jenks",
          "palette": "YlOrRd",
          "classes": [
            {"index": 1, "min": 1500, "max": 3200,
             "label": "1 500 – 3 200", "color": "#ffffb2", "count": 47},
            ...
          ],
          "total_features": 234,
          "nan_count": 3
        }
    """
    meta = dict(gdf.attrs.get("gispulse_style") or {})
    field = meta.get("field")
    method = meta.get("method")
    palette = meta.get("palette")
    breaks = meta.get("breaks")
    n_bins = meta.get("bins") or 0

    # Class counts
    classes = gdf[class_col] if class_col in gdf.columns else pd.Series([], dtype="Int64")
    counts = classes.dropna().astype(int).value_counts().to_dict()
    total = int(len(gdf))
    nan_count = int(classes.isna().sum()) if len(classes) else 0

    # Representative color per class (from color_col if present, else palette)
    color_map: dict[int, str] = {}
    if color_col and color_col in gdf.columns:
        for idx in range(1, n_bins + 1):
            rows = gdf[gdf[class_col] == idx]
            if not rows.empty:
                sample = rows[color_col].dropna()
                if not sample.empty:
                    color_map[idx] = str(sample.iloc[0])
    if palette and not color_map:
        # Fallback: resolve the palette directly
        try:
            resolved = resolve_palette(palette, n_bins)
            if resolved:
                for idx in range(1, n_bins + 1):
                    color_map[idx] = resolved[idx - 1]
        except (ValueError, TypeError):
            pass

    fmt = label_fmt or "{lo:g} – {hi:g}"
    classes_out: list[dict] = []
    for idx in range(1, n_bins + 1):
        cls: dict = {
            "index": idx,
            "count": int(counts.get(idx, 0)),
        }
        if breaks and len(breaks) >= n_bins + 1:
            lo, hi = breaks[idx - 1], breaks[idx]
            cls["min"] = float(lo)
            cls["max"] = float(hi)
            cls["label"] = fmt.format(lo=lo, hi=hi)
        if idx in color_map:
            cls["color"] = color_map[idx]
        classes_out.append(cls)

    return {
        "type": "legend",
        "field": field,
        "method": method,
        "palette": palette,
        "classes": classes_out,
        "total_features": total,
        "nan_count": nan_count,
    }


def build_graduated_style_def(
    gdf: gpd.GeoDataFrame,
    *,
    class_col: str = "class",
    color_col: str = "color",
    geom_type: str | None = None,
) -> dict:
    """Build a LayerStyleDef ``graduated`` dict compatible with QML/SLD export.

    Pairs with :class:`ChoroplethCapability` output so
    :func:`persistence.style_converter.style_def_to_qml` and
    :func:`persistence.sld_converter.style_def_to_sld` can consume it directly.
    """
    meta = dict(gdf.attrs.get("gispulse_style") or {})
    field = meta.get("field") or ""
    method = meta.get("method") or "quantile"
    breaks = meta.get("breaks") or []
    n_bins = meta.get("bins") or 0
    geom = geom_type or _infer_geom_type(gdf)

    # Color per class from the classified output
    color_map: dict[int, str] = {}
    if color_col in gdf.columns:
        for idx in range(1, n_bins + 1):
            rows = gdf[gdf[class_col] == idx]
            if not rows.empty:
                sample = rows[color_col].dropna()
                if not sample.empty:
                    color_map[idx] = str(sample.iloc[0])

    classes: list[dict] = []
    for idx in range(1, n_bins + 1):
        if not breaks or len(breaks) < n_bins + 1:
            continue
        lo, hi = float(breaks[idx - 1]), float(breaks[idx])
        color = color_map.get(idx, "#3b82f6")
        classes.append({
            "lower": lo,
            "upper": hi,
            "label": f"{lo:g} – {hi:g}",
            "symbol": _default_symbol_for_geom(geom, color),
        })

    # Map internal method names to the QML/SLD vocabulary
    method_vocab = {
        "quantile": "Quantile",
        "equal_interval": "EqualInterval",
        "jenks": "NaturalBreaks",
        "pretty": "Pretty",
        "std_dev": "StdDev",
        "manual": "Custom",
    }

    return {
        "renderer": "graduated",
        "graduatedField": field,
        "classifyMethod": method,
        "classifyMethodLabel": method_vocab.get(method, method),
        "classes": classes,
    }


@register
class ChoroplethCapability(Capability):
    """Produce a complete choropleth bundle from a numeric field.

    Runs :class:`ClassifyCapability` under the hood then enriches the output
    with:

    - ``gdf.attrs["gispulse_style"]``   — LayerStyleDef ``graduated`` renderer
      (QML/SLD ready via :mod:`persistence.style_converter` /
      :mod:`persistence.sld_converter`)
    - ``gdf.attrs["gispulse_legend"]``  — structured legend dict (counts,
      labels, colors per class, NaN count)

    Intentionally strict: requires a numeric ``field`` and a ``palette`` so the
    output is visually meaningful out of the box. For bare classification
    without a renderer, use the ``classify`` capability directly.

    Example::

        {
            "capability": "choropleth",
            "params": {
                "field": "price_per_m2",
                "method": "jenks",
                "bins": 5,
                "palette": "YlOrRd"
            }
        }
    """

    name = "choropleth"
    description = (
        "Classify a numeric field and attach a full LayerStyleDef + legend "
        "for QML/SLD export and portal rendering."
    )

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        field: str | None = None,
        method: str = "quantile",
        bins: int = 5,
        palette: str | list[str] = "YlOrRd",
        class_col: str = "class",
        color_col: str = "color",
        breaks: list[float] | None = None,
        std_multiplier: float = 1.0,
        geom_type: str | None = None,
        label_fmt: str | None = None,
        **_,
    ) -> gpd.GeoDataFrame:
        if not field:
            raise ValueError("choropleth: 'field' parameter is required")
        if palette is None:
            raise ValueError("choropleth: 'palette' is required (use 'classify' for bare classification)")

        classified = ClassifyCapability().execute(
            gdf,
            field=field,
            method=method,
            bins=bins,
            class_col=class_col,
            color_col=color_col,
            palette=palette,
            breaks=breaks,
            std_multiplier=std_multiplier,
        )

        # Enrich the style attr with the full graduated renderer shape.
        # Kept under the same ``gispulse_style`` key so QML/SLD converters find it.
        classified.attrs["gispulse_style"] = {
            **classified.attrs.get("gispulse_style", {}),
            **build_graduated_style_def(
                classified,
                class_col=class_col,
                color_col=color_col,
                geom_type=geom_type,
            ),
        }
        classified.attrs["gispulse_legend"] = build_legend(
            classified,
            class_col=class_col,
            color_col=color_col,
            label_fmt=label_fmt,
        )
        return classified

    def get_schema(self) -> dict:
        base = ClassifyCapability().get_schema()
        base["properties"]["palette"]["description"] = (
            "Named palette or hex list — REQUIRED for choropleth "
            "(use 'classify' for bare classification)."
        )
        base["properties"]["geom_type"] = {
            "type": ["string", "null"],
            "enum": ["point", "line", "polygon", None],
            "description": "Force geometry type for symbol generation (auto-detected if null).",
        }
        base["properties"]["label_fmt"] = {
            "type": ["string", "null"],
            "description": "Python format string for legend labels (default '{lo:g} – {hi:g}').",
        }
        base["required"] = ["field", "palette"]
        return base


# ── Categorical (unique values) ──────────────────────────────────────────


@register
class ClassifyCategoricalCapability(Capability):
    """Classify a discrete (string/int) field by unique values.

    Complements :class:`ClassifyCapability` which is numeric-only. Typical
    usage: FTTH ``status`` (planned/in_progress/deployed), DVF
    ``type_mutation``, PLU ``usage_sol``.

    Value → color resolution order:
      1. Explicit ``palette`` dict (``{"deployed": "#2ca02c", ...}``) wins
      2. Named qualitative palette (``"Set2"``) cycled over sorted-by-frequency values
      3. ``"other"`` bucket for values beyond ``max_categories`` (queue longue)

    Adds two columns:
      - ``class_col`` (default ``"class"``): 1-indexed class, ``other_label`` for the tail bucket
      - ``color_col`` (default ``"color"``): hex color (``None`` for NaN)

    Also attaches ``gdf.attrs["gispulse_style"]`` as a LayerStyleDef
    ``categorized`` renderer ready for QML/SLD export.
    """

    name = "classify_categorical"
    description = (
        "Classify a discrete field by unique values with a qualitative palette "
        "(and an optional 'other' bucket for the long tail)."
    )

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        field: str | None = None,
        palette: str | list[str] | dict[str, str] = "Set2",
        class_col: str = "class",
        color_col: str | None = "color",
        other_label: str = "Other",
        max_categories: int | None = None,
        **_,
    ) -> gpd.GeoDataFrame:
        if not field:
            raise ValueError("classify_categorical: 'field' parameter is required")
        if field not in gdf.columns:
            raise ValueError(f"classify_categorical: field '{field}' not in layer columns")

        result = gdf.copy()
        series = result[field]

        # Unique values sorted by frequency (desc), NaN excluded
        counts = series.dropna().value_counts()
        values_ordered: list = counts.index.tolist()

        other_used = False
        if isinstance(palette, dict):
            # Explicit dict: only mapped values become named classes; everything
            # else collapses into the "other" bucket at index len(dict)+1.
            values_ordered = [v for v in values_ordered if v in palette]
            unmapped = [v for v in counts.index if v not in palette]
            other_used = bool(unmapped)
            color_of: dict = dict(palette)
            if other_used:
                color_of["__OTHER__"] = palette.get("__other__", "#bdbdbd")
        elif isinstance(palette, (str, list)):
            if max_categories is not None and len(values_ordered) > max_categories:
                values_ordered = values_ordered[:max_categories]
                other_used = True
            n = len(values_ordered) + (1 if other_used else 0)
            if n < 2:
                n = 2
            resolved = resolve_palette(palette, n) or []
            color_of = {v: resolved[i] for i, v in enumerate(values_ordered)}
            if other_used and len(resolved) > len(values_ordered):
                color_of["__OTHER__"] = resolved[len(values_ordered)]
            elif other_used and resolved:
                color_of["__OTHER__"] = "#bdbdbd"
        else:
            raise TypeError(
                "classify_categorical: palette must be a name (str), list of hex, "
                "or {value: hex} dict"
            )

        # Build class + color columns
        value_to_idx: dict = {v: i + 1 for i, v in enumerate(values_ordered)}
        other_idx = len(values_ordered) + 1
        classes: list = []
        colors: list = []
        for v in series:
            if pd.isna(v):
                classes.append(pd.NA)
                colors.append(None)
            elif v in value_to_idx:
                classes.append(value_to_idx[v])
                colors.append(color_of.get(v))
            else:
                # Beyond max_categories or absent from explicit dict → other bucket
                classes.append(other_idx)
                colors.append(color_of.get("__OTHER__"))

        result[class_col] = pd.Series(classes, index=result.index, dtype="Int64")
        if color_col:
            result[color_col] = pd.Series(colors, index=result.index, dtype="object")

        # LayerStyleDef categorized renderer for QML/SLD roundtrip
        categories: list[dict] = []
        for i, v in enumerate(values_ordered, start=1):
            color = color_of.get(v, "#3b82f6")
            categories.append({
                "value": v,
                "label": str(v),
                "symbol": _default_symbol_for_geom(_infer_geom_type(result), color),
            })
        if other_used:
            other_color = color_of.get("__OTHER__", "#bdbdbd")
            categories.append({
                "value": None,  # null bucket / ElseFilter in SLD
                "label": other_label,
                "symbol": _default_symbol_for_geom(_infer_geom_type(result), other_color),
            })

        result.attrs["gispulse_style"] = {
            "renderer": "categorized",
            "classField": field,
            "categories": categories,
        }
        result.attrs["gispulse_legend"] = {
            "type": "legend",
            "field": field,
            "method": "categorical",
            "palette": palette if not isinstance(palette, dict) else None,
            "classes": [
                {
                    "index": i,
                    "value": v,
                    "label": str(v),
                    "color": color_of.get(v),
                    "count": int(counts.get(v, 0)),
                }
                for i, v in enumerate(values_ordered, start=1)
            ] + (
                [{
                    "index": other_idx,
                    "value": None,
                    "label": other_label,
                    "color": color_of.get("__OTHER__"),
                    "count": int(series.notna().sum() - sum(counts.get(v, 0) for v in values_ordered)),
                }] if other_used else []
            ),
            "total_features": int(len(series)),
            "nan_count": int(series.isna().sum()),
        }

        return result

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "field": {"type": "string", "description": "Discrete field to classify."},
                "palette": {
                    "description": (
                        "Named qualitative palette (e.g. 'Set2', 'Dark2'), hex list, "
                        "or explicit {value: hex} mapping."
                    ),
                    "oneOf": [
                        {"type": "string", "enum": list_palettes()},
                        {"type": "array", "items": {"type": "string"}},
                        {"type": "object", "additionalProperties": {"type": "string"}},
                    ],
                },
                "class_col": {"type": "string", "default": "class"},
                "color_col": {"type": ["string", "null"], "default": "color"},
                "other_label": {"type": "string", "default": "Other"},
                "max_categories": {
                    "type": ["integer", "null"],
                    "minimum": 1,
                    "description": "Collapse values beyond this rank into the 'other' bucket.",
                },
            },
            "required": ["field"],
        }


# ── Normalize (preprocessing for classify) ───────────────────────────────


_NORMALIZE_METHODS = {"minmax", "zscore", "log", "log1p", "rank", "percent"}


@register
class NormalizeCapability(Capability):
    """Normalize a numeric column in preparation for classification.

    Six methods covering the common choropleth pre-processing:

    - ``minmax``  : (x − min) / (max − min) → [0, 1]
    - ``zscore``  : (x − μ) / σ → centered, unit variance
    - ``log``     : ``np.log(x)`` — raises if any x ≤ 0
    - ``log1p``   : ``np.log(x + 1)`` — accepts 0
    - ``rank``    : rank / N → [0, 1], robust to outliers
    - ``percent`` : x / Σx × 100

    ``denom_field`` divides ``field`` by another column BEFORE normalization
    (per-area density, per-capita rate, etc.). Division by zero → NaN.

    Example::

        # price per m² normalized 0-1 for a choropleth
        {"field": "valeur_fonciere", "denom_field": "surface_bati",
         "method": "minmax", "out_field": "price_norm"}
    """

    name = "normalize"
    description = "Normalize a numeric column (minmax / zscore / log / log1p / rank / percent) with optional denom."

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        field: str | None = None,
        method: str = "minmax",
        out_field: str | None = None,
        denom_field: str | None = None,
        **_,
    ) -> gpd.GeoDataFrame:
        if not field:
            raise ValueError("normalize: 'field' parameter is required")
        if field not in gdf.columns:
            raise ValueError(f"normalize: field '{field}' not in layer columns")
        if method not in _NORMALIZE_METHODS:
            raise ValueError(
                f"normalize: method must be one of {sorted(_NORMALIZE_METHODS)}, got '{method}'"
            )
        out_name = out_field or f"{field}_{method}"

        result = gdf.copy()
        values = pd.to_numeric(result[field], errors="coerce")
        if denom_field is not None:
            if denom_field not in gdf.columns:
                raise ValueError(f"normalize: denom_field '{denom_field}' not in layer columns")
            denom = pd.to_numeric(result[denom_field], errors="coerce")
            # Division by zero → NaN (not inf) so downstream classify handles it cleanly
            values = values.where(denom != 0, np.nan) / denom.replace(0, np.nan)

        arr = values.to_numpy(dtype=float)
        mask = ~np.isnan(arr)

        if method == "minmax":
            if mask.sum() == 0:
                out = arr
            else:
                vmin, vmax = float(np.nanmin(arr)), float(np.nanmax(arr))
                if vmax == vmin:
                    out = np.where(mask, 0.5, np.nan)
                else:
                    out = (arr - vmin) / (vmax - vmin)
        elif method == "zscore":
            if mask.sum() == 0:
                out = arr
            else:
                mu = float(np.nanmean(arr))
                sigma = float(np.nanstd(arr))
                if sigma == 0:
                    out = np.where(mask, 0.0, np.nan)
                else:
                    out = (arr - mu) / sigma
        elif method == "log":
            # log(0) = -inf, log(-x) = NaN; demand strictly positive input
            valid = mask & (arr > 0)
            if (mask & ~valid).any():
                raise ValueError(
                    "normalize[log]: input contains non-positive values; use 'log1p' or filter first"
                )
            out = np.where(valid, np.log(np.where(valid, arr, 1.0)), np.nan)
        elif method == "log1p":
            valid = mask & (arr >= 0)
            if (mask & ~valid).any():
                raise ValueError(
                    "normalize[log1p]: input contains negative values; shift first or use 'log' after filter"
                )
            out = np.where(valid, np.log1p(np.where(valid, arr, 0.0)), np.nan)
        elif method == "rank":
            # Dense rank on non-NaN values, normalized to [0, 1]
            ranks = values.rank(method="average", na_option="keep")
            n = int(values.notna().sum())
            if n <= 1:
                out = np.where(mask, 1.0, np.nan)
            else:
                out = ((ranks - 1) / (n - 1)).to_numpy(dtype=float)
        else:  # percent
            if mask.sum() == 0:
                out = arr
            else:
                total = float(np.nansum(arr))
                if total == 0:
                    out = np.where(mask, 0.0, np.nan)
                else:
                    out = arr / total * 100.0

        result[out_name] = out
        return result

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "field": {"type": "string", "description": "Numeric column to normalize."},
                "method": {
                    "type": "string",
                    "enum": sorted(_NORMALIZE_METHODS),
                    "default": "minmax",
                },
                "out_field": {
                    "type": ["string", "null"],
                    "description": "Output column name (default: '<field>_<method>').",
                },
                "denom_field": {
                    "type": ["string", "null"],
                    "description": "Divide 'field' by this column before normalizing (densities, rates).",
                },
            },
            "required": ["field"],
        }


# ── Graduated size (proportional symbols) ────────────────────────────────


_SIZE_SCALINGS = {"linear", "sqrt", "log"}


@register
class GraduatedSizeCapability(Capability):
    """Map a numeric field to a per-feature size range — proportional symbols.

    Complement to choropleth color: for points and lines, variation in size
    often reads better than variation in color. Uses the same classification
    methods as :class:`ClassifyCapability` for class assignment, then maps
    classes to interpolated sizes in ``size_range``.

    Scaling:
      - ``linear`` (default): size ∝ class index
      - ``sqrt``: size ∝ √class index (perceptually correct for area — circles)
      - ``log``: size ∝ log(1 + class index)

    Example::

        # Large markers for high-value DVF transactions
        {"field": "valeur_fonciere", "method": "quantile", "bins": 5,
         "size_range": [4, 24], "scaling": "sqrt"}
    """

    name = "graduated_size"
    description = "Map a numeric column to proportional symbol sizes (points/lines)."

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        field: str | None = None,
        method: str = "quantile",
        bins: int = 5,
        size_range: list[float] | tuple[float, float] = (4.0, 20.0),
        size_col: str = "marker_size",
        scaling: str = "linear",
        class_col: str = "class",
        breaks: list[float] | None = None,
        std_multiplier: float = 1.0,
        **_,
    ) -> gpd.GeoDataFrame:
        if not field:
            raise ValueError("graduated_size: 'field' parameter is required")
        if scaling not in _SIZE_SCALINGS:
            raise ValueError(
                f"graduated_size: scaling must be one of {sorted(_SIZE_SCALINGS)}, got '{scaling}'"
            )
        if len(size_range) != 2 or size_range[0] <= 0 or size_range[1] <= 0:
            raise ValueError("graduated_size: size_range must be [min_px, max_px] with positive values")
        s_min, s_max = float(size_range[0]), float(size_range[1])
        if s_min >= s_max:
            raise ValueError("graduated_size: size_range[0] must be < size_range[1]")

        classified = ClassifyCapability().execute(
            gdf,
            field=field,
            method=method,
            bins=bins,
            class_col=class_col,
            color_col=None,  # no color emission — we're dealing with sizes
            palette=None,
            breaks=breaks,
            std_multiplier=std_multiplier,
        )

        n = int(classified.attrs.get("gispulse_style", {}).get("bins", bins))
        if n < 1:
            n = 1

        def _size_for_class(idx: int) -> float:
            if n <= 1:
                return (s_min + s_max) / 2
            t = (idx - 1) / (n - 1)  # [0, 1]
            if scaling == "sqrt":
                t = t ** 0.5
            elif scaling == "log":
                # Map [0, 1] → [0, 1] via log1p to spread small values
                import math
                t = math.log1p(t * (math.e - 1)) / 1.0
            return s_min + t * (s_max - s_min)

        sizes = [
            _size_for_class(int(c)) if pd.notna(c) else None
            for c in classified[class_col]
        ]
        classified[size_col] = pd.Series(sizes, index=classified.index, dtype="Float64")

        # Style metadata: record the size range so downstream exporters (QML)
        # can map class_col to scale.
        classified.attrs["gispulse_style"] = {
            **classified.attrs.get("gispulse_style", {}),
            "renderer": "graduated_size",
            "sizeField": field,
            "sizeRange": [s_min, s_max],
            "scaling": scaling,
            "sizeCol": size_col,
        }
        return classified

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "field": {"type": "string"},
                "method": {
                    "type": "string",
                    "enum": sorted(_CLASSIFY_METHODS),
                    "default": "quantile",
                },
                "bins": {"type": "integer", "minimum": 2, "default": 5},
                "size_range": {
                    "type": "array",
                    "items": {"type": "number", "minimum": 0},
                    "minItems": 2,
                    "maxItems": 2,
                    "default": [4.0, 20.0],
                },
                "size_col": {"type": "string", "default": "marker_size"},
                "scaling": {
                    "type": "string",
                    "enum": sorted(_SIZE_SCALINGS),
                    "default": "linear",
                },
                "class_col": {"type": "string", "default": "class"},
                "breaks": {"type": ["array", "null"], "items": {"type": "number"}},
                "std_multiplier": {"type": "number", "minimum": 0, "default": 1.0},
            },
            "required": ["field"],
        }


# ── Head/Tail Breaks (Jiang 2013) ────────────────────────────────────────


def _head_tail_breaks(
    values: np.ndarray,
    *,
    max_depth: int = 12,
    head_ratio_cutoff: float = 0.4,
) -> list[float]:
    """Compute Head/Tail breaks recursively.

    Stops recursing when the "head" (values above the mean) exceeds
    ``head_ratio_cutoff`` of the subset — the distribution is no longer
    heavy-tailed at that point. Returns the list of breakpoints (means) in
    ascending order; the caller composes final edges with data min/max.
    """
    breaks: list[float] = []
    vals = values.astype(float)
    vals = vals[~np.isnan(vals)]
    for _ in range(max_depth):
        if vals.size <= 1:
            break
        mu = float(np.mean(vals))
        breaks.append(mu)
        head = vals[vals > mu]
        if head.size == 0:
            break
        ratio = head.size / vals.size
        if ratio > head_ratio_cutoff:
            break
        vals = head
    return breaks


@register
class HeadTailBreaksCapability(Capability):
    """Head/Tail breaks classification for heavy-tail distributions (Jiang 2013).

    Purpose-built for power-law / long-tail data (city populations, real-estate
    prices, social network metrics) where quantiles and Jenks produce flat
    maps. The number of classes is **determined by the data** — ``bins`` is
    ignored (with a warning attached to the style metadata).

    Recursive definition: partition around the mean → keep the "head"
    (values > mean) → recurse until the head is ≥ 40% of its subset (the
    distribution has become quasi-uniform).

    Output columns match :class:`ClassifyCapability`.
    """

    name = "head_tail_breaks"
    description = "Head/Tail breaks classification — number of classes determined by the distribution (Jiang 2013)."

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        field: str | None = None,
        class_col: str = "class",
        color_col: str | None = "color",
        palette: str | list[str] | None = None,
        max_depth: int = 12,
        head_ratio_cutoff: float = 0.4,
        **_,
    ) -> gpd.GeoDataFrame:
        if not field:
            raise ValueError("head_tail_breaks: 'field' parameter is required")
        if field not in gdf.columns:
            raise ValueError(f"head_tail_breaks: field '{field}' not in layer columns")

        result = gdf.copy()
        values = pd.to_numeric(result[field], errors="coerce")
        arr = values.to_numpy(dtype=float)
        internal_breaks = _head_tail_breaks(
            arr, max_depth=max_depth, head_ratio_cutoff=head_ratio_cutoff
        )

        vmin = float(np.nanmin(arr)) if np.isfinite(np.nanmin(arr)) else 0.0
        vmax = float(np.nanmax(arr)) if np.isfinite(np.nanmax(arr)) else 1.0
        edges = [vmin, *internal_breaks, vmax]
        edges = sorted(set(edges))
        effective_bins = len(edges) - 1
        if effective_bins < 1:
            effective_bins = 1
            edges = [vmin, vmax]

        labels = pd.cut(values, bins=edges, labels=False, include_lowest=True)
        result[class_col] = labels.astype("Int64") + 1

        if palette is not None:
            resolved = resolve_palette(palette, effective_bins)
            if color_col and resolved is not None:
                def _to_color(c):
                    if pd.isna(c):
                        return None
                    idx = int(c) - 1
                    if 0 <= idx < len(resolved):
                        return resolved[idx]
                    return None
                result[color_col] = result[class_col].map(_to_color)

        result.attrs["gispulse_style"] = {
            "field": field,
            "method": "head_tail_breaks",
            "bins": effective_bins,
            "class_col": class_col,
            "breaks": [float(e) for e in edges],
        }
        if palette is not None:
            result.attrs["gispulse_style"]["palette"] = palette
        return result

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "field": {"type": "string"},
                "class_col": {"type": "string", "default": "class"},
                "color_col": {"type": ["string", "null"], "default": "color"},
                "palette": {
                    "description": "Named palette or hex list (length determined by the algorithm).",
                    "oneOf": [
                        {"type": "string", "enum": list_palettes()},
                        {"type": "array", "items": {"type": "string"}},
                        {"type": "null"},
                    ],
                },
                "max_depth": {"type": "integer", "minimum": 1, "default": 12},
                "head_ratio_cutoff": {"type": "number", "minimum": 0, "maximum": 1, "default": 0.4},
            },
            "required": ["field"],
        }


# ── Continuous ramp (no bins — gradient) ─────────────────────────────────


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _rgb_to_hex(r: float, g: float, b: float) -> str:
    return f"#{round(r):02x}{round(g):02x}{round(b):02x}"


def _interpolate_palette(palette: list[str], t: float) -> str:
    """Interpolate color at normalized position t in [0, 1] across palette stops."""
    if t <= 0:
        return palette[0]
    if t >= 1:
        return palette[-1]
    pos = t * (len(palette) - 1)
    lo = int(pos)
    frac = pos - lo
    if frac == 0:
        return palette[lo]
    r1, g1, b1 = _hex_to_rgb(palette[lo])
    r2, g2, b2 = _hex_to_rgb(palette[lo + 1])
    return _rgb_to_hex(
        r1 + (r2 - r1) * frac,
        g1 + (g2 - g1) * frac,
        b1 + (b2 - b1) * frac,
    )


_RAMP_SCALINGS = {"linear", "log", "sqrt"}


@register
class ContinuousRampCapability(Capability):
    """Continuous color ramp — no bins, per-feature gradient mapping.

    Useful for densities, kernel estimates, or rasters vectorized into
    polygons where discrete classes lose information. Values are normalized
    to [0, 1] via ``domain`` and ``scaling``, then interpolated across the
    palette's stops.

    Example::

        # Viridis gradient for a density field
        {"field": "density", "palette": "Viridis", "scaling": "log"}
    """

    name = "continuous_ramp"
    description = "Map a numeric column to a continuous color gradient (no classes)."

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        field: str | None = None,
        palette: str | list[str] = "Viridis",
        color_col: str = "color",
        domain: list[float] | str = "auto",
        scaling: str = "linear",
        stops: int = 9,
        **_,
    ) -> gpd.GeoDataFrame:
        if not field:
            raise ValueError("continuous_ramp: 'field' parameter is required")
        if field not in gdf.columns:
            raise ValueError(f"continuous_ramp: field '{field}' not in layer columns")
        if scaling not in _RAMP_SCALINGS:
            raise ValueError(
                f"continuous_ramp: scaling must be one of {sorted(_RAMP_SCALINGS)}, got '{scaling}'"
            )

        result = gdf.copy()
        values = pd.to_numeric(result[field], errors="coerce")
        arr = values.to_numpy(dtype=float)

        if domain == "auto":
            vmin, vmax = float(np.nanmin(arr)), float(np.nanmax(arr))
        elif isinstance(domain, (list, tuple)) and len(domain) == 2:
            vmin, vmax = float(domain[0]), float(domain[1])
        else:
            raise ValueError("continuous_ramp: domain must be 'auto' or [vmin, vmax]")

        # Resolve palette once — small fixed number of stops for interpolation
        resolved = resolve_palette(palette, max(2, min(stops, 9)))
        if resolved is None:
            raise ValueError("continuous_ramp: could not resolve palette")

        def _norm(v: float) -> float:
            if np.isnan(v):
                return float("nan")
            if vmax == vmin:
                return 0.5
            if scaling == "log":
                if v <= 0 or vmin <= 0:
                    return float("nan")
                return float((np.log(v) - np.log(vmin)) / (np.log(vmax) - np.log(vmin)))
            if scaling == "sqrt":
                if v < 0 or vmin < 0:
                    return float("nan")
                return float((np.sqrt(v) - np.sqrt(vmin)) / (np.sqrt(vmax) - np.sqrt(vmin)))
            return float((v - vmin) / (vmax - vmin))

        colors: list = []
        for v in arr:
            t = _norm(v)
            if np.isnan(t):
                colors.append(None)
            else:
                # Clip outside [0, 1] (values beyond the domain)
                t = max(0.0, min(1.0, t))
                colors.append(_interpolate_palette(resolved, t))

        result[color_col] = pd.Series(colors, index=result.index, dtype="object")
        result.attrs["gispulse_style"] = {
            "renderer": "continuous",
            "field": field,
            "method": "continuous_ramp",
            "palette": palette,
            "domain": [vmin, vmax],
            "scaling": scaling,
        }
        return result

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "field": {"type": "string"},
                "palette": {
                    "oneOf": [
                        {"type": "string", "enum": list_palettes()},
                        {"type": "array", "items": {"type": "string"}},
                    ],
                    "default": "Viridis",
                },
                "color_col": {"type": "string", "default": "color"},
                "domain": {
                    "oneOf": [
                        {"type": "string", "enum": ["auto"]},
                        {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2},
                    ],
                    "default": "auto",
                },
                "scaling": {
                    "type": "string",
                    "enum": sorted(_RAMP_SCALINGS),
                    "default": "linear",
                },
                "stops": {"type": "integer", "minimum": 2, "maximum": 9, "default": 9},
            },
            "required": ["field"],
        }


# ── Bivariate choropleth ─────────────────────────────────────────────────

# Stevens-style 3×3 bivariate palettes (Joshua Stevens, open license)
# Indexed by grid cell [y][x] so the low-low corner is at [0][0] and the
# high-high corner is at [grid-1][grid-1].
_BIVARIATE_PALETTES_3X3: dict[str, list[list[str]]] = {
    "BlueOrange": [
        ["#e8e8e8", "#e4acac", "#c85a5a"],
        ["#b0d5df", "#ad9ea5", "#985356"],
        ["#64acbe", "#627f8c", "#574249"],
    ],
    "PurpleGreen": [
        ["#e8e8e8", "#ace4e4", "#5ac8c8"],
        ["#dfb0d6", "#a5add3", "#5698b9"],
        ["#be64ac", "#8c62aa", "#3b4994"],
    ],
    "BluePink": [
        ["#e8e8e8", "#e4d9ac", "#c8b35a"],
        ["#cbb8d7", "#c8ada0", "#af8e53"],
        ["#9972af", "#976b82", "#804d36"],
    ],
}


def _bivariate_palette(name: str, grid: int) -> list[list[str]]:
    """Return a grid × grid bivariate palette as rows[y][x]."""
    if name not in _BIVARIATE_PALETTES_3X3:
        raise ValueError(
            f"Unknown bivariate palette '{name}'. Available: {sorted(_BIVARIATE_PALETTES_3X3)}"
        )
    base = _BIVARIATE_PALETTES_3X3[name]
    if grid == 3:
        return [list(row) for row in base]
    # Linear RGB interpolation of the 3x3 base to arbitrary grid size
    out: list[list[str]] = []
    for y in range(grid):
        ty = y / (grid - 1) * 2  # map [0, grid-1] to [0, 2]
        row: list[str] = []
        for x in range(grid):
            tx = x / (grid - 1) * 2
            lo_y, lo_x = int(ty), int(tx)
            fy = ty - lo_y
            fx = tx - lo_x
            c00 = _hex_to_rgb(base[min(lo_y, 2)][min(lo_x, 2)])
            c10 = _hex_to_rgb(base[min(lo_y + 1, 2)][min(lo_x, 2)])
            c01 = _hex_to_rgb(base[min(lo_y, 2)][min(lo_x + 1, 2)])
            c11 = _hex_to_rgb(base[min(lo_y + 1, 2)][min(lo_x + 1, 2)])
            # Bilinear
            r = (1 - fy) * ((1 - fx) * c00[0] + fx * c01[0]) + fy * ((1 - fx) * c10[0] + fx * c11[0])
            g = (1 - fy) * ((1 - fx) * c00[1] + fx * c01[1]) + fy * ((1 - fx) * c10[1] + fx * c11[1])
            b = (1 - fy) * ((1 - fx) * c00[2] + fx * c01[2]) + fy * ((1 - fx) * c10[2] + fx * c11[2])
            row.append(_rgb_to_hex(r, g, b))
        out.append(row)
    return out


@register
class BivariateChoroplethCapability(Capability):
    """Bivariate choropleth — two numeric fields on a grid × grid palette.

    Classifies ``field_x`` and ``field_y`` independently with the chosen
    methods (quantile by default) and combines the two class indices into a
    grid × grid color matrix. Useful to show trade-offs (price × volume,
    density × vulnerability, etc.).

    Output columns:
      - ``class_col`` (default ``"bi_class"``): ``"y_x"`` string like ``"3_1"``
      - ``color_col`` (default ``"bi_color"``): bivariate hex

    Example::

        {
            "field_x": "price_per_m2",
            "field_y": "transaction_count",
            "grid": 3,
            "palette": "BlueOrange"
        }
    """

    name = "bivariate_choropleth"
    description = "Two-variable choropleth on a grid × grid bivariate palette."

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        field_x: str | None = None,
        field_y: str | None = None,
        method_x: str = "quantile",
        method_y: str = "quantile",
        grid: int = 3,
        palette: str = "BlueOrange",
        class_col: str = "bi_class",
        color_col: str = "bi_color",
        **_,
    ) -> gpd.GeoDataFrame:
        if not field_x or not field_y:
            raise ValueError("bivariate_choropleth: 'field_x' and 'field_y' are required")
        if grid < 2 or grid > 5:
            raise ValueError("bivariate_choropleth: grid must be between 2 and 5")

        matrix = _bivariate_palette(palette, grid)

        # Classify each axis (reuse ClassifyCapability infrastructure)
        classify = ClassifyCapability()
        x_out = classify.execute(
            gdf, field=field_x, method=method_x, bins=grid, class_col="__bi_x"
        )
        xy_out = classify.execute(
            x_out, field=field_y, method=method_y, bins=grid, class_col="__bi_y"
        )

        def _combine(x_cls, y_cls):
            if pd.isna(x_cls) or pd.isna(y_cls):
                return None, None
            xi = max(0, min(grid - 1, int(x_cls) - 1))
            yi = max(0, min(grid - 1, int(y_cls) - 1))
            return f"{yi + 1}_{xi + 1}", matrix[yi][xi]

        bi_class: list = []
        bi_color: list = []
        for xc, yc in zip(xy_out["__bi_x"], xy_out["__bi_y"]):
            cls, col = _combine(xc, yc)
            bi_class.append(cls)
            bi_color.append(col)

        result = xy_out.drop(columns=["__bi_x", "__bi_y"])
        result[class_col] = pd.Series(bi_class, index=result.index, dtype="object")
        result[color_col] = pd.Series(bi_color, index=result.index, dtype="object")

        # Flatten matrix into a legend grid
        legend_cells: list[dict] = []
        for y in range(grid):
            for x in range(grid):
                legend_cells.append({
                    "x_class": x + 1,
                    "y_class": y + 1,
                    "color": matrix[y][x],
                })

        result.attrs["gispulse_style"] = {
            "renderer": "bivariate",
            "fieldX": field_x,
            "fieldY": field_y,
            "grid": grid,
            "palette": palette,
            "matrix": matrix,
        }
        result.attrs["gispulse_legend"] = {
            "type": "bivariate_legend",
            "fieldX": field_x,
            "fieldY": field_y,
            "grid": grid,
            "palette": palette,
            "cells": legend_cells,
        }
        return result

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "field_x": {"type": "string"},
                "field_y": {"type": "string"},
                "method_x": {
                    "type": "string",
                    "enum": sorted(_CLASSIFY_METHODS),
                    "default": "quantile",
                },
                "method_y": {
                    "type": "string",
                    "enum": sorted(_CLASSIFY_METHODS),
                    "default": "quantile",
                },
                "grid": {"type": "integer", "minimum": 2, "maximum": 5, "default": 3},
                "palette": {
                    "type": "string",
                    "enum": sorted(_BIVARIATE_PALETTES_3X3.keys()),
                    "default": "BlueOrange",
                },
                "class_col": {"type": "string", "default": "bi_class"},
                "color_col": {"type": "string", "default": "bi_color"},
            },
            "required": ["field_x", "field_y"],
        }
