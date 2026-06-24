"""Cliente RPC do lado requisitante.

Conecta ao dono dos arquivos, autentica e faz pedidos FS_*. Sequencial — um
pedido por vez — o que basta para o clone/sync da POC; o ``req_id`` é verificado
na resposta para casar pedido e réplica.
"""

from __future__ import annotations

import asyncio
import itertools
from typing import Any

from zephyrlink.config import SecurityConfig
from zephyrlink.transport.security import build_client_ssl_context

from foshar.foshar_protocol.channel import FsChannel, connect_handshake
from foshar.foshar_protocol.messages import FsMessage, FsMsgType


class RpcError(Exception):
    """O dono recusou a operação (ex.: caminho fora do share, somente leitura)."""


class FosharClient:
    def __init__(self, host: str, port: int, security: SecurityConfig) -> None:
        self._host = host
        self._port = port
        self._security = security
        self._channel: FsChannel | None = None
        self._ids = itertools.count(1)
        self.shares: list[dict[str, str]] = []

    async def connect(self) -> None:
        ssl_context = build_client_ssl_context(self._security)
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(self._host, self._port, ssl=ssl_context), timeout=10.0
        )
        self._channel = FsChannel(reader, writer)
        self.shares = await connect_handshake(self._channel, self._security.shared_key)

    async def close(self) -> None:
        if self._channel is not None:
            await self._channel.close()
            self._channel = None

    async def request(self, msg_type: FsMsgType, data: dict[str, Any]) -> dict[str, Any]:
        if self._channel is None:
            raise RpcError("não conectado")
        req_id = str(next(self._ids))
        await self._channel.send(FsMessage(msg_type, {**data, "req_id": req_id}))
        reply = await asyncio.wait_for(self._channel.receive(), timeout=30.0)
        if reply.type != FsMsgType.FS_REPLY:
            raise RpcError(f"resposta inesperada: {reply.type}")
        if reply.data.get("req_id") != req_id:
            raise RpcError("req_id divergente")
        if not reply.data.get("ok"):
            raise RpcError(str(reply.data.get("error") or "falha"))
        return reply.data

    async def list_dir(self, share: str, path: str = ".") -> list[dict[str, Any]]:
        return (await self.request(FsMsgType.FS_LIST, {"share": share, "path": path}))["entries"]

    async def manifest(self, share: str, path: str = ".") -> list[dict[str, Any]]:
        return (await self.request(FsMsgType.FS_MANIFEST, {"share": share, "path": path}))["entries"]

    async def read(self, share: str, path: str) -> dict[str, Any]:
        return await self.request(FsMsgType.FS_READ, {"share": share, "path": path})

    async def write(self, share: str, path: str, data_b64: str) -> dict[str, Any]:
        return await self.request(
            FsMsgType.FS_WRITE, {"share": share, "path": path, "data_b64": data_b64}
        )
