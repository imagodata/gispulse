"""Tests for ``persistence.gpkg_connection`` — issue #141.

Validates that every code path that opens a GPKG via ``connect_gpkg``
applies WAL + busy_timeout pragmas, and that the helper survives the
concurrent-writer scenario that produced the Py3.10 ``test_p02`` flake.
"""
from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

import pytest

from gispulse.persistence.gpkg_connection import DEFAULT_BUSY_TIMEOUT_MS, connect_gpkg


@pytest.fixture
def fresh_gpkg(tmp_path: Path) -> Path:
    """Create a minimal valid SQLite file (good enough for pragma tests)."""
    path = tmp_path / "fixture.gpkg"
    raw = sqlite3.connect(str(path))
    try:
        raw.execute("CREATE TABLE features (fid INTEGER PRIMARY KEY, name TEXT)")
        raw.commit()
    finally:
        raw.close()
    return path


def test_connect_gpkg_sets_wal_mode(fresh_gpkg: Path) -> None:
    conn = connect_gpkg(fresh_gpkg)
    try:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode.lower() == "wal"
    finally:
        conn.close()


def test_connect_gpkg_sets_busy_timeout(fresh_gpkg: Path) -> None:
    conn = connect_gpkg(fresh_gpkg)
    try:
        timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        assert timeout == DEFAULT_BUSY_TIMEOUT_MS
    finally:
        conn.close()


def test_connect_gpkg_custom_busy_timeout(fresh_gpkg: Path) -> None:
    conn = connect_gpkg(fresh_gpkg, busy_timeout_ms=2000)
    try:
        timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        assert timeout == 2000
    finally:
        conn.close()


def test_connect_gpkg_force_wal_false_skips_pragma(tmp_path: Path) -> None:
    """force_wal=False must not mutate journal_mode if the file is in DELETE."""
    path = tmp_path / "delete.gpkg"
    raw = sqlite3.connect(str(path))
    try:
        raw.execute("PRAGMA journal_mode = DELETE")
        raw.execute("CREATE TABLE t (x INTEGER)")
        raw.commit()
    finally:
        raw.close()

    conn = connect_gpkg(path, force_wal=False)
    try:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode.lower() == "delete"
    finally:
        conn.close()


def test_connect_gpkg_row_factory(fresh_gpkg: Path) -> None:
    conn = connect_gpkg(fresh_gpkg, row_factory=sqlite3.Row)
    try:
        conn.execute("INSERT INTO features (name) VALUES ('a')")
        row = conn.execute("SELECT * FROM features").fetchone()
        assert row["name"] == "a"
    finally:
        conn.close()


def test_connect_gpkg_autocommit_default(fresh_gpkg: Path) -> None:
    """isolation_level=None means autocommit — caller does not need .commit()."""
    conn1 = connect_gpkg(fresh_gpkg)
    try:
        conn1.execute("INSERT INTO features (name) VALUES ('autocommit')")
    finally:
        conn1.close()

    conn2 = connect_gpkg(fresh_gpkg)
    try:
        rows = conn2.execute("SELECT name FROM features").fetchall()
        assert ("autocommit",) in rows
    finally:
        conn2.close()


def test_connect_gpkg_concurrent_writer_reader_no_busy(fresh_gpkg: Path) -> None:
    """Writer + reader on the same GPKG must not raise SQLITE_BUSY.

    Reproduces the contention pattern of QGIS-edit + watcher-poll. With
    WAL + busy_timeout=5000, both threads should complete cleanly.
    """
    iterations = 50
    errors: list[str] = []
    barrier = threading.Barrier(2)

    def writer() -> None:
        conn = connect_gpkg(fresh_gpkg)
        try:
            barrier.wait(timeout=5.0)
            for i in range(iterations):
                try:
                    conn.execute("INSERT INTO features (name) VALUES (?)", (f"w{i}",))
                except sqlite3.OperationalError as exc:  # pragma: no cover
                    errors.append(f"writer: {exc}")
                    return
                time.sleep(0.001)
        finally:
            conn.close()

    def reader() -> None:
        conn = connect_gpkg(fresh_gpkg)
        try:
            barrier.wait(timeout=5.0)
            for _ in range(iterations):
                try:
                    conn.execute("SELECT COUNT(*) FROM features").fetchone()
                except sqlite3.OperationalError as exc:  # pragma: no cover
                    errors.append(f"reader: {exc}")
                    return
                time.sleep(0.001)
        finally:
            conn.close()

    tw = threading.Thread(target=writer)
    tr = threading.Thread(target=reader)
    tw.start()
    tr.start()
    tw.join(timeout=30.0)
    tr.join(timeout=30.0)

    assert not errors, f"concurrent access produced errors: {errors}"

    conn = connect_gpkg(fresh_gpkg)
    try:
        n = conn.execute("SELECT COUNT(*) FROM features").fetchone()[0]
        assert n == iterations
    finally:
        conn.close()


def test_connect_gpkg_string_path(fresh_gpkg: Path) -> None:
    """Helper accepts both ``str`` and ``Path``."""
    conn = connect_gpkg(str(fresh_gpkg))
    try:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode.lower() == "wal"
    finally:
        conn.close()
