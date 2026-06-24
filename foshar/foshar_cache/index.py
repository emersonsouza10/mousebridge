"""Índice de sincronização em SQLite.

Guarda, por caminho, o ``base_hash`` da última versão sincronizada. É o que
permite o diff incremental sobreviver a reinício: sem ele, toda reabertura
re-baixaria o projeto inteiro.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class IndexEntry:
    path: str
    size: int
    mtime: float
    base_hash: str


class SyncIndex:
    def __init__(self, db_path: str | Path) -> None:
        db_path = Path(db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path))
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS files ("
            "path TEXT PRIMARY KEY, size INTEGER NOT NULL, "
            "mtime REAL NOT NULL, base_hash TEXT NOT NULL)"
        )
        self._conn.commit()

    def get(self, path: str) -> IndexEntry | None:
        row = self._conn.execute(
            "SELECT path, size, mtime, base_hash FROM files WHERE path=?", (path,)
        ).fetchone()
        return IndexEntry(*row) if row else None

    def all(self) -> dict[str, IndexEntry]:
        rows = self._conn.execute("SELECT path, size, mtime, base_hash FROM files")
        return {row[0]: IndexEntry(*row) for row in rows}

    def upsert(self, entry: IndexEntry) -> None:
        self._conn.execute(
            "INSERT INTO files(path, size, mtime, base_hash) VALUES(?,?,?,?) "
            "ON CONFLICT(path) DO UPDATE SET "
            "size=excluded.size, mtime=excluded.mtime, base_hash=excluded.base_hash",
            (entry.path, entry.size, entry.mtime, entry.base_hash),
        )
        self._conn.commit()

    def delete(self, path: str) -> None:
        self._conn.execute("DELETE FROM files WHERE path=?", (path,))
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()
