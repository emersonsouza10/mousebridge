"""Injeção de eventos de teclado na máquina secundária.

Rastreia teclas pressionadas para poder liberá-las todas se a conexão
cair no meio de uma combinação (evita modificador "preso").
"""

from __future__ import annotations

import logging
from typing import Any

from pynput import keyboard

from mousebridge.keyboard.keymap import payload_to_key

logger = logging.getLogger(__name__)


class KeyboardInjector:
    def __init__(self) -> None:
        self._controller = keyboard.Controller()
        self._held: set[Any] = set()

    def key_event(self, payload: dict[str, Any], pressed: bool) -> None:
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
