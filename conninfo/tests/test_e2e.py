"""End-to-end em loopback: ProviderHost (lado banco) + API do agente, sem Oracle real.

Prova o fluxo da SPEC §24.3 com o **OracleProvider** rodando de verdade sobre um
``oracledb`` falso (sqlite por baixo, só no teste): o agente lista conexões, abre
sessão, executa SQL, recebe JSON, faz streaming, tem DML bloqueado e encerra — sem
nenhum driver de banco no "servidor" (o agente).
"""

from __future__ import annotations

import asyncio
import socket
import threading
import time

import pytest

from conninfo.ci_gateway.api import connect, list_connections
from conninfo.ci_gateway.client import GatewayError
from conninfo.ci_host import ProviderHost
from conninfo.config import ConnDef, ConnInfoConfig
from conninfo.tests import fake_oracle
from zephyrlink.config import SecurityConfig

KEY = "chave-de-teste"


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class _HostThread:
    def __init__(self, config: ConnInfoConfig) -> None:
        self.host = ProviderHost(config)
        self.loop: asyncio.AbstractEventLoop | None = None
        self._started = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self._started.set()
        self.loop.run_until_complete(self.host.run())

    def start(self) -> None:
        self.thread.start()
        self._started.wait(5)

    def stop(self) -> None:
        if self.loop is not None:
            self.loop.call_soon_threadsafe(self.host.stop)
        self.thread.join(timeout=5)


def _wait_listening(host: str, port: int, timeout: float = 5.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return
        except OSError:
            time.sleep(0.05)
    raise RuntimeError("host não começou a escutar a tempo")


def oracle_config(db, *, port=None, **kwargs) -> ConnInfoConfig:
    """Config com uma conexão Oracle apontando para o backend falso."""
    conn = ConnDef(id="vendas", engine="oracle", name="Vendas", dsn={"dsn": "HOMOL"}, read_only=True)
    return ConnInfoConfig(
        enabled=True, port=port or _free_port(), max_rows_default=1000,
        connections=(conn,), security=SecurityConfig(shared_key=KEY), **kwargs,
    )


@pytest.fixture()
def running_host(tmp_path, monkeypatch):
    db = tmp_path / "vendas.db"
    fake_oracle.make_db(db, rows=5)
    fake_oracle.install(monkeypatch, db)
    config = oracle_config(db)
    host = _HostThread(config)
    host.start()
    _wait_listening("127.0.0.1", config.port)
    yield config.port
    host.stop()


def test_list_connections(running_host):
    conns = list_connections(host="127.0.0.1", port=running_host, key=KEY)
    assert [c["id"] for c in conns] == ["vendas"]
    assert conns[0]["engine"] == "oracle"


def test_execute_returns_json_rows(running_host):
    conn = connect("vendas", host="127.0.0.1", port=running_host, key=KEY)
    try:
        cur = conn.execute("select id, nome from clientes order by id")
        assert cur.columns == ["id", "nome"]
        rows = cur.fetchall()
        assert rows[0] == [1, "cliente-1"]
        assert len(rows) == 5
    finally:
        conn.close()


def test_streaming_paginates(running_host):
    conn = connect("vendas", host="127.0.0.1", port=running_host, key=KEY)
    try:
        cur = conn.execute("select id from clientes order by id", fetch_size=2)
        ids = [row[0] for row in cur]  # itera puxando chunks de 2
        assert ids == [1, 2, 3, 4, 5]
    finally:
        conn.close()


def test_max_rows_limits(running_host):
    conn = connect("vendas", host="127.0.0.1", port=running_host, key=KEY)
    try:
        rows = conn.execute("select id from clientes order by id", max_rows=3).fetchall()
        assert len(rows) == 3
    finally:
        conn.close()


def test_read_only_blocks_dml(running_host):
    conn = connect("vendas", host="127.0.0.1", port=running_host, key=KEY)
    try:
        with pytest.raises(GatewayError) as exc:
            conn.execute("delete from clientes")
        assert exc.value.code == "SQL_BLOCKED_BY_POLICY"
    finally:
        conn.close()


def test_version(running_host):
    conn = connect("vendas", host="127.0.0.1", port=running_host, key=KEY)
    try:
        assert conn.version().startswith("Oracle")
    finally:
        conn.close()


def test_bad_key_is_rejected(running_host):
    with pytest.raises(Exception):
        connect("vendas", host="127.0.0.1", port=running_host, key="chave-errada")
