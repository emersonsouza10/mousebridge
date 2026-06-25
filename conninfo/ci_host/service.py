"""ProviderHost: lado DONO do banco (roda na máquina cliente, ESCUTA).

Mesmo modelo do servidor do foshar — allowlist de host, handshake HMAC, laço de
despacho — mas **multiplexado**: cada pedido vira uma task e a parte bloqueante
(DBAPI) roda no thread pool, então várias sessões/consultas convivem num socket e
o controle (CANCEL/disconnect) nunca fica preso atrás de uma consulta longa.

Erros viram ``REPLY ok=false`` (com ``code``) — nunca derrubam a conexão, e nunca
vazam credencial/connection string.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import secrets
import time
from typing import Any

from zephyrlink.transport.security import build_server_ssl_context, host_allowed

from conninfo.ci_host.executor import DbExecutor
from conninfo.ci_host.metacache import MetaCache
from conninfo.ci_host.registry import get_provider
from conninfo.ci_host.session import Session, SessionManager
from conninfo.ci_security.acl import Acl, AclDecision
from conninfo.ci_security.audit import AuditLog
from conninfo.ci_security.limits import PolicyError, RateLimiter, clamp_max_rows, ensure_read_only
from conninfo.ci_discovery import merged_connections
from conninfo.config import ConnInfoConfig
from conninfo.errors import ConnectionNotAllowed, ConnInfoError, QueryCancelled, QueryTimeout, RateLimited
from conninfo.ci_protocol.channel import CiChannel, accept_handshake
from conninfo.ci_protocol.messages import CiMessage, CiMsgType
from conninfo.providers.base import DbError, ResultCursor
from conninfo.util import encode_rows

logger = logging.getLogger(__name__)

DEFAULT_FETCH_SIZE = 200
_REAP_INTERVAL_S = 30.0


class _ConnState:
    """Estado por conexão TCP: identidade do agente e sessões que ele criou."""

    def __init__(self, host: str, decision: AclDecision) -> None:
        self.host = host
        self.decision = decision
        self.session_ids: set[str] = set()
        self.tasks: set[asyncio.Task] = set()

    @property
    def user(self) -> str:
        return self.decision.user


class ProviderHost:
    def __init__(self, config: ConnInfoConfig) -> None:
        self._config = config
        self._sessions = SessionManager(config.session_timeout_s)
        self._executor = DbExecutor(config.pool_size)
        self._audit = AuditLog(config.audit_file)
        self._acl = Acl(config.acl)
        self._rate = RateLimiter(config.rate_limit_per_min)
        self._meta = MetaCache(config.metadata_cache_ttl_s)
        # catálogo efetivo: cadastro manual + descoberta automática (tnsnames etc.)
        self._connections = {c.id: c for c in merged_connections(config)}
        self._stopping = asyncio.Event()

    def _catalog(self) -> list[dict[str, str]]:
        return [c.catalog_entry() for c in self._connections.values()]

    async def run(self) -> None:
        ssl_context = build_server_ssl_context(self._config.security)
        server = await asyncio.start_server(
            self._handle, host="0.0.0.0", port=self._config.port, ssl=ssl_context
        )
        logger.info(
            "connInfo ProviderHost escutando em 0.0.0.0:%d (TLS=%s, %d conexão(ões))",
            self._config.port,
            bool(ssl_context),
            len(self._config.connections),
        )
        reaper = asyncio.create_task(self._reap_loop())
        try:
            async with server:
                await self._stopping.wait()
        finally:
            reaper.cancel()
            self._executor.shutdown()

    def stop(self) -> None:
        self._stopping.set()

    # --- ciclo de conexão ---

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        channel = CiChannel(reader, writer)
        host = channel.peer_host
        if not host_allowed(host, self._config.security.allowed_hosts):
            logger.warning("Conexão de host não autorizado recusada: %s", host)
            await channel.close()
            return
        decision = await accept_handshake(
            channel, self._config.security.shared_key, self._acl.authorize, self._catalog()
        )
        if decision is None:
            logger.warning("Autenticação/ACL falhou para %s", host)
            await channel.close()
            return
        logger.info("Agente connInfo conectado: %s (user=%s)", host, decision.user)
        state = _ConnState(host, decision)
        try:
            await self._serve_loop(channel, state)
        except (ConnectionError, asyncio.IncompleteReadError, OSError, asyncio.TimeoutError) as exc:
            logger.info("Conexão com %s encerrada: %s", host, exc)
        finally:
            for task in list(state.tasks):
                task.cancel()
            self._cleanup_connection(state)
            await channel.close()

    async def _serve_loop(self, channel: CiChannel, state: _ConnState) -> None:
        while True:
            message = await channel.receive()
            task = asyncio.create_task(self._dispatch(channel, state, message))
            state.tasks.add(task)
            task.add_done_callback(state.tasks.discard)

    async def _dispatch(self, channel: CiChannel, state: _ConnState, message: CiMessage) -> None:
        req_id = message.data.get("req_id")
        try:
            payload = await self._route(state, message)
            reply = {"req_id": req_id, "ok": True, **payload}
        except (PolicyError, DbError, ConnInfoError) as exc:
            reply = {"req_id": req_id, "ok": False, "code": exc.code, "error": str(exc)}
        except (KeyError, ValueError) as exc:
            reply = {"req_id": req_id, "ok": False, "code": "INTERNAL_ERROR", "error": str(exc)}
        await channel.send(CiMessage(CiMsgType.REPLY, reply))

    async def _route(self, state: _ConnState, message: CiMessage) -> dict[str, Any]:
        data = message.data
        handler = {
            CiMsgType.LIST_CONNECTIONS: self._list_connections,
            CiMsgType.CONNECT: self._connect,
            CiMsgType.EXECUTE: self._execute,
            CiMsgType.FETCH: self._fetch,
            CiMsgType.CANCEL: self._cancel,
            CiMsgType.CLOSE_CURSOR: self._close_cursor,
            CiMsgType.DISCONNECT: self._disconnect,
            CiMsgType.BEGIN: self._begin,
            CiMsgType.COMMIT: self._commit,
            CiMsgType.ROLLBACK: self._rollback,
            CiMsgType.VERSION: self._version,
            CiMsgType.LIST_TABLES: self._list_tables,
            CiMsgType.DESCRIBE: self._describe,
            CiMsgType.LIST_INDEXES: self._list_indexes,
            CiMsgType.LIST_CONSTRAINTS: self._list_constraints,
            CiMsgType.LIST_PK: self._list_pk,
            CiMsgType.LIST_FK: self._list_fk,
            CiMsgType.EXPLAIN: self._explain,
        }.get(message.type)
        if handler is None:
            raise DbError(f"ação desconhecida: {message.type}")
        return await handler(state, data)

    # --- handlers ---

    async def _list_connections(self, state: _ConnState, data: dict[str, Any]) -> dict[str, Any]:
        return {"connections": state.decision.filter_catalog(self._catalog())}

    async def _connect(self, state: _ConnState, data: dict[str, Any]) -> dict[str, Any]:
        conn_id = data["connection_id"]
        conn_def = self._connections.get(conn_id)
        if conn_def is None:
            raise DbError(f"conexão não encontrada: {conn_id}")  # CONNECTION_NOT_FOUND
        if not state.decision.allows(conn_id):  # SPEC §22: allowlist por usuário
            raise ConnectionNotAllowed(f"conexão '{conn_id}' não permitida para '{state.user}'")
        # read-only efetivo: a conexão é ro, OU a ACL do usuário força somente leitura
        read_only = conn_def.read_only or state.decision.force_read_only
        provider = get_provider(conn_def.engine)
        # credencial única global (ou específica da conexão) resolvida NO CLIENTE;
        # `secret` do agente, se vier, é usado uma vez e não persistido (só sob TLS)
        dsn, secret = self._config.resolve_login(conn_def, data.get("secret"))
        conn = await self._executor.run(provider.connect, dsn, secret)
        max_rows = clamp_max_rows(None, conn_def.max_rows, self._config.max_rows_default)
        session = self._sessions.create(
            engine=conn_def.engine,
            connection_id=conn_id,
            provider=provider,
            conn=conn,
            read_only=read_only,
            max_rows=max_rows,
            user=state.user,
            db_user=dsn.get("user"),
        )
        state.session_ids.add(session.id)
        self._audit.record(
            action="connect", client_host=state.host, agent_user=state.user,
            database_user=session.db_user, connection_id=conn_id,
            engine=conn_def.engine, status="success", read_only=read_only,
        )
        return {
            "session_id": session.id,
            "engine": conn_def.engine,
            "connection_id": conn_id,
            "read_only": read_only,
        }

    async def _execute(self, state: _ConnState, data: dict[str, Any]) -> dict[str, Any]:
        session = self._require_session(data)
        if not self._rate.allow(state.user):  # RNF05: cota por usuário
            raise RateLimited(f"limite de {self._config.rate_limit_per_min}/min excedido")
        sql = data["sql"]
        options = data.get("options") or {}
        if session.read_only:
            ensure_read_only(sql)  # SPEC §16: DDL/DML bloqueados por padrão
        max_rows = clamp_max_rows(options.get("max_rows"), session.max_rows, session.max_rows)
        fetch_size = int(options.get("fetch_size") or DEFAULT_FETCH_SIZE)
        timeout_s = options.get("timeout_seconds")
        started = time.perf_counter()
        session.executing = True
        session.cancel_requested = False
        try:
            result, rows, has_more = await self._run_query(session, sql, data.get("params"), max_rows, fetch_size, timeout_s)
        except QueryTimeout:
            self._audit_exec(state, session, sql, 0, started, "timeout", QueryTimeout.code)
            raise
        except DbError as exc:
            if session.cancel_requested:  # interrompida por CANCEL, não erro de SQL
                self._audit_exec(state, session, sql, 0, started, "cancelled", QueryCancelled.code)
                raise QueryCancelled("consulta cancelada pelo agente") from exc
            self._audit_exec(state, session, sql, 0, started, "error", exc.code)
            raise
        finally:
            session.executing = False
        cursor_id = ""
        if has_more:
            cursor_id = "cur-" + secrets.token_hex(8)
            session.cursors[cursor_id] = result
        else:
            session.provider.close_cursor(result)
        session.touch()
        self._audit_exec(state, session, sql, len(rows), started, "success", None)
        return {
            "columns": [c.name for c in result.columns],
            "rows": encode_rows(rows),
            "row_count": len(rows),
            "has_more": has_more,
            "cursor_id": cursor_id,
        }

    async def _run_query(self, session, sql, params, max_rows, fetch_size, timeout_s):
        """Executa e drena o primeiro lote; aplica timeout server-side se pedido.

        A parte bloqueante roda no thread pool. No timeout, ``provider.cancel`` (rápido
        e thread-safe) interrompe a chamada DBAPI e a thread desenrola — sem deixar um
        worker preso numa consulta runaway."""
        async def work():
            result = await self._executor.run(session.provider.execute, session.conn, sql, params)
            result.extra["remaining"] = max_rows
            result.extra["fetch_size"] = fetch_size
            rows, has_more = await self._drain(session, result, fetch_size)
            return result, rows, has_more

        if not timeout_s:
            return await work()
        fut = asyncio.ensure_future(work())
        try:
            return await asyncio.wait_for(asyncio.shield(fut), timeout=float(timeout_s))
        except asyncio.TimeoutError:
            session.provider.cancel(session.conn)
            with contextlib.suppress(Exception):
                await fut  # deixa a thread bloqueada desenrolar (levanta 'interrupted')
            raise QueryTimeout(f"tempo de execução excedido ({timeout_s}s)") from None

    async def _cancel(self, state: _ConnState, data: dict[str, Any]) -> dict[str, Any]:
        """Cancela a execução em andamento da sessão (SPEC §9/RF09)."""
        session = self._require_session(data)
        if session.executing:
            session.cancel_requested = True
            session.provider.cancel(session.conn)  # interrupt rápido, thread-safe
            return {"cancelled": True}
        return {"cancelled": False}

    async def _fetch(self, state: _ConnState, data: dict[str, Any]) -> dict[str, Any]:
        session = self._require_session(data)
        cursor_id = data["cursor_id"]
        result = session.cursors.get(cursor_id)
        if result is None:
            raise DbError(f"cursor não encontrado: {cursor_id}")  # CURSOR_NOT_FOUND
        fetch_size = int(data.get("fetch_size") or result.extra.get("fetch_size") or DEFAULT_FETCH_SIZE)
        rows, has_more = await self._drain(session, result, fetch_size)
        if not has_more:
            session.provider.close_cursor(result)
            session.cursors.pop(cursor_id, None)
            cursor_id = ""
        session.touch()
        return {
            "columns": [c.name for c in result.columns],
            "rows": encode_rows(rows),
            "row_count": len(rows),
            "has_more": has_more,
            "cursor_id": cursor_id,
        }

    async def _close_cursor(self, state: _ConnState, data: dict[str, Any]) -> dict[str, Any]:
        session = self._require_session(data)
        result = session.cursors.pop(data["cursor_id"], None)
        if result is not None:
            session.provider.close_cursor(result)
        return {"closed": True}

    async def _disconnect(self, state: _ConnState, data: dict[str, Any]) -> dict[str, Any]:
        session_id = data["session_id"]
        session = self._sessions.drop(session_id)
        state.session_ids.discard(session_id)
        if session is not None:
            await self._close_session(session)
        self._audit.record(action="disconnect", client_host=state.host, status="success")
        return {"disconnected": True}

    # --- transação (SPEC RF08) ---

    async def _begin(self, state: _ConnState, data: dict[str, Any]) -> dict[str, Any]:
        session = self._require_session(data)
        await self._executor.run(session.provider.begin, session.conn)
        session.in_tx = True
        session.touch()
        return {"in_tx": True}

    async def _commit(self, state: _ConnState, data: dict[str, Any]) -> dict[str, Any]:
        session = self._require_session(data)
        await self._executor.run(session.provider.commit, session.conn)
        session.in_tx = False
        session.touch()
        return {"in_tx": False}

    async def _rollback(self, state: _ConnState, data: dict[str, Any]) -> dict[str, Any]:
        session = self._require_session(data)
        await self._executor.run(session.provider.rollback, session.conn)
        session.in_tx = False
        session.touch()
        return {"in_tx": False}

    # --- metadata (com cache por conexão) ---

    async def _version(self, state: _ConnState, data: dict[str, Any]) -> dict[str, Any]:
        session = self._require_session(data)
        version = await self._cached_meta(session, "version", (), session.provider.version, session.conn)
        return {"version": version}

    async def _list_tables(self, state: _ConnState, data: dict[str, Any]) -> dict[str, Any]:
        session = self._require_session(data)
        schema = data.get("schema")
        tables = await self._cached_meta(session, "list_tables", (schema,), session.provider.list_tables, session.conn, schema)
        return {"tables": tables}

    async def _describe(self, state: _ConnState, data: dict[str, Any]) -> dict[str, Any]:
        session = self._require_session(data)
        table = data["table"]
        columns = await self._cached_meta(session, "describe", (table,), session.provider.describe, session.conn, table)
        return {"columns": columns}

    async def _list_indexes(self, state: _ConnState, data: dict[str, Any]) -> dict[str, Any]:
        session = self._require_session(data)
        table = data["table"]
        indexes = await self._cached_meta(session, "list_indexes", (table,), session.provider.list_indexes, session.conn, table)
        return {"indexes": indexes}

    async def _list_constraints(self, state: _ConnState, data: dict[str, Any]) -> dict[str, Any]:
        session = self._require_session(data)
        table = data["table"]
        cons = await self._cached_meta(session, "list_constraints", (table,), session.provider.list_constraints, session.conn, table)
        return {"constraints": cons}

    async def _list_pk(self, state: _ConnState, data: dict[str, Any]) -> dict[str, Any]:
        session = self._require_session(data)
        table = data["table"]
        pk = await self._cached_meta(session, "list_pk", (table,), session.provider.list_pk, session.conn, table)
        return {"primary_key": pk}

    async def _list_fk(self, state: _ConnState, data: dict[str, Any]) -> dict[str, Any]:
        session = self._require_session(data)
        table = data["table"]
        fk = await self._cached_meta(session, "list_fk", (table,), session.provider.list_fk, session.conn, table)
        return {"foreign_keys": fk}

    async def _explain(self, state: _ConnState, data: dict[str, Any]) -> dict[str, Any]:
        session = self._require_session(data)  # não cacheia: depende da SQL
        plan = await self._executor.run(session.provider.explain, session.conn, data["sql"])
        return {"plan": plan}

    # --- helpers ---

    async def _cached_meta(self, session: Session, method: str, args: tuple, fn, *fn_args):
        cached = self._meta.get(session.connection_id, method, *args)
        if cached is not None:
            return cached
        value = await self._executor.run(fn, *fn_args)
        self._meta.set(session.connection_id, method, args, value)
        session.touch()
        return value

    def _require_session(self, data: dict[str, Any]) -> Session:
        session = self._sessions.get(data.get("session_id", ""))
        if session is None:
            raise DbError(f"sessão não encontrada: {data.get('session_id')}")  # SESSION_NOT_FOUND
        return session

    async def _drain(self, session: Session, result: ResultCursor, fetch_size: int) -> tuple[list[list[Any]], bool]:
        remaining = int(result.extra.get("remaining", session.max_rows))
        if remaining <= 0:
            return [], False
        n = min(fetch_size, remaining)
        rows, driver_has_more = await self._executor.run(session.provider.fetchmany, result, n)
        remaining -= len(rows)
        result.extra["remaining"] = remaining
        has_more = driver_has_more and remaining > 0
        return rows, has_more

    async def _close_session(self, session: Session) -> None:
        for result in session.cursors.values():
            session.provider.close_cursor(result)
        session.cursors.clear()
        await self._executor.run(session.provider.disconnect, session.conn)

    def _cleanup_connection(self, state: _ConnState) -> None:
        for session_id in list(state.session_ids):
            session = self._sessions.drop(session_id)
            if session is not None:
                # síncrono no cleanup (loop pode estar encerrando)
                try:
                    for result in session.cursors.values():
                        session.provider.close_cursor(result)
                    session.provider.disconnect(session.conn)
                except Exception:  # noqa: BLE001
                    pass

    def _audit_exec(self, state, session, sql, row_count, started, status, code) -> None:
        from conninfo.util import sql_hash, sql_preview

        self._audit.record(
            action="execute", client_host=state.host, agent_user=session.user,
            database_user=session.db_user, connection_id=session.connection_id, engine=session.engine,
            sql_hash=sql_hash(sql), sql_preview=sql_preview(sql),
            duration_ms=round((time.perf_counter() - started) * 1000, 1),
            row_count=row_count, status=status, error_code=code, read_only=session.read_only,
        )

    async def _reap_loop(self) -> None:
        while True:
            await asyncio.sleep(_REAP_INTERVAL_S)
            for session in self._sessions.expired():
                logger.info("Expirando sessão inativa: %s", session.id)
                self._sessions.drop(session.id)
                await self._close_session(session)
