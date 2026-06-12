"""Cliente ZephyrLink: roda na máquina secundária (controlada remotamente).

Conecta ao servidor (por descoberta UDP ou IP manual), autentica e injeta
os eventos recebidos. Detecta o retorno do controle: quando um movimento
tenta cruzar a borda voltada para a máquina principal, envia LEAVE com a
posição relativa para o servidor reposicionar o cursor.

Reconexão automática: qualquer queda de conexão volta ao laço de
descoberta/conexão após ``reconnect_delay`` segundos.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from typing import Any

from zephyrlink.clipboard import ClipboardSync
from zephyrlink.config import AppConfig
from zephyrlink.discovery import discover_server
from zephyrlink.discovery.beacon import get_local_ip
from zephyrlink.keyboard.layout import activate_layout
from zephyrlink.mouse import ScreenInfo, entry_position, get_virtual_screen, opposite_edge
from zephyrlink.transport import Message, MessageStream, MsgType
from zephyrlink.transport.security import build_client_ssl_context, sign_challenge

logger = logging.getLogger(__name__)

StatusCallback = Callable[[dict[str, Any]], None]


class ZephyrLinkClient:
    def __init__(self, config: AppConfig, on_status: StatusCallback | None = None) -> None:
        self._config = config
        self._on_status = on_status
        self._screen: ScreenInfo | None = None
        self._mouse: Any = None
        self._keyboard: Any = None
        self._clipboard = ClipboardSync(config.clipboard)
        self._stream: MessageStream | None = None
        self._active = False
        self._return_edge: str | None = None
        self._server_layout: str | None = None
        self._stopping = asyncio.Event()

    async def run(self) -> None:
        from zephyrlink.keyboard.injector import KeyboardInjector
        from zephyrlink.mouse.injector import MouseInjector

        self._screen = get_virtual_screen()
        self._mouse = MouseInjector(self._screen)
        self._keyboard = KeyboardInjector()
        logger.info("Cliente iniciado (tela=%dx%d)", self._screen.width, self._screen.height)

        while not self._stopping.is_set():
            endpoint = await self._resolve_server()
            if endpoint is None:
                await self._wait_retry()
                continue
            host, port = endpoint
            try:
                await self._session(host, port)
            except (ConnectionError, asyncio.IncompleteReadError, OSError, asyncio.TimeoutError) as exc:
                logger.info("Sessão encerrada (%s); reconectando...", exc or type(exc).__name__)
            finally:
                await self._teardown_session()
            await self._wait_retry()
        logger.info("Cliente finalizado")

    def stop(self) -> None:
        self._stopping.set()

    async def _wait_retry(self) -> None:
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(
                self._stopping.wait(), timeout=self._config.network.reconnect_delay
            )

    async def _resolve_server(self) -> tuple[str, int] | None:
        if self._config.network.manual_host:
            return (self._config.network.manual_host, self._config.network.tcp_port)
        logger.info("Procurando servidor via broadcast UDP...")
        return await discover_server(
            self._config.network.discovery_port,
            timeout=self._config.network.discovery_timeout,
        )

    async def _session(self, host: str, port: int) -> None:
        logger.info("Conectando a %s:%d...", host, port)
        ssl_context = build_client_ssl_context(self._config.security)
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port, ssl=ssl_context), timeout=10.0
        )
        stream = MessageStream(reader, writer)

        challenge = await asyncio.wait_for(stream.receive(), timeout=10.0)
        if challenge.type != MsgType.AUTH_CHALLENGE:
            raise ConnectionError(f"esperava desafio de autenticação, veio {challenge.type}")
        digest = sign_challenge(self._config.security.shared_key, str(challenge.data["nonce"]))
        await stream.send(Message(MsgType.AUTH_RESPONSE, {"digest": digest}))
        result = await asyncio.wait_for(stream.receive(), timeout=10.0)
        if result.type != MsgType.AUTH_OK:
            logger.error("Autenticação recusada pelo servidor (verifique shared_key)")
            await stream.close()
            await self._wait_retry()
            return
        self._server_layout = result.data.get("layout")
        assert self._screen is not None
        await stream.send(
            Message(
                MsgType.SCREEN_INFO,
                {"screen": self._screen.to_dict(), "edge": self._config.layout.edge},
            )
        )

        logger.info("Conectado a %s:%d na borda '%s' do servidor", host, port, self._config.layout.edge)
        self._stream = stream
        self._emit_status()
        self._clipboard.start(self._send_clipboard)
        watchdog = asyncio.create_task(self._watchdog_loop(stream), name="watchdog")
        try:
            await self._receive_loop(stream)
        finally:
            watchdog.cancel()

    async def _teardown_session(self) -> None:
        self._clipboard.stop()
        if self._keyboard is not None:
            self._keyboard.release_all()
        self._active = False
        stream, self._stream = self._stream, None
        if stream is not None:
            await stream.close()
        self._emit_status()

    async def _receive_loop(self, stream: MessageStream) -> None:
        while True:
            message = await stream.receive()
            match message.type:
                case MsgType.MOUSE_MOVE:
                    self._handle_move(int(message.data["dx"]), int(message.data["dy"]))
                case MsgType.MOUSE_BUTTON if self._active:
                    self._mouse.button(str(message.data["button"]), bool(message.data["pressed"]))
                case MsgType.MOUSE_SCROLL if self._active:
                    self._mouse.scroll(int(message.data["dx"]), int(message.data["dy"]))
                case MsgType.KEY_EVENT if self._active:
                    self._keyboard.key_event(message.data["key"], bool(message.data["pressed"]))
                case MsgType.ENTER:
                    self._handle_enter(str(message.data["edge"]), float(message.data["ratio"]))
                case MsgType.CLIPBOARD:
                    await self._clipboard.apply_remote(str(message.data.get("text", "")))
                case MsgType.PING:
                    await stream.send(Message(MsgType.PONG, {}))
                case MsgType.AUTH_FAIL:
                    reason = str(message.data.get("reason", "desconhecido"))
                    raise ConnectionError(f"servidor recusou a conexão: {reason}")
                case _:
                    logger.debug("Mensagem ignorada: %s", message.type)

    def _handle_enter(self, server_edge: str, ratio: float) -> None:
        assert self._screen is not None
        self._return_edge = opposite_edge(server_edge)
        x, y = entry_position(self._return_edge, ratio, self._screen, inset=2)
        self._mouse.set_position(x, y)
        activate_layout(self._server_layout)
        self._active = True
        logger.info("Controle recebido (entrada pela borda %s)", self._return_edge)
        self._emit_status()

    def _handle_move(self, dx: int, dy: int) -> None:
        if not self._active:
            return
        x, y, over_x, over_y = self._mouse.move_by(dx, dy)
        ratio = self._check_return(x, y, over_x, over_y)
        if ratio is not None:
            asyncio.create_task(self._send_leave(ratio))

    def _check_return(self, x: int, y: int, over_x: int, over_y: int) -> float | None:
        """Se o movimento estourou a borda de retorno, devolve o ratio."""
        assert self._screen is not None
        s = self._screen
        match self._return_edge:
            case "left" if over_x < 0:
                return (y - s.y) / max(1, s.height - 1)
            case "right" if over_x > 0:
                return (y - s.y) / max(1, s.height - 1)
            case "top" if over_y < 0:
                return (x - s.x) / max(1, s.width - 1)
            case "bottom" if over_y > 0:
                return (x - s.x) / max(1, s.width - 1)
        return None

    async def _send_leave(self, ratio: float) -> None:
        if not self._active or self._stream is None:
            return
        self._active = False
        self._keyboard.release_all()
        logger.info("Controle devolvido ao servidor (ratio=%.2f)", ratio)
        with contextlib.suppress(ConnectionError, OSError):
            await self._stream.send(Message(MsgType.LEAVE, {"ratio": ratio}))
        self._emit_status()

    async def _watchdog_loop(self, stream: MessageStream) -> None:
        """Fecha a conexão se o servidor parar de enviar PINGs."""
        timeout = self._config.network.heartbeat_timeout
        loop = asyncio.get_running_loop()
        while True:
            await asyncio.sleep(self._config.network.heartbeat_interval)
            if loop.time() - stream.last_received > timeout:
                logger.warning("Servidor silencioso por %.1fs, derrubando conexão", timeout)
                await stream.close()
                return

    async def _send_clipboard(self, text: str) -> None:
        if self._stream is not None:
            with contextlib.suppress(ConnectionError, OSError):
                await self._stream.send(Message(MsgType.CLIPBOARD, {"text": text}))

    def _emit_status(self) -> None:
        if self._on_status is None:
            return
        self._on_status(
            {
                "role": "client",
                "connected": self._stream is not None,
                "local_ip": get_local_ip(),
                "remote_ip": self._stream.peer_host if self._stream else None,
                "active": "esta máquina" if self._active else "servidor",
                "edge": self._return_edge,
            }
        )
