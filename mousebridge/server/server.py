"""Servidor MouseBridge: roda na máquina que possui mouse e teclado físicos.

Responsabilidades:

* aceitar a conexão TCP do cliente (com autenticação e allowlist);
* monitorar o cursor e, ao atingir a borda configurada, suprimir o input
  local e encaminhar eventos para o cliente;
* devolver o controle quando o cliente reporta retorno pela borda;
* heartbeat, clipboard e responder de descoberta UDP.

Concorrência: listeners pynput rodam em threads próprias; todo handoff
para o mundo asyncio é feito com ``loop.call_soon_threadsafe``. Eventos de
input entram numa fila e uma task dedicada os envia, mantendo a ordem.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from typing import Any

from mousebridge.clipboard import ClipboardSync
from mousebridge.config import AppConfig
from mousebridge.discovery import DiscoveryResponder
from mousebridge.discovery.beacon import get_local_ip
from mousebridge.mouse import EdgeDetector, ScreenInfo, get_virtual_screen, return_position
from mousebridge.transport import Message, MessageStream, MsgType
from mousebridge.transport.security import (
    build_server_ssl_context,
    host_allowed,
    make_challenge,
    verify_challenge,
)

logger = logging.getLogger(__name__)

StatusCallback = Callable[[dict[str, Any]], None]


class MouseBridgeServer:
    def __init__(self, config: AppConfig, on_status: StatusCallback | None = None) -> None:
        self._config = config
        self._on_status = on_status
        self._screen: ScreenInfo | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stream: MessageStream | None = None
        self._active: str = "local"  # "local" | "remote"
        self._event_queue: asyncio.Queue[Message] = asyncio.Queue(maxsize=2048)
        self._mouse_capture: Any = None
        self._keyboard_capture: Any = None
        self._clipboard = ClipboardSync(config.clipboard)
        self._stopping = asyncio.Event()
        self._tasks: list[asyncio.Task[None]] = []

    # ------------------------------------------------------------- ciclo

    async def run(self) -> None:
        from mousebridge.keyboard.capture import KeyboardCapture
        from mousebridge.mouse.capture import MouseCapture

        self._loop = asyncio.get_running_loop()
        self._screen = get_virtual_screen()
        detector = EdgeDetector(
            edge=self._config.layout.edge,
            screen=self._screen,
            margin=self._config.layout.switch_margin,
        )
        self._mouse_capture = MouseCapture(self._screen, detector)
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
            "Servidor escutando em %s:%d (TLS=%s, tela=%dx%d, borda=%s)",
            get_local_ip(),
            self._config.network.tcp_port,
            bool(ssl_context),
            self._screen.width,
            self._screen.height,
            self._config.layout.edge,
        )
        self._mouse_capture.start_monitor(self._edge_hit_from_thread)
        self._emit_status()

        try:
            await self._stopping.wait()
        finally:
            server.close()
            discovery.close()
            await self._disconnect_client()
            self._mouse_capture.stop()
            self._keyboard_capture.stop()
            logger.info("Servidor finalizado")

    def stop(self) -> None:
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._stopping.set)

    # -------------------------------------------------------- conexões

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        stream = MessageStream(reader, writer)
        host = stream.peer_host
        if not host_allowed(host, self._config.security.allowed_hosts):
            logger.warning("Conexão de host não autorizado recusada: %s", host)
            await stream.close()
            return
        if self._stream is not None:
            logger.warning("Já existe cliente conectado; recusando %s", host)
            await stream.close()
            return

        try:
            if not await self._authenticate(stream):
                await stream.close()
                return
        except (ConnectionError, asyncio.IncompleteReadError, OSError):
            await stream.close()
            return

        logger.info("Cliente conectado e autenticado: %s", host)
        self._stream = stream
        self._emit_status()
        self._clipboard.start(self._send_clipboard)
        sender = asyncio.create_task(self._sender_loop(stream), name="sender")
        heartbeat = asyncio.create_task(self._heartbeat_loop(stream), name="heartbeat")
        self._tasks = [sender, heartbeat]
        try:
            await self._receive_loop(stream)
        except (ConnectionError, asyncio.IncompleteReadError, OSError) as exc:
            logger.info("Conexão com %s perdida: %s", host, exc)
        finally:
            await self._disconnect_client()

    async def _authenticate(self, stream: MessageStream) -> bool:
        nonce = make_challenge()
        await stream.send(Message(MsgType.AUTH_CHALLENGE, {"nonce": nonce}))
        response = await asyncio.wait_for(stream.receive(), timeout=10.0)
        if response.type != MsgType.AUTH_RESPONSE or not verify_challenge(
            self._config.security.shared_key, nonce, str(response.data.get("digest", ""))
        ):
            logger.warning("Autenticação falhou para %s", stream.peer_host)
            await stream.send(Message(MsgType.AUTH_FAIL, {"reason": "chave inválida"}))
            return False
        assert self._screen is not None
        await stream.send(Message(MsgType.AUTH_OK, {"screen": self._screen.to_dict()}))
        info = await asyncio.wait_for(stream.receive(), timeout=10.0)
        if info.type != MsgType.SCREEN_INFO:
            return False
        logger.info("Tela remota: %s", info.data.get("screen"))
        return True

    async def _disconnect_client(self) -> None:
        for task in self._tasks:
            task.cancel()
        self._tasks = []
        self._clipboard.stop()
        stream, self._stream = self._stream, None
        if stream is not None:
            await stream.close()
        if self._active == "remote":
            # Cliente caiu com o controle do lado de lá: recupera o input local.
            await self._return_to_local(ratio=0.5)
        self._emit_status()

    # ------------------------------------------------- troca de controle

    def _edge_hit_from_thread(self, ratio: float) -> None:
        assert self._loop is not None
        self._loop.call_soon_threadsafe(self._schedule_enter_remote, ratio)

    def _schedule_enter_remote(self, ratio: float) -> None:
        if self._active == "local" and self._stream is not None:
            asyncio.create_task(self._enter_remote(ratio))

    async def _enter_remote(self, ratio: float) -> None:
        if self._active != "local" or self._stream is None:
            return
        self._active = "remote"
        logger.info("Controle transferido para o cliente (ratio=%.2f)", ratio)
        try:
            await self._stream.send(
                Message(MsgType.ENTER, {"edge": self._config.layout.edge, "ratio": ratio})
            )
        except (ConnectionError, OSError):
            self._active = "local"
            return
        self._mouse_capture.start_forward(
            on_move=self._forward(MsgType.MOUSE_MOVE, "dx", "dy"),
            on_button=self._forward(MsgType.MOUSE_BUTTON, "button", "pressed"),
            on_scroll=self._forward(MsgType.MOUSE_SCROLL, "dx", "dy"),
        )
        self._keyboard_capture.start(self._forward(MsgType.KEY_EVENT, "key", "pressed"))
        self._emit_status()

    async def _return_to_local(self, ratio: float) -> None:
        if self._active != "remote":
            return
        self._active = "local"
        self._keyboard_capture.stop()
        assert self._screen is not None
        x, y = return_position(
            self._config.layout.edge, ratio, self._screen, inset=self._config.layout.return_inset
        )
        self._mouse_capture.start_monitor(self._edge_hit_from_thread)
        self._mouse_capture.set_position(x, y)
        logger.info("Controle retornou para a máquina local (ratio=%.2f)", ratio)
        self._emit_status()

    # --------------------------------------------------- envio/recepção

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

    async def _sender_loop(self, stream: MessageStream) -> None:
        while True:
            message = await self._event_queue.get()
            if self._active != "remote":
                continue  # evento atrasado de uma sessão remota já encerrada
            await stream.send(message)

    async def _receive_loop(self, stream: MessageStream) -> None:
        while True:
            message = await stream.receive()
            match message.type:
                case MsgType.LEAVE:
                    await self._return_to_local(float(message.data.get("ratio", 0.5)))
                case MsgType.CLIPBOARD:
                    await self._clipboard.apply_remote(str(message.data.get("text", "")))
                case MsgType.PONG:
                    pass  # last_received já atualizado pelo stream
                case _:
                    logger.debug("Mensagem inesperada do cliente: %s", message.type)

    async def _heartbeat_loop(self, stream: MessageStream) -> None:
        interval = self._config.network.heartbeat_interval
        timeout = self._config.network.heartbeat_timeout
        loop = asyncio.get_running_loop()
        while True:
            await asyncio.sleep(interval)
            if loop.time() - stream.last_received > timeout:
                logger.warning("Heartbeat expirou (%.1fs sem resposta)", timeout)
                await stream.close()
                return
            with contextlib.suppress(ConnectionError, OSError):
                await stream.send(Message(MsgType.PING, {}))

    async def _send_clipboard(self, text: str) -> None:
        if self._stream is not None:
            with contextlib.suppress(ConnectionError, OSError):
                await self._stream.send(Message(MsgType.CLIPBOARD, {"text": text}))

    # ------------------------------------------------------------ status

    def _emit_status(self) -> None:
        if self._on_status is None:
            return
        self._on_status(
            {
                "role": "server",
                "connected": self._stream is not None,
                "local_ip": get_local_ip(),
                "remote_ip": self._stream.peer_host if self._stream else None,
                "active": "remoto" if self._active == "remote" else "local",
                "edge": self._config.layout.edge,
            }
        )
