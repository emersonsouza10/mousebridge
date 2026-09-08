"""Consentimento local na máquina-alvo antes de compartilhar a tela.

Antes de transmitir qualquer frame, o dono da máquina controlada precisa
aprovar a sessão. Segue o padrão de ``launcher/confirm.py`` (nega por padrão se
não houver como perguntar), mas cobre Windows (MessageBoxW), macOS (osascript)
e Linux (Tk, se disponível). Sempre é chamado de dentro do asyncio via
``run_in_executor`` para não bloquear o event loop.
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
import sys

logger = logging.getLogger(__name__)

_TITLE = "ZephyrLink — acesso remoto"
_MB_YESNO = 0x4
_MB_ICONWARNING = 0x30
_MB_TOPMOST = 0x40000
_IDYES = 6


def _ask_windows(text: str) -> bool:
    import ctypes

    result = ctypes.windll.user32.MessageBoxW(0, text, _TITLE, _MB_YESNO | _MB_ICONWARNING | _MB_TOPMOST)
    return result == _IDYES


def _ask_macos(text: str) -> bool:
    # 'display dialog' devolve código 0 no botão default e 1 (erro) ao cancelar.
    script = (
        f'display dialog {_as_applescript_string(text)} '
        f'with title {_as_applescript_string(_TITLE)} '
        'buttons {"Recusar", "Permitir"} default button "Recusar" '
        'cancel button "Recusar" with icon caution'
    )
    proc = subprocess.run(
        ["osascript", "-e", script], capture_output=True, text=True, timeout=120
    )
    return proc.returncode == 0 and "Permitir" in proc.stdout


def _as_applescript_string(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _ask_tk(text: str) -> bool:
    import tkinter as tk
    from tkinter import messagebox

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        return bool(messagebox.askyesno(_TITLE, text))
    finally:
        root.destroy()


def _prompt(host: str) -> bool:
    text = (
        f"O computador {host} está pedindo para VER e CONTROLAR esta máquina "
        "remotamente.\n\nSua tela será transmitida e o mouse/teclado poderão ser "
        "operados à distância.\n\nPermitir o acesso remoto?"
    )
    try:
        if sys.platform.startswith("win"):
            return _ask_windows(text)
        if sys.platform == "darwin":
            return _ask_macos(text)
        return _ask_tk(text)
    except (OSError, subprocess.SubprocessError, Exception) as exc:  # noqa: BLE001
        logger.warning("Falha ao pedir consentimento (%s); negando por padrão", exc)
        return False


async def ask_consent(host: str) -> bool:
    """Pergunta ao dono da máquina-alvo se aceita a sessão. Nega em caso de erro."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _prompt, host)
