"""Gravação do catálogo do launcher de volta no arquivo YAML.

Carrega o YAML existente, substitui apenas a seção ``launcher`` e regrava,
mantendo as demais seções. Comentários do arquivo original não sobrevivem
(PyYAML não faz round-trip de comentários).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def _app_to_dict(app: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": app["id"],
        "label": app["label"],
        "command": list(app["command"]),
    }
    if app.get("platform"):
        out["platform"] = app["platform"]
    return out


def save_launcher(path: str, enabled: bool, apps: list[dict[str, Any]]) -> None:
    file = Path(path)
    raw: dict[str, Any] = {}
    if file.exists():
        loaded = yaml.safe_load(file.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            raw = loaded
    launcher = raw.get("launcher")
    if not isinstance(launcher, dict):
        launcher = {}
    launcher["enabled"] = enabled
    launcher["apps"] = [_app_to_dict(a) for a in apps]
    raw["launcher"] = launcher
    file.write_text(
        yaml.safe_dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
