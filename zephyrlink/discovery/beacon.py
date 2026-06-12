"""Descoberta automática do servidor por broadcast UDP.

O cliente envia um datagrama de consulta em broadcast; o servidor, que
mantém um responder escutando na porta de descoberta, responde com nome e
porta TCP. O IP do servidor vem do próprio datagrama de resposta. Pacotes
são JSON com um campo mágico para ignorar tráfego alheio na porta.
"""

from __future__ import annotations

import asyncio
import json
import logging
import socket

logger = logging.getLogger(__name__)

MAGIC = "zephyrlink/1"


def build_query() -> bytes:
    return json.dumps({"magic": MAGIC, "op": "query"}).encode("utf-8")


def build_reply(name: str, tcp_port: int) -> bytes:
    return json.dumps({"magic": MAGIC, "op": "reply", "name": name, "port": tcp_port}).encode("utf-8")


def parse_packet(data: bytes) -> dict | None:
    """Retorna o pacote decodificado ou None se não for do ZephyrLink."""
    try:
        packet = json.loads(data.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(packet, dict) or packet.get("magic") != MAGIC:
        return None
    return packet


class DiscoveryResponder(asyncio.DatagramProtocol):
    """Roda no servidor: responde consultas de descoberta."""

    def __init__(self, name: str, tcp_port: int) -> None:
        self._name = name
        self._tcp_port = tcp_port
        self._transport: asyncio.DatagramTransport | None = None

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self._transport = transport  # type: ignore[assignment]

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        packet = parse_packet(data)
        if packet and packet.get("op") == "query" and self._transport is not None:
            logger.info("Consulta de descoberta de %s", addr[0])
            self._transport.sendto(build_reply(self._name, self._tcp_port), addr)

    @classmethod
    async def start(cls, name: str, tcp_port: int, discovery_port: int) -> asyncio.DatagramTransport:
        loop = asyncio.get_running_loop()
        transport, _ = await loop.create_datagram_endpoint(
            lambda: cls(name, tcp_port),
            local_addr=("0.0.0.0", discovery_port),
            allow_broadcast=True,
        )
        logger.info("Responder de descoberta na porta UDP %d", discovery_port)
        return transport


class _DiscoveryQuery(asyncio.DatagramProtocol):
    def __init__(self) -> None:
        self.result: asyncio.Future[tuple[str, int]] = asyncio.get_running_loop().create_future()

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        packet = parse_packet(data)
        if packet and packet.get("op") == "reply" and not self.result.done():
            self.result.set_result((addr[0], int(packet["port"])))


async def discover_server(discovery_port: int, timeout: float = 5.0) -> tuple[str, int] | None:
    """Procura o servidor na rede. Retorna ``(ip, porta_tcp)`` ou None.

    Reenvia o broadcast a cada segundo até obter resposta ou estourar o
    timeout (broadcasts UDP podem se perder).
    """
    loop = asyncio.get_running_loop()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.bind(("0.0.0.0", 0))
    transport, protocol = await loop.create_datagram_endpoint(_DiscoveryQuery, sock=sock)
    try:
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            transport.sendto(build_query(), ("255.255.255.255", discovery_port))
            try:
                remaining = min(1.0, deadline - loop.time())
                return await asyncio.wait_for(asyncio.shield(protocol.result), remaining)
            except asyncio.TimeoutError:
                continue
        logger.warning("Nenhum servidor encontrado em %.1fs", timeout)
        return None
    finally:
        transport.close()


def get_local_ip() -> str:
    """IP local usado para alcançar a rede (sem enviar nada de fato)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()
