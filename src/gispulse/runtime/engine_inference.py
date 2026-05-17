"""Engine inference from dataset URIs.

GISPulse v1.6.0 introduces a multi-engine runtime: every dataset eventually
maps to one of four engines that share the same DSL but differ in how DML
is detected and how mutations are written back.

================  =================================================
Engine kind       Detection / write-back path
================  =================================================
``gpkg``          AFTER triggers in SQLite, write-back via pyogrio
                  or DuckDB COPY (fast path, see Atlas R1 bench)
``spatialite``    AFTER triggers in SQLite without GPKG metadata,
                  write-back via pyogrio
``postgis``       LISTEN/NOTIFY + asyncpg, BEFORE/AFTER triggers
``duckdb_diff``   File-blob CDC: mtime watcher + DuckDB snapshot
                  diff, write-back via pyogrio (Shapefile/GeoJSON/
                  FlatGeobuf/...) — placeholder for v1.6.1
================  =================================================

Inference is purely string-based: we do not open the dataset. That keeps
``gispulse run`` cheap when the file is on a remote NFS or about to be
created. The downstream engine factory does the actual probing.

A user can override inference with ``engine:`` in ``triggers.yaml``;
:func:`resolve_engine` raises :class:`EngineInferenceError` when the
override conflicts with the URI (e.g. ``engine: postgis`` on a ``.gpkg``
file) so misconfigurations surface immediately rather than at runtime.
"""

from __future__ import annotations

from typing import Literal
from urllib.parse import urlsplit

EngineKind = Literal["gpkg", "spatialite", "postgis", "duckdb_diff"]

ALL_ENGINES: tuple[EngineKind, ...] = ("gpkg", "spatialite", "postgis", "duckdb_diff")

# Filename suffix → engine. Lowercased before lookup. ``.sqlite`` defaults
# to spatialite; the GPKG-vs-SpatiaLite distinction needs the file open
# (presence of ``gpkg_geometry_columns``) so the engine factory decides.
_SUFFIX_MAP: dict[str, EngineKind] = {
    ".gpkg": "gpkg",
    ".sqlite": "spatialite",
    ".db": "spatialite",
    ".geojson": "duckdb_diff",
    ".json": "duckdb_diff",
    ".fgb": "duckdb_diff",
    ".shp": "duckdb_diff",
    ".kml": "duckdb_diff",
    ".kmz": "duckdb_diff",
    ".tab": "duckdb_diff",
    ".csv": "duckdb_diff",
}

_POSTGRES_SCHEMES: frozenset[str] = frozenset(
    {"postgres", "postgresql", "postgis"}
)


class EngineInferenceError(ValueError):
    """Raised when an explicit ``engine:`` override conflicts with the URI."""


def infer_engine(uri: str) -> EngineKind | None:
    """Return the engine inferred from ``uri`` or ``None`` if unrecognised.

    The function is intentionally tolerant: unknown suffixes return ``None``
    so callers can fall back to a default or surface a config error of their
    own. Empty / whitespace input returns ``None``.
    """
    if not uri or not uri.strip():
        return None

    parsed = urlsplit(uri)
    scheme = parsed.scheme.lower()

    if scheme in _POSTGRES_SCHEMES:
        return "postgis"

    # ``urlsplit`` treats ``C:\foo.gpkg`` on Windows as scheme ``c``; treat
    # any single-letter scheme as a path so suffix matching takes over.
    if len(scheme) > 1 and scheme not in {"file"}:
        # Schemes we do not recognise (``s3://``, ``http://``...) — bail out
        # rather than guessing. Future v1.7+ may add object-store adapters.
        return None

    # Strip ``file://`` prefix; for plain paths ``parsed.path`` is fine.
    path = parsed.path or uri
    lower = path.lower()
    for suffix, engine in _SUFFIX_MAP.items():
        if lower.endswith(suffix):
            return engine
    return None


def resolve_engine(uri: str, override: str | None = None) -> EngineKind:
    """Return the engine for ``uri`` honouring an explicit ``override``.

    Parameters
    ----------
    uri:
        Dataset URI (``triggers.yaml`` ``gpkg:`` field or future
        ``dataset:`` field). Required.
    override:
        Explicit ``engine:`` value from the YAML. If ``None``, inference
        decides. If provided, it must either match the inferred engine or
        belong to the broader compatible set (e.g. requesting ``duckdb_diff``
        on a ``.gpkg`` file is allowed for advanced users who want CDC mode
        instead of native triggers; requesting ``postgis`` on a file path
        is not, because the wiring would mismatch).

    Raises
    ------
    EngineInferenceError
        When the URI cannot be classified at all (and no override supplies
        the answer) or when the override is incompatible with the URI.
    """
    inferred = infer_engine(uri)

    if override is None:
        if inferred is None:
            raise EngineInferenceError(
                f"cannot infer engine for {uri!r}; "
                f"set `engine:` explicitly to one of {sorted(ALL_ENGINES)}"
            )
        return inferred

    if override not in ALL_ENGINES:
        raise EngineInferenceError(
            f"unknown engine {override!r}; expected one of {sorted(ALL_ENGINES)}"
        )

    if inferred is None:
        # Unrecognised URI but explicit override → trust the user.
        return override  # type: ignore[return-value]

    if override == inferred:
        return override  # type: ignore[return-value]

    if not _is_override_compatible(override, inferred):
        raise EngineInferenceError(
            f"engine override {override!r} is incompatible with URI {uri!r} "
            f"(inferred {inferred!r}). Either drop the override or change "
            f"the dataset URI."
        )

    return override  # type: ignore[return-value]


def _is_override_compatible(override: str, inferred: EngineKind) -> bool:
    """Decide whether an override may legally replace the inferred engine.

    Compatibility rules (v1.6.0):
    - File-based formats (``gpkg``/``spatialite``/``duckdb_diff``) may be
      forced into ``duckdb_diff`` mode when the user opts out of native
      triggers and prefers file-blob CDC.
    - ``gpkg`` ↔ ``spatialite`` swap is allowed: SQLite siblings.
    - Anything → ``postgis`` requires a ``postgresql://`` URI; rejected.
    - ``postgis`` URIs cannot be downgraded to file engines.
    """
    if override == "duckdb_diff" and inferred in {"gpkg", "spatialite"}:
        return True
    if {override, inferred} == {"gpkg", "spatialite"}:
        return True
    return False
