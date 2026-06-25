"""Junta a descoberta automática com o cadastro manual num catálogo único.

Rodado no cliente (onde os arquivos vivem). Conexões manuais do ``config.yaml``
têm precedência sobre as descobertas em caso de mesmo ``id``.
"""

from __future__ import annotations

from pathlib import Path

from conninfo.ci_discovery.base import ConnectionRef
from conninfo.ci_discovery.oracle import OracleScanner
from conninfo.config import ConnDef, ConnInfoConfig


def _to_conndef(ref: ConnectionRef) -> ConnDef:
    return ConnDef(
        id=ref.id, engine=ref.engine, name=ref.name, dsn=dict(ref.dsn),
        read_only=True, source=ref.source,
    )


def discover(config: ConnInfoConfig) -> list[ConnDef]:
    """Conexões encontradas no ambiente local (hoje: Oracle/tnsnames)."""
    found: list[ConnDef] = []
    disc = config.discovery
    if disc.oracle_enabled:
        paths = [Path(p).expanduser() for p in disc.oracle_tnsnames] or None
        found.extend(_to_conndef(ref) for ref in OracleScanner(paths=paths).scan())
    return found


def merged_connections(config: ConnInfoConfig) -> list[ConnDef]:
    """Catálogo efetivo: descobertas primeiro, manuais sobrescrevem por ``id``."""
    by_id: dict[str, ConnDef] = {c.id: c for c in discover(config)}
    for manual in config.connections:
        by_id[manual.id] = manual
    return list(by_id.values())
