"""Stream de mensagens tipadas sobre uma conexão TCP asyncio."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import socket
import time

from zephyrlink.transport.framing import encode_frame, read_frame, write_frame
from zephyrlink.transport.messages import Message

logger = logging.getLogger(__name__)


def _disable_nagle(writer: asyncio.StreamWriter) -> None:
    """Desativa o algoritmo de Nagle no socket TCP subjacente.

    Eventos de input são pacotes minúsculos (~40 bytes). Com Nagle ativo o
    kernel segura cada pacote esperando um ACK pendente ou mais dados,
    somando até ~40ms de latência por movimento — fatal para a fluidez do
    cursor. ``TCP_NODELAY`` envia imediatamente.
    """
    sock = writer.get_extra_info("socket")
    if sock is not None:
        with contextlib.suppress(OSError):
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)


class MessageStream:
    """Envia/recebe ``Message`` sobre um par StreamReader/StreamWriter.

    ``send`` é serializado por lock pois várias tasks (eventos de input,
    heartbeat, clipboard) escrevem na mesma conexão.
    """

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._reader = reader
        self._writer = writer
        self._send_lock = asyncio.Lock()
        self.last_received: float = time.monotonic()
        _disable_nagle(writer)

    @property
    def peer_host(self) -> str:
        peer = self._writer.get_extra_info("peername")
        return peer[0] if peer else "?"

    async def send(self, message: Message) -> None:
        async with self._send_lock:
            await write_frame(self._writer, message.encode())

    async def send_many(self, messages: list[Message]) -> None:
        """Escreve vários frames sob um único lock e drena uma só vez.

        Reduz o número de ``await drain()`` (e syscalls) quando o laço de
        envio coalesce uma rajada de eventos em um lote.
        """
        if not messages:
            return
        async with self._send_lock:
            for message in messages:
                self._writer.write(encode_frame(message.encode()))
            await self._writer.drain()

    async def receive(self) -> Message:
        payload = await read_frame(self._reader)
        self.last_received = time.monotonic()
        return Message.decode(payload)

    async def close(self) -> None:
        try:
            self._writer.close()
            await self._writer.wait_closed()
        except (ConnectionError, OSError):
            pass
