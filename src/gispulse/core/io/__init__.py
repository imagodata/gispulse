"""GISPulse core I/O helpers — format-specific readers/writers."""

from gispulse.core.io.csv_encoding import (
    CSV_ENCODING_FALLBACKS,
    detect_text_encoding,
    read_csv_with_encoding_fallback,
)
from gispulse.core.io.geoparquet import read_geoparquet, write_geoparquet

__all__ = [
    "CSV_ENCODING_FALLBACKS",
    "detect_text_encoding",
    "read_csv_with_encoding_fallback",
    "read_geoparquet",
    "write_geoparquet",
]
