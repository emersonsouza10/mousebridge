"""Configuração da connInfo.

Lê o mesmo ``config.yaml`` do ZephyrLink: a seção ``conninfo:`` (porta, conexões,
limites) e reusa a seção ``security:`` (chave/TLS/allowlist). As conexões e suas
políticas (``read_only``) vivem **no cliente** — nunca no servidor.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import yaml

from zephyrlink.config import SecurityConfig

DEFAULT_PORT = 50514
DEFAULT_SESSION_TIMEOUT_S = 1800
DEFAULT_MAX_ROWS = 10000
DEFAULT_RATE_LIMIT = 600
DEFAULT_POOL_SIZE = 16
DEFAULT_META_CACHE_TTL_S = 300

VALID_ENGINES = ("oracle",)


class ConnInfoConfigError(Exception):
    """Configuração da connInfo inválida ou ilegível."""


@dataclass(frozen=True, slots=True)
class ConnDef:
    """Uma conexão de banco publicada pelo cliente."""

    id: str
    engine: str
    name: str
    dsn: dict[str, Any]
    read_only: bool = True
    max_rows: int | None = None
    timeout_ms: int | None = None
    source: str = "manual"  # "manual" | "tnsnames" | ...

    def catalog_entry(self) -> dict[str, str]:
        """O que o agente vê no catálogo — nunca expõe credenciais."""
        return {
            "id": self.id,
            "engine": self.engine,
            "name": self.name,
            "source": self.source,
            "read_only": str(self.read_only).lower(),
        }


@dataclass(frozen=True, slots=True)
class DiscoveryConfig:
    """Quais scanners de descoberta rodar e onde procurar (SPEC §13)."""

    oracle_enabled: bool = False
    oracle_tnsnames: tuple[str, ...] = ()  # caminhos explícitos; vazio = usa TNS_ADMIN/ORACLE_HOME


# Engines que usam login usuário/senha.
LOGIN_ENGINES = frozenset({"oracle"})


@dataclass(frozen=True, slots=True)
class Credentials:
    """Credencial única, global, resolvida NO CLIENTE (SPEC §14/§22).

    A senha fica no cliente (texto no YAML ou, preferível, via ``password_env``) e
    nunca trafega: o agente só recebe o ``session_id``. A credencial de uma conexão
    específica (campo ``user`` no ``dsn``) sempre tem precedência sobre esta global.
    """

    username: str | None = None
    password: str | None = None


@dataclass(frozen=True, slots=True)
class AclEntry:
    """Permissões de um agente (SPEC §22). ``connections=None`` significa todas."""

    user: str
    token: str | None = None
    connections: tuple[str, ...] | None = None
    mode: str = "read-only"  # "read-only" | "read-write"


@dataclass(frozen=True, slots=True)
class ConnInfoConfig:
    enabled: bool = False
    port: int = DEFAULT_PORT
    audit_file: str | None = None
    session_timeout_s: int = DEFAULT_SESSION_TIMEOUT_S
    max_rows_default: int = DEFAULT_MAX_ROWS
    rate_limit_per_min: int = DEFAULT_RATE_LIMIT
    pool_size: int = DEFAULT_POOL_SIZE
    metadata_cache_ttl_s: int = DEFAULT_META_CACHE_TTL_S
    connections: tuple[ConnDef, ...] = ()
    acl: tuple[AclEntry, ...] = ()
    discovery: DiscoveryConfig = field(default_factory=DiscoveryConfig)
    credentials: Credentials = field(default_factory=Credentials)
    security: SecurityConfig = field(default_factory=SecurityConfig)

    def resolve_login(self, conn_def: "ConnDef", agent_secret: str | None) -> tuple[dict[str, Any], str | None]:
        """DSN e senha efetivos para abrir uma conexão (credencial específica vence a global)."""
        dsn = dict(conn_def.dsn)
        if conn_def.engine not in LOGIN_ENGINES:
            return dsn, None  # engine sem login usuário/senha
        if self.credentials.username and "user" not in dsn:
            dsn["user"] = self.credentials.username
        secret = agent_secret or self.credentials.password
        return dsn, secret

    def connection(self, conn_id: str) -> ConnDef | None:
        return next((c for c in self.connections if c.id == conn_id), None)

    def catalog(self) -> list[dict[str, str]]:
        return [c.catalog_entry() for c in self.connections]


def _build_connections(entries: Any) -> tuple[ConnDef, ...]:
    if not isinstance(entries, list):
        raise ConnInfoConfigError("conninfo.connections deve ser uma lista")
    conns: list[ConnDef] = []
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ConnInfoConfigError("cada item de conninfo.connections deve ser um mapeamento")
        conn_id = str(entry.get("id", "")).strip()
        if not conn_id:
            raise ConnInfoConfigError("conninfo.connections: cada conexão precisa de um 'id'")
        if conn_id in seen:
            raise ConnInfoConfigError(f"conninfo.connections: id duplicado: {conn_id!r}")
        engine = str(entry.get("engine", "")).lower()
        if engine not in VALID_ENGINES:
            raise ConnInfoConfigError(
                f"conninfo.connections[{conn_id}].engine deve ser um de {VALID_ENGINES}"
            )
        dsn = entry.get("dsn") or {}
        if not isinstance(dsn, dict):
            raise ConnInfoConfigError(f"conninfo.connections[{conn_id}].dsn deve ser um mapeamento")
        seen.add(conn_id)
        conns.append(
            ConnDef(
                id=conn_id,
                engine=engine,
                name=str(entry.get("name", conn_id)),
                dsn=dsn,
                read_only=bool(entry.get("read_only", True)),
                max_rows=entry.get("max_rows"),
                timeout_ms=entry.get("timeout_ms"),
            )
        )
    return tuple(conns)


_VALID_MODES = ("read-only", "read-write")


def _build_acl(entries: Any) -> tuple[AclEntry, ...]:
    if not entries:
        return ()
    if not isinstance(entries, list):
        raise ConnInfoConfigError("conninfo.acl deve ser uma lista")
    acl: list[AclEntry] = []
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ConnInfoConfigError("cada item de conninfo.acl deve ser um mapeamento")
        user = str(entry.get("user", "")).strip()
        if not user:
            raise ConnInfoConfigError("conninfo.acl: cada item precisa de 'user'")
        if user in seen:
            raise ConnInfoConfigError(f"conninfo.acl: user duplicado: {user!r}")
        mode = str(entry.get("mode", "read-only")).lower()
        if mode not in _VALID_MODES:
            raise ConnInfoConfigError(f"conninfo.acl[{user}].mode deve ser um de {_VALID_MODES}")
        raw_conns = entry.get("connections")
        if raw_conns in (None, "*"):
            conns: tuple[str, ...] | None = None
        elif isinstance(raw_conns, list):
            conns = tuple(str(c) for c in raw_conns)
        else:
            raise ConnInfoConfigError(f"conninfo.acl[{user}].connections deve ser lista ou '*'")
        token = entry.get("token")
        seen.add(user)
        acl.append(AclEntry(user=user, token=(str(token) if token else None), connections=conns, mode=mode))
    return tuple(acl)


def _build_discovery(raw: Any) -> DiscoveryConfig:
    if not raw:
        return DiscoveryConfig()
    if not isinstance(raw, dict):
        raise ConnInfoConfigError("conninfo.discovery deve ser um mapeamento")
    oracle = raw.get("oracle")
    if oracle is None:
        return DiscoveryConfig()
    if isinstance(oracle, bool):
        return DiscoveryConfig(oracle_enabled=oracle)
    if not isinstance(oracle, dict):
        raise ConnInfoConfigError("conninfo.discovery.oracle deve ser bool ou mapeamento")
    tns = oracle.get("tnsnames")
    if tns is None:
        paths: tuple[str, ...] = ()
    elif isinstance(tns, str):
        paths = (tns,)
    elif isinstance(tns, list):
        paths = tuple(str(p) for p in tns)
    else:
        raise ConnInfoConfigError("conninfo.discovery.oracle.tnsnames deve ser string ou lista")
    return DiscoveryConfig(oracle_enabled=bool(oracle.get("enabled", True)), oracle_tnsnames=paths)


def _build_credentials(raw: Any) -> Credentials:
    if not raw:
        return Credentials()
    if not isinstance(raw, dict):
        raise ConnInfoConfigError("conninfo.credentials deve ser um mapeamento")
    import os

    username = raw.get("username")
    password = raw.get("password")
    env_name = raw.get("password_env")
    if env_name:  # senha via variável de ambiente: não fica no arquivo
        password = os.environ.get(str(env_name), password)
    return Credentials(
        username=(str(username) if username else None),
        password=(str(password) if password is not None else None),
    )


def _build_security(raw: dict[str, Any]) -> SecurityConfig:
    return SecurityConfig(
        shared_key=str(raw.get("shared_key", "change-me")),
        use_tls=bool(raw.get("use_tls", False)),
        tls_cert=raw.get("tls_cert"),
        tls_key=raw.get("tls_key"),
        tls_ca=raw.get("tls_ca"),
        allowed_hosts=tuple(raw.get("allowed_hosts") or ()),
    )


def load_conninfo_config(path: str | None) -> ConnInfoConfig:
    if path is None:
        raw: dict[str, Any] = {}
    else:
        from pathlib import Path

        try:
            raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        except FileNotFoundError:
            raise ConnInfoConfigError(f"Arquivo de configuração não encontrado: {path}") from None
        except yaml.YAMLError as exc:
            raise ConnInfoConfigError(f"YAML inválido em {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConnInfoConfigError("Configuração deve ser um mapeamento YAML")
    section = raw.get("conninfo") or {}
    if not isinstance(section, dict):
        raise ConnInfoConfigError("Seção 'conninfo' deve ser um mapeamento")
    return ConnInfoConfig(
        enabled=bool(section.get("enabled", False)),
        port=int(section.get("port", DEFAULT_PORT)),
        audit_file=section.get("audit_file"),
        session_timeout_s=int(section.get("session_timeout_s", DEFAULT_SESSION_TIMEOUT_S)),
        max_rows_default=int(section.get("max_rows_default", DEFAULT_MAX_ROWS)),
        rate_limit_per_min=int(section.get("rate_limit_per_min", DEFAULT_RATE_LIMIT)),
        pool_size=int(section.get("pool_size", DEFAULT_POOL_SIZE)),
        metadata_cache_ttl_s=int(section.get("metadata_cache_ttl_s", DEFAULT_META_CACHE_TTL_S)),
        connections=_build_connections(section.get("connections") or []),
        acl=_build_acl(section.get("acl")),
        discovery=_build_discovery(section.get("discovery")),
        credentials=_build_credentials(section.get("credentials")),
        security=_build_security(raw.get("security") or {}),
    )
