"""Catálogo de aplicações autorizadas, mantido pelo cliente (máquina-alvo).

O servidor só conhece o ``id`` de cada app; o mapeamento ``id → comando`` vive
aqui, do lado de quem executa. Isso impede que o operador peça um executável
arbitrário: ele só pode disparar o que esta máquina declarou.
"""

from __future__ import annotations

import sys

from zephyrlink.config import LaunchableApp, LauncherConfig
from zephyrlink.transport import Message, MsgType


def resolve_app_ref(catalog: list[dict], ref: str) -> str:
    """Resolve uma referência de app (``id`` ou ``label``) para o ``id``.

    Usado no servidor para que ``launch --app`` aceite o nome amigável. Casa
    primeiro por ``id`` exato, depois por ``label`` exato e por fim por
    ``label`` ignorando maiúsculas. Não resolvido devolve ``ref`` como veio —
    o cliente recusa se não existir."""
    ids = {a.get("id") for a in catalog}
    if ref in ids:
        return ref
    for app in catalog:
        if app.get("label") == ref:
            return str(app["id"])
    lowered = ref.lower()
    for app in catalog:
        if str(app.get("label", "")).lower() == lowered:
            return str(app["id"])
    return ref


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
