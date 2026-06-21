"""Trilha de auditoria opt-in das decisões de abertura (lado do cliente).

Append-only em JSON-lines. Desligada por padrão (``audit_file`` nulo): só grava
se o dono da máquina-alvo configurar um caminho. Cada decisão — aceita, recusada,
concluída ou falha — vira uma linha com carimbo de tempo. Falha de escrita nunca
bloqueia a abertura; apenas registra um aviso.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class AuditLog:
    def __init__(self, path: str | None) -> None:
        self._path = Path(path) if path else None

    def _append(self, line: str) -> None:
        assert self._path is not None
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    async def write(self, event: dict[str, Any]) -> None:
        if self._path is None:
            return
        record = {"ts": datetime.now(timezone.utc).isoformat(), **event}
        line = json.dumps(record, ensure_ascii=False)
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(None, self._append, line)
        except OSError as exc:
            logger.warning("Falha ao gravar auditoria em %s: %s", self._path, exc)
