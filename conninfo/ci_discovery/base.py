"""Descoberta automática de conexões no cliente (SPEC §13).

Um scanner por engine identifica **quais** conexões existem no ambiente local
(tnsnames.ora, pg_service.conf, DSNs ODBC…) — nunca lê ou transmite a senha. O
catálogo descoberto se funde com o cadastro manual do ``config.yaml`` por ``id``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class ConnectionRef:
    id: str
    engine: str
    name: str
    dsn: dict[str, Any] = field(default_factory=dict)
    source: str = "manual"
    needs_secret: bool = True

    def catalog_entry(self) -> dict[str, str]:
        return {"id": self.id, "engine": self.engine, "name": self.name, "source": self.source}


class ConnectionScanner(ABC):
    engine: str

    @abstractmethod
    def scan(self) -> list[ConnectionRef]:
        """Varre o ambiente local e devolve as conexões encontradas."""
