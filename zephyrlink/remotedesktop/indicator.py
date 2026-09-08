"""Indicador visível de compartilhamento de tela na máquina-alvo.

Enquanto a tela é compartilhada, o dono da máquina PRECISA ver que está sendo
observado/controlado — é um requisito de segurança, não cosmético. Sobe uma
faixa topmost (processo ``_banner``) e registra um aviso repetido no log. A
faixa é um subprocesso próprio porque o cliente roda headless num loop asyncio,
onde o Tk é instável. Se o subprocesso falhar (sem display, etc.), o log
periódico mantém o rastro.
"""

from __future__ import annotations

import logging
import subprocess
import sys
import threading

logger = logging.getLogger(__name__)

_HEARTBEAT_SECONDS = 30.0


class ScreenShareIndicator:
    def __init__(self, host: str, enabled: bool = True) -> None:
        self._host = host
        self._enabled = enabled
        self._proc: subprocess.Popen[bytes] | None = None
        self._stop = threading.Event()
        self._heartbeat: threading.Thread | None = None

    def show(self) -> None:
        logger.warning("TELA SENDO COMPARTILHADA/CONTROLADA remotamente por %s", self._host)
        if not self._enabled:
            return
        self._spawn_banner()
        self._stop.clear()
        self._heartbeat = threading.Thread(target=self._heartbeat_loop, daemon=True)
        self._heartbeat.start()

    def _spawn_banner(self) -> None:
        try:
            self._proc = subprocess.Popen(
                [sys.executable, "-m", "zephyrlink.remotedesktop._banner", self._host],
                stdin=subprocess.PIPE,
            )
        except OSError as exc:
            logger.warning("Não foi possível exibir a faixa de compartilhamento: %s", exc)
            self._proc = None

    def _heartbeat_loop(self) -> None:
        while not self._stop.wait(_HEARTBEAT_SECONDS):
            logger.warning("Compartilhamento de tela ATIVO (controlado por %s)", self._host)

    def hide(self) -> None:
        self._stop.set()
        proc, self._proc = self._proc, None
        if proc is not None and proc.poll() is None:
            # Fechar o stdin pede saída limpa; termina se não obedecer.
            try:
                if proc.stdin is not None:
                    proc.stdin.close()
            except OSError:
                pass
            try:
                proc.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                proc.terminate()
        logger.info("Compartilhamento de tela encerrado (era controlado por %s)", self._host)
