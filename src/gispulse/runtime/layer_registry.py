"""Cross-source layer registry — push-down via DuckDB views.

The DSL functions :func:`geom_within`, :func:`geom_overlaps_any` and
:func:`layer_lookup` emit SQL of the form ``FROM "<layer>" AS _L``. By
default DuckDB resolves ``<layer>`` against whatever schema is currently
``USE``-d. The validation runner already ATTACHes the project GPKG and
``USE``-s its catalog (see :func:`make_gpkg_sql_evaluator`), so layers
inside the same GPKG resolve naturally.

Cross-source layers (a separate GeoPackage on disk, a Parquet file, a
PostGIS database) need help. This module wires them in:

1. Operators declare them in ``triggers.yaml`` under the top-level
   ``layers:`` key (see :class:`LayerSourceConfigModel`).
2. At runtime build time, :func:`build_layer_views` opens a DuckDB
   session, ATTACHes each external source under a private alias, and
   creates a *view* for each declared layer. The view name matches the
   logical layer name the DSL references, so no SQL rewriting is needed
   downstream.
3. DuckDB's optimiser pushes spatial predicates down into the underlying
   reader (Parquet predicate filters, SQLite indexes, postgres_scanner
   filter push-down) — exactly the v1.6.x #122 perf goal.

The registry intentionally stays narrow: it does not own the DuckDB
connection (the validation runner does) and it never executes user SQL
itself. It just emits the prep DDL.

Supported sources (v1.6.x):

==============  ==========================================================
Scheme          DDL emitted
==============  ==========================================================
``*.gpkg``      ``ATTACH '<path>' AS <alias> (TYPE SQLITE);``
                ``CREATE VIEW "<layer>" AS SELECT * FROM <alias>."<table>"``
``*.parquet``   ``CREATE VIEW "<layer>" AS SELECT * FROM read_parquet('<path>')``
``*.geoparquet`` (treated as parquet)
``postgresql://`` ``ATTACH '<dsn>' AS <alias> (TYPE postgres, READ_ONLY);``
                ``CREATE VIEW "<layer>" AS SELECT * FROM <alias>.<schema>.<table>``
==============  ==========================================================

Other schemes (``s3://``, ``http(s)://``, ``.shp``…) are deferred to the
v1.7+ object-store / file-blob CDC adapters; an explicit error tells
operators which extension would be needed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

if TYPE_CHECKING:  # pragma: no cover
    from duckdb import DuckDBPyConnection

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")


class LayerRegistryError(ValueError):
    """Raised on misconfiguration of the layer registry.

    Includes: bad URI, unsupported scheme, identifier validation failure,
    duplicate layer name, missing source file, ATTACH failure.
    """


@dataclass(frozen=True, slots=True)
class LayerSource:
    """One cross-source layer declaration.

    Attributes
    ----------
    name:
        Logical layer name as referenced by the DSL (e.g. ``communes``
        in ``geom_within(layer='communes')``). Must be a valid SQL
        identifier — the registry quotes it but enforces the regex
        belt-and-braces against injection.
    uri:
        Source URI. ``./communes.gpkg``, ``/abs/path/parcels.parquet``,
        ``postgresql://user@host/db`` are the supported shapes.
    table:
        Table inside the source. Defaults to :attr:`name` for GPKG
        sources (the convention is ``layer name == table name``);
        Parquet sources ignore it.
    schema:
        PostgreSQL schema, used only for ``postgresql://`` URIs.
        Defaults to ``public``.
    """

    name: str
    uri: str
    table: str | None = None
    schema: str = "public"

    def __post_init__(self) -> None:
        if not _IDENT_RE.match(self.name):
            raise LayerRegistryError(
                f"invalid layer name {self.name!r} — must match "
                f"[A-Za-z_][A-Za-z0-9_]*"
            )
        if "'" in self.uri or "\x00" in self.uri:
            raise LayerRegistryError(
                f"layer {self.name!r} URI contains illegal characters"
            )
        if self.table is not None and not _IDENT_RE.match(self.table):
            raise LayerRegistryError(
                f"layer {self.name!r} table {self.table!r} is not a valid identifier"
            )
        if not _IDENT_RE.match(self.schema):
            raise LayerRegistryError(
                f"layer {self.name!r} schema {self.schema!r} is not a valid identifier"
            )

    def resolved_table(self) -> str:
        """Return the source-side table name, defaulting to :attr:`name`."""
        return self.table or self.name


def _classify_source(uri: str) -> str:
    """Return one of ``"gpkg"``, ``"parquet"``, ``"postgis"``.

    Raises :class:`LayerRegistryError` for unsupported schemes / suffixes.
    """
    parsed = urlsplit(uri)
    scheme = parsed.scheme.lower()
    if scheme in {"postgres", "postgresql", "postgis"}:
        return "postgis"

    # Treat single-letter schemes (``c:\foo.gpkg`` on Windows) as paths.
    if len(scheme) > 1 and scheme not in {"file"}:
        raise LayerRegistryError(
            f"unsupported source scheme {scheme!r} in {uri!r} — "
            f"GISPulse v1.6.x supports gpkg / parquet / postgresql"
        )

    path = parsed.path or uri
    lower = path.lower()
    if lower.endswith(".gpkg"):
        return "gpkg"
    if lower.endswith(".parquet") or lower.endswith(".geoparquet"):
        return "parquet"
    raise LayerRegistryError(
        f"cannot classify source {uri!r} — supported suffixes are "
        f".gpkg / .parquet / .geoparquet (or postgresql:// URIs)"
    )


class LayerRegistry:
    """Registry of cross-source layers ready to be installed on a DuckDB session.

    Usage
    -----
    ::

        reg = LayerRegistry()
        reg.register(LayerSource(name="communes", uri="./data/communes.gpkg"))
        reg.register(LayerSource(name="zonage", uri="./data/zonage.parquet"))

        conn = get_spatial_connection()
        reg.install(conn)
        # ``geom_within(layer='communes')`` now resolves against the
        # external GPKG; spatial predicates push down via DuckDB's
        # SQLite scanner.

    The registry is content-addressable on the (name, uri) pair: calling
    :meth:`register` twice with the same name and URI is a no-op;
    registering a different URI under the same name raises.
    """

    def __init__(self) -> None:
        self._sources: dict[str, LayerSource] = {}

    def register(self, source: LayerSource) -> None:
        existing = self._sources.get(source.name)
        if existing is not None and existing != source:
            raise LayerRegistryError(
                f"layer {source.name!r} is already registered with a "
                f"different source ({existing.uri!r} vs {source.uri!r})"
            )
        self._sources[source.name] = source

    def names(self) -> list[str]:
        return sorted(self._sources)

    def __len__(self) -> int:
        return len(self._sources)

    def __contains__(self, name: str) -> bool:
        return name in self._sources

    def install(self, conn: "DuckDBPyConnection") -> None:
        """ATTACH external sources and create one view per registered layer.

        The connection must already have the spatial extension loaded
        (see :func:`gispulse.runtime.duckdb_engine.get_spatial_connection`).
        Postgres sources additionally trigger ``INSTALL postgres_scanner``
        on first use; we let DuckDB handle the cache so subsequent calls
        in the same process are no-ops.

        Aliases are derived from the layer name (``__layer_<name>``) so
        operators don't have to invent unique names. Views are created
        with ``CREATE OR REPLACE`` so re-installing the same registry
        on a fresh connection is idempotent.
        """
        attached_pg: set[str] = set()
        for source in self._sources.values():
            kind = _classify_source(source.uri)
            alias = f"__layer_{source.name}"
            if not _IDENT_RE.match(alias):  # pragma: no cover — defence
                raise LayerRegistryError(
                    f"derived alias {alias!r} is not a valid identifier"
                )

            if kind == "gpkg":
                path = Path(source.uri).expanduser().resolve()
                if not path.exists():
                    raise LayerRegistryError(
                        f"layer {source.name!r}: source GPKG not found at "
                        f"{path}"
                    )
                conn.execute(
                    f"ATTACH '{path}' AS {alias} (TYPE SQLITE, READ_ONLY)"
                )
                table = source.resolved_table()
                conn.execute(
                    f'CREATE OR REPLACE VIEW "{source.name}" AS '
                    f'SELECT * FROM {alias}."{table}"'
                )
            elif kind == "parquet":
                path = Path(source.uri).expanduser().resolve()
                if not path.exists():
                    raise LayerRegistryError(
                        f"layer {source.name!r}: source Parquet not found at "
                        f"{path}"
                    )
                conn.execute(
                    f'CREATE OR REPLACE VIEW "{source.name}" AS '
                    f"SELECT * FROM read_parquet('{path}')"
                )
            elif kind == "postgis":
                # Lazy-load the postgres extension. ``INSTALL`` is a no-op
                # after the first call so the cost is paid once per
                # process. We dedupe ATTACH per DSN within this install
                # call so multiple layers from the same database share
                # one alias.
                _ensure_postgres_loaded(conn)
                if source.uri not in attached_pg:
                    conn.execute(
                        f"ATTACH '{source.uri}' AS {alias} "
                        f"(TYPE postgres, READ_ONLY)"
                    )
                    attached_pg.add(source.uri)
                table = source.resolved_table()
                conn.execute(
                    f'CREATE OR REPLACE VIEW "{source.name}" AS '
                    f'SELECT * FROM {alias}.{source.schema}."{table}"'
                )
            else:  # pragma: no cover — _classify_source is exhaustive
                raise LayerRegistryError(
                    f"layer {source.name!r}: unhandled source kind {kind!r}"
                )


def _ensure_postgres_loaded(conn: "DuckDBPyConnection") -> None:
    """Install + load the ``postgres_scanner`` extension idempotently."""
    try:
        conn.execute("LOAD postgres;")
    except Exception:  # noqa: BLE001 — fall through to install
        try:
            conn.execute("INSTALL postgres;")
            conn.execute("LOAD postgres;")
        except Exception as exc:
            raise LayerRegistryError(
                f"PostgreSQL layer requires the DuckDB ``postgres`` "
                f"extension; install failed: {exc}"
            ) from exc
