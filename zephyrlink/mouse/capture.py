"""Captura de mouse na máquina principal (servidor).

Dois modos, cada um com seu listener pynput (listeners rodam em thread
própria; trocar ``suppress`` exige recriar o listener):

* **monitor**: modo local. Listener não-supressivo apenas observa a posição
  para detectar quando o cursor atinge a borda configurada.
* **forward**: modo remoto. Listener supressivo (eventos não chegam às
  aplicações locais) captura movimento como *deltas* usando o truque de
  recentralização: após cada movimento o cursor é devolvido ao centro da
  tela e o delta em relação ao centro é encaminhado. Isso permite movimento
  contínuo sem o cursor local esbarrar nas bordas.

Callbacks são chamados na thread do listener; quem consome deve fazer o
handoff para o loop asyncio (ver server.py).
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

from pynput import mouse

from zephyrlink.mouse.edge import EdgeDetector
from zephyrlink.mouse.screen import ScreenInfo, primary_center

logger = logging.getLogger(__name__)

OnEdgeHit = Callable[[str, float], None]     # borda atingida, ratio ao longo dela
OnMove = Callable[[int, int], None]          # dx, dy
OnButton = Callable[[str, bool], None]       # botão, pressionado
OnScroll = Callable[[int, int], None]        # dx, dy

BUTTON_NAMES: dict[Any, str] = {
    mouse.Button.left: "left",
    mouse.Button.right: "right",
    mouse.Button.middle: "middle",
}
NAME_TO_BUTTON: dict[str, Any] = {name: btn for btn, name in BUTTON_NAMES.items()}


class MouseCapture:
    def __init__(self, screen: ScreenInfo) -> None:
        self._screen = screen
        self._controller = mouse.Controller()
        self._listener: mouse.Listener | None = None
        self._lock = threading.Lock()
        self._mode: str = "stopped"

    @property
    def mode(self) -> str:
        return self._mode

    def position(self) -> tuple[int, int]:
        return self._controller.position

    def set_position(self, x: int, y: int) -> None:
        self._controller.position = (x, y)

    def start_monitor(self, detectors: list[EdgeDetector], on_edge_hit: OnEdgeHit) -> None:
        """Observa o cursor aguardando o cruzamento de qualquer borda vigiada.

        ``detectors`` pode conter uma borda por cliente conectado (topologia
        estrela); a primeira borda atingida dispara ``on_edge_hit`` com o
        nome da borda e o ratio da posição ao longo dela.
        """
        with self._lock:
            self._stop_listener()
            watched = list(detectors)

            def on_move(x: int, y: int) -> None:
                for detector in watched:
                    if detector.hit(int(x), int(y)):
                        on_edge_hit(detector.edge, detector.exit_ratio(int(x), int(y)))
                        return

            self._listener = mouse.Listener(on_move=on_move)
            self._listener.start()
            self._mode = "monitor"
            logger.debug("Captura de mouse: modo monitor (%d bordas)", len(watched))

    def start_forward(
        self,
        on_move: OnMove,
        on_button: OnButton,
        on_scroll: OnScroll,
    ) -> None:
        """Suprime input local e encaminha eventos como deltas."""
        with self._lock:
            self._stop_listener()
            # Recentraliza no monitor primário, não no centro da tela virtual:
            # com monitores de tamanhos diferentes, o centro virtual pode cair
            # numa "área morta" fora de qualquer monitor físico.
            target = primary_center() or self._screen.center
            self._controller.position = target
            # Lê onde o cursor REALMENTE parou (o Windows pode ter grudado numa
            # borda) e usa essa âncora para os deltas — caso contrário todo
            # movimento sairia com um deslocamento fixo.
            anchor = self._controller.position
            anchor_x, anchor_y = int(anchor[0]), int(anchor[1])

            def handle_move(x: int, y: int) -> None:
                dx, dy = int(x) - anchor_x, int(y) - anchor_y
                if dx == 0 and dy == 0:
                    return  # movimento gerado pela própria recentralização
                self._controller.position = (anchor_x, anchor_y)
                on_move(dx, dy)

            def handle_click(x: int, y: int, button: Any, pressed: bool) -> None:
                name = BUTTON_NAMES.get(button)
                if name is not None:
                    on_button(name, pressed)

            def handle_scroll(x: int, y: int, dx: int, dy: int) -> None:
                on_scroll(int(dx), int(dy))

            self._listener = mouse.Listener(
                on_move=handle_move,
                on_click=handle_click,
                on_scroll=handle_scroll,
                suppress=True,
            )
            self._listener.start()
            self._mode = "forward"
            logger.debug("Captura de mouse: modo forward (input local suprimido)")

    def stop(self) -> None:
        with self._lock:
            self._stop_listener()
            self._mode = "stopped"

    def _stop_listener(self) -> None:
        if self._listener is not None:
            self._listener.stop()
            self._listener = None
