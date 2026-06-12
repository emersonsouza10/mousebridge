"""Visibilidade global do ponteiro (Windows).

Durante o encaminhamento o cursor do servidor fica ancorado no centro para
a captura de deltas; ocultá-lo passa a impressão de que o mouse "foi" para
a outra máquina. O Windows não tem API para esconder o ponteiro do sistema
inteiro, então os cursores padrão são trocados por um cursor transparente
e depois restaurados a partir do registro. Fora do Windows é no-op.

Um processo-guardião garante a restauração mesmo se o processo principal
for morto à força (sem atexit): ele bloqueia lendo um pipe que o sistema
operacional fecha quando o pai morre — aí restaura os cursores e sai.
"""

from __future__ import annotations

import atexit
import logging
import sys

logger = logging.getLogger(__name__)

_SYSTEM_CURSOR_IDS = (
    32512,  # OCR_NORMAL
    32513,  # OCR_IBEAM
    32514,  # OCR_WAIT
    32515,  # OCR_CROSS
    32516,  # OCR_UP
    32642,  # OCR_SIZENWSE
    32643,  # OCR_SIZENESW
    32644,  # OCR_SIZEWE
    32645,  # OCR_SIZENS
    32646,  # OCR_SIZEALL
    32648,  # OCR_NO
    32649,  # OCR_HAND
    32650,  # OCR_APPSTARTING
)
SPI_SETCURSORS = 0x0057

_hidden = False
_guardian = None

_GUARDIAN_SCRIPT = (
    "import ctypes,sys;"
    "sys.stdin.buffer.read();"
    "ctypes.windll.user32.SystemParametersInfoW(0x0057,0,None,0)"
)


def _ensure_guardian() -> None:
    global _guardian
    if _guardian is not None and _guardian.poll() is None:
        return
    import subprocess

    CREATE_NO_WINDOW = 0x08000000
    try:
        _guardian = subprocess.Popen(
            [sys.executable, "-c", _GUARDIAN_SCRIPT],
            stdin=subprocess.PIPE,
            creationflags=CREATE_NO_WINDOW,
        )
    except OSError:
        logger.warning("Não foi possível iniciar o guardião de cursores")


def hide_pointer() -> None:
    global _hidden
    if sys.platform != "win32" or _hidden:
        return
    import ctypes

    _ensure_guardian()
    user32 = ctypes.windll.user32
    try:
        SM_CXCURSOR, SM_CYCURSOR = 13, 14
        width = user32.GetSystemMetrics(SM_CXCURSOR)
        height = user32.GetSystemMetrics(SM_CYCURSOR)
        mask_bytes = (width // 8) * height
        for cursor_id in _SYSTEM_CURSOR_IDS:
            # AND=1 e XOR=0 preservam o pixel da tela: cursor invisível.
            # SetSystemCursor consome o handle, então cria um por id.
            and_mask = (ctypes.c_ubyte * mask_bytes)(*([0xFF] * mask_bytes))
            xor_mask = (ctypes.c_ubyte * mask_bytes)()
            blank = user32.CreateCursor(None, 0, 0, width, height, and_mask, xor_mask)
            if blank:
                user32.SetSystemCursor(blank, cursor_id)
        _hidden = True
        logger.debug("Ponteiro do sistema ocultado")
    except (AttributeError, OSError):
        logger.warning("Não foi possível ocultar o ponteiro")


def show_pointer() -> None:
    global _hidden
    if sys.platform != "win32" or not _hidden:
        return
    import ctypes

    try:
        ctypes.windll.user32.SystemParametersInfoW(SPI_SETCURSORS, 0, None, 0)
        _hidden = False
        logger.debug("Ponteiro do sistema restaurado")
    except (AttributeError, OSError):
        logger.warning("Não foi possível restaurar o ponteiro")


atexit.register(show_pointer)
