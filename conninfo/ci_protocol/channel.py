"""Canal dedicado da connInfo sobre TCP, reusando a pilha do ZephyrLink.

Framing (``framing.py``), autenticação HMAC e TLS (``security.py``) vêm prontos do
core — a connInfo só acrescenta o catálogo de conexões no ``AUTH_OK``. Conexão
separada da de input/foshar (porta própria), para não disputar nenhum socket.
"""

from __future__ import annotations

import asyncio
import contextlib
import socket

from typing import Any, Callable

from zephyrlink.transport.framing import read_frame, write_frame
from zephyrlink.transport.security import make_challenge, sign_challenge, verify_challenge

from conninfo.ci_protocol.messages import CiMessage, CiMsgType


class CiChannel:
    """Envia/recebe ``CiMessage`` sobre um par StreamReader/StreamWriter."""

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._reader = reader
        self._writer = writer
        self._send_lock = asyncio.Lock()
        sock = writer.get_extra_info("socket")
        if sock is not None:
            with contextlib.suppress(OSError):
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

    @property
    def peer_host(self) -> str:
        peer = self._writer.get_extra_info("peername")
        return peer[0] if peer else "?"

    async def send(self, message: CiMessage) -> None:
        async with self._send_lock:
            await write_frame(self._writer, message.encode())

    async def receive(self) -> CiMessage:
        return CiMessage.decode(await read_frame(self._reader))

    async def close(self) -> None:
        with contextlib.suppress(ConnectionError, OSError):
            self._writer.close()
            await self._writer.wait_closed()


async def accept_handshake(
    channel: CiChannel,
    shared_key: str,
    authorize: Callable[[str | None, str | None], "Any"],
    catalog: list[dict[str, str]],
) -> "Any":
    """Lado ProviderHost: prova de chave + identidade do agente + catálogo filtrado.

    ``authorize(user, token)`` devolve uma ``AclDecision`` (ou ``None`` se negado).
    Retorna a decisão em caso de sucesso, ou ``None`` se chave/ACL recusarem.
    """
    nonce = make_challenge()
    await channel.send(CiMessage(CiMsgType.AUTH_CHALLENGE, {"nonce": nonce}))
    response = await asyncio.wait_for(channel.receive(), timeout=10.0)
    if response.type != CiMsgType.AUTH_RESPONSE or not verify_challenge(
        shared_key, nonce, str(response.data.get("digest", ""))
    ):
        await channel.send(CiMessage(CiMsgType.AUTH_FAIL, {"reason": "chave inválida"}))
        return None
    decision = authorize(response.data.get("user"), response.data.get("token"))
    if decision is None:
        await channel.send(CiMessage(CiMsgType.AUTH_FAIL, {"reason": "acesso negado"}))
        return None
    await channel.send(CiMessage(CiMsgType.AUTH_OK, {"connections": decision.filter_catalog(catalog)}))
    return decision


async def connect_handshake(
    channel: CiChannel, shared_key: str, *, user: str | None = None, token: str | None = None
) -> list[dict[str, str]]:
    """Lado Gateway (agente): responde ao desafio (com identidade) e recebe o catálogo."""
    challenge = await asyncio.wait_for(channel.receive(), timeout=10.0)
    if challenge.type != CiMsgType.AUTH_CHALLENGE:
        raise ConnectionError("esperava desafio de autenticação")
    digest = sign_challenge(shared_key, str(challenge.data["nonce"]))
    await channel.send(CiMessage(CiMsgType.AUTH_RESPONSE, {"digest": digest, "user": user, "token": token}))
    result = await asyncio.wait_for(channel.receive(), timeout=10.0)
    if result.type != CiMsgType.AUTH_OK:
        reason = result.data.get("reason", "?") if result.type == CiMsgType.AUTH_FAIL else "?"
        raise ConnectionError(f"autenticação recusada ({reason})")
    return list(result.data.get("connections") or [])
