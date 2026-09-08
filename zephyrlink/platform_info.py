"""Detecção centralizada de plataforma.

Evita espalhar ``sys.platform``/``platform.system()`` pelo código novo. O
projeto histórico usa ``sys.platform`` inline; estes sinalizadores derivam do
mesmo valor para manter coerência.
"""

from __future__ import annotations

import sys

IS_MACOS = sys.platform == "darwin"
IS_WINDOWS = sys.platform == "win32"
IS_LINUX = sys.platform.startswith("linux")

#: Nome legível da plataforma, para logs.
PLATFORM_NAME = "macOS" if IS_MACOS else "Windows" if IS_WINDOWS else "Linux" if IS_LINUX else sys.platform
