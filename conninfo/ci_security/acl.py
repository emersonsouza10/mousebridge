"""ACL por usuário do agente (SPEC §22, ARQUITETURA §10.3).

Cada agente tem uma identidade (``user``) provada no handshake (token opcional por
agente). A ACL decide **quais conexões** ele enxerga/usa e se é forçado a
**somente leitura**. Sem ACL configurada, o comportamento é retrocompatível: um
único agente implícito ``agent`` com acesso a todas as conexões e o ``read_only``
de cada conexão valendo como está.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AclDecision:
    user: str
    allowed: frozenset[str] | None  # None = todas
    force_read_only: bool

    def allows(self, connection_id: str) -> bool:
        return self.allowed is None or connection_id in self.allowed

    def filter_catalog(self, catalog: list[dict[str, str]]) -> list[dict[str, str]]:
        return [c for c in catalog if self.allows(c.get("id", ""))]


class Acl:
    def __init__(self, entries: tuple) -> None:
        self._by_user = {e.user: e for e in entries}
        self.enabled = bool(entries)

    def authorize(self, user: str | None, token: str | None) -> AclDecision | None:
        """Devolve a decisão para ``user``/``token`` ou ``None`` se negado."""
        if not self.enabled:
            return AclDecision(user=user or "agent", allowed=None, force_read_only=False)
        if not user:
            return None
        entry = self._by_user.get(user)
        if entry is None:
            return None
        # comparação em tempo constante evita oráculo de timing no token
        if entry.token is not None and not secrets.compare_digest(entry.token, token or ""):
            return None
        allowed = None if entry.connections is None else frozenset(entry.connections)
        return AclDecision(user=user, allowed=allowed, force_read_only=(entry.mode == "read-only"))
