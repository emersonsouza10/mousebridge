"""Cache de metadata no ProviderHost (ARQUITETURA §15, oportunidade 2).

Metadata (describe/list_tables/list_indexes…) muda pouco e é muito consultada por
agentes de inventário. O cache é por **conexão** (não por sessão — o esquema é o
mesmo para todas as sessões da mesma conexão) com TTL curto. ``ttl_s <= 0`` desliga.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Hashable


class MetaCache:
    def __init__(self, ttl_s: int) -> None:
        self._ttl = ttl_s
        self._data: dict[tuple, tuple[float, Any]] = {}
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return self._ttl > 0

    def get(self, connection_id: str, method: str, *args: Hashable) -> Any | None:
        if not self.enabled:
            return None
        key = (connection_id, method, args)
        with self._lock:
            hit = self._data.get(key)
            if hit is None:
                return None
            expires_at, value = hit
            if expires_at < time.monotonic():
                self._data.pop(key, None)
                return None
            return value

    def set(self, connection_id: str, method: str, args: tuple, value: Any) -> None:
        if not self.enabled:
            return
        with self._lock:
            self._data[(connection_id, method, args)] = (time.monotonic() + self._ttl, value)

    def invalidate(self, connection_id: str) -> None:
        with self._lock:
            for key in [k for k in self._data if k[0] == connection_id]:
                self._data.pop(key, None)
