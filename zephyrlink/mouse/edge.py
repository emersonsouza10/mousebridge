"""Detecção de borda e mapeamento de posição entre telas.

Lógica pura (sem dependência de pynput) para ser testável sem display.

A posição ao longo da borda é transmitida como *ratio* (0.0–1.0) em vez de
pixels: assim telas com resoluções diferentes mapeiam proporcionalmente —
sair no meio da borda direita da tela A entra no meio da borda esquerda
da tela B, independentemente das resoluções.
"""

from __future__ import annotations

from dataclasses import dataclass

from zephyrlink.mouse.screen import ScreenInfo

OPPOSITE: dict[str, str] = {
    "left": "right",
    "right": "left",
    "top": "bottom",
    "bottom": "top",
}


def opposite_edge(edge: str) -> str:
    return OPPOSITE[edge]


def _ratio(value: int, start: int, length: int) -> float:
    if length <= 1:
        return 0.0
    return min(1.0, max(0.0, (value - start) / (length - 1)))


@dataclass(frozen=True, slots=True)
class EdgeDetector:
    """Detecta quando o cursor atinge uma borda da tela."""

    edge: str
    screen: ScreenInfo
    margin: int = 1

    def hit(self, x: int, y: int) -> bool:
        s = self.screen
        match self.edge:
            case "left":
                return x <= s.x + self.margin - 1
            case "right":
                return x >= s.right - self.margin + 1
            case "top":
                return y <= s.y + self.margin - 1
            case "bottom":
                return y >= s.bottom - self.margin + 1
        return False

    def exit_ratio(self, x: int, y: int) -> float:
        """Posição relativa (0–1) ao longo da borda no momento da saída."""
        s = self.screen
        if self.edge in ("left", "right"):
            return _ratio(y, s.y, s.height)
        return _ratio(x, s.x, s.width)


def entry_position(edge: str, ratio: float, screen: ScreenInfo, inset: int = 2) -> tuple[int, int]:
    """Coordenada de entrada do cursor ao cruzar para esta tela.

    ``edge`` é a borda por onde o cursor entra; ``inset`` afasta o cursor
    alguns pixels da borda para não disparar retorno imediato.
    """
    ratio = min(1.0, max(0.0, ratio))
    s = screen
    match edge:
        case "left":
            return s.clamp(s.x + inset, s.y + round(ratio * (s.height - 1)))
        case "right":
            return s.clamp(s.right - inset, s.y + round(ratio * (s.height - 1)))
        case "top":
            return s.clamp(s.x + round(ratio * (s.width - 1)), s.y + inset)
        case "bottom":
            return s.clamp(s.x + round(ratio * (s.width - 1)), s.bottom - inset)
    raise ValueError(f"borda inválida: {edge!r}")


def return_position(edge: str, ratio: float, screen: ScreenInfo, inset: int = 2) -> tuple[int, int]:
    """Posição do cursor na tela principal quando o controle retorna.

    ``edge`` é a borda da tela principal por onde o cursor saiu
    originalmente (a borda configurada no layout).
    """
    return entry_position(edge, ratio, screen, inset)
