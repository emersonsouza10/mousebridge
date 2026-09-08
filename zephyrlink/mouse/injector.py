"""Injeção de eventos de mouse na máquina secundária (cliente).

Mantém a posição como estado próprio (em vez de mover relativo) para poder
limitar o cursor à tela e detectar o retorno pela borda com precisão.
"""

from __future__ import annotations

import logging
import sys
import time
from typing import Any

from pynput import mouse

from zephyrlink.mouse.capture import NAME_TO_BUTTON
from zephyrlink.mouse.screen import MonitorLayout

logger = logging.getLogger(__name__)

# Duplo/triplo clique: cliques do mesmo botão dentro deste intervalo e distância.
_MULTI_CLICK_SECONDS = 0.5
_MULTI_CLICK_PIXELS = 4


class MouseInjector:
    def __init__(self, layout: MonitorLayout) -> None:
        self._layout = layout
        self._controller = mouse.Controller()
        self._last_click: dict[str, Any] | None = None

    def set_layout(self, layout: MonitorLayout) -> None:
        self._layout = layout

    def set_position(self, x: int, y: int) -> None:
        self._controller.position = self._layout.clamp(x, y)

    def position(self) -> tuple[int, int]:
        return self._controller.position

    def move_by(self, dx: int, dy: int) -> tuple[int, int, int, int]:
        """Move o cursor pelo delta, limitado aos monitores físicos.

        Retorna ``(x, y, overflow_x, overflow_y)``. O overflow é diferente
        de zero apenas quando o movimento sai da área de trabalho pela borda
        externa (usado na detecção de retorno); uma zona morta *entre*
        monitores devolve overflow zero e o cursor desliza para o vizinho.
        """
        cur_x, cur_y = self._controller.position
        target_x, target_y = int(cur_x) + dx, int(cur_y) + dy
        if self._layout.monitor_at(target_x, target_y) is not None:
            self._controller.position = (target_x, target_y)
            return target_x, target_y, 0, 0
        over_x, over_y = self._layout.band_overflow(target_x, target_y)
        new_x, new_y = self._layout.clamp(target_x, target_y)
        self._controller.position = (new_x, new_y)
        return new_x, new_y, over_x, over_y

    def button(self, name: str, pressed: bool) -> None:
        btn = NAME_TO_BUTTON.get(name)
        if btn is None:
            logger.warning("Botão desconhecido: %s", name)
            return
        if sys.platform == "darwin":
            self._button_darwin(btn, name, pressed)
        elif pressed:
            self._controller.press(btn)
        else:
            self._controller.release(btn)

    def _button_darwin(self, btn: Any, name: str, pressed: bool) -> None:
        """No macOS, define o 'click count' para o sistema reconhecer duplo/
        triplo clique. Press/release soltos (como chegam encaminhados) não têm
        contexto de clique, então o macOS não os agrupa sozinho."""
        if pressed:
            now = time.monotonic()
            x, y = self._controller.position
            last = self._last_click
            if (
                last is not None
                and last["name"] == name
                and now - last["time"] <= _MULTI_CLICK_SECONDS
                and abs(x - last["x"]) <= _MULTI_CLICK_PIXELS
                and abs(y - last["y"]) <= _MULTI_CLICK_PIXELS
            ):
                count = last["count"] + 1
            else:
                count = 1
            self._last_click = {"name": name, "time": now, "x": x, "y": y, "count": count}
            # _press faz self._click += 1; começando em count-1, o evento sai com
            # o click count correto (2 = duplo, 3 = triplo).
            self._controller._click = count - 1
            self._controller.press(btn)
        else:
            self._controller.release(btn)
            self._controller._click = None

    def scroll(self, dx: int, dy: int) -> None:
        self._controller.scroll(dx, dy)
