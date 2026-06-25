"""Transações (begin/commit/rollback) e cache de metadata.

Transações via OracleProvider real sobre o ``oracledb`` falso (sqlite). O cache é
provado contando quantas vezes o provider é realmente chamado.
"""

from __future__ import annotations

import time

import pytest

from conninfo.ci_gateway.api import connect
from conninfo.ci_host.metacache import MetaCache
from conninfo.config import ConnDef, ConnInfoConfig
from conninfo.providers.oracle import OracleProvider
from conninfo.tests import fake_oracle
from conninfo.tests.test_e2e import _HostThread, _wait_listening, _free_port
from zephyrlink.config import SecurityConfig

KEY = "chave-de-teste"


def _start(tmp_path, monkeypatch, *, read_only=True, ttl=300):
    db = tmp_path / "v.db"
    fake_oracle.make_db(db, rows=3)
    fake_oracle.install(monkeypatch, db)
    conn = ConnDef(id="vendas", engine="oracle", name="V", dsn={"dsn": "HOMOL"}, read_only=read_only)
    config = ConnInfoConfig(
        enabled=True, port=_free_port(), max_rows_default=1000, metadata_cache_ttl_s=ttl,
        connections=(conn,), security=SecurityConfig(shared_key=KEY),
    )
    host = _HostThread(config)
    host.start()
    _wait_listening("127.0.0.1", config.port)
    return host, config.port


# --- MetaCache (unitário) ---

def test_metacache_basic():
    c = MetaCache(ttl_s=300)
    assert c.get("oracle_prd", "version") is None
    c.set("oracle_prd", "version", (), "19c")
    assert c.get("oracle_prd", "version") == "19c"
    c.invalidate("oracle_prd")
    assert c.get("oracle_prd", "version") is None


def test_metacache_disabled_when_ttl_zero():
    c = MetaCache(ttl_s=0)
    c.set("x", "version", (), "v")
    assert c.get("x", "version") is None
    assert c.enabled is False


def test_metacache_expires(monkeypatch):
    c = MetaCache(ttl_s=10)
    now = [1000.0]
    monkeypatch.setattr("conninfo.ci_host.metacache.time.monotonic", lambda: now[0])
    c.set("x", "list_tables", (None,), ["A"])
    assert c.get("x", "list_tables", None) == ["A"]
    now[0] += 11
    assert c.get("x", "list_tables", None) is None  # TTL venceu


# --- cache no host (prova por contagem) ---

def test_metadata_is_cached(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(OracleProvider, "version", lambda self, conn: calls.append(1) or "Oracle 19c")
    host, port = _start(tmp_path, monkeypatch, ttl=300)
    try:
        c = connect("vendas", host="127.0.0.1", port=port, key=KEY)
        assert c.version() == "Oracle 19c"
        assert c.version() == "Oracle 19c"
        assert len(calls) == 1  # segunda chamada veio do cache
        c.close()
    finally:
        host.stop()


def test_metadata_not_cached_when_disabled(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(OracleProvider, "version", lambda self, conn: calls.append(1) or "Oracle 19c")
    host, port = _start(tmp_path, monkeypatch, ttl=0)
    try:
        c = connect("vendas", host="127.0.0.1", port=port, key=KEY)
        c.version()
        c.version()
        assert len(calls) == 2  # sem cache, dois hits no provider
        c.close()
    finally:
        host.stop()


# --- transações ---

def test_commit_persists(tmp_path, monkeypatch):
    host, port = _start(tmp_path, monkeypatch, read_only=False)
    try:
        c = connect("vendas", host="127.0.0.1", port=port, key=KEY)
        c.begin()
        c.execute("insert into clientes (id, nome) values (99, 'novo')").fetchall()
        c.commit()
        assert c.execute("select nome from clientes where id = 99").fetchall() == [["novo"]]
        c.close()
    finally:
        host.stop()


def test_rollback_discards(tmp_path, monkeypatch):
    host, port = _start(tmp_path, monkeypatch, read_only=False)
    try:
        c = connect("vendas", host="127.0.0.1", port=port, key=KEY)
        c.begin()
        c.execute("insert into clientes (id, nome) values (100, 'temp')").fetchall()
        c.rollback()
        assert c.execute("select nome from clientes where id = 100").fetchall() == []
        c.close()
    finally:
        host.stop()


def test_write_still_blocked_on_read_only(tmp_path, monkeypatch):
    from conninfo.ci_gateway.client import GatewayError

    host, port = _start(tmp_path, monkeypatch, read_only=True)
    try:
        c = connect("vendas", host="127.0.0.1", port=port, key=KEY)
        c.begin()  # begin em si é permitido…
        with pytest.raises(GatewayError) as exc:
            c.execute("insert into clientes (id, nome) values (1, 'x')")  # …mas a escrita não
        assert exc.value.code == "SQL_BLOCKED_BY_POLICY"
        c.close()
    finally:
        host.stop()
