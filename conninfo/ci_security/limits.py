"""Política de segurança por padrão: read-only e limite de linhas (SPEC §16, RNF05).

O modo padrão é somente leitura. A SPEC pede bloqueio de DDL/DML por padrão; aqui
isso é feito por classificação do primeiro comando significativo da SQL — barato e
suficiente para o MVP. (Evolução possível: transação read-only no driver, mais
robusta que checagem de palavra-chave, ver docs/ARQUITETURA.md §10.)
"""

from __future__ import annotations

import re
import threading
import time
from collections import defaultdict, deque

# Comandos de escrita/estrutura bloqueados quando a conexão é somente leitura.
_BLOCKED = frozenset(
    {
        "INSERT", "UPDATE", "DELETE", "MERGE", "UPSERT",
        "DROP", "TRUNCATE", "ALTER", "CREATE", "REPLACE",
        "GRANT", "REVOKE", "COMMIT", "ROLLBACK", "SET",
        "CALL", "EXEC", "EXECUTE", "ATTACH", "DETACH", "PRAGMA",
        "VACUUM", "REINDEX", "ANALYZE",
    }
)
# Comandos de leitura permitidos em modo somente leitura.
_READ_ONLY = frozenset({"SELECT", "WITH", "EXPLAIN", "SHOW", "DESCRIBE", "DESC", "VALUES"})

_COMMENT = re.compile(r"(/\*.*?\*/|--[^\n]*)", re.DOTALL)
_FIRST_WORD = re.compile(r"[A-Za-z]+")


class PolicyError(Exception):
    """Operação recusada pela política (read-only, fora dos limites)."""

    code = "SQL_BLOCKED_BY_POLICY"


def first_keyword(sql: str) -> str:
    stripped = _COMMENT.sub(" ", sql).lstrip()
    match = _FIRST_WORD.match(stripped)
    return match.group(0).upper() if match else ""


def ensure_read_only(sql: str) -> None:
    """Levanta ``PolicyError`` se ``sql`` não for claramente uma leitura."""
    keyword = first_keyword(sql)
    if not keyword:
        raise PolicyError("comando vazio ou não reconhecido")
    if keyword in _READ_ONLY:
        return
    if keyword in _BLOCKED:
        raise PolicyError(f"comando '{keyword}' bloqueado: conexão é somente leitura")
    raise PolicyError(f"comando '{keyword}' não permitido em modo somente leitura")


def clamp_max_rows(requested: int | None, conn_limit: int | None, default: int) -> int:
    """Menor entre o pedido, o teto da conexão e o default — nunca acima do teto."""
    ceiling = conn_limit if conn_limit is not None else default
    if requested is None or requested <= 0:
        return ceiling
    return min(requested, ceiling)


class RateLimiter:
    """Janela deslizante de 60 s por usuário (espelha o ``_allow_rate`` do foshar)."""

    def __init__(self, per_minute: int) -> None:
        self._limit = per_minute
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, user: str) -> bool:
        if self._limit <= 0:
            return True
        now = time.monotonic()
        with self._lock:
            window = self._hits[user]
            while window and window[0] <= now - 60.0:
                window.popleft()
            if len(window) >= self._limit:
                return False
            window.append(now)
            return True
