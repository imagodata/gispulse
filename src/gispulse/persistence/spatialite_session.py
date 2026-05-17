"""
SpatiaLiteSession — session GISPulse niveau 2 (mono-client, sans serveur).

P-8 #91 : SpatiaLite comme moteur de session portable. Charge un GPKG,
maintient un journal de changements via SQLite triggers, évalue les Triggers
GISPulse optionnellement via un évaluateur injecté, puis exporte en GPKG.

Architecture::

    GPKG (import)
        │
        ▼ load_gpkg()
    SpatiaLite (.db en mémoire ou disque)
        │
        ├── SQLite triggers → table _change_log
        │                          │
        │                     polling Python (100ms)
        │                          │
        │                    evaluator.evaluate(record, triggers)  ← injecté
        │                          │
        │                    FiredTrigger accumulés
        │
        ▼ commit_to_gpkg()
    GPKG (export enrichi)

Contraintes :
    - Mono-client uniquement (SQLite = 1 writer)
    - Polling ~100ms (pas de pg_notify)
    - Portal / CLI uniquement (QGIS/ArcGIS ne peuvent pas se connecter)

Note architecture :
    ``SpatiaLiteSession`` appartient à la couche ``persistence``. Elle ne
    dépend pas directement de ``rules.TriggerEvaluator`` : l'évaluateur est
    injecté au moment de ``start_polling``. Cela respecte la direction de
    dépendance persistence → core.
"""
from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely import wkt as shapely_wkt

from gispulse.core.models import ChangeOperation, ChangeRecord, FiredTrigger, Trigger


# ---------------------------------------------------------------------------
# SQL helpers
# ---------------------------------------------------------------------------

_SQL_CREATE_CHANGE_LOG = """
CREATE TABLE IF NOT EXISTS _change_log (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    table_name TEXT    NOT NULL,
    operation  TEXT    NOT NULL,
    row_pk     TEXT,
    changed_at TEXT    DEFAULT (datetime('now')),
    processed  INTEGER DEFAULT 0
)
"""


def _build_sqlite_triggers(table_name: str) -> list[str]:
    """Retourne les CREATE TRIGGER SQLite pour INSERT / UPDATE / DELETE."""
    ops: list[tuple[str, str, str]] = [
        ("insert", "INSERT", "NEW.rowid"),
        ("update", "UPDATE", "NEW.rowid"),
        ("delete", "DELETE", "OLD.rowid"),
    ]
    sqls = []
    for suffix, op, pk_expr in ops:
        sqls.append(
            f'CREATE TRIGGER IF NOT EXISTS "_trg_{table_name}_{suffix}" '
            f'AFTER {op} ON "{table_name}" '
            f"BEGIN "
            f"  INSERT INTO _change_log(table_name, operation, row_pk) "
            f"  VALUES ('{table_name}', '{op}', {pk_expr}); "
            f"END"
        )
    return sqls


# ---------------------------------------------------------------------------
# Evaluator protocol
# ---------------------------------------------------------------------------

class _TriggerEvaluatorProtocol:
    """Minimal protocol expected from an injected evaluator."""

    def evaluate(
        self, change_record: ChangeRecord, triggers: list[Trigger]
    ) -> list[FiredTrigger]:
        ...  # pragma: no cover


# ---------------------------------------------------------------------------
# SpatiaLiteSession
# ---------------------------------------------------------------------------

class SpatiaLiteSession:
    """Session mono-client SpatiaLite — niveau 2 (sans serveur PostGIS).

    Usage::

        from gispulse.rules.trigger_evaluator import TriggerEvaluator

        session = SpatiaLiteSession(db_path=":memory:")
        session.open()
        session.load_gpkg("input.gpkg", layer="parcels")
        evaluator = TriggerEvaluator()
        session.start_polling(triggers=[...], evaluator=evaluator)
        # ... éditions via session.conn ...
        session.stop_polling()
        session.commit_to_gpkg("output.gpkg", layer="parcels")
        session.close()

    If ``evaluator`` is ``None``, ``ChangeRecord`` events are still accumulated
    in ``session.change_records`` but no ``FiredTrigger`` is generated.
    """

    def __init__(self, db_path: str = ":memory:") -> None:
        self.db_path = db_path
        self._conn: sqlite3.Connection | None = None
        self._polling = False
        self._poll_thread: threading.Thread | None = None
        self._poll_interval: float = 0.1
        self._triggers: list[Trigger] = []
        self._fired: list[FiredTrigger] = []
        self._change_records: list[ChangeRecord] = []
        self._evaluator: _TriggerEvaluatorProtocol | None = None
        self._tables: list[str] = []

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    def open(self) -> None:
        """Ouvre la connexion SQLite et crée _change_log."""
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute(_SQL_CREATE_CHANGE_LOG)
        self._conn.commit()

    def close(self) -> None:
        """Arrête le polling et ferme la connexion SQLite."""
        self.stop_polling()
        if self._conn:
            self._conn.close()
            self._conn = None

    @property
    def conn(self) -> sqlite3.Connection:
        """Connexion SQLite active (lecture/écriture directe)."""
        if self._conn is None:
            raise RuntimeError("Session not open — call open() first")
        return self._conn

    # ------------------------------------------------------------------
    # GPKG I/O
    # ------------------------------------------------------------------

    def load_gpkg(self, gpkg_path: str, layer: str | None = None) -> str:
        """Charge une couche GPKG dans SpatiaLite.

        La géométrie est sérialisée en WKT dans une colonne ``geometry``.

        Args:
            gpkg_path: Chemin du fichier GPKG source.
            layer:     Nom de la couche (None → première couche disponible).

        Returns:
            Nom de la table créée dans SQLite.
        """
        if self._conn is None:
            raise RuntimeError("Session not open — call open() first")

        gdf = gpd.read_file(gpkg_path, layer=layer)
        table_name = layer or Path(gpkg_path).stem

        df = gdf.copy()
        if gdf.geometry is not None and not gdf.empty:
            df["geometry"] = gdf.geometry.apply(
                lambda g: g.wkt if g is not None else None
            )
        else:
            df = df.drop(columns=["geometry"], errors="ignore")

        df.to_sql(table_name, self._conn, if_exists="replace", index=False)
        self._conn.commit()

        for sql in _build_sqlite_triggers(table_name):
            self._conn.execute(sql)
        self._conn.commit()

        if table_name not in self._tables:
            self._tables.append(table_name)

        return table_name

    def commit_to_gpkg(self, gpkg_path: str, layer: str | None = None) -> None:
        """Exporte une table SpatiaLite → GPKG enrichi.

        Args:
            gpkg_path: Chemin du fichier GPKG de destination.
            layer:     Nom de la table source (None → première table chargée).
        """
        if self._conn is None:
            raise RuntimeError("Session not open — call open() first")

        table_name = layer or (self._tables[0] if self._tables else None)
        if table_name is None:
            raise ValueError("No table loaded — call load_gpkg() first")

        cursor = self._conn.execute(f'SELECT * FROM "{table_name}"')
        columns = [d[0] for d in cursor.description]
        rows = cursor.fetchall()

        if not rows:
            gdf = gpd.GeoDataFrame()
        else:
            pdf = pd.DataFrame([dict(zip(columns, r)) for r in rows])
            if "geometry" in pdf.columns:
                geoms = pdf["geometry"].apply(
                    lambda g: shapely_wkt.loads(g) if g else None
                )
                pdf = pdf.drop(columns=["geometry"])
                gdf = gpd.GeoDataFrame(pdf, geometry=geoms)
            else:
                gdf = gpd.GeoDataFrame(pdf)

        gdf.to_file(gpkg_path, driver="GPKG", layer=table_name)

    # ------------------------------------------------------------------
    # Polling
    # ------------------------------------------------------------------

    def start_polling(
        self,
        triggers: list[Trigger],
        interval: float = 0.1,
        session_id: str = "",
        evaluator: _TriggerEvaluatorProtocol | None = None,
    ) -> None:
        """Démarre le thread de polling des changements SQLite.

        Args:
            triggers:   Triggers GISPulse à évaluer sur chaque changement.
            interval:   Intervalle de polling en secondes (défaut 100ms).
            session_id: Identifiant de session injecté dans chaque ChangeRecord.
            evaluator:  Evaluateur injecté (ex. TriggerEvaluator). Si None,
                        les ChangeRecords sont accumulés sans évaluation.
        """
        if self._polling:
            return
        self._triggers = triggers
        self._poll_interval = interval
        self._evaluator = evaluator
        self._polling = True
        self._poll_thread = threading.Thread(
            target=self._poll_loop,
            args=(session_id,),
            daemon=True,
        )
        self._poll_thread.start()

    def stop_polling(self) -> None:
        """Arrête le thread de polling et attend sa terminaison."""
        self._polling = False
        if self._poll_thread:
            self._poll_thread.join(timeout=1.0)
            self._poll_thread = None

    def _poll_loop(self, session_id: str) -> None:
        """Boucle interne — exécutée dans un thread démon."""
        while self._polling and self._conn is not None:
            self._process_pending_changes(session_id)
            time.sleep(self._poll_interval)

    def _process_pending_changes(self, session_id: str) -> list[FiredTrigger]:
        """Lit _change_log, évalue les triggers, marque les lignes traitées.

        Args:
            session_id: Injecté dans chaque ChangeRecord produit.

        Returns:
            Liste des FiredTrigger générés lors de ce cycle (vide si pas d'évaluateur).
        """
        if self._conn is None:
            return []

        rows = self._conn.execute(
            "SELECT id, table_name, operation, row_pk "
            "FROM _change_log WHERE processed = 0 ORDER BY id"
        ).fetchall()

        if not rows:
            return []

        fired: list[FiredTrigger] = []
        ids_to_mark: list[int] = []

        for row in rows:
            change_id, table_name, operation_str, row_pk = (
                row[0], row[1], row[2], row[3]
            )
            try:
                operation = ChangeOperation(operation_str.upper())
            except ValueError:
                operation = ChangeOperation.INSERT

            record = ChangeRecord(
                session_id=session_id,
                table_name=table_name,
                operation=operation,
                feature_id=str(row_pk) if row_pk is not None else None,
            )

            self._change_records.append(record)

            if self._triggers:
                evaluator = self._evaluator
                if evaluator is None:
                    from gispulse.rules.trigger_evaluator import TriggerEvaluator
                    evaluator = TriggerEvaluator()
                    self._evaluator = evaluator
                fired.extend(evaluator.evaluate(record, self._triggers))

            ids_to_mark.append(change_id)

        if ids_to_mark:
            placeholders = ",".join("?" * len(ids_to_mark))
            self._conn.execute(
                f"UPDATE _change_log SET processed = 1 WHERE id IN ({placeholders})",
                ids_to_mark,
            )
            self._conn.commit()

        self._fired.extend(fired)
        return fired

    # ------------------------------------------------------------------
    # Result registries
    # ------------------------------------------------------------------

    @property
    def fired_triggers(self) -> list[FiredTrigger]:
        """Tous les FiredTrigger accumulés depuis l'ouverture de la session.

        Only populated when an ``evaluator`` was passed to ``start_polling``.
        """
        return list(self._fired)

    @property
    def change_records(self) -> list[ChangeRecord]:
        """Raw ChangeRecords accumulated since the session was opened."""
        return list(self._change_records)

    def clear_fired(self) -> None:
        """Vide le registre des FiredTrigger."""
        self._fired.clear()

    def clear_change_records(self) -> None:
        """Vide le registre des ChangeRecords."""
        self._change_records.clear()
