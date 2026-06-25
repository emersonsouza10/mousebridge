"""Trilha de auditoria append-only em JSON-lines (SPEC §21, espelha launcher/audit).

Nunca registra senha, connection string ou a SQL crua inteira: só ``sql_hash`` +
``sql_preview`` sanitizado. Falha de escrita jamais bloqueia a operação.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class AuditLog:
    def __init__(self, path: str | None) -> None:
        self._path = Path(path).expanduser() if path else None
        self._lock = threading.Lock()
        if self._path is not None:
            self._path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, **fields: Any) -> None:
        entry = {"timestamp": time.time(), **fields}
        if self._path is None:
            logger.info("audit %s", entry)
            return
        line = json.dumps(entry, ensure_ascii=False, separators=(",", ":"))
        try:
            with self._lock, self._path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except OSError as exc:  # auditoria nunca derruba a operação
            logger.warning("Falha ao gravar auditoria: %s", exc)
