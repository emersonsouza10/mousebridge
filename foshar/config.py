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
