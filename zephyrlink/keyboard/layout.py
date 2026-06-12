"""Sincronização do layout de teclado entre servidor e cliente.

O servidor anuncia o identificador do seu layout ativo (KLID, ex.
``"00010416"`` para ABNT2) e o cliente ativa esse mesmo layout ao receber
o controle. Sem isso, a injeção de caracteres no cliente é reinterpretada
pelo layout local e os símbolos saem trocados.

Só o Windows expõe layouts identificáveis por KLID; nas demais plataformas
as funções viram no-ops e a injeção segue o comportamento padrão.
"""

from __future__ import annotations

import logging
import sys

logger = logging.getLogger(__name__)

_KLID_LENGTH = 9
_KLF_ACTIVATE = 0x00000001
_WM_INPUTLANGCHANGEREQUEST = 0x0050


def current_layout_id() -> str | None:
    """KLID do layout ativo nesta máquina (servidor), ou ``None``."""
    if sys.platform != "win32":
        return None
    import ctypes
    import ctypes.wintypes

    user32 = ctypes.windll.user32
    user32.GetKeyboardLayoutNameW.restype = ctypes.wintypes.BOOL
    buffer = ctypes.create_unicode_buffer(_KLID_LENGTH)
    if not user32.GetKeyboardLayoutNameW(buffer):
        return None
    return buffer.value or None


def activate_layout(klid: str | None) -> bool:
    """Carrega e ativa ``klid`` para a janela em foco (cliente).

    A ativação é encaminhada à janela em foco via
    ``WM_INPUTLANGCHANGEREQUEST`` porque ``LoadKeyboardLayout`` sozinho só
    afetaria a thread chamadora, não o aplicativo que receberá a digitação.
    """
    if not klid or sys.platform != "win32":
        return False
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    user32.LoadKeyboardLayoutW.restype = wintypes.HKL
    user32.LoadKeyboardLayoutW.argtypes = (wintypes.LPCWSTR, wintypes.UINT)
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.PostMessageW.argtypes = (
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
    )

    hkl = user32.LoadKeyboardLayoutW(klid, _KLF_ACTIVATE)
    if not hkl:
        logger.warning("Falha ao carregar layout do servidor: %s", klid)
        return False
    hwnd = user32.GetForegroundWindow()
    if hwnd:
        user32.PostMessageW(hwnd, _WM_INPUTLANGCHANGEREQUEST, 0, hkl)
    logger.debug("Layout do servidor ativado no cliente: %s", klid)
    return True
