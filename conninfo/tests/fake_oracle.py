"""Driver ``oracledb`` falso para testes, sem um Oracle real.

O produto é Oracle-only; aqui simulamos o ``oracledb`` com ``sqlite3`` (stdlib) por
baixo, apenas no código de teste. Isso preserva a cobertura ponta a ponta do host
(connect/execute/streaming/cancel/timeout) — o ``OracleProvider`` roda de verdade,
só o banco embaixo é simulado. Ignora o DSN Oracle e abre o arquivo sqlite indicado.

``conn.cancel()`` mapeia para ``interrupt()`` (como o OCIBreak), então os testes de
cancelamento/timeout exercitam o caminho real.
"""

from __future__ import annotations

import sqlite3
import sys


class FakeOracleCursor:
    def __init__(self, raw: sqlite3.Cursor) -> None:
        self._raw = raw

    @property
    def description(self):
        return self._raw.description

    def execute(self, sql, params=None):
        self._raw.execute(sql, params or [])
        return self

    def fetchmany(self, n):
        return self._raw.fetchmany(n)

    def fetchall(self):
        return self._raw.fetchall()

    def close(self):
        self._raw.close()


class FakeOracleConn:
    version = "19.0.0.0.0"

    def __init__(self, db_path: str) -> None:
        self._db = sqlite3.connect(db_path, check_same_thread=False, timeout=10.0)

    def cursor(self):
        return FakeOracleCursor(self._db.cursor())

    def cancel(self):
        self._db.interrupt()  # equivalente ao OCIBreak

    def commit(self):
        self._db.commit()

    def rollback(self):
        self._db.rollback()

    def close(self):
        self._db.close()


class FakeOracledb:
    Error = sqlite3.Error

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self.last_kwargs: dict | None = None

    def connect(self, **kwargs):
        self.last_kwargs = kwargs
        return FakeOracleConn(self.db_path)


def install(monkeypatch, db_path: str) -> FakeOracledb:
    """Coloca o driver falso em ``sys.modules['oracledb']`` (visível a todas as threads)."""
    fake = FakeOracledb(str(db_path))
    monkeypatch.setitem(sys.modules, "oracledb", fake)
    return fake


def make_db(path, rows: int = 5) -> None:
    """Cria o arquivo sqlite que o Oracle falso vai servir, com a tabela ``clientes``."""
    con = sqlite3.connect(path)
    con.execute("create table clientes (id integer primary key, nome text)")
    con.executemany("insert into clientes (id, nome) values (?, ?)",
                    [(i, f"cliente-{i}") for i in range(1, rows + 1)])
    con.commit()
    con.close()
