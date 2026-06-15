"""Framing de mensagens sobre TCP.

TCP é um stream de bytes sem fronteiras; cada mensagem é prefixada por um
header de 4 bytes (uint32 big-endian) com o tamanho do payload. O decoder é
incremental para lidar com leituras parciais e mensagens coladas.
"""

from __future__ import annotations

import asyncio
import struct

HEADER = struct.Struct(">I")
MAX_FRAME_SIZE = 32 * 1024 * 1024  # protege contra payloads absurdos/corrompidos


class FrameError(Exception):
    """Frame inválido (tamanho fora do limite)."""


def encode_frame(payload: bytes) -> bytes:
    if len(payload) > MAX_FRAME_SIZE:
        raise FrameError(f"payload de {len(payload)} bytes excede o máximo de {MAX_FRAME_SIZE}")
    return HEADER.pack(len(payload)) + payload


class FrameDecoder:
    """Decoder incremental: alimente bytes, receba payloads completos."""

    def __init__(self) -> None:
        self._buffer = bytearray()

    def feed(self, data: bytes) -> list[bytes]:
        self._buffer.extend(data)
        frames: list[bytes] = []
        while True:
            if len(self._buffer) < HEADER.size:
                break
            (length,) = HEADER.unpack_from(self._buffer)
            if length > MAX_FRAME_SIZE:
                raise FrameError(f"frame anuncia {length} bytes, máximo é {MAX_FRAME_SIZE}")
            if len(self._buffer) < HEADER.size + length:
                break
            start = HEADER.size
            frames.append(bytes(self._buffer[start : start + length]))
            del self._buffer[: start + length]
        return frames


async def read_frame(reader: asyncio.StreamReader) -> bytes:
    header = await reader.readexactly(HEADER.size)
    (length,) = HEADER.unpack(header)
    if length > MAX_FRAME_SIZE:
        raise FrameError(f"frame anuncia {length} bytes, máximo é {MAX_FRAME_SIZE}")
    return await reader.readexactly(length)


async def write_frame(writer: asyncio.StreamWriter, payload: bytes) -> None:
    writer.write(encode_frame(payload))
    await writer.drain()
