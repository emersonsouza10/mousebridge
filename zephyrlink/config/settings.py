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
VALID_PLATFORMS = ("windows", "linux", "macos")
VALID_ARG_KINDS = ("none", "url", "path_in_dir", "enum")


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
    max_bytes: int = 16_000_000
    files_enabled: bool = True
    file_max_bytes: int = 200_000_000


@dataclass(frozen=True, slots=True)
class LaunchableApp:
    """Uma aplicação que o cliente autoriza o servidor a abrir.

    ``command`` é resolvido apenas no cliente (dono da máquina-alvo): o
    servidor nunca envia um caminho livre, só o ``id`` desta entrada.
    ``platform`` ``None`` vale para qualquer SO.

    Parâmetros do operador (Fase 2): quando ``accepts_args``, o cliente valida
    o único parâmetro conforme ``arg_kind`` antes de anexá-lo ao ``command``:

    * ``url`` — esquema em ``allowed_url_schemes`` e host em
      ``allowed_url_hosts`` (vazio = qualquer host);
    * ``path_in_dir`` — caminho resolvido dentro de ``allowed_dirs``;
    * ``enum`` — valor exato em ``enum_values``.
    """

    id: str
    label: str
    command: tuple[str, ...]
    platform: str | None = None
    accepts_args: bool = False
    arg_kind: str = "none"
    allowed_dirs: tuple[str, ...] = ()
    allowed_url_schemes: tuple[str, ...] = ("https",)
    allowed_url_hosts: tuple[str, ...] = ()
    enum_values: tuple[str, ...] = ()
    require_confirm: bool = False
    sha256: str | None = None


@dataclass(frozen=True, slots=True)
class LauncherConfig:
    enabled: bool = False
    rate_limit_per_min: int = 30
    audit_file: str | None = None
    apps: tuple[LaunchableApp, ...] = ()


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
    launcher: LauncherConfig = field(default_factory=LauncherConfig)


def _section(raw: dict[str, Any], key: str) -> dict[str, Any]:
    value = raw.get(key) or {}
    if not isinstance(value, dict):
        raise ConfigError(f"Seção '{key}' deve ser um mapeamento YAML")
    return value


def _build_launcher(raw: dict[str, Any]) -> LauncherConfig:
    entries = raw.get("apps") or []
    if not isinstance(entries, list):
        raise ConfigError("launcher.apps deve ser uma lista")
    apps: list[LaunchableApp] = []
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ConfigError("cada item de launcher.apps deve ser um mapeamento")
        app_id = str(entry.get("id", "")).strip()
        if not app_id:
            raise ConfigError("launcher.apps: cada app precisa de um 'id'")
        if app_id in seen:
            raise ConfigError(f"launcher.apps: id duplicado: {app_id!r}")
        command = entry.get("command")
        if isinstance(command, str):
            command = [command]
        if not isinstance(command, list) or not command:
            raise ConfigError(f"launcher.apps[{app_id}].command deve ser lista não vazia")
        if command[0] is None or not str(command[0]).strip():
            raise ConfigError(f"launcher.apps[{app_id}].command: o executável (1º item) não pode ser vazio")
        platform = entry.get("platform")
        if platform is not None and str(platform).lower() not in VALID_PLATFORMS:
            raise ConfigError(
                f"launcher.apps[{app_id}].platform deve ser um de {VALID_PLATFORMS}"
            )
        accepts_args = bool(entry.get("accepts_args", False))
        arg_kind = str(entry.get("arg_kind", "none")).lower()
        if arg_kind not in VALID_ARG_KINDS:
            raise ConfigError(f"launcher.apps[{app_id}].arg_kind deve ser um de {VALID_ARG_KINDS}")
        if accepts_args and arg_kind == "none":
            raise ConfigError(f"launcher.apps[{app_id}] aceita args mas arg_kind é 'none'")
        if not accepts_args:
            arg_kind = "none"
        allowed_dirs = tuple(str(d) for d in (entry.get("allowed_dirs") or ()))
        enum_values = tuple(str(v) for v in (entry.get("enum_values") or ()))
        if arg_kind == "path_in_dir" and not allowed_dirs:
            raise ConfigError(f"launcher.apps[{app_id}] arg_kind 'path_in_dir' exige allowed_dirs")
        if arg_kind == "enum" and not enum_values:
            raise ConfigError(f"launcher.apps[{app_id}] arg_kind 'enum' exige enum_values")
        sha256 = entry.get("sha256")
        if sha256 is not None:
            sha256 = str(sha256).strip().lower()
            if len(sha256) != 64 or any(c not in "0123456789abcdef" for c in sha256):
                raise ConfigError(f"launcher.apps[{app_id}].sha256 deve ser hex de 64 caracteres")
        seen.add(app_id)
        apps.append(
            LaunchableApp(
                id=app_id,
                label=str(entry.get("label", app_id)),
                command=tuple(str(part) for part in command),
                platform=str(platform).lower() if platform is not None else None,
                accepts_args=accepts_args,
                arg_kind=arg_kind,
                allowed_dirs=allowed_dirs,
                allowed_url_schemes=tuple(str(s).lower() for s in (entry.get("allowed_url_schemes") or ("https",))),
                allowed_url_hosts=tuple(str(h).lower() for h in (entry.get("allowed_url_hosts") or ())),
                enum_values=enum_values,
                require_confirm=bool(entry.get("require_confirm", False)),
                sha256=sha256,
            )
        )
    rate = int(raw.get("rate_limit_per_min", 30))
    if rate <= 0:
        raise ConfigError("launcher.rate_limit_per_min deve ser positivo")
    audit_file = raw.get("audit_file")
    return LauncherConfig(
        enabled=bool(raw.get("enabled", False)),
        rate_limit_per_min=rate,
        audit_file=str(audit_file) if audit_file else None,
        apps=tuple(apps),
    )


def build_config(raw: dict[str, Any]) -> AppConfig:
    """Constrói e valida um ``AppConfig`` a partir de um dicionário."""
    net = _section(raw, "network")
    sec = _section(raw, "security")
    layout = _section(raw, "layout")
    clip = _section(raw, "clipboard")
    launcher = _section(raw, "launcher")

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
            max_bytes=int(clip.get("max_bytes", 16_000_000)),
            files_enabled=bool(clip.get("files_enabled", True)),
            file_max_bytes=int(clip.get("file_max_bytes", 200_000_000)),
        ),
        launcher=_build_launcher(launcher),
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
