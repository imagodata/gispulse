"""Unit tests for the named palette registry."""

from __future__ import annotations

import re

import pytest

from capabilities.palettes import (
    get_palette,
    list_palettes,
    palette_kind,
    resolve_palette,
)


_HEX_RE = re.compile(r"^#[0-9a-f]{6}$")


class TestPaletteRegistry:
    def test_list_palettes_not_empty(self):
        names = list_palettes()
        assert len(names) >= 15
        assert "YlOrRd" in names
        assert "Viridis" in names
        assert "RdBu" in names
        assert "Set2" in names

    def test_list_by_kind(self):
        seq = list_palettes(kind="sequential")
        div = list_palettes(kind="diverging")
        qual = list_palettes(kind="qualitative")
        assert "YlOrRd" in seq and "Viridis" in seq
        assert "RdBu" in div and "Spectral" in div
        assert "Set2" in qual and "Dark2" in qual
        # Kinds are disjoint
        assert set(seq).isdisjoint(div)
        assert set(seq).isdisjoint(qual)

    def test_palette_kind(self):
        assert palette_kind("YlOrRd") == "sequential"
        assert palette_kind("RdBu") == "diverging"
        assert palette_kind("Set2") == "qualitative"
        assert palette_kind("Viridis") == "sequential"

    # ── Editorial (urban-design-lab) palettes ───────────────────────────

    def test_editorial_palettes_registered(self):
        """UrbanRose/Sage/Terracotta are sequential; UrbanAtlas diverging; UrbanPaper qualitative."""
        assert palette_kind("UrbanRose") == "sequential"
        assert palette_kind("UrbanSage") == "sequential"
        assert palette_kind("UrbanTerracotta") == "sequential"
        assert palette_kind("UrbanAtlas") == "diverging"
        assert palette_kind("UrbanPaper") == "qualitative"

    def test_editorial_palette_shapes(self):
        """Editorial palettes support the 3/5/7/9 anchor sizes like the ColorBrewer ones."""
        for name in ("UrbanRose", "UrbanSage", "UrbanTerracotta", "UrbanAtlas", "UrbanPaper"):
            for n in (3, 5, 7, 9):
                colors = get_palette(name, n)
                assert len(colors) == n
                for c in colors:
                    assert _HEX_RE.match(c), f"{name}[{n}]: invalid hex {c!r}"

    def test_palette_kind_unknown(self):
        with pytest.raises(ValueError, match="Unknown palette"):
            palette_kind("NotAPalette")

    # ── get_palette ─────────────────────────────────────────────────────

    def test_get_exact_size(self):
        p = get_palette("YlOrRd", 5)
        assert p == ["#ffffb2", "#fecc5c", "#fd8d3c", "#f03b20", "#bd0026"]

    def test_get_all_hex_valid(self):
        for name in list_palettes():
            for n in (3, 5, 7, 9):
                colors = get_palette(name, n)
                assert len(colors) == n
                for c in colors:
                    assert _HEX_RE.match(c), f"{name}[{n}]: invalid hex {c!r}"

    def test_resample_to_unstored_size(self):
        """YlOrRd stored at 3/5/7/9 — request 6 should interpolate to 6 valid hex."""
        p = get_palette("YlOrRd", 6)
        assert len(p) == 6
        for c in p:
            assert _HEX_RE.match(c)

    def test_case_insensitive_lookup(self):
        """Case forgiveness for common typos like 'ylorrd' → 'YlOrRd'."""
        assert get_palette("ylorrd", 5) == get_palette("YlOrRd", 5)
        assert get_palette("VIRIDIS", 5) == get_palette("Viridis", 5)

    def test_unknown_name_with_suggestion(self):
        with pytest.raises(ValueError, match="Did you mean.*Set"):
            get_palette("Setz", 5)  # close to Set1/Set2/Set3 — fuzzy should suggest

    def test_n_too_small(self):
        with pytest.raises(ValueError, match="n must be >= 2"):
            get_palette("YlOrRd", 1)

    # ── resolve_palette ─────────────────────────────────────────────────

    def test_resolve_none(self):
        assert resolve_palette(None, 5) is None

    def test_resolve_named(self):
        p = resolve_palette("YlOrRd", 5)
        assert p == ["#ffffb2", "#fecc5c", "#fd8d3c", "#f03b20", "#bd0026"]

    def test_resolve_explicit_list(self):
        explicit = ["#000", "#fff"]
        assert resolve_palette(explicit, 2) == explicit

    def test_resolve_list_length_mismatch(self):
        with pytest.raises(ValueError, match="palette length"):
            resolve_palette(["#000", "#fff"], 5)

    def test_resolve_invalid_type(self):
        with pytest.raises(TypeError, match="palette must be"):
            resolve_palette(42, 5)  # type: ignore[arg-type]
