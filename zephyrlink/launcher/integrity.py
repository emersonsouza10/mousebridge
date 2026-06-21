"""Verificação de integridade do executável antes de abrir.

Calcula o SHA-256 do binário resolvido (``command[0]``) e compara com o valor
declarado no catálogo. Defende contra a troca do binário no disco — por exemplo
um malware que substitui ``notepad.exe``: se o hash não bater, a abertura é
recusada. Apps sem ``sha256`` passam direto (verificação opcional por app).
"""

from __future__ import annotations

import asyncio
import hashlib
import shutil
from pathlib import Path

from zephyrlink.config import LaunchableApp

_CHUNK = 1 << 20


class IntegrityError(Exception):
    """Hash do executável não confere ou binário ausente."""


def _resolve(command: tuple[str, ...]) -> str:
    exe = command[0]
    found = shutil.which(exe)
    if found is None and Path(exe).is_file():
        found = exe
    if found is None:
        raise IntegrityError(f"executável não encontrado: {exe}")
    return found


def _digest(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verify(app: LaunchableApp) -> None:
    if app.sha256 is None:
        return
    path = _resolve(app.command)
    try:
        actual = _digest(path)
    except OSError as exc:
        raise IntegrityError(str(exc)) from exc
    if actual != app.sha256:
        raise IntegrityError("hash do executável não confere")


async def verify_executable(app: LaunchableApp) -> None:
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _verify, app)
