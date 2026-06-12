"""Captura de teclado na máquina principal.

Ativa apenas em modo remoto: um listener supressivo intercepta todas as
teclas (inclusive Alt+Tab e Win+R, via low-level hook no Windows) e as
encaminha. Em modo local nenhum listener de teclado fica ativo.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

from pynput import keyboard

from zephyrlink.keyboard.keymap import key_to_payload

logger = logging.getLogger(__name__)

OnKey = Callable[[dict[str, Any], bool], None]  # payload, pressionada


class KeyboardCapture:
    def __init__(self) -> None:
        self._listener: keyboard.Listener | None = None
        self._lock = threading.Lock()

    def start(self, on_key: OnKey) -> None:
        with self._lock:
            self._stop_listener()

            def handle(key: Any, pressed: bool) -> None:
                payload = key_to_payload(key)
                if payload is not None:
                    on_key(payload, pressed)

            self._listener = keyboard.Listener(
                on_press=lambda key: handle(key, True),
                on_release=lambda key: handle(key, False),
                suppress=True,
            )
            self._listener.start()
            logger.debug("Captura de teclado ativa (input local suprimido)")

    def stop(self) -> None:
        with self._lock:
            self._stop_listener()

    def _stop_listener(self) -> None:
        if self._listener is not None:
            self._listener.stop()
            self._listener = None
            logger.debug("Captura de teclado parada")
