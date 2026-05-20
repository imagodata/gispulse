"""Per-model data-quality assertions (ELT Lot 4F — issue #252).

ADR 0005 reserves a declarative ``assert:`` block per model. The
checks ride on the *materialised* GeoDataFrame — pragmatically the
same data either path (Lot 1-3 SQL push-down or Python) would produce
— so they are implemented in Python here rather than re-compiled per
dialect. The runner stays engine-agnostic.

Supported assertion kinds (extensible — each maps to a callable that
takes the materialised gdf and returns either ``None`` for pass or an
:class:`AssertionFailure` for fail):

  - ``not_null: [col, …]``
  - ``unique: [col, …]``
  - ``geometry_valid: <col>`` (or ``true`` to use the active geometry)
  - ``expect_rows: {min?: int, max?: int}``

Each entry may carry a ``severity:`` (default ``error``) — *errors*
raise :class:`AssertionFailedError`; *warnings* surface via the
caller's collection.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import geopandas as gpd

__all__ = [
    "AssertionSpec",
    "AssertionFailure",
    "AssertionFailedError",
    "parse_assertions",
    "run_assertions",
]


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


_KIND_KEYS = frozenset(
    {"not_null", "unique", "geometry_valid", "expect_rows"}
)


@dataclass
class AssertionSpec:
    """One parsed assertion entry from a model's ``assert:`` block."""

    kind: str
    config: Any
    severity: str = "error"


@dataclass
class AssertionFailure:
    """A single failed assertion attached to a materialised model."""

    model: str
    kind: str
    severity: str
    message: str
    extra: dict[str, Any] = field(default_factory=dict)


class AssertionFailedError(RuntimeError):
    """At least one ``severity=error`` assertion failed on a model."""

    def __init__(self, failures: list[AssertionFailure]):
        self.failures = list(failures)
        first = failures[0]
        super().__init__(
            f"data-quality gate failed on model {first.model!r}: "
            f"{first.kind} — {first.message}"
            + (f" (+{len(failures) - 1} more)" if len(failures) > 1 else "")
        )


# ---------------------------------------------------------------------------
# Parsing — manifest dict → list[AssertionSpec]
# ---------------------------------------------------------------------------


def parse_assertions(entries: list[Any] | None) -> list[AssertionSpec]:
    """Parse a model's ``assert:`` list into typed AssertionSpecs.

    Each entry is one of:

    - ``{"not_null": ["a", "b"]}``
    - ``{"unique": ["id"]}``
    - ``{"geometry_valid": "geometry"}`` (or ``True``)
    - ``{"expect_rows": {"min": 1, "max": 1000}}``
    - Any of the above with an extra ``severity`` key (``error`` /
      ``warning``).

    Unknown keys raise ``ValueError`` so a typo doesn't become a silent
    no-op gate.
    """
    out: list[AssertionSpec] = []
    for i, raw in enumerate(entries or []):
        if not isinstance(raw, dict) or not raw:
            raise ValueError(
                f"assertion[{i}]: must be a non-empty object "
                f"({{kind: config}}); got {raw!r}"
            )
        severity = str(raw.get("severity", "error")).lower()
        if severity not in ("error", "warning"):
            raise ValueError(
                f"assertion[{i}]: severity must be 'error' or 'warning', "
                f"got {severity!r}"
            )
        kind_keys = [k for k in raw if k != "severity"]
        if len(kind_keys) != 1:
            raise ValueError(
                f"assertion[{i}]: must have exactly one assertion kind, "
                f"got {kind_keys!r}"
            )
        kind = kind_keys[0]
        if kind not in _KIND_KEYS:
            raise ValueError(
                f"assertion[{i}]: unknown kind {kind!r}; "
                f"expected one of {sorted(_KIND_KEYS)}"
            )
        out.append(AssertionSpec(kind=kind, config=raw[kind], severity=severity))
    return out


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def _check_not_null(
    gdf: gpd.GeoDataFrame, cols: Any
) -> tuple[bool, str, dict[str, Any]]:
    if not isinstance(cols, list) or not cols:
        return False, "not_null requires a non-empty list of columns", {}
    missing_cols = [c for c in cols if c not in gdf.columns]
    if missing_cols:
        return False, f"not_null columns absent: {missing_cols}", {}
    null_counts = {
        c: int(gdf[c].isna().sum()) for c in cols if int(gdf[c].isna().sum())
    }
    if null_counts:
        return (
            False,
            f"null values found in {sorted(null_counts)}",
            {"null_counts": null_counts},
        )
    return True, "", {}


def _check_unique(
    gdf: gpd.GeoDataFrame, cols: Any
) -> tuple[bool, str, dict[str, Any]]:
    if not isinstance(cols, list) or not cols:
        return False, "unique requires a non-empty list of columns", {}
    missing_cols = [c for c in cols if c not in gdf.columns]
    if missing_cols:
        return False, f"unique columns absent: {missing_cols}", {}
    if gdf.empty:
        return True, "", {}
    duplicates = gdf.duplicated(subset=cols, keep=False).sum()
    if duplicates:
        return (
            False,
            f"duplicate rows on {cols}: {int(duplicates)} offending row(s)",
            {"n_duplicates": int(duplicates)},
        )
    return True, "", {}


def _check_geometry_valid(
    gdf: gpd.GeoDataFrame, config: Any
) -> tuple[bool, str, dict[str, Any]]:
    if config is True or config is None:
        col = gdf.geometry.name if hasattr(gdf, "geometry") else "geometry"
    elif isinstance(config, str):
        col = config
    else:
        return (
            False,
            f"geometry_valid: expected column name or true, got {config!r}",
            {},
        )
    if col not in gdf.columns:
        return False, f"geometry_valid: column {col!r} absent", {}
    series = gdf[col]
    # ``GeoSeries.is_valid`` would be ideal; for a non-geo column we
    # fall back to a generic notna check so the assertion still produces
    # a useful message.
    try:
        invalid = (~series.is_valid).sum()
    except AttributeError:
        return False, f"geometry_valid: {col!r} is not a geometry column", {}
    if invalid:
        return (
            False,
            f"geometry_valid: {int(invalid)} invalid geometr{'y' if invalid == 1 else 'ies'}",
            {"n_invalid": int(invalid)},
        )
    return True, "", {}


def _check_expect_rows(
    gdf: gpd.GeoDataFrame, config: Any
) -> tuple[bool, str, dict[str, Any]]:
    if not isinstance(config, dict):
        return False, "expect_rows: config must be an object", {}
    n = len(gdf)
    lo = config.get("min")
    hi = config.get("max")
    if lo is not None and n < int(lo):
        return False, f"expect_rows: got {n} < min={lo}", {"n_rows": n, "min": int(lo)}
    if hi is not None and n > int(hi):
        return False, f"expect_rows: got {n} > max={hi}", {"n_rows": n, "max": int(hi)}
    return True, "", {}


_CHECKS: dict[
    str, Callable[[gpd.GeoDataFrame, Any], tuple[bool, str, dict[str, Any]]]
] = {
    "not_null": _check_not_null,
    "unique": _check_unique,
    "geometry_valid": _check_geometry_valid,
    "expect_rows": _check_expect_rows,
}


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def run_assertions(
    model_name: str,
    gdf: gpd.GeoDataFrame,
    assertions: list[AssertionSpec],
    *,
    raise_on_error: bool = True,
) -> list[AssertionFailure]:
    """Evaluate every assertion and return the failures.

    Args:
        model_name:     The model under test — surfaces in error messages.
        gdf:            The materialised GeoDataFrame (Lot 4C output).
        assertions:     Parsed assertion specs.
        raise_on_error: When ``True`` (default), any
            ``severity=error`` failure raises
            :class:`AssertionFailedError` after all checks have run, so
            the caller sees the full picture in one go. Pass ``False``
            to collect everything as a list (useful for an ``explain``-
            style dry-run).

    Returns:
        The list of failures (errors + warnings). Empty list = all
        gates passed.
    """
    failures: list[AssertionFailure] = []
    for spec in assertions:
        check = _CHECKS.get(spec.kind)
        if check is None:
            # parse_assertions guards against this — defensive only.
            failures.append(
                AssertionFailure(
                    model=model_name,
                    kind=spec.kind,
                    severity=spec.severity,
                    message=f"unknown assertion kind {spec.kind!r}",
                )
            )
            continue
        try:
            ok, message, extra = check(gdf, spec.config)
        except Exception as exc:
            failures.append(
                AssertionFailure(
                    model=model_name,
                    kind=spec.kind,
                    severity=spec.severity,
                    message=f"{spec.kind} raised: {exc}",
                )
            )
            continue
        if not ok:
            failures.append(
                AssertionFailure(
                    model=model_name,
                    kind=spec.kind,
                    severity=spec.severity,
                    message=message,
                    extra=extra,
                )
            )

    if raise_on_error:
        hard = [f for f in failures if f.severity == "error"]
        if hard:
            raise AssertionFailedError(hard)
    return failures
