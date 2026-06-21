"""Confirmação local no cliente antes de abrir um app marcado ``require_confirm``.

Usa a caixa de diálogo nativa do Windows (``MessageBoxW``), que funciona a
partir de qualquer thread e não depende de Tk. Em outros sistemas sem um
mecanismo disponível, **nega por padrão** (seguro): um app que exige
confirmação não roda sem alguém poder confirmar.
"""

from __future__ import annotations

import asyncio
import logging
import sys

logger = logging.getLogger(__name__)

_MB_YESNO = 0x4
_MB_ICONWARNING = 0x30
_MB_TOPMOST = 0x40000
_IDYES = 6


def _ask_windows(title: str, text: str) -> bool:
    import ctypes

    result = ctypes.windll.user32.MessageBoxW(
        0, text, title, _MB_YESNO | _MB_ICONWARNING | _MB_TOPMOST
    )
    return result == _IDYES


async def confirm(title: str, text: str) -> bool:
    if sys.platform.startswith("win"):
        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(None, _ask_windows, title, text)
        except OSError:
            logger.warning("Falha ao exibir confirmação; negando por padrão")
            return False
    logger.warning("Sem mecanismo de confirmação neste SO; negando por padrão")
    return False
