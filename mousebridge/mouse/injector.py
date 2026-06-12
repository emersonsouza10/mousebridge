"""Injeção de eventos de mouse na máquina secundária (cliente).

Mantém a posição como estado próprio (em vez de mover relativo) para poder
limitar o cursor à tela e detectar o retorno pela borda com precisão.
"""

from __future__ import annotations

import logging

from pynput import mouse

from mousebridge.mouse.capture import NAME_TO_BUTTON
from mousebridge.mouse.screen import ScreenInfo

logger = logging.getLogger(__name__)


class MouseInjector:
    def __init__(self, screen: ScreenInfo) -> None:
        self._screen = screen
        self._controller = mouse.Controller()

    def set_position(self, x: int, y: int) -> None:
        self._controller.position = self._screen.clamp(x, y)

    def position(self) -> tuple[int, int]:
        return self._controller.position

    def move_by(self, dx: int, dy: int) -> tuple[int, int, int, int]:
        """Move o cursor pelo delta, limitado à tela.

        Retorna ``(x, y, overflow_x, overflow_y)`` — o overflow indica
        quanto do movimento passou da borda (usado na detecção de retorno).
        """
        cur_x, cur_y = self._controller.position
        target_x, target_y = int(cur_x) + dx, int(cur_y) + dy
        new_x, new_y = self._screen.clamp(target_x, target_y)
        self._controller.position = (new_x, new_y)
        return new_x, new_y, target_x - new_x, target_y - new_y

    def button(self, name: str, pressed: bool) -> None:
        btn = NAME_TO_BUTTON.get(name)
        if btn is None:
            logger.warning("Botão desconhecido: %s", name)
            return
        if pressed:
            self._controller.press(btn)
        else:
            self._controller.release(btn)

    def scroll(self, dx: int, dy: int) -> None:
        self._controller.scroll(dx, dy)
