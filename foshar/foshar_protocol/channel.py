"""Canal dedicado do Foshar sobre TCP, reusando a pilha do ZephyrLink.

Usa o enquadramento de ``zephyrlink.transport.framing`` (header uint32 + payload)
e a autenticação de ``zephyrlink.transport.security`` (desafio-resposta HMAC). É
uma conexão **separada** da de input — arquivos nunca disputam o socket do mouse.
"""

from __future__ import annotations

import asyncio
import contextlib
import socket

from zephyrlink.transport.framing import read_frame, write_frame
from zephyrlink.transport.security import make_challenge, sign_challenge, verify_challenge

from foshar.foshar_protocol.messages import FsMessage, FsMsgType


class FsChannel:
    """Envia/recebe ``FsMessage`` sobre um par StreamReader/StreamWriter."""

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

    async def send(self, message: FsMessage) -> None:
        async with self._send_lock:
            await write_frame(self._writer, message.encode())

    async def receive(self) -> FsMessage:
        return FsMessage.decode(await read_frame(self._reader))

    async def close(self) -> None:
        with contextlib.suppress(ConnectionError, OSError):
            self._writer.close()
            await self._writer.wait_closed()


async def accept_handshake(
    channel: FsChannel, shared_key: str, shares_catalog: list[dict[str, str]]
) -> bool:
    """Lado dono: prova de chave e anúncio dos shares publicados."""
    nonce = make_challenge()
    await channel.send(FsMessage(FsMsgType.AUTH_CHALLENGE, {"nonce": nonce}))
    response = await asyncio.wait_for(channel.receive(), timeout=10.0)
    if response.type != FsMsgType.AUTH_RESPONSE or not verify_challenge(
        shared_key, nonce, str(response.data.get("digest", ""))
    ):
        await channel.send(FsMessage(FsMsgType.AUTH_FAIL, {"reason": "chave inválida"}))
        return False
    await channel.send(FsMessage(FsMsgType.AUTH_OK, {"shares": shares_catalog}))
    return True


async def connect_handshake(channel: FsChannel, shared_key: str) -> list[dict[str, str]]:
    """Lado requisitante: responde ao desafio e recebe o catálogo de shares."""
    challenge = await asyncio.wait_for(channel.receive(), timeout=10.0)
    if challenge.type != FsMsgType.AUTH_CHALLENGE:
        raise ConnectionError("esperava desafio de autenticação")
    digest = sign_challenge(shared_key, str(challenge.data["nonce"]))
    await channel.send(FsMessage(FsMsgType.AUTH_RESPONSE, {"digest": digest}))
    result = await asyncio.wait_for(channel.receive(), timeout=10.0)
    if result.type != FsMsgType.AUTH_OK:
        raise ConnectionError("autenticação recusada (verifique shared_key)")
    return list(result.data.get("shares") or [])
