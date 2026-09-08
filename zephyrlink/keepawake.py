"""Mantém a máquina acordada enquanto há sessão ativa (Windows e macOS).

Cada SO tem seu mecanismo; a API é unificada:

* ``prevent_sleep``/``allow_sleep`` — impedem/liberam suspensão e apagar a tela.
  Válidas nas duas plataformas (idempotentes); no-op nos demais SOs.
* ``keep_awake`` — reafirma o estado "não dormir". No Windows o
  ``SetThreadExecutionState`` vale enquanto a thread viver, por isso é
  reafirmado a cada ciclo de um laço; no macOS apenas garante o token de
  atividade (idempotente). No-op fora de Windows/macOS.
* ``nudge`` — só no Windows: injeta um toque em F15 (sem efeito prático) para
  zerar o cronômetro de ociosidade lido por ``GetLastInputInfo``, evitando
  protetor de tela e bloqueio automático da sessão. No-op fora do Windows.
"""

from __future__ import annotations

import logging
import sys
import threading

logger = logging.getLogger(__name__)

# --- Windows: SetThreadExecutionState + toque F15 ---------------------------
ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
ES_DISPLAY_REQUIRED = 0x00000002

_VK_F15 = 0x7E
_KEYEVENTF_KEYUP = 0x0002


def _win_set_execution_state(flags: int) -> None:
    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.SetThreadExecutionState.argtypes = (ctypes.c_uint32,)
    kernel32.SetThreadExecutionState.restype = ctypes.c_uint32
    if kernel32.SetThreadExecutionState(flags) == 0:
        logger.warning("SetThreadExecutionState falhou (erro %d)", ctypes.get_last_error())


def nudge() -> None:
    """Windows: toque em F15 para zerar o cronômetro de ociosidade. No-op fora."""
    if sys.platform != "win32":
        return
    import ctypes

    from zephyrlink.keyboard.win32_input import Input, KeyboardInput

    inputs = (Input * 2)(
        Input(type=1, ki=KeyboardInput(wVk=_VK_F15)),
        Input(type=1, ki=KeyboardInput(wVk=_VK_F15, dwFlags=_KEYEVENTF_KEYUP)),
    )
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.SendInput.argtypes = (ctypes.c_uint, ctypes.POINTER(Input), ctypes.c_int)
    user32.SendInput.restype = ctypes.c_uint
    if user32.SendInput(len(inputs), inputs, ctypes.sizeof(Input)) != len(inputs):
        logger.warning("Toque anti-ociosidade recusado (erro %d)", ctypes.get_last_error())


# --- macOS: token de atividade do NSProcessInfo -----------------------------
_lock = threading.Lock()
_token = None


def _mac_prevent(reason: str) -> None:
    global _token
    with _lock:
        if _token is not None:
            return
        try:
            import Foundation

            options = (
                Foundation.NSActivityUserInitiated
                | Foundation.NSActivityIdleDisplaySleepDisabled
                | Foundation.NSActivityIdleSystemSleepDisabled
            )
            _token = Foundation.NSProcessInfo.processInfo().beginActivityWithOptions_reason_(
                options, reason
            )
            logger.info("Mac mantido ativo (sem dormir/bloquear) enquanto o núcleo roda")
        except Exception:  # noqa: BLE001
            logger.warning("Não foi possível impedir o Mac de dormir", exc_info=True)
            _token = None


def _mac_allow() -> None:
    global _token
    with _lock:
        if _token is None:
            return
        try:
            import Foundation

            Foundation.NSProcessInfo.processInfo().endActivity_(_token)
            logger.info("Mac liberado para dormir/bloquear normalmente")
        except Exception:  # noqa: BLE001
            logger.warning("Falha ao liberar o Mac para dormir", exc_info=True)
        finally:
            _token = None


# --- API unificada ----------------------------------------------------------
def keep_awake(reason: str = "ZephyrLink ativo") -> None:
    """Reafirma que o sistema/tela não devem dormir. No-op fora de Win/macOS."""
    if sys.platform == "win32":
        _win_set_execution_state(ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED)
    elif sys.platform == "darwin":
        _mac_prevent(reason)


def prevent_sleep(reason: str = "ZephyrLink ativo") -> None:
    """Mantém tela e sistema acordados (idempotente); alias de :func:`keep_awake`."""
    keep_awake(reason)


def allow_sleep() -> None:
    """Libera a máquina para dormir/bloquear normalmente (idempotente)."""
    if sys.platform == "win32":
        _win_set_execution_state(ES_CONTINUOUS)
    elif sys.platform == "darwin":
        _mac_allow()
