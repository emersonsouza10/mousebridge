"""Fachada pública in-process que o agente importa (SPEC §28, ARQUITETURA §11).

PEP 249-like e mínima. Esconde a multiplexação assíncrona atrás de uma API
síncrona: um event loop roda numa thread dedicada e os métodos bloqueiam até a
resposta. A **mesma API serve qualquer engine** — é o que o OpenClaw consome.

    import conninfo
    conn = conninfo.connect("oracle_prod", host="10.0.0.5", key="segredo")
    for row in conn.execute("select * from dual"):
        print(row)
    conn.close()
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any, Coroutine

from zephyrlink.config import SecurityConfig

from conninfo.ci_gateway.client import GatewayClient, GatewayError
from conninfo.ci_gateway.cursor import Cursor
from conninfo.ci_protocol.messages import CiMsgType
from conninfo.config import DEFAULT_PORT


class _Runtime:
    """Event loop numa thread dedicada; ``call`` roda uma corrotina e bloqueia."""

    def __init__(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()

    def call(self, coro: Coroutine) -> Any:
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result()

    def shutdown(self) -> None:
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=2.0)


def _security(key: str | None, use_tls: bool, security: SecurityConfig | None) -> SecurityConfig:
    if security is not None:
        return security
    return SecurityConfig(shared_key=key or "change-me", use_tls=use_tls)


class Connection:
    """Sessão de banco aberta no cliente, vista pelo agente."""

    def __init__(self, runtime: _Runtime, client: GatewayClient, owns_runtime: bool) -> None:
        self._runtime = runtime
        self._client = client
        self._owns_runtime = owns_runtime
        self.session_id: str | None = None
        self.engine: str | None = None
        self.read_only: bool | None = None

    def _open(self, connection_id: str, secret: str | None) -> None:
        reply = self._runtime.call(
            self._client.request(CiMsgType.CONNECT, {"connection_id": connection_id, "secret": secret})
        )
        self.session_id = reply["session_id"]
        self.engine = reply["engine"]
        self.read_only = reply["read_only"]

    def execute(
        self,
        sql: str,
        params: Any = None,
        *,
        max_rows: int | None = None,
        timeout_seconds: int | None = None,
        fetch_size: int | None = None,
    ) -> Cursor:
        options = {"max_rows": max_rows, "timeout_seconds": timeout_seconds, "fetch_size": fetch_size}
        options = {k: v for k, v in options.items() if v is not None}
        reply = self._runtime.call(
            self._client.request(
                CiMsgType.EXECUTE,
                {"session_id": self.session_id, "sql": sql, "params": params, "options": options},
            )
        )
        return Cursor(self, reply)

    def list_tables(self, schema: str | None = None) -> list[str]:
        reply = self._runtime.call(
            self._client.request(CiMsgType.LIST_TABLES, {"session_id": self.session_id, "schema": schema})
        )
        return reply.get("tables") or []

    def describe(self, table: str) -> list[dict[str, Any]]:
        reply = self._runtime.call(
            self._client.request(CiMsgType.DESCRIBE, {"session_id": self.session_id, "table": table})
        )
        return reply.get("columns") or []

    def list_indexes(self, table: str) -> list[dict[str, Any]]:
        reply = self._meta(CiMsgType.LIST_INDEXES, table)
        return reply.get("indexes") or []

    def list_constraints(self, table: str) -> list[dict[str, Any]]:
        reply = self._meta(CiMsgType.LIST_CONSTRAINTS, table)
        return reply.get("constraints") or []

    def list_pk(self, table: str) -> list[str]:
        reply = self._meta(CiMsgType.LIST_PK, table)
        return reply.get("primary_key") or []

    def list_fk(self, table: str) -> list[dict[str, Any]]:
        reply = self._meta(CiMsgType.LIST_FK, table)
        return reply.get("foreign_keys") or []

    def explain(self, sql: str) -> str:
        reply = self._runtime.call(
            self._client.request(CiMsgType.EXPLAIN, {"session_id": self.session_id, "sql": sql})
        )
        return reply.get("plan", "")

    def _meta(self, msg_type: CiMsgType, table: str) -> dict[str, Any]:
        return self._runtime.call(
            self._client.request(msg_type, {"session_id": self.session_id, "table": table})
        )

    # --- transação (SPEC RF08) ---

    def begin(self) -> None:
        self._runtime.call(self._client.request(CiMsgType.BEGIN, {"session_id": self.session_id}))

    def commit(self) -> None:
        self._runtime.call(self._client.request(CiMsgType.COMMIT, {"session_id": self.session_id}))

    def rollback(self) -> None:
        self._runtime.call(self._client.request(CiMsgType.ROLLBACK, {"session_id": self.session_id}))

    def version(self) -> str:
        reply = self._runtime.call(
            self._client.request(CiMsgType.VERSION, {"session_id": self.session_id})
        )
        return reply.get("version", "")

    def cancel(self) -> bool:
        """Cancela a consulta em andamento desta sessão (SPEC RF09).

        Chame de **outra thread** enquanto ``execute()`` bloqueia: o event loop é
        compartilhado, então o CANCEL é processado em paralelo."""
        reply = self._runtime.call(
            self._client.request(CiMsgType.CANCEL, {"session_id": self.session_id}, timeout=10.0)
        )
        return bool(reply.get("cancelled"))

    def close(self) -> None:
        if self.session_id is not None:
            try:
                self._runtime.call(
                    self._client.request(CiMsgType.DISCONNECT, {"session_id": self.session_id})
                )
            except GatewayError:
                pass
            self.session_id = None
        self._runtime.call(self._client.close())
        if self._owns_runtime:
            self._runtime.shutdown()

    # usados pelo Cursor
    def _fetch(self, cursor_id: str) -> dict[str, Any]:
        return self._runtime.call(
            self._client.request(CiMsgType.FETCH, {"session_id": self.session_id, "cursor_id": cursor_id})
        )

    def _close_cursor(self, cursor_id: str) -> None:
        try:
            self._runtime.call(
                self._client.request(CiMsgType.CLOSE_CURSOR, {"session_id": self.session_id, "cursor_id": cursor_id})
            )
        except GatewayError:
            pass


def connect(
    connection_id: str,
    *,
    host: str,
    port: int = DEFAULT_PORT,
    key: str | None = None,
    use_tls: bool = False,
    secret: str | None = None,
    user: str | None = None,
    token: str | None = None,
    security: SecurityConfig | None = None,
) -> Connection:
    """Abre uma sessão de banco no cliente e devolve uma ``Connection``.

    ``user``/``token`` identificam o agente para a ACL (SPEC §22); sem ACL no host,
    podem ser omitidos."""
    runtime = _Runtime()
    client = GatewayClient(host, port, _security(key, use_tls, security), user=user, token=token)
    runtime.call(client.connect())
    conn = Connection(runtime, client, owns_runtime=True)
    try:
        conn._open(connection_id, secret)
    except Exception:
        conn.close()
        raise
    return conn


def list_connections(
    *,
    host: str,
    port: int = DEFAULT_PORT,
    key: str | None = None,
    use_tls: bool = False,
    user: str | None = None,
    token: str | None = None,
    security: SecurityConfig | None = None,
) -> list[dict[str, str]]:
    """Lista as conexões que o cliente publica (já filtradas pela ACL do usuário)."""
    runtime = _Runtime()
    client = GatewayClient(host, port, _security(key, use_tls, security), user=user, token=token)
    try:
        runtime.call(client.connect())
        return client.connections
    finally:
        runtime.call(client.close())
        runtime.shutdown()
