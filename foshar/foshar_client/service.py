"""Serviço do dono dos arquivos: aceita conexões e executa operações FS_*.

Mesmo modelo do servidor do ZephyrLink: allowlist de host na aceitação,
handshake HMAC, e então um laço que despacha cada pedido para ``ops`` dentro do
sandbox, devolvendo ``FS_REPLY``. Erros viram resposta ``ok=false`` — nunca
derrubam a conexão.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

from zephyrlink.transport.security import build_server_ssl_context, host_allowed

from foshar.config import FosharConfig
from foshar.foshar_client import ops
from foshar.foshar_client.ops import OpError
from foshar.foshar_protocol.channel import FsChannel, accept_handshake
from foshar.foshar_protocol.messages import FsMessage, FsMsgType
from foshar.foshar_security.sandbox import Sandbox, SandboxError

logger = logging.getLogger(__name__)

_DISPATCH: dict[FsMsgType, Callable[[Sandbox, dict[str, Any]], dict[str, Any]]] = {
    FsMsgType.FS_LIST: ops.op_list,
    FsMsgType.FS_STAT: ops.op_stat,
    FsMsgType.FS_READ: ops.op_read,
    FsMsgType.FS_WRITE: ops.op_write,
    FsMsgType.FS_WRITE_CHUNK: ops.op_write_chunk,
    FsMsgType.FS_CREATE: ops.op_create,
    FsMsgType.FS_DELETE: ops.op_delete,
    FsMsgType.FS_RENAME: ops.op_rename,
    FsMsgType.FS_MKDIR: ops.op_mkdir,
    FsMsgType.FS_RMDIR: ops.op_rmdir,
    FsMsgType.FS_MANIFEST: ops.op_manifest,
}


class FosharService:
    def __init__(self, config: FosharConfig) -> None:
        self._config = config
        self._sandbox = Sandbox(config.shares)
        self._stopping = asyncio.Event()

    async def run(self) -> None:
        ssl_context = build_server_ssl_context(self._config.security)
        server = await asyncio.start_server(
            self._handle, host="0.0.0.0", port=self._config.port, ssl=ssl_context
        )
        logger.info(
            "Foshar escutando em 0.0.0.0:%d (TLS=%s, %d share(s))",
            self._config.port,
            bool(ssl_context),
            len(self._config.shares),
        )
        async with server:
            await self._stopping.wait()

    def stop(self) -> None:
        self._stopping.set()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        channel = FsChannel(reader, writer)
        host = channel.peer_host
        if not host_allowed(host, self._config.security.allowed_hosts):
            logger.warning("Conexão de host não autorizado recusada: %s", host)
            await channel.close()
            return
        try:
            if not await accept_handshake(
                channel, self._config.security.shared_key, self._sandbox.shares_catalog()
            ):
                logger.warning("Autenticação falhou para %s", host)
                await channel.close()
                return
            logger.info("Cliente Foshar conectado: %s", host)
            await self._serve_loop(channel)
        except (ConnectionError, asyncio.IncompleteReadError, OSError, asyncio.TimeoutError) as exc:
            logger.info("Conexão com %s encerrada: %s", host, exc)
        finally:
            await channel.close()

    async def _serve_loop(self, channel: FsChannel) -> None:
        while True:
            message = await channel.receive()
            req_id = message.data.get("req_id")
            handler = _DISPATCH.get(message.type)
            if handler is None:
                await channel.send(
                    FsMessage(FsMsgType.FS_REPLY, {"req_id": req_id, "ok": False, "error": "operação desconhecida"})
                )
                continue
            try:
                result = handler(self._sandbox, message.data)
                reply = {"req_id": req_id, "ok": True, **result}
            except (SandboxError, OpError, OSError, KeyError, ValueError) as exc:
                reply = {"req_id": req_id, "ok": False, "error": str(exc) or type(exc).__name__}
            await channel.send(FsMessage(FsMsgType.FS_REPLY, reply))
