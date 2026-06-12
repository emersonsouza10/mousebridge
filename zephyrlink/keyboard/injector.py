"""Injeção de eventos de teclado na máquina secundária.

Rastreia teclas pressionadas para poder liberá-las todas se a conexão
cair no meio de uma combinação (evita modificador "preso").
"""

from __future__ import annotations

import logging
import sys
from typing import Any

from pynput import keyboard

from zephyrlink.keyboard.keymap import payload_to_key
from zephyrlink.keyboard.win32_input import send_unicode

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
            if use_unicode:
                sent = send_unicode(char, pressed)
                if sent and pressed:
                    self._unicode_held.add(char)
                elif not pressed:
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
            send_unicode(char, False)
        self._unicode_held.clear()
        self._shortcut_modifiers.clear()
