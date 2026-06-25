"""Fase 4: ACL por usuário, timeout server-side, cancelamento remoto e rate-limit.

Em loopback com o OracleProvider real sobre um ``oracledb`` falso (sqlite por
baixo). O cancelamento usa uma consulta infinita (CTE recursiva) que ``cancel()``
(mapeado para ``interrupt()``) aborta — o mesmo caminho do OCIBreak.
"""

from __future__ import annotations

import threading
import time

import pytest

from conninfo.ci_gateway.api import connect, list_connections
from conninfo.ci_gateway.client import GatewayError
from conninfo.config import AclEntry, ConnDef, ConnInfoConfig
from conninfo.tests import fake_oracle
from conninfo.tests.test_e2e import _HostThread, _wait_listening, _free_port
from zephyrlink.config import SecurityConfig

KEY = "chave-de-teste"
INFINITE_SQL = "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM c) SELECT count(*) FROM c"


def _ora(conn_id, *, read_only=True):
    return ConnDef(id=conn_id, engine="oracle", name=conn_id, dsn={"dsn": "HOMOL"}, read_only=read_only)


def _host(tmp_path, monkeypatch, *, acl=(), rate=600, connections=None):
    db = tmp_path / "v.db"
    fake_oracle.make_db(db, rows=3)
    fake_oracle.install(monkeypatch, db)
    config = ConnInfoConfig(
        enabled=True, port=_free_port(), max_rows_default=1000, rate_limit_per_min=rate,
        connections=tuple(connections or (_ora("vendas"),)), acl=tuple(acl),
        security=SecurityConfig(shared_key=KEY),
    )
    host = _HostThread(config)
    host.start()
    _wait_listening("127.0.0.1", config.port)
    return host, config.port


def test_acl_filters_catalog_and_blocks_connect(tmp_path, monkeypatch):
    acl = (AclEntry(user="leitor", token="t0k", connections=("vendas",), mode="read-only"),)
    host, port = _host(tmp_path, monkeypatch, acl=acl, connections=(_ora("vendas"), _ora("secreto")))
    try:
        cat = list_connections(host="127.0.0.1", port=port, key=KEY, user="leitor", token="t0k")
        assert [c["id"] for c in cat] == ["vendas"]  # só o permitido
        with pytest.raises(GatewayError) as exc:
            connect("secreto", host="127.0.0.1", port=port, key=KEY, user="leitor", token="t0k")
        assert exc.value.code == "CONNECTION_NOT_ALLOWED"
    finally:
        host.stop()


def test_acl_rejects_unknown_user_and_bad_token(tmp_path, monkeypatch):
    acl = (AclEntry(user="leitor", token="t0k", connections=None, mode="read-only"),)
    host, port = _host(tmp_path, monkeypatch, acl=acl)
    try:
        with pytest.raises(Exception):
            connect("vendas", host="127.0.0.1", port=port, key=KEY, user="ninguem", token="t0k")
        with pytest.raises(Exception):
            connect("vendas", host="127.0.0.1", port=port, key=KEY, user="leitor", token="errado")
    finally:
        host.stop()


def test_acl_mode_forces_read_only_even_on_rw_connection(tmp_path, monkeypatch):
    acl = (AclEntry(user="leitor", token=None, connections=None, mode="read-only"),)
    host, port = _host(tmp_path, monkeypatch, acl=acl, connections=(_ora("rw", read_only=False),))
    try:
        conn = connect("rw", host="127.0.0.1", port=port, key=KEY, user="leitor")
        assert conn.read_only is True  # ACL sobrepõe o read_only=False da conexão
        with pytest.raises(GatewayError) as exc:
            conn.execute("update clientes set nome='x'")
        assert exc.value.code == "SQL_BLOCKED_BY_POLICY"
        conn.close()
    finally:
        host.stop()


def test_query_timeout(tmp_path, monkeypatch):
    host, port = _host(tmp_path, monkeypatch)
    try:
        conn = connect("vendas", host="127.0.0.1", port=port, key=KEY)
        with pytest.raises(GatewayError) as exc:
            conn.execute(INFINITE_SQL, timeout_seconds=1)
        assert exc.value.code == "QUERY_TIMEOUT"
        # a sessão continua usável depois do timeout
        assert conn.execute("select id from clientes order by id").fetchall() == [[1], [2], [3]]
        conn.close()
    finally:
        host.stop()


def test_remote_cancel(tmp_path, monkeypatch):
    host, port = _host(tmp_path, monkeypatch)
    try:
        conn = connect("vendas", host="127.0.0.1", port=port, key=KEY)
        result = {}

        def run_infinite():
            try:
                conn.execute(INFINITE_SQL).fetchall()
            except GatewayError as exc:
                result["error"] = exc.code

        worker = threading.Thread(target=run_infinite)
        worker.start()
        time.sleep(0.5)  # deixa a consulta começar
        assert conn.cancel() is True  # de outra thread, pelo loop compartilhado
        worker.join(timeout=5)
        assert not worker.is_alive()
        assert result.get("error") == "QUERY_CANCELLED"
        conn.close()
    finally:
        host.stop()


def test_rate_limit(tmp_path, monkeypatch):
    host, port = _host(tmp_path, monkeypatch, rate=2)
    try:
        conn = connect("vendas", host="127.0.0.1", port=port, key=KEY)
        conn.execute("select 1").fetchall()
        conn.execute("select 1").fetchall()
        with pytest.raises(GatewayError) as exc:
            conn.execute("select 1").fetchall()
        assert exc.value.code == "RATE_LIMITED"
        conn.close()
    finally:
        host.stop()
