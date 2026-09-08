"""Captura de tela multiplataforma via ``mss``.

``mss`` funciona em Windows/macOS/Linux e não depende do RDP/Terminal Services
do SO — é captura de framebuffer em espaço de usuário, então roda mesmo com a
"Área de Trabalho Remota" desabilitada. Cada instância ``mss.mss()`` é presa à
thread que a criou, então o grabber é sempre construído dentro da thread de
captura (ver session.py).

No macOS a captura exige a permissão *Screen Recording* (TCC) concedida ao
processo Python/terminal; sem ela o SO devolve frames em branco/da área de
trabalho.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class CaptureError(RuntimeError):
    """``mss`` ausente ou falha ao capturar a tela."""


def _require_mss():  # type: ignore[no-untyped-def]
    try:
        import mss
    except ImportError as exc:  # pragma: no cover - caminho de dependência ausente
        raise CaptureError(
            "o pacote 'mss' é necessário para capturar a tela; instale com 'pip install mss'"
        ) from exc
    return mss


def mss_available() -> bool:
    try:
        import mss  # noqa: F401
    except ImportError:
        return False
    return True


def _select_monitor(monitors: list[dict], index: int) -> tuple[int, dict]:
    """Resolve o índice de monitor pedido contra a lista do mss.

    Índice 0 = tela virtual (todos os monitores); 1..N = monitor físico. Um
    índice fora do intervalo cai para 0 (tudo), com aviso.
    """
    if index < 0 or index >= len(monitors):
        if index != 0:
            logger.warning("Monitor %d inexistente (há %d); usando a tela toda", index, len(monitors) - 1)
        index = 0
    return index, monitors[index]


def monitor_region(monitor: int) -> tuple[int, int, int, int]:
    """Caixa ``(left, top, width, height)`` do monitor pedido.

    Lida numa instância mss efêmera (fora da thread de captura) só para o alvo
    saber a geometria da região que vai transmitir — necessária para mapear o
    input absoluto de volta a pixels.
    """
    mss = _require_mss()
    try:
        with mss.mss() as sct:
            _, mon = _select_monitor(list(sct.monitors), monitor)
            return int(mon["left"]), int(mon["top"]), int(mon["width"]), int(mon["height"])
    except Exception as exc:  # noqa: BLE001 - mss lança exceções próprias por plataforma
        raise CaptureError(f"falha ao consultar monitores: {exc}") from exc


class FrameGrabber:
    """Captura frames de um monitor. Criar e usar SEMPRE na mesma thread."""

    def __init__(self, monitor: int) -> None:
        mss = _require_mss()
        try:
            self._sct = mss.mss()
        except Exception as exc:  # noqa: BLE001
            raise CaptureError(f"falha ao iniciar captura: {exc}") from exc
        self._index, self._mon = _select_monitor(list(self._sct.monitors), monitor)

    @property
    def region(self) -> tuple[int, int, int, int]:
        return int(self._mon["left"]), int(self._mon["top"]), int(self._mon["width"]), int(self._mon["height"])

    def grab(self) -> tuple[bytes, int, int]:
        """Captura um frame; devolve ``(rgb_bytes, width, height)``."""
        shot = self._sct.grab(self._mon)
        return bytes(shot.rgb), shot.width, shot.height

    def close(self) -> None:
        try:
            self._sct.close()
        except Exception:  # noqa: BLE001 - fechar nunca deve derrubar a sessão
            pass
