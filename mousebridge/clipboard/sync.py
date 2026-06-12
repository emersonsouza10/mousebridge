"""Sincronização de área de transferência.

Não existe API de notificação de clipboard portátil, então a detecção é
por *polling* (pyperclip). As chamadas bloqueantes rodam em executor para
não travar o loop asyncio. Para evitar eco infinito (A envia → B aplica →
B detecta mudança → B reenvia → ...), o último texto aplicado remotamente
é lembrado e não é reenviado.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

import pyperclip

from mousebridge.config import ClipboardConfig

logger = logging.getLogger(__name__)

OnLocalChange = Callable[[str], Awaitable[None]]


class ClipboardSync:
    def __init__(self, config: ClipboardConfig) -> None:
        self._config = config
        self._last_seen: str | None = None
        self._task: asyncio.Task[None] | None = None

    def start(self, on_local_change: OnLocalChange) -> None:
        if not self._config.enabled or self._task is not None:
            return
        self._task = asyncio.create_task(self._poll_loop(on_local_change), name="clipboard-poll")
        logger.info("Sincronização de clipboard ativa (poll %.1fs)", self._config.poll_interval)

    def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None

    async def apply_remote(self, text: str) -> None:
        """Aplica texto recebido da outra máquina sem reenviá-lo."""
        if len(text.encode("utf-8")) > self._config.max_bytes:
            logger.warning("Clipboard remoto excede max_bytes, ignorado")
            return
        self._last_seen = text
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(None, pyperclip.copy, text)
            logger.debug("Clipboard remoto aplicado (%d chars)", len(text))
        except pyperclip.PyperclipException as exc:
            logger.warning("Falha ao aplicar clipboard: %s", exc)

    async def _poll_loop(self, on_local_change: OnLocalChange) -> None:
        loop = asyncio.get_running_loop()
        while True:
            try:
                text = await loop.run_in_executor(None, pyperclip.paste)
            except pyperclip.PyperclipException as exc:
                logger.debug("Leitura de clipboard falhou: %s", exc)
                text = None
            if (
                isinstance(text, str)
                and text != self._last_seen
                and 0 < len(text.encode("utf-8")) <= self._config.max_bytes
            ):
                first_read = self._last_seen is None
                self._last_seen = text
                # Conteúdo pré-existente na inicialização não é sincronizado.
                if not first_read:
                    logger.debug("Clipboard local mudou (%d chars), enviando", len(text))
                    await on_local_change(text)
            await asyncio.sleep(self._config.poll_interval)
