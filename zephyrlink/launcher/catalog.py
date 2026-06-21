"""Catálogo de aplicações autorizadas, mantido pelo cliente (máquina-alvo).

O servidor só conhece o ``id`` de cada app; o mapeamento ``id → comando`` vive
aqui, do lado de quem executa. Isso impede que o operador peça um executável
arbitrário: ele só pode disparar o que esta máquina declarou.
"""

from __future__ import annotations

import sys

from zephyrlink.config import LaunchableApp, LauncherConfig
from zephyrlink.transport import Message, MsgType


def current_platform() -> str:
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


class AppCatalog:
    """Apps que esta máquina aceita abrir, filtrados pelo SO atual."""

    def __init__(self, apps: list[LaunchableApp]) -> None:
        plat = current_platform()
        self._apps = {a.id: a for a in apps if a.platform in (None, plat)}

    @classmethod
    def from_config(cls, config: LauncherConfig) -> "AppCatalog":
        return cls(list(config.apps) if config.enabled else [])

    def resolve(self, app_id: str) -> LaunchableApp | None:
        return self._apps.get(app_id)

    def entries(self) -> list[LaunchableApp]:
        return list(self._apps.values())

    def catalog_message(self) -> Message:
        return Message(
            MsgType.LAUNCH_CATALOG,
            {"apps": [
                {"id": a.id, "label": a.label, "accepts_args": a.accepts_args, "arg_kind": a.arg_kind}
                for a in self._apps.values()
            ]},
        )
