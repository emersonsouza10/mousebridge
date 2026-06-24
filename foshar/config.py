"""Configuração do Foshar.

Lê o mesmo ``config.yaml`` do ZephyrLink: a seção ``foshar:`` (porta, cache,
shares) e a seção ``security:`` (reusa a chave/TLS/allowlist). Assim as duas
ferramentas compartilham a mesma chave sem duplicar configuração.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from zephyrlink.config import SecurityConfig

from foshar.foshar_security.shares import VALID_MODES, Share

DEFAULT_PORT = 50513
DEFAULT_CACHE = "~/.foshar/cache"


class FosharConfigError(Exception):
    """Configuração do Foshar inválida ou ilegível."""


@dataclass(frozen=True, slots=True)
class FosharConfig:
    enabled: bool = False
    port: int = DEFAULT_PORT
    cache_dir: str = DEFAULT_CACHE
    audit_file: str | None = None
    rate_limit_per_min: int = 600
    shares: tuple[Share, ...] = ()
    security: SecurityConfig = field(default_factory=SecurityConfig)


def _build_shares(entries: Any) -> tuple[Share, ...]:
    if not isinstance(entries, list):
        raise FosharConfigError("foshar.shares deve ser uma lista")
    shares: list[Share] = []
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise FosharConfigError("cada item de foshar.shares deve ser um mapeamento")
        share_id = str(entry.get("id", "")).strip()
        if not share_id:
            raise FosharConfigError("foshar.shares: cada share precisa de um 'id'")
        if share_id in seen:
            raise FosharConfigError(f"foshar.shares: id duplicado: {share_id!r}")
        raw_path = entry.get("path")
        if not raw_path:
            raise FosharConfigError(f"foshar.shares[{share_id}] precisa de 'path'")
        mode = str(entry.get("mode", "ro")).lower()
        if mode not in VALID_MODES:
            raise FosharConfigError(f"foshar.shares[{share_id}].mode deve ser um de {VALID_MODES}")
        seen.add(share_id)
        shares.append(Share(id=share_id, path=Path(str(raw_path)).expanduser().resolve(), mode=mode))
    return tuple(shares)


def _build_security(raw: dict[str, Any]) -> SecurityConfig:
    return SecurityConfig(
        shared_key=str(raw.get("shared_key", "change-me")),
        use_tls=bool(raw.get("use_tls", False)),
        tls_cert=raw.get("tls_cert"),
        tls_key=raw.get("tls_key"),
        tls_ca=raw.get("tls_ca"),
        allowed_hosts=tuple(raw.get("allowed_hosts") or ()),
    )


def read_foshar_section(path: str | Path | None) -> tuple[list[dict[str, str]], int, str]:
    """Lê a seção ``foshar`` crua (strings como o usuário digitou) para a GUI editar.

    Diferente de ``load_foshar_config``, não resolve os caminhos para absolutos —
    preserva ``~/dev/projeto`` como veio, para o formulário reexibir e regravar.
    """
    if path is None or not Path(path).exists():
        return [], DEFAULT_PORT, DEFAULT_CACHE
    loaded = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    foshar = loaded.get("foshar") if isinstance(loaded, dict) else {}
    if not isinstance(foshar, dict):
        foshar = {}
    shares: list[dict[str, str]] = []
    for entry in foshar.get("shares") or []:
        if isinstance(entry, dict) and entry.get("path"):
            shares.append(
                {
                    "id": str(entry.get("id", "")).strip(),
                    "path": str(entry["path"]),
                    "mode": str(entry.get("mode", "ro")).lower(),
                }
            )
    return shares, int(foshar.get("port", DEFAULT_PORT)), str(foshar.get("cache_dir", DEFAULT_CACHE))


def save_shares(
    path: str | Path, *, enabled: bool, port: int, cache_dir: str, shares: list[dict[str, str]]
) -> None:
    """Grava a seção ``foshar`` no ``config.yaml``, preservando as demais seções.

    Comentários do arquivo original não sobrevivem (PyYAML não faz round-trip de
    comentários), mas as outras seções (``security`` etc.) são mantidas.
    """
    file = Path(path)
    raw: dict[str, Any] = {}
    if file.exists():
        loaded = yaml.safe_load(file.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            raw = loaded
    foshar = raw.get("foshar")
    if not isinstance(foshar, dict):
        foshar = {}
    foshar["enabled"] = enabled
    foshar["port"] = port
    foshar["cache_dir"] = cache_dir
    foshar["shares"] = [
        {"id": s["id"], "path": s["path"], "mode": s["mode"]} for s in shares
    ]
    raw["foshar"] = foshar
    file.write_text(yaml.safe_dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8")


def load_foshar_config(path: str | Path | None) -> FosharConfig:
    if path is None:
        raw: dict[str, Any] = {}
    else:
        try:
            raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        except FileNotFoundError:
            raise FosharConfigError(f"Arquivo de configuração não encontrado: {path}") from None
        except yaml.YAMLError as exc:
            raise FosharConfigError(f"YAML inválido em {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise FosharConfigError("Configuração deve ser um mapeamento YAML")
    foshar = raw.get("foshar") or {}
    if not isinstance(foshar, dict):
        raise FosharConfigError("Seção 'foshar' deve ser um mapeamento")
    return FosharConfig(
        enabled=bool(foshar.get("enabled", False)),
        port=int(foshar.get("port", DEFAULT_PORT)),
        cache_dir=str(foshar.get("cache_dir", DEFAULT_CACHE)),
        audit_file=foshar.get("audit_file"),
        rate_limit_per_min=int(foshar.get("rate_limit_per_min", 600)),
        shares=_build_shares(foshar.get("shares") or []),
        security=_build_security(raw.get("security") or {}),
    )
