"""Mapeamento entre razões [0,1] e coordenadas absolutas de tela.

O operador (viewer) não conhece a geometria física do alvo — ele só sabe onde,
dentro da imagem exibida, o mouse está. Então o input viaja como uma razão
``(x, y)`` em ``[0,1]`` relativa à região capturada. O alvo converte a razão de
volta para pixels absolutos da mesma região que capturou. Manter isso em
funções puras (sem pynput/mss) permite testar o mapeamento isoladamente.
"""

from __future__ import annotations


def clamp01(value: float) -> float:
    """Prende ``value`` ao intervalo ``[0.0, 1.0]``."""
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return value


def ratio_to_abs(
    x_ratio: float, y_ratio: float, left: int, top: int, width: int, height: int
) -> tuple[int, int]:
    """Razão da região capturada → pixel absoluto na tela do alvo.

    ``(left, top, width, height)`` é a caixa capturada (um monitor físico ou a
    tela virtual inteira). A razão é presa a ``[0,1]`` antes de escalar, então
    um viewer que reporte levemente fora dos limites nunca projeta para fora da
    região.
    """
    x_ratio = clamp01(x_ratio)
    y_ratio = clamp01(y_ratio)
    x = left + round(x_ratio * max(0, width - 1))
    y = top + round(y_ratio * max(0, height - 1))
    return x, y


def abs_to_ratio(x: int, y: int, left: int, top: int, width: int, height: int) -> tuple[float, float]:
    """Inverso de :func:`ratio_to_abs` (usado em testes e diagnósticos)."""
    x_ratio = 0.0 if width <= 1 else (x - left) / (width - 1)
    y_ratio = 0.0 if height <= 1 else (y - top) / (height - 1)
    return clamp01(x_ratio), clamp01(y_ratio)
