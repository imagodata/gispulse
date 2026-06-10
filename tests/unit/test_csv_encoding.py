"""Tests for core.io.csv_encoding — encoding detection and CSV fallback reader.

Covers:
- detect_text_encoding returns the correct encoding for UTF-8, cp1252, latin-1 fixtures
- read_csv_with_encoding_fallback reads all three without corruption
- explicit encoding= kwarg is forwarded as-is (and error propagates when wrong)
- source as a callable (handle factory) works correctly
- _read_csv_geo in persistence.io reads a cp1252 CSV with lat/lon columns
"""

from __future__ import annotations

import contextlib
import io
from pathlib import Path

import geopandas as gpd
import pytest

from gispulse.core.io.csv_encoding import (
    detect_text_encoding,
    read_csv_with_encoding_fallback,
)


# ---------------------------------------------------------------------------
# Fixtures — binary CSV content in each encoding
# ---------------------------------------------------------------------------

_UTF8_CONTENT = "nom,valeur\ncafé,résumé\nà bientôt,naïf\n".encode("utf-8")

# cp1252 has € at 0x80, which is not valid latin-1
_CP1252_CONTENT = "nom,valeur\ncafé,€uro\nà bientôt,naïf\n".encode("cp1252")

# Pure latin-1 (no characters outside latin-1 range)
_LATIN1_CONTENT = "nom,valeur\ncafé,résumé\nà bientôt,naïf\n".encode("latin-1")

# Strict latin-1: contains 0x81, which is undefined in cp1252 — forces the
# fallback to skip cp1252 and land on latin-1.
# b"\x81" is the byte for U+0081 (a latin-1 C1 control) — invalid in cp1252.
_LATIN1_STRICT_CONTENT = b"nom,valeur\ncaf\xe9,r\xe9sum\xe9\n\x81special,ok\n"


@pytest.fixture()
def utf8_csv(tmp_path: Path) -> Path:
    p = tmp_path / "utf8.csv"
    p.write_bytes(_UTF8_CONTENT)
    return p


@pytest.fixture()
def cp1252_csv(tmp_path: Path) -> Path:
    p = tmp_path / "cp1252.csv"
    p.write_bytes(_CP1252_CONTENT)
    return p


@pytest.fixture()
def latin1_csv(tmp_path: Path) -> Path:
    p = tmp_path / "latin1.csv"
    p.write_bytes(_LATIN1_CONTENT)
    return p


@pytest.fixture()
def latin1_strict_csv(tmp_path: Path) -> Path:
    """CSV containing 0x81, undefined in cp1252, forcing the latin-1 branch."""
    p = tmp_path / "latin1_strict.csv"
    p.write_bytes(_LATIN1_STRICT_CONTENT)
    return p


# ---------------------------------------------------------------------------
# detect_text_encoding
# ---------------------------------------------------------------------------


class TestDetectTextEncoding:
    def test_utf8_detected(self, utf8_csv: Path) -> None:
        assert detect_text_encoding(utf8_csv) == "utf-8"

    def test_cp1252_detected(self, cp1252_csv: Path) -> None:
        # utf-8 will fail on the 0x80 byte; cp1252 must win
        assert detect_text_encoding(cp1252_csv) == "cp1252"

    def test_latin1_detected_after_utf8_fails(self, latin1_csv: Path) -> None:
        # latin-1 is a superset of utf-8 for pure ASCII, but bytes like 0xE9
        # (é) are invalid in strict utf-8; latin-1 should win here.
        # Note: cp1252 is also valid for 0xE9, so the detected encoding will be
        # cp1252 (comes before latin-1 in the fallback list) — that is
        # acceptable as long as the data round-trips correctly.
        enc = detect_text_encoding(latin1_csv)
        assert enc in ("cp1252", "latin-1")

    def test_latin1_strict_detected(self, latin1_strict_csv: Path) -> None:
        # 0x81 is undefined in cp1252; utf-8 and cp1252 must both fail so that
        # latin-1 (which accepts every byte 0x00-0xFF) is returned.
        assert detect_text_encoding(latin1_strict_csv) == "latin-1"

    def test_accepts_str_path(self, utf8_csv: Path) -> None:
        assert detect_text_encoding(str(utf8_csv)) == "utf-8"

    def test_custom_encodings_respected(self, latin1_csv: Path) -> None:
        enc = detect_text_encoding(latin1_csv, encodings=("latin-1",))
        assert enc == "latin-1"

    def test_raises_on_total_failure(self, tmp_path: Path) -> None:
        # Write bytes that are invalid in all fallback encodings; craft a byte
        # sequence that is invalid UTF-8 AND invalid cp1252 AND invalid latin-1.
        # latin-1 decodes *every* byte 0x00-0xFF, so a pure latin-1 fallback
        # list with only utf-8 is the practical way to trigger failure.
        bad = tmp_path / "bad.csv"
        bad.write_bytes(b"\xff\xfe")  # BOM without continuation
        with pytest.raises(UnicodeDecodeError):
            detect_text_encoding(bad, encodings=("utf-8",))


# ---------------------------------------------------------------------------
# read_csv_with_encoding_fallback — path source
# ---------------------------------------------------------------------------


class TestReadCsvFallbackPath:
    def test_reads_utf8(self, utf8_csv: Path) -> None:
        df = read_csv_with_encoding_fallback(utf8_csv)
        assert list(df["nom"]) == ["café", "à bientôt"]

    def test_reads_cp1252(self, cp1252_csv: Path) -> None:
        df = read_csv_with_encoding_fallback(cp1252_csv)
        assert df["nom"].iloc[0] == "café"
        assert df["valeur"].iloc[0] == "€uro"

    def test_reads_latin1(self, latin1_csv: Path) -> None:
        df = read_csv_with_encoding_fallback(latin1_csv)
        assert "café" in list(df["nom"])

    def test_explicit_encoding_respected(self, utf8_csv: Path) -> None:
        df = read_csv_with_encoding_fallback(utf8_csv, encoding="utf-8")
        assert df["nom"].iloc[0] == "café"

    def test_explicit_wrong_encoding_raises(self, cp1252_csv: Path) -> None:
        with pytest.raises(UnicodeDecodeError):
            read_csv_with_encoding_fallback(cp1252_csv, encoding="utf-8")

    def test_explicit_none_encoding_raises_on_cp1252(self, cp1252_csv: Path) -> None:
        # encoding=None is pandas' way of saying "use default strict UTF-8".
        # It must be forwarded as-is (not trigger the cascade), so a cp1252 file
        # must raise UnicodeDecodeError — not silently fall back.
        with pytest.raises(UnicodeDecodeError):
            read_csv_with_encoding_fallback(cp1252_csv, encoding=None)

    def test_latin1_strict_read(self, latin1_strict_csv: Path) -> None:
        # Must fall through utf-8 and cp1252 and succeed via latin-1.
        df = read_csv_with_encoding_fallback(latin1_strict_csv)
        assert len(df) == 2
        # The second row's nom column decoded as latin-1 C1 control + "special"
        assert "special" in df["nom"].iloc[1]

    def test_str_path(self, utf8_csv: Path) -> None:
        df = read_csv_with_encoding_fallback(str(utf8_csv))
        assert len(df) == 2

    def test_custom_encodings_tuple(self, cp1252_csv: Path) -> None:
        df = read_csv_with_encoding_fallback(
            cp1252_csv, encodings=("cp1252", "latin-1")
        )
        assert df["valeur"].iloc[0] == "€uro"

    def test_total_failure_reraises(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.csv"
        bad.write_bytes(b"\xff\xfenom\n")
        with pytest.raises(UnicodeDecodeError):
            read_csv_with_encoding_fallback(bad, encodings=("utf-8",))


# ---------------------------------------------------------------------------
# read_csv_with_encoding_fallback — callable source (handle factory)
# ---------------------------------------------------------------------------


class TestReadCsvFallbackCallable:
    def _make_factory(self, data: bytes):
        """Return a zero-argument callable that yields an in-memory binary stream."""

        @contextlib.contextmanager
        def _factory():
            yield io.BytesIO(data)

        return _factory

    def test_callable_utf8(self) -> None:
        factory = self._make_factory(_UTF8_CONTENT)
        df = read_csv_with_encoding_fallback(factory)
        assert df["nom"].iloc[0] == "café"

    def test_callable_cp1252(self) -> None:
        factory = self._make_factory(_CP1252_CONTENT)
        df = read_csv_with_encoding_fallback(factory)
        assert df["valeur"].iloc[0] == "€uro"

    def test_callable_explicit_encoding(self) -> None:
        factory = self._make_factory(_UTF8_CONTENT)
        df = read_csv_with_encoding_fallback(factory, encoding="utf-8")
        assert df["nom"].iloc[0] == "café"

    def test_callable_wrong_explicit_encoding_raises(self) -> None:
        factory = self._make_factory(_CP1252_CONTENT)
        with pytest.raises(UnicodeDecodeError):
            read_csv_with_encoding_fallback(factory, encoding="utf-8")


# ---------------------------------------------------------------------------
# _read_csv_geo integration — cp1252 CSV with lat/lon
# ---------------------------------------------------------------------------


class TestReadCsvGeoEncoding:
    def test_reads_cp1252_latlon(self, tmp_path: Path) -> None:
        """_read_csv_geo must read a cp1252 CSV with lat/lon without error."""
        content = "nom,lat,lon\ncafé,48.8566,2.3522\nà Nice,43.7102,7.2620\n"
        csv_path = tmp_path / "places.csv"
        csv_path.write_bytes(content.encode("cp1252"))

        from gispulse.persistence.io import _read_csv_geo

        gdf = _read_csv_geo(str(csv_path))
        assert isinstance(gdf, gpd.GeoDataFrame)
        assert len(gdf) == 2
        assert gdf["nom"].iloc[0] == "café"
        assert gdf.crs is not None
