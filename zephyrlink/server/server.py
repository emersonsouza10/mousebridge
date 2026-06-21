"""Servidor ZephyrLink: roda na máquina que possui mouse e teclado físicos.

Suporta topologia em **estrela**: a máquina principal no centro e até um
cliente por borda (esquerda, direita, superior, inferior). Cada cliente
declara, ao conectar, qual borda do servidor ele ocupa; o servidor monta
o mapa ``borda → cliente`` dinamicamente e, quando o cursor atinge uma
borda com cliente, encaminha o controle para aquele cliente.

Responsabilidades:

* aceitar conexões TCP dos clientes (autenticação + allowlist);
* vigiar o cursor e, ao atingir a borda de um cliente, suprimir o input
  local e encaminhar eventos para esse cliente;
* devolver o controle quando o cliente ativo reporta retorno pela borda;
* heartbeat por cliente, clipboard sincronizado entre todas as máquinas e
  responder de descoberta UDP.

Concorrência: listeners pynput rodam em threads próprias; todo handoff
para o mundo asyncio é feito com ``loop.call_soon_threadsafe``. Eventos de
input entram numa fila e uma task dedicada os envia ao cliente ativo,
mantendo a ordem.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from zephyrlink.clipboard.sync import ClipboardSync
from zephyrlink.clipboard.transfer import send_files
from zephyrlink.config import AppConfig
from zephyrlink.config.settings import VALID_EDGES
from zephyrlink.discovery import DiscoveryResponder
from zephyrlink.discovery.beacon import get_local_ip
from zephyrlink.mouse import EdgeDetector, ScreenInfo, get_virtual_screen, return_position
from zephyrlink.transport import Message, MessageStream, MsgType, coalesce_moves
from zephyrlink.transport.security import (
    build_server_ssl_context,
    host_allowed,
    make_challenge,
    verify_challenge,
)

logger = logging.getLogger(__name__)

StatusCallback = Callable[[dict[str, Any]], None]


# Ordem em que bordas livres são atribuídas a clientes que conectam.
ASSIGN_ORDER = ("right", "left", "bottom", "top")


@dataclass(slots=True)
class ClientSession:
    """Uma conexão de cliente ativa. ``edge`` é a borda do servidor que ela
    ocupa no momento (``None`` enquanto não houver borda livre); o servidor é
    quem decide e pode remapear em runtime."""

    cid: int
    host: str
    stream: MessageStream
    screen: dict[str, int]
    edge: str | None = None
    catalog: list[dict[str, str]] = field(default_factory=list)
    tasks: list[asyncio.Task[None]] = field(default_factory=list)


class ZephyrLinkServer:
    def __init__(self, config: AppConfig, on_status: StatusCallback | None = None) -> None:
        self._config = config
        self._on_status = on_status
        self._screen: ScreenInfo | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._clients: dict[int, ClientSession] = {}
        self._edges: dict[str, int] = {}
        self._next_cid: int = 0
        self._active_edge: str | None = None
        self._event_queue: asyncio.Queue[Message] = asyncio.Queue(maxsize=2048)
        self._mouse_capture: Any = None
        self._keyboard_capture: Any = None
        self._clipboard = ClipboardSync(config.clipboard)
        self._launches: dict[str, dict[str, Any]] = {}
        self._stopping = asyncio.Event()

    async def run(self) -> None:
        from zephyrlink.keyboard.capture import KeyboardCapture
        from zephyrlink.mouse.capture import MouseCapture

        self._loop = asyncio.get_running_loop()
        self._screen = get_virtual_screen()
        self._mouse_capture = MouseCapture(self._screen)
        self._keyboard_capture = KeyboardCapture()

        discovery = await DiscoveryResponder.start(
            self._config.name,
            self._config.network.tcp_port,
            self._config.network.discovery_port,
        )
        ssl_context = build_server_ssl_context(self._config.security)
        server = await asyncio.start_server(
            self._handle_client,
            host="0.0.0.0",
            port=self._config.network.tcp_port,
            ssl=ssl_context,
        )
        logger.info(
            "Servidor escutando em %s:%d (TLS=%s, tela=%dx%d) — aguardando clientes",
            get_local_ip(),
            self._config.network.tcp_port,
            bool(ssl_context),
            self._screen.width,
            self._screen.height,
        )
        sender = asyncio.create_task(self._sender_loop(), name="sender")
        self._clipboard.start(self._on_local_clipboard, self._on_local_files)
        self._emit_status()

        try:
            await self._stopping.wait()
        finally:
            sender.cancel()
            server.close()
            discovery.close()
            self._clipboard.stop()
            for cid in list(self._clients):
                await self._drop_client(cid, return_local=False)
            self._mouse_capture.stop()
            self._keyboard_capture.stop()
            logger.info("Servidor finalizado")

    def stop(self) -> None:
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._stopping.set)

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        stream = MessageStream(reader, writer)
        host = stream.peer_host
        if not host_allowed(host, self._config.security.allowed_hosts):
            logger.warning("Conexão de host não autorizado recusada: %s", host)
            await stream.close()
            return

        try:
            session = await self._authenticate(stream, host)
        except (ConnectionError, asyncio.IncompleteReadError, OSError):
            await stream.close()
            return
        if session is None:
            await stream.close()
            return

        self._clients[session.cid] = session
        edge = self._first_free_edge()
        if edge is not None:
            self._edges[edge] = session.cid
            session.edge = edge
        logger.info(
            "Cliente conectado: %s %s (%d cliente(s) ativo(s))",
            host,
            f"na borda '{edge}'" if edge else "(sem borda livre; arraste na GUI)",
            len(self._clients),
        )
        self._refresh_monitor()
        self._emit_status()

        heartbeat = asyncio.create_task(self._heartbeat_loop(session), name=f"hb-{session.cid}")
        session.tasks = [heartbeat]
        try:
            await self._receive_loop(session)
        except (ConnectionError, asyncio.IncompleteReadError, OSError) as exc:
            logger.info("Conexão com %s (borda '%s') perdida: %s", host, session.edge, exc)
        finally:
            await self._drop_client(session.cid)

    async def _authenticate(self, stream: MessageStream, host: str) -> ClientSession | None:
        nonce = make_challenge()
        await stream.send(Message(MsgType.AUTH_CHALLENGE, {"nonce": nonce}))
        response = await asyncio.wait_for(stream.receive(), timeout=10.0)
        if response.type != MsgType.AUTH_RESPONSE or not verify_challenge(
            self._config.security.shared_key, nonce, str(response.data.get("digest", ""))
        ):
            logger.warning("Autenticação falhou para %s", host)
            await stream.send(Message(MsgType.AUTH_FAIL, {"reason": "chave inválida"}))
            return None
        assert self._screen is not None
        from zephyrlink.keyboard.layout import current_layout_id

        await stream.send(
            Message(
                MsgType.AUTH_OK,
                {"screen": self._screen.to_dict(), "layout": current_layout_id()},
            )
        )

        info = await asyncio.wait_for(stream.receive(), timeout=10.0)
        if info.type != MsgType.SCREEN_INFO:
            return None
        logger.info("Tela remota de %s: %s", host, info.data.get("screen"))
        cid = self._next_cid
        self._next_cid += 1
        return ClientSession(cid=cid, host=host, stream=stream, screen=info.data.get("screen") or {})

    async def _drop_client(self, cid: int, return_local: bool = True) -> None:
        session = self._clients.pop(cid, None)
        if session is None:
            return
        for task in session.tasks:
            task.cancel()
        await session.stream.close()
        edge = session.edge
        if edge is not None and self._edges.get(edge) == cid:
            del self._edges[edge]
        if return_local and edge is not None and self._active_edge == edge:
            await self._return_to_local(ratio=0.5)
        else:
            self._refresh_monitor()
        logger.info(
            "Cliente %s (borda '%s') desconectado (%d restante(s))",
            session.host, edge, len(self._clients),
        )
        self._emit_status()

    def _first_free_edge(self) -> str | None:
        for edge in ASSIGN_ORDER:
            if edge not in self._edges:
                return edge
        return None

    def assign_edge(self, cid: int, edge: str | None) -> None:
        """Reatribui (ou desencaixa) um cliente a uma borda. Thread-safe:
        chamável pela GUI, que roda em thread separada."""
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._assign_edge, cid, edge)

    def _assign_edge(self, cid: int, edge: str | None) -> None:
        session = self._clients.get(cid)
        if session is None or (edge is not None and edge not in VALID_EDGES):
            return
        if session.edge is not None and self._edges.get(session.edge) == cid:
            del self._edges[session.edge]
        if edge is not None:
            occupant = self._edges.get(edge)
            if occupant is not None and occupant != cid:
                # Desencaixa o ocupante anterior; ele fica sem borda.
                self._clients[occupant].edge = None
            self._edges[edge] = cid
        session.edge = edge
        logger.info("Cliente %s movido para a borda '%s'", session.host, edge)
        self._refresh_monitor()
        self._emit_status()

    def _refresh_monitor(self) -> None:
        """Reconstrói a vigília de bordas a partir dos clientes conectados.

        Só atua em modo local; em modo encaminhamento o listener é o de
        captura (forward), recriado ao retornar o controle.
        """
        if self._active_edge is not None or self._mouse_capture is None:
            return
        assert self._screen is not None
        detectors = [
            EdgeDetector(edge=edge, screen=self._screen, margin=self._config.layout.switch_margin)
            for edge in self._edges
        ]
        if detectors:
            self._mouse_capture.start_monitor(detectors, self._edge_hit_from_thread)
        else:
            self._mouse_capture.stop()

    def _edge_hit_from_thread(self, edge: str, ratio: float) -> None:
        assert self._loop is not None
        self._loop.call_soon_threadsafe(self._schedule_enter_remote, edge, ratio)

    def _schedule_enter_remote(self, edge: str, ratio: float) -> None:
        if self._active_edge is None and edge in self._edges:
            asyncio.create_task(self._enter_remote(edge, ratio))

    async def _enter_remote(self, edge: str, ratio: float) -> None:
        if self._active_edge is not None or edge not in self._edges:
            return
        session = self._clients[self._edges[edge]]
        self._active_edge = edge
        logger.info("Controle transferido para cliente '%s' (ratio=%.2f)", edge, ratio)
        try:
            await session.stream.send(Message(MsgType.ENTER, {"edge": edge, "ratio": ratio}))
        except (ConnectionError, OSError):
            self._active_edge = None
            return
        self._mouse_capture.start_forward(
            on_move=self._forward(MsgType.MOUSE_MOVE, "dx", "dy"),
            on_button=self._forward(MsgType.MOUSE_BUTTON, "button", "pressed"),
            on_scroll=self._forward(MsgType.MOUSE_SCROLL, "dx", "dy"),
        )
        self._keyboard_capture.start(self._forward(MsgType.KEY_EVENT, "key", "pressed"))
        self._emit_status()

    async def _return_to_local(self, ratio: float) -> None:
        if self._active_edge is None:
            return
        edge = self._active_edge
        self._active_edge = None
        self._keyboard_capture.stop()
        assert self._screen is not None
        x, y = return_position(
            edge, ratio, self._screen, inset=self._config.layout.return_inset
        )
        self._refresh_monitor()
        self._mouse_capture.set_position(x, y)
        logger.info("Controle retornou para a máquina local (da borda '%s', ratio=%.2f)", edge, ratio)
        self._emit_status()

    def _forward(self, msg_type: MsgType, *fields: str) -> Callable[..., None]:
        """Cria callback de captura que enfileira o evento (thread-safe)."""
        assert self._loop is not None
        loop = self._loop

        def callback(*values: Any) -> None:
            message = Message(msg_type, dict(zip(fields, values)))
            loop.call_soon_threadsafe(self._enqueue_event, message)

        return callback

    def _enqueue_event(self, message: Message) -> None:
        try:
            self._event_queue.put_nowait(message)
        except asyncio.QueueFull:
            logger.warning("Fila de eventos cheia, evento descartado")

    async def _sender_loop(self) -> None:
        while True:
            batch = [await self._event_queue.get()]
            # Drena sem aguardar tudo que se acumulou enquanto o envio
            # anterior estava em voo: sob movimento rápido (mouse a 125Hz+)
            # vários eventos chegam entre dois envios e serão coalescidos.
            while True:
                try:
                    batch.append(self._event_queue.get_nowait())
                except asyncio.QueueEmpty:
                    break
            edge = self._active_edge
            if edge is None:
                continue  # evento atrasado de uma sessão remota já encerrada
            cid = self._edges.get(edge)
            session = self._clients.get(cid) if cid is not None else None
            if session is None:
                continue
            with contextlib.suppress(ConnectionError, OSError):
                await session.stream.send_many(coalesce_moves(batch))

    async def _receive_loop(self, session: ClientSession) -> None:
        while True:
            message = await session.stream.receive()
            match message.type:
                case MsgType.LEAVE:
                    if self._active_edge == session.edge:
                        await self._return_to_local(float(message.data.get("ratio", 0.5)))
                case MsgType.CLIPBOARD:
                    text = str(message.data.get("text", ""))
                    await self._clipboard.apply_remote(text)
                    await self._broadcast_clipboard(text, exclude_cid=session.cid)
                case MsgType.FILE_OFFER | MsgType.FILE_DATA | MsgType.FILE_END:
                    await self._clipboard.apply_file_message(message)
                    await self._broadcast_message(message, exclude_cid=session.cid)
                case MsgType.LAUNCH_CATALOG:
                    session.catalog = list(message.data.get("apps") or [])
                    logger.info("Catálogo de '%s': %d app(s)", session.host, len(session.catalog))
                    self._emit_status()
                case MsgType.LAUNCH_ACK:
                    self._on_launch_ack(message.data)
                case MsgType.LAUNCH_RESULT:
                    self._on_launch_result(message.data)
                case MsgType.PONG:
                    pass  # last_received já atualizado pelo stream
                case _:
                    logger.debug("Mensagem inesperada de '%s': %s", session.edge, message.type)

    async def _heartbeat_loop(self, session: ClientSession) -> None:
        interval = self._config.network.heartbeat_interval
        timeout = self._config.network.heartbeat_timeout
        loop = asyncio.get_running_loop()
        while True:
            await asyncio.sleep(interval)
            if loop.time() - session.stream.last_received > timeout:
                logger.warning("Heartbeat de '%s' expirou (%.1fs)", session.edge, timeout)
                await session.stream.close()
                return
            with contextlib.suppress(ConnectionError, OSError):
                await session.stream.send(Message(MsgType.PING, {}))

    async def _on_local_clipboard(self, text: str) -> None:
        await self._broadcast_clipboard(text)

    async def _broadcast_clipboard(self, text: str, exclude_cid: int | None = None) -> None:
        await self._broadcast_message(Message(MsgType.CLIPBOARD, {"text": text}), exclude_cid)

    async def _broadcast_message(self, message: Message, exclude_cid: int | None = None) -> None:
        for cid, session in list(self._clients.items()):
            if cid == exclude_cid:
                continue
            with contextlib.suppress(ConnectionError, OSError):
                await session.stream.send(message)

    async def _on_local_files(self, paths: list[str]) -> None:
        if not self._clients:
            return
        await send_files(paths, self._broadcast_message, max_total=self._config.clipboard.file_max_bytes)

    def launch_app(self, cid: int, app_id: str, args: list[str] | None = None) -> None:
        """Pede a um cliente que abra ``app_id``. Thread-safe (chamável pela GUI)."""
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._start_launch, cid, app_id, list(args or []))

    def _start_launch(self, cid: int, app_id: str, args: list[str]) -> None:
        session = self._clients.get(cid)
        if session is None:
            return
        label = next((a["label"] for a in session.catalog if a.get("id") == app_id), app_id)
        req_id = secrets.token_hex(4)
        record = {
            "req_id": req_id, "cid": cid, "host": session.host, "app_id": app_id,
            "label": label, "args": args, "state": "sent", "ts": time.time(),
            "pid": None, "error": None,
        }
        self._launches[req_id] = record
        while len(self._launches) > 100:
            del self._launches[next(iter(self._launches))]
        logger.info("Launch solicitado: %s em %s (req %s)", app_id, session.host, req_id)
        asyncio.create_task(self._send_launch(session, req_id, app_id, args))
        self._emit_status()

    async def _send_launch(
        self, session: ClientSession, req_id: str, app_id: str, args: list[str]
    ) -> None:
        try:
            await session.stream.send(
                Message(
                    MsgType.LAUNCH_REQUEST,
                    {"req_id": req_id, "app_id": app_id, "args": args, "ts": time.time()},
                )
            )
        except (ConnectionError, OSError):
            self._set_launch(req_id, state="failed", error="conexão perdida")
            return
        await asyncio.sleep(5.0)
        record = self._launches.get(req_id)
        if record is not None and record["state"] == "sent":
            logger.warning("Launch sem confirmação em 5s (req %s)", req_id)
            self._set_launch(req_id, state="timeout")

    def _on_launch_ack(self, data: dict[str, Any]) -> None:
        req_id = str(data.get("req_id", ""))
        if data.get("accepted"):
            self._set_launch(req_id, state="received")
        else:
            self._set_launch(req_id, state="rejected", error=str(data.get("reason") or ""))

    def _on_launch_result(self, data: dict[str, Any]) -> None:
        state = str(data.get("state", ""))
        if state not in ("launching", "completed", "failed"):
            return
        self._set_launch(
            str(data.get("req_id", "")), state=state, pid=data.get("pid"), error=data.get("error")
        )

    def _set_launch(self, req_id: str, **fields: Any) -> None:
        record = self._launches.get(req_id)
        if record is None:
            return
        record.update(fields)
        self._emit_status()

    def _emit_status(self) -> None:
        if self._on_status is None:
            return
        clients = [
            {"cid": s.cid, "host": s.host, "edge": s.edge, "screen": s.screen, "catalog": s.catalog}
            for s in self._clients.values()
        ]
        self._on_status(
            {
                "role": "server",
                "connected": bool(self._clients),
                "local_ip": get_local_ip(),
                "remote_ip": ", ".join(s.host for s in self._clients.values()) or None,
                "active": f"cliente '{self._active_edge}'" if self._active_edge else "local",
                "clients": clients,
                "launches": sorted(self._launches.values(), key=lambda r: r["ts"]),
            }
        )
