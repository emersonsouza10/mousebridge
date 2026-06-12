"""Injeção de eventos de teclado na máquina secundária.

Rastreia teclas pressionadas para poder liberá-las todas se a conexão
cair no meio de uma combinação (evita modificador "preso").
"""

from __future__ import annotations

import ctypes
import logging
import sys
from typing import Any

from pynput import keyboard

from zephyrlink.keyboard.keymap import payload_to_key

logger = logging.getLogger(__name__)

_SHORTCUT_MODIFIERS = {
    "alt",
    "alt_l",
    "alt_r",
    "alt_gr",
    "cmd",
    "cmd_l",
    "cmd_r",
    "ctrl",
    "ctrl_l",
    "ctrl_r",
}


def _send_unicode(char: str, pressed: bool) -> bool:
    if sys.platform != "win32":
        return False

    from ctypes import wintypes

    class KeyboardInput(ctypes.Structure):
        _fields_ = (
            ("wVk", wintypes.WORD),
            ("wScan", wintypes.WORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ctypes.c_size_t),
        )

    class InputUnion(ctypes.Union):
        _fields_ = (("ki", KeyboardInput),)

    class Input(ctypes.Structure):
        _anonymous_ = ("value",)
        _fields_ = (("type", wintypes.DWORD), ("value", InputUnion))

    key_up = 0x0002
    unicode_flag = 0x0004
    flags = unicode_flag | (key_up if not pressed else 0)
    code_units = char.encode("utf-16-le", errors="surrogatepass")
    inputs = [
        Input(type=1, ki=KeyboardInput(wScan=int.from_bytes(code_units[i : i + 2], "little"), dwFlags=flags))
        for i in range(0, len(code_units), 2)
    ]
    if not inputs:
        return False

    array = (Input * len(inputs))(*inputs)
    user32 = ctypes.windll.user32
    user32.SendInput.argtypes = (
        wintypes.UINT,
        ctypes.POINTER(Input),
        ctypes.c_int,
    )
    user32.SendInput.restype = wintypes.UINT
    sent = user32.SendInput(len(array), array, ctypes.sizeof(Input))
    return sent == len(array)


class KeyboardInjector:
    def __init__(self) -> None:
        self._controller = keyboard.Controller()
        self._held: set[Any] = set()
        self._shortcut_modifiers: set[str] = set()
        self._unicode_held: set[str] = set()

    def key_event(self, payload: dict[str, Any], pressed: bool) -> None:
        kind = payload.get("kind")
        name = payload.get("name")
        char = payload.get("char")

        if kind == "named" and name in _SHORTCUT_MODIFIERS:
            if pressed:
                self._shortcut_modifiers.add(name)
            else:
                self._shortcut_modifiers.discard(name)

        if kind == "char" and isinstance(char, str):
            use_unicode = (
                sys.platform == "win32"
                and (
                    char in self._unicode_held
                    or (pressed and not self._shortcut_modifiers)
                )
            )
            if use_unicode and _send_unicode(char, pressed):
                if pressed:
                    self._unicode_held.add(char)
                else:
                    self._unicode_held.discard(char)
                return

        key = payload_to_key(payload)
        if key is None:
            logger.warning("Tecla não reconhecida: %s", payload)
            return
        try:
            if pressed:
                self._controller.press(key)
                self._held.add(key)
            else:
                self._controller.release(key)
                self._held.discard(key)
        except self._controller.InvalidKeyException:
            logger.warning("Tecla inválida para esta plataforma: %s", payload)

    def release_all(self) -> None:
        """Libera teclas presas (chamado em desconexão/saída do modo remoto)."""
        for key in list(self._held):
            try:
                self._controller.release(key)
            except Exception:  # noqa: BLE001
                pass
        self._held.clear()
        for char in list(self._unicode_held):
            _send_unicode(char, False)
        self._unicode_held.clear()
        self._shortcut_modifiers.clear()
