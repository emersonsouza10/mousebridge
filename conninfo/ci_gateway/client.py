"""Cliente assíncrono multiplexado do lado agente (Gateway).

Disca para o ProviderHost, autentica e envia pedidos correlacionados por
``req_id``. Uma task de leitura em background casa cada ``REPLY`` com o ``Future``
do pedido — então várias sessões/consultas (e o cancelamento) convivem no mesmo
socket, fora de ordem.
"""

from __future__ import annotations

import asyncio
import itertools
from typing import Any

from zephyrlink.config import SecurityConfig
from zephyrlink.transport.security import build_client_ssl_context

from conninfo.ci_protocol.channel import CiChannel, connect_handshake
from conninfo.ci_protocol.messages import CiMessage, CiMsgType


class GatewayError(Exception):
    """O host recusou a operação (carrega ``code`` normalizado quando disponível)."""

    def __init__(self, message: str, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code


class GatewayClient:
    def __init__(
        self,
        host: str,
        port: int,
        security: SecurityConfig,
        *,
        user: str | None = None,
        token: str | None = None,
    ) -> None:
        self._host = host
        self._port = port
        self._security = security
        self._user = user
        self._token = token
        self._channel: CiChannel | None = None
        self._ids = itertools.count(1)
        self._pending: dict[str, asyncio.Future] = {}
        self._reader_task: asyncio.Task | None = None
        self.connections: list[dict[str, str]] = []

    async def connect(self) -> None:
        ssl_context = build_client_ssl_context(self._security)
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(self._host, self._port, ssl=ssl_context), timeout=10.0
        )
        self._channel = CiChannel(reader, writer)
        self.connections = await connect_handshake(
            self._channel, self._security.shared_key, user=self._user, token=self._token
        )
        self._reader_task = asyncio.create_task(self._read_loop())

    async def close(self) -> None:
        if self._reader_task is not None:
            self._reader_task.cancel()
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(GatewayError("conexão encerrada"))
        self._pending.clear()
        if self._channel is not None:
            await self._channel.close()
            self._channel = None

    async def _read_loop(self) -> None:
        assert self._channel is not None
        try:
            while True:
                message = await self._channel.receive()
                if message.type != CiMsgType.REPLY:
                    continue
                req_id = message.data.get("req_id")
                fut = self._pending.pop(str(req_id), None)
                if fut is not None and not fut.done():
                    fut.set_result(message.data)
        except (ConnectionError, asyncio.IncompleteReadError, OSError) as exc:
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(GatewayError(f"conexão perdida: {exc}"))
            self._pending.clear()

    async def request(self, msg_type: CiMsgType, data: dict[str, Any], *, timeout: float = 60.0) -> dict[str, Any]:
        if self._channel is None:
            raise GatewayError("não conectado")
        req_id = str(next(self._ids))
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        self._pending[req_id] = fut
        await self._channel.send(CiMessage(msg_type, {**data, "req_id": req_id}))
        try:
            reply = await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            self._pending.pop(req_id, None)
            raise GatewayError("tempo esgotado aguardando resposta", "QUERY_TIMEOUT") from None
        if not reply.get("ok"):
            raise GatewayError(str(reply.get("error") or "falha"), reply.get("code"))
        return reply
