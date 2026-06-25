"""Descoberta de conexões Oracle a partir do ``tnsnames.ora`` (SPEC §13.1).

Procura o arquivo em ``$TNS_ADMIN`` e ``$ORACLE_HOME/network/admin`` e lista os
aliases. Cada alias vira um ``ConnectionRef`` com ``dsn={"dsn": alias}`` — o
cliente resolve host/serviço pelo próprio ``tnsnames.ora``; nenhuma credencial é
lida ou transmitida.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from conninfo.ci_discovery.base import ConnectionRef, ConnectionScanner

_COMMENT = re.compile(r"#[^\n]*")


def parse_tnsnames(text: str) -> list[str]:
    """Extrai os aliases de um conteúdo de ``tnsnames.ora`` (preserva a ordem)."""
    text = _COMMENT.sub("", text)
    aliases: list[str] = []
    seen: set[str] = set()
    depth = 0
    token = ""
    for ch in text:
        if ch == "(":
            if depth == 0:
                name_part = token.split("=")[0]
                for raw in name_part.split(","):
                    name = raw.strip()
                    if name and name.upper() not in seen:
                        seen.add(name.upper())
                        aliases.append(name)
                token = ""
            depth += 1
        elif ch == ")":
            if depth > 0:
                depth -= 1
            if depth == 0:
                token = ""
        elif depth == 0:
            token += ch
    return aliases


def _tnsnames_paths() -> list[Path]:
    paths: list[Path] = []
    tns_admin = os.environ.get("TNS_ADMIN")
    if tns_admin:
        paths.append(Path(tns_admin) / "tnsnames.ora")
    oracle_home = os.environ.get("ORACLE_HOME")
    if oracle_home:
        paths.append(Path(oracle_home) / "network" / "admin" / "tnsnames.ora")
    return paths


class OracleScanner(ConnectionScanner):
    engine = "oracle"

    def __init__(self, paths: list[Path] | None = None) -> None:
        self._paths = paths if paths is not None else _tnsnames_paths()

    def scan(self) -> list[ConnectionRef]:
        refs: list[ConnectionRef] = []
        seen: set[str] = set()
        for path in self._paths:
            if not path.exists():
                continue
            for alias in parse_tnsnames(path.read_text(encoding="utf-8", errors="replace")):
                key = alias.upper()
                if key in seen:
                    continue
                seen.add(key)
                refs.append(
                    ConnectionRef(
                        id=f"oracle_{alias.lower()}",
                        engine="oracle",
                        name=alias,
                        dsn={"dsn": alias},
                        source="tnsnames",
                        needs_secret=True,
                    )
                )
        return refs
