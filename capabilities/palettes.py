"""
Named color palette registry for GISPulse classification/choropleth.

Sources:
  - ColorBrewer 2.0 (Cynthia Brewer, Penn State — colorbrewer2.org)
  - Matplotlib perceptual colormaps (Viridis/Plasma/Magma/Inferno/Cividis)
  - Editorial / urban-design-lab set: muted paper-map palettes inspired
    by studio choropleths (warm roses, sages, terracottas on cream).

All palettes are returned as lists of lowercase hex strings. Requests for
unsupported sizes are approximated by linear subsampling of the largest
available entry, which is good enough for the 3-9 class range this module
covers in practice.
"""

from __future__ import annotations

from difflib import get_close_matches

# ── ColorBrewer sequential (single hue / multi hue) ──────────────────────
_SEQUENTIAL: dict[str, dict[int, list[str]]] = {
    "Blues": {
        3: ["#deebf7", "#9ecae1", "#3182bd"],
        5: ["#eff3ff", "#bdd7e7", "#6baed6", "#3182bd", "#08519c"],
        7: ["#eff3ff", "#c6dbef", "#9ecae1", "#6baed6", "#4292c6", "#2171b5", "#084594"],
        9: ["#f7fbff", "#deebf7", "#c6dbef", "#9ecae1", "#6baed6", "#4292c6", "#2171b5", "#08519c", "#08306b"],
    },
    "Greens": {
        3: ["#e5f5e0", "#a1d99b", "#31a354"],
        5: ["#edf8e9", "#bae4b3", "#74c476", "#31a354", "#006d2c"],
        7: ["#edf8e9", "#c7e9c0", "#a1d99b", "#74c476", "#41ab5d", "#238b45", "#005a32"],
        9: ["#f7fcf5", "#e5f5e0", "#c7e9c0", "#a1d99b", "#74c476", "#41ab5d", "#238b45", "#006d2c", "#00441b"],
    },
    "Oranges": {
        3: ["#fee6ce", "#fdae6b", "#e6550d"],
        5: ["#feedde", "#fdbe85", "#fd8d3c", "#e6550d", "#a63603"],
        7: ["#feedde", "#fdd0a2", "#fdae6b", "#fd8d3c", "#f16913", "#d94801", "#8c2d04"],
        9: ["#fff5eb", "#fee6ce", "#fdd0a2", "#fdae6b", "#fd8d3c", "#f16913", "#d94801", "#a63603", "#7f2704"],
    },
    "Reds": {
        3: ["#fee0d2", "#fc9272", "#de2d26"],
        5: ["#fee5d9", "#fcae91", "#fb6a4a", "#de2d26", "#a50f15"],
        7: ["#fee5d9", "#fcbba1", "#fc9272", "#fb6a4a", "#ef3b2c", "#cb181d", "#99000d"],
        9: ["#fff5f0", "#fee0d2", "#fcbba1", "#fc9272", "#fb6a4a", "#ef3b2c", "#cb181d", "#a50f15", "#67000d"],
    },
    "Purples": {
        3: ["#efedf5", "#bcbddc", "#756bb1"],
        5: ["#f2f0f7", "#cbc9e2", "#9e9ac8", "#756bb1", "#54278f"],
        7: ["#f2f0f7", "#dadaeb", "#bcbddc", "#9e9ac8", "#807dba", "#6a51a3", "#4a1486"],
        9: ["#fcfbfd", "#efedf5", "#dadaeb", "#bcbddc", "#9e9ac8", "#807dba", "#6a51a3", "#54278f", "#3f007d"],
    },
    "Greys": {
        3: ["#f0f0f0", "#bdbdbd", "#636363"],
        5: ["#f7f7f7", "#cccccc", "#969696", "#636363", "#252525"],
        7: ["#f7f7f7", "#d9d9d9", "#bdbdbd", "#969696", "#737373", "#525252", "#252525"],
        9: ["#ffffff", "#f0f0f0", "#d9d9d9", "#bdbdbd", "#969696", "#737373", "#525252", "#252525", "#000000"],
    },
    "YlOrRd": {
        3: ["#ffeda0", "#feb24c", "#f03b20"],
        5: ["#ffffb2", "#fecc5c", "#fd8d3c", "#f03b20", "#bd0026"],
        7: ["#ffffb2", "#fed976", "#feb24c", "#fd8d3c", "#fc4e2a", "#e31a1c", "#b10026"],
        9: ["#ffffcc", "#ffeda0", "#fed976", "#feb24c", "#fd8d3c", "#fc4e2a", "#e31a1c", "#bd0026", "#800026"],
    },
    "YlOrBr": {
        3: ["#fff7bc", "#fec44f", "#d95f0e"],
        5: ["#ffffd4", "#fed98e", "#fe9929", "#d95f0e", "#993404"],
        7: ["#ffffd4", "#fee391", "#fec44f", "#fe9929", "#ec7014", "#cc4c02", "#8c2d04"],
        9: ["#ffffe5", "#fff7bc", "#fee391", "#fec44f", "#fe9929", "#ec7014", "#cc4c02", "#993404", "#662506"],
    },
    "YlGnBu": {
        3: ["#edf8b1", "#7fcdbb", "#2c7fb8"],
        5: ["#ffffcc", "#a1dab4", "#41b6c4", "#2c7fb8", "#253494"],
        7: ["#ffffcc", "#c7e9b4", "#7fcdbb", "#41b6c4", "#1d91c0", "#225ea8", "#0c2c84"],
        9: ["#ffffd9", "#edf8b1", "#c7e9b4", "#7fcdbb", "#41b6c4", "#1d91c0", "#225ea8", "#253494", "#081d58"],
    },
    "YlGn": {
        3: ["#f7fcb9", "#addd8e", "#31a354"],
        5: ["#ffffcc", "#c2e699", "#78c679", "#31a354", "#006837"],
        7: ["#ffffcc", "#d9f0a3", "#addd8e", "#78c679", "#41ab5d", "#238443", "#005a32"],
        9: ["#ffffe5", "#f7fcb9", "#d9f0a3", "#addd8e", "#78c679", "#41ab5d", "#238443", "#006837", "#004529"],
    },
    "BuPu": {
        3: ["#e0ecf4", "#9ebcda", "#8856a7"],
        5: ["#edf8fb", "#b3cde3", "#8c96c6", "#8856a7", "#810f7c"],
        7: ["#edf8fb", "#bfd3e6", "#9ebcda", "#8c96c6", "#8c6bb1", "#88419d", "#6e016b"],
        9: ["#f7fcfd", "#e0ecf4", "#bfd3e6", "#9ebcda", "#8c96c6", "#8c6bb1", "#88419d", "#810f7c", "#4d004b"],
    },
    "PuRd": {
        3: ["#e7e1ef", "#c994c7", "#dd1c77"],
        5: ["#f1eef6", "#d7b5d8", "#df65b0", "#dd1c77", "#980043"],
        7: ["#f1eef6", "#d4b9da", "#c994c7", "#df65b0", "#e7298a", "#ce1256", "#91003f"],
        9: ["#f7f4f9", "#e7e1ef", "#d4b9da", "#c994c7", "#df65b0", "#e7298a", "#ce1256", "#980043", "#67001f"],
    },
    # ── Editorial (urban-design-lab) — warm paper-map aesthetics ─────────
    # Desaturated studio tones (cream → rose/sage/terracotta) for editorial
    # choropleths. Less clinical than ColorBrewer, intended for print-style
    # maps, dashboards and hero visuals.
    "UrbanRose": {
        3: ["#fbeae4", "#e5a095", "#a23c3a"],
        5: ["#fbeae4", "#f2c6bb", "#dd8a7d", "#b85752", "#7a2c2e"],
        7: ["#fdf2ed", "#f7d4c8", "#ecab9d", "#d98074", "#b85752", "#8d3a37", "#5e1f20"],
        9: ["#fef6f1", "#fbe1d7", "#f2bdac", "#e59a8b", "#d17268", "#b35650", "#8c3c39", "#632626", "#3f1415"],
    },
    "UrbanSage": {
        3: ["#ecefdf", "#93ac89", "#3a5e3a"],
        5: ["#f2f1e3", "#cfd8bd", "#96ae8b", "#5e8058", "#2f4d34"],
        7: ["#f5f3e6", "#dde0c6", "#bcc79d", "#93ac89", "#6a8e63", "#446847", "#223d2a"],
        9: ["#f7f5ea", "#e5e4ce", "#cfd8bd", "#b4c2a0", "#93ac89", "#73956c", "#547852", "#365a3c", "#1b3923"],
    },
    "UrbanTerracotta": {
        3: ["#f4e9d8", "#d99585", "#8c3936"],
        5: ["#f7ead9", "#ecc7ad", "#d28f7c", "#a95a4f", "#722a2a"],
        7: ["#faf0de", "#f0d3b7", "#dfa88e", "#c67e69", "#a85749", "#813832", "#55201f"],
        9: ["#fbf3e3", "#f2ddc3", "#e8bea0", "#d99c80", "#c57765", "#ab574c", "#893b34", "#612420", "#3c1213"],
    },
}

# ── Matplotlib perceptual (5-stop downsample from 256) ───────────────────
# Values generated from matplotlib colormaps at normalized positions
# 0, 0.25, 0.5, 0.75, 1.0 for size 5 (etc.). Kept as static tables so
# GISPulse doesn't pull matplotlib just for palette lookup.
_PERCEPTUAL: dict[str, dict[int, list[str]]] = {
    "Viridis": {
        3: ["#440154", "#21918c", "#fde725"],
        5: ["#440154", "#3b528b", "#21918c", "#5ec962", "#fde725"],
        7: ["#440154", "#443983", "#31688e", "#21918c", "#35b779", "#90d743", "#fde725"],
        9: ["#440154", "#482878", "#3e4989", "#31688e", "#26828e", "#1f9e89", "#35b779", "#6ece58", "#fde725"],
    },
    "Plasma": {
        3: ["#0d0887", "#cc4778", "#f0f921"],
        5: ["#0d0887", "#7e03a8", "#cc4778", "#f89541", "#f0f921"],
        7: ["#0d0887", "#5302a3", "#8b0aa5", "#b83289", "#db5c68", "#f48849", "#f0f921"],
        9: ["#0d0887", "#42049e", "#6a00a8", "#900da4", "#b12a90", "#cc4778", "#e16462", "#f2844b", "#f0f921"],
    },
    "Magma": {
        3: ["#000004", "#b63679", "#fcfdbf"],
        5: ["#000004", "#51127c", "#b63679", "#fb8761", "#fcfdbf"],
        7: ["#000004", "#2c105c", "#721f81", "#b63679", "#f1605d", "#feae77", "#fcfdbf"],
        9: ["#000004", "#1c1044", "#4f127b", "#812581", "#b5367a", "#e55964", "#fb8761", "#fec287", "#fcfdbf"],
    },
    "Inferno": {
        3: ["#000004", "#bc3754", "#fcffa4"],
        5: ["#000004", "#57106e", "#bc3754", "#f98e09", "#fcffa4"],
        7: ["#000004", "#2a115f", "#781c6d", "#bc3754", "#ed6925", "#fcb519", "#fcffa4"],
        9: ["#000004", "#1f0c48", "#550f6d", "#88226a", "#ba3655", "#e35932", "#f98e09", "#fbc01a", "#fcffa4"],
    },
    "Cividis": {
        3: ["#00224e", "#868479", "#fee838"],
        5: ["#00224e", "#414878", "#7c7b78", "#bcae5d", "#fee838"],
        7: ["#00224e", "#2c3a6e", "#575773", "#7c7b78", "#a59c64", "#d3c057", "#fee838"],
        9: ["#00224e", "#1f2f66", "#40497e", "#5d6173", "#7c7b78", "#9a9670", "#bbb057", "#dcce3e", "#fee838"],
    },
}

# ── ColorBrewer diverging ────────────────────────────────────────────────
_DIVERGING: dict[str, dict[int, list[str]]] = {
    "RdBu": {
        3: ["#ef8a62", "#f7f7f7", "#67a9cf"],
        5: ["#ca0020", "#f4a582", "#f7f7f7", "#92c5de", "#0571b0"],
        7: ["#b2182b", "#ef8a62", "#fddbc7", "#f7f7f7", "#d1e5f0", "#67a9cf", "#2166ac"],
        9: ["#b2182b", "#d6604d", "#f4a582", "#fddbc7", "#f7f7f7", "#d1e5f0", "#92c5de", "#4393c3", "#2166ac"],
    },
    "RdYlGn": {
        3: ["#fc8d59", "#ffffbf", "#91cf60"],
        5: ["#d7191c", "#fdae61", "#ffffbf", "#a6d96a", "#1a9641"],
        7: ["#d73027", "#fc8d59", "#fee08b", "#ffffbf", "#d9ef8b", "#91cf60", "#1a9850"],
        9: ["#d73027", "#f46d43", "#fdae61", "#fee08b", "#ffffbf", "#d9ef8b", "#a6d96a", "#66bd63", "#1a9850"],
    },
    "RdYlBu": {
        3: ["#fc8d59", "#ffffbf", "#91bfdb"],
        5: ["#d7191c", "#fdae61", "#ffffbf", "#abd9e9", "#2c7bb6"],
        7: ["#d73027", "#fc8d59", "#fee090", "#ffffbf", "#e0f3f8", "#91bfdb", "#4575b4"],
        9: ["#d73027", "#f46d43", "#fdae61", "#fee090", "#ffffbf", "#e0f3f8", "#abd9e9", "#74add1", "#4575b4"],
    },
    "BrBG": {
        3: ["#d8b365", "#f5f5f5", "#5ab4ac"],
        5: ["#a6611a", "#dfc27d", "#f5f5f5", "#80cdc1", "#018571"],
        7: ["#8c510a", "#d8b365", "#f6e8c3", "#f5f5f5", "#c7eae5", "#5ab4ac", "#01665e"],
        9: ["#8c510a", "#bf812d", "#dfc27d", "#f6e8c3", "#f5f5f5", "#c7eae5", "#80cdc1", "#35978f", "#01665e"],
    },
    "PiYG": {
        3: ["#e9a3c9", "#f7f7f7", "#a1d76a"],
        5: ["#d01c8b", "#f1b6da", "#f7f7f7", "#b8e186", "#4dac26"],
        7: ["#c51b7d", "#e9a3c9", "#fde0ef", "#f7f7f7", "#e6f5d0", "#a1d76a", "#4d9221"],
        9: ["#c51b7d", "#de77ae", "#f1b6da", "#fde0ef", "#f7f7f7", "#e6f5d0", "#b8e186", "#7fbc41", "#4d9221"],
    },
    "PuOr": {
        3: ["#f1a340", "#f7f7f7", "#998ec3"],
        5: ["#e66101", "#fdb863", "#f7f7f7", "#b2abd2", "#5e3c99"],
        7: ["#b35806", "#f1a340", "#fee0b6", "#f7f7f7", "#d8daeb", "#998ec3", "#542788"],
        9: ["#b35806", "#e08214", "#fdb863", "#fee0b6", "#f7f7f7", "#d8daeb", "#b2abd2", "#8073ac", "#542788"],
    },
    "Spectral": {
        3: ["#fc8d59", "#ffffbf", "#99d594"],
        5: ["#d7191c", "#fdae61", "#ffffbf", "#abdda4", "#2b83ba"],
        7: ["#d53e4f", "#fc8d59", "#fee08b", "#ffffbf", "#e6f598", "#99d594", "#3288bd"],
        9: ["#d53e4f", "#f46d43", "#fdae61", "#fee08b", "#ffffbf", "#e6f598", "#abdda4", "#66c2a5", "#3288bd"],
    },
    # Editorial diverging — pine sage ↔ brick rose, crossing on warm cream.
    "UrbanAtlas": {
        3: ["#6a8e63", "#f4ede1", "#c0685c"],
        5: ["#3f5f3a", "#a2b892", "#f4ede1", "#dea090", "#913d3a"],
        7: ["#2f4d34", "#6a8e63", "#bcc89e", "#f4ede1", "#ebbca8", "#c0685c", "#752a2a"],
        9: ["#223d2a", "#527354", "#8ea683", "#c6cfad", "#f4ede1", "#ecc8b5", "#d48c7a", "#a94b43", "#5d1e1f"],
    },
}

# ── ColorBrewer qualitative (fixed-size natural palettes) ────────────────
_QUALITATIVE: dict[str, dict[int, list[str]]] = {
    "Set1": {
        3: ["#e41a1c", "#377eb8", "#4daf4a"],
        5: ["#e41a1c", "#377eb8", "#4daf4a", "#984ea3", "#ff7f00"],
        7: ["#e41a1c", "#377eb8", "#4daf4a", "#984ea3", "#ff7f00", "#ffff33", "#a65628"],
        9: ["#e41a1c", "#377eb8", "#4daf4a", "#984ea3", "#ff7f00", "#ffff33", "#a65628", "#f781bf", "#999999"],
    },
    "Set2": {
        3: ["#66c2a5", "#fc8d62", "#8da0cb"],
        5: ["#66c2a5", "#fc8d62", "#8da0cb", "#e78ac3", "#a6d854"],
        7: ["#66c2a5", "#fc8d62", "#8da0cb", "#e78ac3", "#a6d854", "#ffd92f", "#e5c494"],
        8: ["#66c2a5", "#fc8d62", "#8da0cb", "#e78ac3", "#a6d854", "#ffd92f", "#e5c494", "#b3b3b3"],
    },
    "Set3": {
        3: ["#8dd3c7", "#ffffb3", "#bebada"],
        5: ["#8dd3c7", "#ffffb3", "#bebada", "#fb8072", "#80b1d3"],
        7: ["#8dd3c7", "#ffffb3", "#bebada", "#fb8072", "#80b1d3", "#fdb462", "#b3de69"],
        9: ["#8dd3c7", "#ffffb3", "#bebada", "#fb8072", "#80b1d3", "#fdb462", "#b3de69", "#fccde5", "#d9d9d9"],
        12: ["#8dd3c7", "#ffffb3", "#bebada", "#fb8072", "#80b1d3", "#fdb462", "#b3de69", "#fccde5", "#d9d9d9", "#bc80bd", "#ccebc5", "#ffed6f"],
    },
    "Pastel1": {
        3: ["#fbb4ae", "#b3cde3", "#ccebc5"],
        5: ["#fbb4ae", "#b3cde3", "#ccebc5", "#decbe4", "#fed9a6"],
        7: ["#fbb4ae", "#b3cde3", "#ccebc5", "#decbe4", "#fed9a6", "#ffffcc", "#e5d8bd"],
        9: ["#fbb4ae", "#b3cde3", "#ccebc5", "#decbe4", "#fed9a6", "#ffffcc", "#e5d8bd", "#fddaec", "#f2f2f2"],
    },
    "Dark2": {
        3: ["#1b9e77", "#d95f02", "#7570b3"],
        5: ["#1b9e77", "#d95f02", "#7570b3", "#e7298a", "#66a61e"],
        7: ["#1b9e77", "#d95f02", "#7570b3", "#e7298a", "#66a61e", "#e6ab02", "#a6761d"],
        8: ["#1b9e77", "#d95f02", "#7570b3", "#e7298a", "#66a61e", "#e6ab02", "#a6761d", "#666666"],
    },
    "Accent": {
        3: ["#7fc97f", "#beaed4", "#fdc086"],
        5: ["#7fc97f", "#beaed4", "#fdc086", "#ffff99", "#386cb0"],
        7: ["#7fc97f", "#beaed4", "#fdc086", "#ffff99", "#386cb0", "#f0027f", "#bf5b17"],
        8: ["#7fc97f", "#beaed4", "#fdc086", "#ffff99", "#386cb0", "#f0027f", "#bf5b17", "#666666"],
    },
    # Editorial qualitative — muted studio tones for landuse / categorical
    # maps. Chosen to sit together on cream backgrounds without clashing.
    "UrbanPaper": {
        3: ["#7a9a6f", "#c5877c", "#6e7d8c"],
        5: ["#7a9a6f", "#c5877c", "#6e7d8c", "#d6b78f", "#a8a2bf"],
        7: ["#7a9a6f", "#c5877c", "#6e7d8c", "#d6b78f", "#a8a2bf", "#7fa4a0", "#b85a52"],
        9: ["#7a9a6f", "#c5877c", "#6e7d8c", "#d6b78f", "#a8a2bf", "#7fa4a0", "#b85a52", "#c9a76a", "#86807a"],
    },
}


_KIND: dict[str, str] = {
    **{name: "sequential" for name in _SEQUENTIAL},
    **{name: "sequential" for name in _PERCEPTUAL},
    **{name: "diverging" for name in _DIVERGING},
    **{name: "qualitative" for name in _QUALITATIVE},
}


def _all_registries() -> list[dict[str, dict[int, list[str]]]]:
    return [_SEQUENTIAL, _PERCEPTUAL, _DIVERGING, _QUALITATIVE]


def _find(name: str) -> dict[int, list[str]] | None:
    # Exact match first, then case-insensitive — palette names are a UX
    # concern, not a precise API identifier.
    for reg in _all_registries():
        if name in reg:
            return reg[name]
    lowered = name.lower()
    for reg in _all_registries():
        for key, palette in reg.items():
            if key.lower() == lowered:
                return palette
    return None


def _interpolate_rgb(c1: str, c2: str, t: float) -> str:
    """Linear RGB interpolation between two hex colors."""
    r1, g1, b1 = int(c1[1:3], 16), int(c1[3:5], 16), int(c1[5:7], 16)
    r2, g2, b2 = int(c2[1:3], 16), int(c2[3:5], 16), int(c2[5:7], 16)
    r = round(r1 + (r2 - r1) * t)
    g = round(g1 + (g2 - g1) * t)
    b = round(b1 + (b2 - b1) * t)
    return f"#{r:02x}{g:02x}{b:02x}"


def _resample(palette: list[str], n: int) -> list[str]:
    """Resample a palette to n entries via RGB interpolation on its support points."""
    if n == len(palette):
        return list(palette)
    if n == 1:
        return [palette[len(palette) // 2]]
    out: list[str] = []
    for i in range(n):
        pos = i / (n - 1) * (len(palette) - 1)
        lo = int(pos)
        frac = pos - lo
        if lo >= len(palette) - 1:
            out.append(palette[-1])
        elif frac == 0:
            out.append(palette[lo])
        else:
            out.append(_interpolate_rgb(palette[lo], palette[lo + 1], frac))
    return out


def get_palette(name: str, n: int) -> list[str]:
    """Return ``n`` hex colors for palette ``name``.

    If ``n`` matches a stored size, returns it directly. Otherwise resamples
    from the closest stored size via RGB interpolation.

    Raises:
        ValueError: if ``name`` is unknown, or ``n < 2``.
    """
    if n < 2:
        raise ValueError(f"get_palette: n must be >= 2, got {n}")
    table = _find(name)
    if table is None:
        suggestions = get_close_matches(name, list_palettes(), n=3)
        hint = f" Did you mean: {', '.join(suggestions)}?" if suggestions else ""
        raise ValueError(f"Unknown palette '{name}'.{hint}")
    if n in table:
        return list(table[n])
    # Resample from the largest available anchor for best fidelity
    largest = table[max(table)]
    return _resample(largest, n)


def list_palettes(kind: str | None = None) -> list[str]:
    """Return sorted list of known palette names, optionally filtered by kind."""
    if kind is None:
        return sorted(_KIND.keys())
    return sorted(name for name, k in _KIND.items() if k == kind)


def palette_kind(name: str) -> str:
    """Return ``'sequential' | 'diverging' | 'qualitative'`` for ``name``."""
    if name not in _KIND:
        raise ValueError(f"Unknown palette '{name}'")
    return _KIND[name]


def resolve_palette(palette: str | list[str] | None, n: int) -> list[str] | None:
    """Normalize a classify ``palette`` parameter to a list of ``n`` hex colors.

    - ``None`` → ``None`` (caller decides whether a palette is required).
    - ``str``  → lookup in the named registry (with resampling if needed).
    - ``list`` → validated against ``n`` (length must match).
    """
    if palette is None:
        return None
    if isinstance(palette, str):
        return get_palette(palette, n)
    if isinstance(palette, list):
        if len(palette) != n:
            raise ValueError(
                f"palette length ({len(palette)}) must equal bins ({n})"
            )
        return list(palette)
    raise TypeError(
        f"palette must be a name (str), a list of hex strings, or None — got {type(palette).__name__}"
    )
