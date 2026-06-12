"""Configuração da aplicação carregada de arquivo YAML.

Todas as seções possuem valores padrão; o YAML só precisa declarar o que
difere do padrão. Validação acontece em ``load_config`` para que erros de
configuração apareçam cedo, com mensagens claras.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

VALID_EDGES = ("left", "right", "top", "bottom")
VALID_ROLES = ("server", "client")


class ConfigError(Exception):
    """Configuração inválida ou ilegível."""


@dataclass(frozen=True, slots=True)
class NetworkConfig:
    tcp_port: int = 50510
    discovery_port: int = 50511
    manual_host: str | None = None
    heartbeat_interval: float = 2.0
    heartbeat_timeout: float = 8.0
    reconnect_delay: float = 3.0
    discovery_timeout: float = 5.0


@dataclass(frozen=True, slots=True)
class SecurityConfig:
    shared_key: str = "change-me"
    use_tls: bool = False
    tls_cert: str | None = None
    tls_key: str | None = None
    tls_ca: str | None = None
    allowed_hosts: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class LayoutConfig:
    """Posição da tela secundária em relação à principal."""

    edge: str = "right"
    switch_margin: int = 1
    return_inset: int = 3


@dataclass(frozen=True, slots=True)
class ClipboardConfig:
    enabled: bool = True
    poll_interval: float = 0.5
    max_bytes: int = 1_000_000


@dataclass(frozen=True, slots=True)
class AppConfig:
    role: str = "server"
    name: str = "zephyrlink"
    log_level: str = "INFO"
    log_json: bool = False
    network: NetworkConfig = field(default_factory=NetworkConfig)
    security: SecurityConfig = field(default_factory=SecurityConfig)
    layout: LayoutConfig = field(default_factory=LayoutConfig)
    clipboard: ClipboardConfig = field(default_factory=ClipboardConfig)


def _section(raw: dict[str, Any], key: str) -> dict[str, Any]:
    value = raw.get(key) or {}
    if not isinstance(value, dict):
        raise ConfigError(f"Seção '{key}' deve ser um mapeamento YAML")
    return value


def build_config(raw: dict[str, Any]) -> AppConfig:
    """Constrói e valida um ``AppConfig`` a partir de um dicionário."""
    net = _section(raw, "network")
    sec = _section(raw, "security")
    layout = _section(raw, "layout")
    clip = _section(raw, "clipboard")

    config = AppConfig(
        role=str(raw.get("role", "server")),
        name=str(raw.get("name", "zephyrlink")),
        log_level=str(raw.get("log_level", "INFO")).upper(),
        log_json=bool(raw.get("log_json", False)),
        network=NetworkConfig(
            tcp_port=int(net.get("tcp_port", 50510)),
            discovery_port=int(net.get("discovery_port", 50511)),
            manual_host=net.get("manual_host"),
            heartbeat_interval=float(net.get("heartbeat_interval", 2.0)),
            heartbeat_timeout=float(net.get("heartbeat_timeout", 8.0)),
            reconnect_delay=float(net.get("reconnect_delay", 3.0)),
            discovery_timeout=float(net.get("discovery_timeout", 5.0)),
        ),
        security=SecurityConfig(
            shared_key=str(sec.get("shared_key", "change-me")),
            use_tls=bool(sec.get("use_tls", False)),
            tls_cert=sec.get("tls_cert"),
            tls_key=sec.get("tls_key"),
            tls_ca=sec.get("tls_ca"),
            allowed_hosts=tuple(sec.get("allowed_hosts") or ()),
        ),
        layout=LayoutConfig(
            edge=str(layout.get("edge", "right")).lower(),
            switch_margin=int(layout.get("switch_margin", 1)),
            return_inset=int(layout.get("return_inset", 3)),
        ),
        clipboard=ClipboardConfig(
            enabled=bool(clip.get("enabled", True)),
            poll_interval=float(clip.get("poll_interval", 0.5)),
            max_bytes=int(clip.get("max_bytes", 1_000_000)),
        ),
    )

    if config.role not in VALID_ROLES:
        raise ConfigError(f"role deve ser um de {VALID_ROLES}, recebido: {config.role!r}")
    if config.layout.edge not in VALID_EDGES:
        raise ConfigError(f"layout.edge deve ser um de {VALID_EDGES}, recebido: {config.layout.edge!r}")
    if not (0 < config.network.tcp_port < 65536):
        raise ConfigError(f"network.tcp_port fora do intervalo: {config.network.tcp_port}")
    if not (0 < config.network.discovery_port < 65536):
        raise ConfigError(f"network.discovery_port fora do intervalo: {config.network.discovery_port}")
    if config.network.heartbeat_timeout <= config.network.heartbeat_interval:
        raise ConfigError("network.heartbeat_timeout deve ser maior que heartbeat_interval")
    if config.security.use_tls and not (config.security.tls_cert and config.security.tls_key):
        raise ConfigError("security.use_tls exige tls_cert e tls_key")
    if not config.security.shared_key:
        raise ConfigError("security.shared_key não pode ser vazio")
    return config


def load_config(path: str | Path | None) -> AppConfig:
    """Carrega configuração de um YAML; sem arquivo, usa todos os padrões."""
    if path is None:
        return build_config({})
    path = Path(path)
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ConfigError(f"Arquivo de configuração não encontrado: {path}") from None
    except yaml.YAMLError as exc:
        raise ConfigError(f"YAML inválido em {path}: {exc}") from exc
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ConfigError(f"Configuração em {path} deve ser um mapeamento YAML")
    logger.info("Configuração carregada de %s", path)
    return build_config(raw)
