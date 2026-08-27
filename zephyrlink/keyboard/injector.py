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
# então com modificador ativo a via Unicode nunca pode ser usada: ela
# digitaria o caractere literal sem reaplicar o modificador.
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

# Modificadores que caracterizam um ATALHO (não a produção de um caractere).
# Shift fica de fora: em payloads sem vk (servidor macOS) o caractere já vem
# resolvido com o Shift aplicado (ex.: '@'), então digitá-lo por Unicode é
# correto mesmo com Shift segurado.
_SHORTCUT_MODIFIERS = _MODIFIERS - {"shift", "shift_l", "shift_r"}

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
            # Com o vk físico no payload (servidor Windows) a injeção é por
            # virtual key: o cliente aplica shift/altgr/caps e tecla morta sobre
            # o layout sincronizado. Sem vk (servidor macOS/não-Windows) o char
            # já vem resolvido, então digita-se por Unicode — inclusive com Shift
            # segurado; só um modificador de ATALHO (Ctrl/Alt/Cmd) desativa isso.
            use_unicode = (
                sys.platform == "win32"
                and (
                    char in self._unicode_held
                    or (
                        pressed
                        and not isinstance(vk, int)
                        and not (self._modifiers & _SHORTCUT_MODIFIERS)
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

        # O vk capturado reproduz a tecla física; re-resolver o caractere
        # com VkKeyScan dependeria do layout da thread injetora e cairia
        # em Unicode literal quando não resolve (ex.: tecla ABNT_C1).
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
