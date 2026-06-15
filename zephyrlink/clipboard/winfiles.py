"""Leitura/escrita da lista de arquivos do clipboard no Windows (CF_HDROP).

``pyperclip`` só lida com texto; arquivos copiados no Explorer ficam no
clipboard como ``CF_HDROP`` (uma struct ``DROPFILES`` seguida da lista de
caminhos terminada por duplo NUL). Aqui o acesso é por ctypes puro, no mesmo
estilo do restante do projeto. Fora do Windows tudo é no-op.

Os ``argtypes``/``restype`` são declarados explicitamente: sem isso o ctypes
assume ``c_int`` (32 bits) e truncaria os handles de 64 bits, quebrando
``GetClipboardData``/``SetClipboardData`` no Windows de 64 bits.
"""

from __future__ import annotations

import ctypes
import logging
import sys

logger = logging.getLogger(__name__)

CF_HDROP = 15
GMEM_MOVEABLE = 0x0002


class _DROPFILES(ctypes.Structure):
    # pFiles(4) + pt(8) + fNC(4) + fWide(4) = 20 bytes
    _fields_ = (
        ("pFiles", ctypes.c_uint32),
        ("pt_x", ctypes.c_int32),
        ("pt_y", ctypes.c_int32),
        ("fNC", ctypes.c_int32),
        ("fWide", ctypes.c_int32),
    )


def _bind() -> tuple:
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    shell32 = ctypes.windll.shell32
    c = ctypes

    user32.OpenClipboard.argtypes = (c.c_void_p,)
    user32.OpenClipboard.restype = c.c_int
    user32.GetClipboardData.argtypes = (c.c_uint,)
    user32.GetClipboardData.restype = c.c_void_p
    user32.SetClipboardData.argtypes = (c.c_uint, c.c_void_p)
    user32.SetClipboardData.restype = c.c_void_p
    user32.IsClipboardFormatAvailable.argtypes = (c.c_uint,)

    shell32.DragQueryFileW.argtypes = (c.c_void_p, c.c_uint, c.c_wchar_p, c.c_uint)
    shell32.DragQueryFileW.restype = c.c_uint

    kernel32.GlobalAlloc.argtypes = (c.c_uint, c.c_size_t)
    kernel32.GlobalAlloc.restype = c.c_void_p
    kernel32.GlobalLock.argtypes = (c.c_void_p,)
    kernel32.GlobalLock.restype = c.c_void_p
    kernel32.GlobalUnlock.argtypes = (c.c_void_p,)
    kernel32.GlobalFree.argtypes = (c.c_void_p,)
    return user32, kernel32, shell32


def read_clipboard_files() -> list[str] | None:
    """Caminhos absolutos dos arquivos no clipboard, ou ``None`` se não houver."""
    if sys.platform != "win32":
        return None
    user32, _kernel32, shell32 = _bind()
    if not user32.IsClipboardFormatAvailable(CF_HDROP):
        return None
    if not user32.OpenClipboard(None):
        return None
    try:
        handle = user32.GetClipboardData(CF_HDROP)
        if not handle:
            return None
        count = shell32.DragQueryFileW(handle, 0xFFFFFFFF, None, 0)
        paths: list[str] = []
        for i in range(count):
            length = shell32.DragQueryFileW(handle, i, None, 0)
            buf = ctypes.create_unicode_buffer(length + 1)
            shell32.DragQueryFileW(handle, i, buf, length + 1)
            if buf.value:
                paths.append(buf.value)
        return paths or None
    except OSError:
        return None
    finally:
        user32.CloseClipboard()


def write_clipboard_files(paths: list[str]) -> bool:
    """Coloca ``paths`` no clipboard como CF_HDROP (colável no Explorer)."""
    if sys.platform != "win32" or not paths:
        return False
    user32, kernel32, _shell32 = _bind()

    # Lista de caminhos em UTF-16, cada um terminado por NUL, com NUL extra ao fim.
    listing = ("".join(p + "\0" for p in paths) + "\0").encode("utf-16-le")
    size = ctypes.sizeof(_DROPFILES) + len(listing)

    h_global = kernel32.GlobalAlloc(GMEM_MOVEABLE, size)
    if not h_global:
        return False
    ptr = kernel32.GlobalLock(h_global)
    if not ptr:
        kernel32.GlobalFree(h_global)
        return False
    try:
        header = _DROPFILES(pFiles=ctypes.sizeof(_DROPFILES), fWide=1)
        ctypes.memmove(ptr, ctypes.byref(header), ctypes.sizeof(_DROPFILES))
        ctypes.memmove(ptr + ctypes.sizeof(_DROPFILES), listing, len(listing))
    finally:
        kernel32.GlobalUnlock(h_global)

    if not user32.OpenClipboard(None):
        kernel32.GlobalFree(h_global)
        return False
    try:
        user32.EmptyClipboard()
        # Após SetClipboardData o sistema é dono do bloco; não liberar.
        if not user32.SetClipboardData(CF_HDROP, h_global):
            kernel32.GlobalFree(h_global)
            return False
        return True
    except OSError:
        return False
    finally:
        user32.CloseClipboard()
