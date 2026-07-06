"""Mantém a máquina cliente acordada enquanto há sessão ativa.

Duas frentes, porque o Windows tem dois cronômetros de ociosidade distintos:

* ``keep_awake``/``allow_sleep`` — SetThreadExecutionState impede suspender o
  sistema e apagar a tela. O estado vale enquanto a thread chamadora viver, por
  isso é reafirmado a cada ciclo do laço.
* ``nudge`` — injeta um toque na tecla F15 (sem efeito prático) para zerar o
  cronômetro de ociosidade lido por GetLastInputInfo, evitando protetor de tela
  e bloqueio automático da sessão.
"""

from __future__ import annotations

import ctypes
import logging
import sys

from zephyrlink.keyboard.win32_input import Input, KeyboardInput

logger = logging.getLogger(__name__)

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
ES_DISPLAY_REQUIRED = 0x00000002

_VK_F15 = 0x7E
_KEYEVENTF_KEYUP = 0x0002


def _set_execution_state(flags: int) -> None:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.SetThreadExecutionState.argtypes = (ctypes.c_uint32,)
    kernel32.SetThreadExecutionState.restype = ctypes.c_uint32
    if kernel32.SetThreadExecutionState(flags) == 0:
        logger.warning("SetThreadExecutionState falhou (erro %d)", ctypes.get_last_error())


def keep_awake() -> None:
    if sys.platform != "win32":
        return
    _set_execution_state(ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED)


def allow_sleep() -> None:
    if sys.platform != "win32":
        return
    _set_execution_state(ES_CONTINUOUS)


def nudge() -> None:
    if sys.platform != "win32":
        return
    inputs = (Input * 2)(
        Input(type=1, ki=KeyboardInput(wVk=_VK_F15)),
        Input(type=1, ki=KeyboardInput(wVk=_VK_F15, dwFlags=_KEYEVENTF_KEYUP)),
    )
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.SendInput.argtypes = (ctypes.c_uint, ctypes.POINTER(Input), ctypes.c_int)
    user32.SendInput.restype = ctypes.c_uint
    if user32.SendInput(len(inputs), inputs, ctypes.sizeof(Input)) != len(inputs):
        logger.warning("Toque anti-ociosidade recusado (erro %d)", ctypes.get_last_error())
