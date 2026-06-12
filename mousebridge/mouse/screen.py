"""Geometria da tela, com suporte a múltiplos monitores.

No Windows usa a *virtual screen* (retângulo que envolve todos os
monitores) via GetSystemMetrics; em outros sistemas usa Tkinter como
fallback. Coordenadas podem ser negativas quando há monitor à esquerda
ou acima do principal.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ScreenInfo:
    x: int
    y: int
    width: int
    height: int

    @property
    def right(self) -> int:
        return self.x + self.width - 1

    @property
    def bottom(self) -> int:
        return self.y + self.height - 1

    @property
    def center(self) -> tuple[int, int]:
        return (self.x + self.width // 2, self.y + self.height // 2)

    def clamp(self, x: int, y: int) -> tuple[int, int]:
        return (
            max(self.x, min(self.right, x)),
            max(self.y, min(self.bottom, y)),
        )

    def to_dict(self) -> dict[str, int]:
        return {"x": self.x, "y": self.y, "width": self.width, "height": self.height}

    @classmethod
    def from_dict(cls, data: dict[str, int]) -> "ScreenInfo":
        return cls(
            x=int(data.get("x", 0)),
            y=int(data.get("y", 0)),
            width=int(data["width"]),
            height=int(data["height"]),
        )


def _windows_virtual_screen() -> ScreenInfo:
    import ctypes

    SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN = 76, 77
    SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 78, 79
    metrics = ctypes.windll.user32.GetSystemMetrics
    return ScreenInfo(
        x=metrics(SM_XVIRTUALSCREEN),
        y=metrics(SM_YVIRTUALSCREEN),
        width=metrics(SM_CXVIRTUALSCREEN),
        height=metrics(SM_CYVIRTUALSCREEN),
    )


def _tkinter_screen() -> ScreenInfo:
    import tkinter

    root = tkinter.Tk()
    root.withdraw()
    try:
        return ScreenInfo(0, 0, root.winfo_screenwidth(), root.winfo_screenheight())
    finally:
        root.destroy()


def get_virtual_screen() -> ScreenInfo:
    if sys.platform == "win32":
        try:
            screen = _windows_virtual_screen()
            logger.debug("Tela virtual (Windows): %s", screen)
            return screen
        except Exception:  # noqa: BLE001 - qualquer falha cai no fallback
            logger.warning("GetSystemMetrics falhou, usando fallback Tkinter")
    return _tkinter_screen()
