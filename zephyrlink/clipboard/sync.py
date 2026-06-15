"""Sincronização de área de transferência (texto e arquivos).

Não existe API de notificação de clipboard portátil, então a detecção é
por *polling* (pyperclip para texto; CF_HDROP via ctypes para arquivos no
Windows). As chamadas bloqueantes rodam em executor para não travar o loop
asyncio. Para evitar eco infinito (A envia → B aplica → B detecta mudança →
B reenvia → ...), o último conteúdo aplicado/visto é lembrado e não é
reenviado.

Arquivos não cabem numa mensagem de texto: ao detectá-los localmente o
``on_local_files`` é chamado e quem o consome (servidor/cliente) usa
``transfer.send_files`` para enviá-los em pedaços; na recepção
``apply_file_message`` remonta e os coloca no clipboard local.
"""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from collections.abc import Awaitable, Callable

import pyperclip

from zephyrlink.clipboard.transfer import FileReceiver
from zephyrlink.clipboard.winfiles import read_clipboard_files, write_clipboard_files
from zephyrlink.config import ClipboardConfig
from zephyrlink.transport.messages import Message

logger = logging.getLogger(__name__)

OnLocalChange = Callable[[str], Awaitable[None]]
OnLocalFiles = Callable[[list[str]], Awaitable[None]]


def _files_signature(paths: list[str]) -> tuple[str, ...]:
    return tuple(sorted(os.path.normcase(os.path.abspath(p)) for p in paths))


class ClipboardSync:
    def __init__(self, config: ClipboardConfig) -> None:
        self._config = config
        self._last_seen: str | None = None
        self._last_files: tuple[str, ...] | None = None
        self._on_local_files: OnLocalFiles | None = None
        self._task: asyncio.Task[None] | None = None
        self._dest_base = os.path.join(tempfile.gettempdir(), "zephyrlink-files")
        self._receiver = FileReceiver(self._dest_base)

    def start(self, on_local_change: OnLocalChange, on_local_files: OnLocalFiles | None = None) -> None:
        if not self._config.enabled or self._task is not None:
            return
        self._on_local_files = on_local_files
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

    async def apply_file_message(self, message: Message) -> None:
        """Alimenta o receptor de arquivos; ao concluir, coloca no clipboard."""
        if not self._config.files_enabled:
            return
        roots = await self._receiver.feed(message)
        if roots is None:
            return
        loop = asyncio.get_running_loop()
        ok = await loop.run_in_executor(None, write_clipboard_files, roots)
        if ok:
            self._last_files = _files_signature(roots)
            logger.info("Arquivos recebidos prontos para colar (%d item(ns))", len(roots))

    async def _poll_loop(self, on_local_change: OnLocalChange) -> None:
        loop = asyncio.get_running_loop()
        files_init = False
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

            if self._config.files_enabled and self._on_local_files is not None:
                files = await loop.run_in_executor(None, read_clipboard_files)
                sig = _files_signature(files) if files else None
                if files and sig != self._last_files:
                    self._last_files = sig
                    if files_init:
                        logger.debug("Arquivos no clipboard local (%d), enviando", len(files))
                        await self._on_local_files(list(files))
                files_init = True

            await asyncio.sleep(self._config.poll_interval)
