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
from zephyrlink.keyboard.win32_input import caps_lock_active, send_unicode

logger = logging.getLogger(__name__)

# A captura supressiva no servidor reporta sempre o caractere sem shift
# (teclas suprimidas não atualizam o estado assíncrono dos modificadores),
# então com modificador ativo a injeção vai pela via de virtual key: o
# cliente mantém o modificador e o sistema reaplica shift/altgr sobre o
# layout sincronizado. A via Unicode digitaria o caractere literal.
_MODIFIERS = {
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
    "shift",
    "shift_l",
    "shift_r",
}

class KeyboardInjector:
    def __init__(self) -> None:
        self._controller = keyboard.Controller()
        self._held: set[Any] = set()
        self._modifiers: set[str] = set()
        self._unicode_held: set[str] = set()

    def key_event(self, payload: dict[str, Any], pressed: bool) -> None:
        kind = payload.get("kind")
        name = payload.get("name")
        char = payload.get("char")
        vk = payload.get("vk")

        if kind == "named" and name in _MODIFIERS:
            if pressed:
                self._modifiers.add(name)
            else:
                self._modifiers.discard(name)

        if kind == "char" and isinstance(char, str):
            use_unicode = (
                sys.platform == "win32"
                and (
                    char in self._unicode_held
                    or (
                        pressed
                        and not self._modifiers
                        and not (char.isalpha() and caps_lock_active())
                    )
                )
            )
            if use_unicode:
                sent = send_unicode(char, pressed)
                if sent and pressed:
                    self._unicode_held.add(char)
                elif not pressed:
                    self._unicode_held.discard(char)
                return

        # Pela via de virtual key, re-resolver o caractere com VkKeyScan
        # depende do layout da thread injetora e cai em Unicode literal
        # quando não resolve (ex.: tecla ABNT_C1); o vk capturado no
        # servidor reproduz a tecla física sobre o layout sincronizado.
        if kind == "char" and sys.platform == "win32" and isinstance(vk, int):
            key: Any = keyboard.KeyCode.from_vk(vk)
        else:
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
        self._modifiers.clear()
