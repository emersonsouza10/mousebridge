"""Stream de mensagens tipadas sobre uma conexão TCP asyncio."""

from __future__ import annotations

import asyncio
import logging
import time

from zephyrlink.transport.framing import read_frame, write_frame
from zephyrlink.transport.messages import Message

logger = logging.getLogger(__name__)


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

    @property
    def peer_host(self) -> str:
        peer = self._writer.get_extra_info("peername")
        return peer[0] if peer else "?"

    async def send(self, message: Message) -> None:
        async with self._send_lock:
            await write_frame(self._writer, message.encode())

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
