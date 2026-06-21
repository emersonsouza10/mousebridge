"""Execução do processo no cliente.

Sempre ``shell=False``: o comando é a lista do catálogo (``command``) seguida
dos parâmetros já validados em ``validate_args`` — nunca uma string de shell.
"""

from __future__ import annotations

import asyncio
import subprocess

from zephyrlink.config import LaunchableApp


class LaunchError(Exception):
    """Falha ao iniciar a aplicação."""


def _spawn(command: list[str]) -> int:
    creationflags = 0
    if hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
    try:
        proc = subprocess.Popen(command, shell=False, creationflags=creationflags)
    except (OSError, ValueError) as exc:
        raise LaunchError(str(exc)) from exc
    return proc.pid


async def launch(app: LaunchableApp, args: list[str] | None = None) -> int:
    """Inicia a aplicação e devolve o PID. Não aguarda o encerramento.

    ``args`` já deve estar validado (ver ``validate_args``)."""
    command = [*app.command, *(args or [])]
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _spawn, command)
