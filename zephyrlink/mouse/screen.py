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


@dataclass(frozen=True, slots=True)
class MonitorLayout:
    """Conjunto de monitores físicos do desktop, com geometria ciente das
    *zonas mortas* entre eles.

    A tela virtual (caixa delimitadora) inclui regiões que não pertencem a
    nenhum monitor quando os monitores têm tamanhos ou alinhamentos
    diferentes. Mover o cursor dentro dessas regiões não é possível: o SO o
    prende no monitor real mais próximo. Tratar o desktop como o retângulo
    virtual faz o cursor "encalhar" perto da junção dos monitores e nunca
    estourar a borda externa — por isso o retorno ao servidor não dispara.
    """

    monitors: tuple[ScreenInfo, ...]

    @classmethod
    def detect(cls) -> "MonitorLayout":
        return cls(tuple(get_monitors()))

    @property
    def bounds(self) -> ScreenInfo:
        """Caixa delimitadora (tela virtual) de todos os monitores."""
        left = min(m.x for m in self.monitors)
        top = min(m.y for m in self.monitors)
        right = max(m.right for m in self.monitors)
        bottom = max(m.bottom for m in self.monitors)
        return ScreenInfo(left, top, right - left + 1, bottom - top + 1)

    def monitor_at(self, x: int, y: int) -> ScreenInfo | None:
        for m in self.monitors:
            if m.x <= x <= m.right and m.y <= y <= m.bottom:
                return m
        return None

    def clamp(self, x: int, y: int) -> tuple[int, int]:
        """Prende ``(x, y)`` ao pixel válido mais próximo de algum monitor."""
        if self.monitor_at(x, y) is not None:
            return (x, y)
        best: tuple[int, int] | None = None
        best_dist = float("inf")
        for m in self.monitors:
            cx, cy = m.clamp(x, y)
            dist = (cx - x) ** 2 + (cy - y) ** 2
            if dist < best_dist:
                best_dist, best = dist, (cx, cy)
        assert best is not None
        return best

    def band_overflow(self, x: int, y: int) -> tuple[int, int]:
        """Quanto ``(x, y)`` extrapola a área de trabalho em cada eixo.

        A medição é feita contra os monitores que cobrem a faixa ortogonal
        (a linha ``y`` para o eixo X, a coluna ``x`` para o eixo Y). Assim
        uma zona morta *entre* monitores devolve ``0`` (não é saída — o
        cursor apenas desliza para o monitor vizinho), enquanto sair pela
        borda externa devolve o transbordo com sinal (negativo à
        esquerda/acima, positivo à direita/abaixo).
        """
        rows = [m for m in self.monitors if m.y <= y <= m.bottom] or list(self.monitors)
        left = min(m.x for m in rows)
        right = max(m.right for m in rows)
        over_x = x - left if x < left else (x - right if x > right else 0)

        cols = [m for m in self.monitors if m.x <= x <= m.right] or list(self.monitors)
        top = min(m.y for m in cols)
        bottom = max(m.bottom for m in cols)
        over_y = y - top if y < top else (y - bottom if y > bottom else 0)
        return over_x, over_y


def enable_dpi_awareness() -> None:
    """Declara o processo *per-monitor DPI aware* (Windows).

    Sem isso, em telas com escala != 100% o Windows entrega ao processo
    coordenadas e dimensões "virtualizadas" (escaladas), divergindo das
    coordenadas físicas que ``SetCursorPos`` usa — o cursor injetado vai
    parar no lugar errado. Deve ser chamado uma vez, no início do processo,
    antes de qualquer consulta de tela. Em outros sistemas é no-op.
    """
    if sys.platform != "win32":
        return
    import ctypes

    try:
        # PROCESS_PER_MONITOR_DPI_AWARE = 2 (Windows 8.1+)
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except (AttributeError, OSError):
        try:
            ctypes.windll.user32.SetProcessDPIAware()  # fallback (Vista+)
        except (AttributeError, OSError):
            logger.warning("Não foi possível ativar DPI awareness")


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


def _windows_monitors() -> list[ScreenInfo]:
    import ctypes
    from ctypes import wintypes

    monitors: list[ScreenInfo] = []

    proc = ctypes.WINFUNCTYPE(
        wintypes.BOOL, wintypes.HANDLE, wintypes.HDC,
        ctypes.POINTER(wintypes.RECT), wintypes.LPARAM,
    )

    def _callback(_hmon, _hdc, lprect, _lparam):  # type: ignore[no-untyped-def]
        r = lprect.contents
        monitors.append(ScreenInfo(r.left, r.top, r.right - r.left, r.bottom - r.top))
        return 1

    if not ctypes.windll.user32.EnumDisplayMonitors(0, 0, proc(_callback), 0):
        raise OSError("EnumDisplayMonitors retornou 0")
    return monitors


def get_monitors() -> list[ScreenInfo]:
    """Lista cada monitor físico. Fora do Windows (ou em falha) devolve a
    tela virtual inteira como um único item."""
    if sys.platform == "win32":
        try:
            screens = _windows_monitors()
            if screens:
                logger.debug("Monitores (Windows): %s", screens)
                return screens
        except Exception:  # noqa: BLE001 - qualquer falha cai no fallback
            logger.warning("EnumDisplayMonitors falhou, usando tela virtual")
    return [get_virtual_screen()]


def primary_center() -> tuple[int, int] | None:
    """Centro do monitor primário (sempre dentro de um monitor físico).

    O monitor primário tem origem (0,0) no Windows; seu centro nunca cai
    nas "áreas mortas" que surgem entre monitores de tamanhos diferentes
    na tela virtual. É o ponto ideal para a recentralização do cursor.
    Retorna ``None`` fora do Windows (o chamador usa o centro da tela).
    """
    if sys.platform != "win32":
        return None
    import ctypes

    try:
        SM_CXSCREEN, SM_CYSCREEN = 0, 1
        metrics = ctypes.windll.user32.GetSystemMetrics
        return (metrics(SM_CXSCREEN) // 2, metrics(SM_CYSCREEN) // 2)
    except (AttributeError, OSError):
        return None


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
