"""Gravação de partes do ``config.yaml`` a partir da GUI.

Carrega o YAML existente, substitui apenas as seções tocadas e regrava,
mantendo as demais. Comentários do arquivo original não sobrevivem (PyYAML
não faz round-trip de comentários).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def _load(file: Path) -> dict[str, Any]:
    if file.exists():
        loaded = yaml.safe_load(file.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            return loaded
    return {}


def _dump(file: Path, raw: dict[str, Any]) -> None:
    file.write_text(yaml.safe_dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8")


def _section(raw: dict[str, Any], key: str) -> dict[str, Any]:
    section = raw.get(key)
    return section if isinstance(section, dict) else {}


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
    raw = _load(file)
    launcher = _section(raw, "launcher")
    launcher["enabled"] = enabled
    launcher["apps"] = [_app_to_dict(a) for a in apps]
    raw["launcher"] = launcher
    _dump(file, raw)


def save_connection(path: str, *, role: str, manual_host: str | None, shared_key: str) -> None:
    """Persiste papel, IP manual e chave para a GUI pré-preencher na próxima vez."""
    file = Path(path)
    raw = _load(file)
    raw["role"] = role
    network = _section(raw, "network")
    network["manual_host"] = manual_host or None
    raw["network"] = network
    security = _section(raw, "security")
    security["shared_key"] = shared_key
    raw["security"] = security
    _dump(file, raw)
