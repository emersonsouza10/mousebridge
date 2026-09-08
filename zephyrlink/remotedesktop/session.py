"""Sessão de compartilhamento de tela no lado do ALVO (cliente).

Uma thread dedicada captura + codifica frames (trabalho bloqueante fora do event
loop) e os entrega ao asyncio via ``call_soon_threadsafe``; uma task drena uma
fila pequena e envia pelo stream. A fila é limitada e descarta o frame mais
antigo quando cheia — sob rede/CPU lentas é melhor pular frames do que acumular
latência (o cursor remoto ficaria "no passado").

Encerramento por: ``RD_STOP`` do operador, queda de conexão, tecla de pânico
local ou ociosidade (``idle_timeout``).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
import time
from collections.abc import Callable

from zephyrlink.config import RemoteDesktopConfig
from zephyrlink.launcher.audit import AuditLog
from zephyrlink.remotedesktop.capture import FrameGrabber, monitor_region
from zephyrlink.remotedesktop.codec import encode_jpeg
from zephyrlink.remotedesktop.indicator import ScreenShareIndicator
from zephyrlink.transport import MessageStream, VideoFrame

logger = logging.getLogger(__name__)

_QUEUE_MAX = 2


class RemoteDesktopSession:
    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        stream: MessageStream,
        config: RemoteDesktopConfig,
        host: str,
        on_idle_stop: Callable[[], None] | None = None,
    ) -> None:
        self._loop = loop
        self._stream = stream
        self._host = host
        self._on_idle_stop = on_idle_stop
        self._params_lock = threading.Lock()
        self._fps = config.fps
        self._quality = config.quality
        self._scale = config.scale
        self._monitor = config.monitor
        self._idle_timeout = config.idle_timeout
        self._region = monitor_region(config.monitor)
        self._indicator = ScreenShareIndicator(host, enabled=config.indicator)
        self._audit = AuditLog(config.audit_file)
        self._stop_evt = threading.Event()
        self._worker: threading.Thread | None = None
        self._queue: asyncio.Queue[VideoFrame] = asyncio.Queue(maxsize=_QUEUE_MAX)
        self._sender: asyncio.Task[None] | None = None
        self._idle_task: asyncio.Task[None] | None = None
        self._last_input = time.monotonic()
        self._running = False

    @property
    def region(self) -> tuple[int, int, int, int]:
        """Caixa ``(left, top, width, height)`` que está sendo transmitida."""
        return self._region

    async def start(self) -> None:
        self._running = True
        self._indicator.show()
        await self._audit.write({"event": "rd_start", "host": self._host, "region": list(self._region)})
        self._sender = asyncio.create_task(self._sender_loop(), name="rd-sender")
        if self._idle_timeout > 0:
            self._idle_task = asyncio.create_task(self._idle_loop(), name="rd-idle")
        self._worker = threading.Thread(target=self._capture_loop, name="rd-capture", daemon=True)
        self._worker.start()
        logger.info("Compartilhamento de tela iniciado para %s (região %s)", self._host, self._region)

    async def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        self._stop_evt.set()
        for task in (self._sender, self._idle_task):
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        self._indicator.hide()
        await self._audit.write({"event": "rd_stop", "host": self._host})
        logger.info("Compartilhamento de tela encerrado para %s", self._host)

    def update_config(self, fps: int | None, quality: int | None, scale: float | None) -> None:
        """Aplica novos parâmetros em runtime (chamado ao receber VIDEO_CONFIG)."""
        with self._params_lock:
            if fps is not None:
                self._fps = max(1, min(60, fps))
            if quality is not None:
                self._quality = max(1, min(95, quality))
            if scale is not None:
                self._scale = max(0.05, min(1.0, scale))
        logger.info("Parâmetros de vídeo atualizados: fps=%s q=%s scale=%s", fps, quality, scale)

    def mark_input(self) -> None:
        """Registra atividade do operador (para o idle_timeout)."""
        self._last_input = time.monotonic()

    def _capture_loop(self) -> None:
        try:
            grabber = FrameGrabber(self._monitor)
        except Exception as exc:  # noqa: BLE001 - captura indisponível encerra a sessão
            logger.error("Captura de tela indisponível: %s", exc)
            self._loop.call_soon_threadsafe(self._fail)
            return
        seq = 0
        try:
            while not self._stop_evt.is_set():
                t0 = time.monotonic()
                with self._params_lock:
                    fps, quality, scale = self._fps, self._quality, self._scale
                try:
                    rgb, w, h = grabber.grab()
                    blob = encode_jpeg(rgb, w, h, quality, scale)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Falha ao capturar/codificar frame: %s", exc)
                    self._stop_evt.wait(0.5)
                    continue
                frame = VideoFrame(
                    {"w": w, "h": h, "fmt": "jpeg", "seq": seq, "scale": round(scale, 3)}, blob
                )
                seq += 1
                self._loop.call_soon_threadsafe(self._offer_frame, frame)
                elapsed = time.monotonic() - t0
                self._stop_evt.wait(max(0.0, (1.0 / fps) - elapsed))
        finally:
            grabber.close()

    def _fail(self) -> None:
        if self._on_idle_stop is not None:
            self._on_idle_stop()

    def _offer_frame(self, frame: VideoFrame) -> None:
        # Roda no event loop. Se a fila estiver cheia, descarta o mais antigo.
        if self._queue.full():
            with contextlib.suppress(asyncio.QueueEmpty):
                self._queue.get_nowait()
        with contextlib.suppress(asyncio.QueueFull):
            self._queue.put_nowait(frame)

    async def _sender_loop(self) -> None:
        while True:
            frame = await self._queue.get()
            with contextlib.suppress(ConnectionError, OSError):
                await self._stream.send_frame(frame)

    async def _idle_loop(self) -> None:
        while True:
            await asyncio.sleep(1.0)
            if time.monotonic() - self._last_input > self._idle_timeout:
                logger.info("Sessão remota ociosa por %.0fs; encerrando", self._idle_timeout)
                if self._on_idle_stop is not None:
                    self._on_idle_stop()
                return
