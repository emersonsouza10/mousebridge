from __future__ import annotations

import ctypes
import logging
import sys

logger = logging.getLogger(__name__)

_DWORD = ctypes.c_uint32
_LONG = ctypes.c_int32
_ULONG_PTR = ctypes.c_size_t
_WORD = ctypes.c_uint16


class MouseInput(ctypes.Structure):
    _fields_ = (
        ("dx", _LONG),
        ("dy", _LONG),
        ("mouseData", _DWORD),
        ("dwFlags", _DWORD),
        ("time", _DWORD),
        ("dwExtraInfo", _ULONG_PTR),
    )


class KeyboardInput(ctypes.Structure):
    _fields_ = (
        ("wVk", _WORD),
        ("wScan", _WORD),
        ("dwFlags", _DWORD),
        ("time", _DWORD),
        ("dwExtraInfo", _ULONG_PTR),
    )


class HardwareInput(ctypes.Structure):
    _fields_ = (
        ("uMsg", _DWORD),
        ("wParamL", _WORD),
        ("wParamH", _WORD),
    )


class InputValue(ctypes.Union):
    _fields_ = (
        ("mi", MouseInput),
        ("ki", KeyboardInput),
        ("hi", HardwareInput),
    )


class Input(ctypes.Structure):
    _anonymous_ = ("value",)
    _fields_ = (("type", _DWORD), ("value", InputValue))


def send_unicode(char: str, pressed: bool) -> bool:
    if sys.platform != "win32":
        return False

    expected_size = 40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28
    if ctypes.sizeof(Input) != expected_size:
        logger.error(
            "Estrutura INPUT incompatível: esperado %d bytes, obtido %d",
            expected_size,
            ctypes.sizeof(Input),
        )
        return False

    flags = 0x0004 | (0x0002 if not pressed else 0)
    code_units = char.encode("utf-16-le", errors="surrogatepass")
    inputs = [
        Input(
            type=1,
            ki=KeyboardInput(
                wScan=int.from_bytes(code_units[i : i + 2], "little"),
                dwFlags=flags,
            ),
        )
        for i in range(0, len(code_units), 2)
    ]
    if not inputs:
        return False

    array = (Input * len(inputs))(*inputs)
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.SendInput.argtypes = (
        ctypes.c_uint,
        ctypes.POINTER(Input),
        ctypes.c_int,
    )
    user32.SendInput.restype = ctypes.c_uint
    sent = user32.SendInput(len(array), array, ctypes.sizeof(Input))
    if sent != len(array):
        logger.warning(
            "Windows recusou injeção Unicode (erro %d)",
            ctypes.get_last_error(),
        )
    return sent == len(array)
