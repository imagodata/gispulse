"""Encoding detection and fallback for open-data CSV files.

Many government open-data CSV files are encoded in cp1252 or latin-1 rather
than UTF-8.  The helpers below try a candidate list of encodings and return on
the first strict decoder that succeeds — ``cp1252`` is intentionally preferred
over ``latin-1`` when both would accept the same bytes, as it is the more
common encoding for French open-data sources.  A silent mojibake remains
possible if the actual encoding is neither UTF-8 nor a Windows/ISO-8859 variant.
"""

from __future__ import annotations

import codecs
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pandas as pd

CSV_ENCODING_FALLBACKS: tuple[str, ...] = ("utf-8", "cp1252", "latin-1")

_DECODE_CHUNK_SIZE = 1024 * 1024  # 1 MB

# Sentinel used to distinguish "encoding not supplied" from "encoding=None".
_UNSET: object = object()


def read_csv_with_encoding_fallback(
    source: str | Path | Callable[[], Any],
    *,
    encodings: tuple[str, ...] = CSV_ENCODING_FALLBACKS,
    **kwargs: Any,
) -> pd.DataFrame:
    """Read a CSV file, trying each encoding in *encodings* until one succeeds.

    If ``encoding`` is present in *kwargs* it is forwarded as-is (including
    ``None``) and the cascade is skipped — the caller's intent is honoured
    exactly, preserving pandas default-encoding semantics when ``None`` is
    passed explicitly.

    *source* may be:
    - a file path (``str`` or :class:`pathlib.Path`)
    - a zero-argument callable that returns a context manager yielding a
      readable binary handle (useful for in-memory or pre-opened streams)

    On total failure the last :class:`UnicodeDecodeError` is re-raised.
    """
    requested_encoding = kwargs.pop("encoding", _UNSET)
    if requested_encoding is not _UNSET:
        if callable(source):
            with source() as handle:
                return pd.read_csv(handle, encoding=requested_encoding, **kwargs)
        return pd.read_csv(source, encoding=requested_encoding, **kwargs)

    last_error: UnicodeDecodeError | None = None
    for encoding in encodings:
        try:
            if callable(source):
                with source() as handle:
                    return pd.read_csv(handle, encoding=encoding, **kwargs)
            return pd.read_csv(source, encoding=encoding, **kwargs)
        except UnicodeDecodeError as exc:
            last_error = exc

    if last_error is None:  # pragma: no cover — encodings list is intentionally non-empty
        raise RuntimeError("no CSV encodings configured")
    raise last_error


def detect_text_encoding(
    path: str | Path,
    *,
    encodings: tuple[str, ...] = CSV_ENCODING_FALLBACKS,
) -> str:
    """Return the first encoding in *encodings* whose strict decoder accepts *path*.

    Decoding is done incrementally in 1 MB chunks via
    :func:`codecs.getincrementaldecoder` so the file is never fully loaded into
    memory.  The first candidate that raises no :class:`UnicodeDecodeError` wins;
    when ``cp1252`` and ``latin-1`` both accept the same bytes ``cp1252`` is
    returned because it appears first in the default fallback list.  A silent
    mojibake is still possible when the actual encoding is outside the candidate
    set.

    Raises the last :class:`UnicodeDecodeError` if no candidate succeeds.
    """
    path = Path(path)
    last_error: UnicodeDecodeError | None = None
    for encoding in encodings:
        try:
            _decode_file(path, encoding)
            return encoding
        except UnicodeDecodeError as exc:
            last_error = exc

    if last_error is None:  # pragma: no cover — encodings list is intentionally non-empty
        raise RuntimeError("no CSV encodings configured")
    raise last_error


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _decode_file(path: Path, encoding: str) -> None:
    """Decode *path* incrementally with *encoding*; raise on any invalid byte."""
    decoder = codecs.getincrementaldecoder(encoding)()
    with path.open("rb") as fh:
        while chunk := fh.read(_DECODE_CHUNK_SIZE):
            decoder.decode(chunk, final=False)
    decoder.decode(b"", final=True)
