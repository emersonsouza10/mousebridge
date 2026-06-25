"""Sessões e cursores, mantidos no ProviderHost (SPEC §17/§18).

A conexão DBAPI real vive aqui (no cliente). Cada sessão guarda os cursores de
streaming abertos. Sessões inativas expiram automaticamente (reaping).
"""

from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass, field

from conninfo.providers.base import DatabaseProvider, ResultCursor


@dataclass(slots=True)
class Session:
    id: str
    engine: str
    connection_id: str
    provider: DatabaseProvider
    conn: object
    read_only: bool
    max_rows: int
    created_at: float
    last_activity: float
    user: str = "agent"
    db_user: str | None = None
    cursors: dict[str, ResultCursor] = field(default_factory=dict)
    executing: bool = False
    cancel_requested: bool = False
    in_tx: bool = False

    def touch(self) -> None:
        self.last_activity = time.time()


class SessionManager:
    """Indexa sessões por id; cria, recupera, expira e fecha em massa."""

    def __init__(self, timeout_s: int) -> None:
        self._timeout_s = timeout_s
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()

    def create(
        self,
        *,
        engine: str,
        connection_id: str,
        provider: DatabaseProvider,
        conn: object,
        read_only: bool,
        max_rows: int,
        user: str = "agent",
        db_user: str | None = None,
    ) -> Session:
        now = time.time()
        session = Session(
            id="sess-" + secrets.token_hex(8),
            engine=engine,
            connection_id=connection_id,
            provider=provider,
            conn=conn,
            read_only=read_only,
            max_rows=max_rows,
            created_at=now,
            last_activity=now,
            user=user,
            db_user=db_user,
        )
        with self._lock:
            self._sessions[session.id] = session
        return session

    def get(self, session_id: str) -> Session | None:
        with self._lock:
            return self._sessions.get(session_id)

    def drop(self, session_id: str) -> Session | None:
        with self._lock:
            return self._sessions.pop(session_id, None)

    def expired(self) -> list[Session]:
        cutoff = time.time() - self._timeout_s
        with self._lock:
            return [s for s in self._sessions.values() if s.last_activity < cutoff]

    def all(self) -> list[Session]:
        with self._lock:
            return list(self._sessions.values())
