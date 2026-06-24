"""Orquestra o clone/sync e a abertura no VSCode (Opção B).

``clone`` é idempotente: pede o manifesto, faz o diff por hash contra o índice
SQLite local e só transfere o que mudou; o que sumiu no remoto é apagado no
espelho. Reexecutar depois de uma mudança remota baixa apenas o delta.
"""

from __future__ import annotations

import base64
import logging
import subprocess
from pathlib import Path

from foshar.foshar_cache.index import IndexEntry, SyncIndex
from foshar.foshar_cache.mirror import Mirror
from foshar.foshar_server.rpc import FosharClient
from foshar.foshar_sync.manifest import diff

logger = logging.getLogger(__name__)


async def clone(client: FosharClient, share: str, cache_dir: str | Path) -> Path:
    """Sincroniza o share remoto para o espelho local; devolve a raiz do espelho."""
    cache_dir = Path(cache_dir).expanduser()
    project_dir = cache_dir / share
    mirror = Mirror(project_dir)
    index = SyncIndex(cache_dir / ".foshar" / f"{share}.index.db")
    try:
        remote = await client.manifest(share)
        plan = diff(remote, index.all())
        remote_by_path = {entry["path"]: entry for entry in remote}
        for rel in plan.fetch:
            data = await client.read(share, rel)
            mirror.write(rel, base64.b64decode(data.get("data_b64", "")))
            entry = remote_by_path[rel]
            index.upsert(
                IndexEntry(path=rel, size=entry["size"], mtime=entry["mtime"], base_hash=entry["hash"])
            )
        for rel in plan.delete:
            mirror.delete(rel)
            index.delete(rel)
        logger.info(
            "Clone de '%s': %d baixado(s), %d removido(s) → %s",
            share, len(plan.fetch), len(plan.delete), mirror.root,
        )
        return mirror.root
    finally:
        index.close()


def open_in_vscode(path: Path) -> bool:
    try:
        subprocess.Popen(["code", str(path)], shell=False)
        return True
    except OSError:
        logger.warning("VSCode ('code') não encontrado no PATH; abra manualmente: %s", path)
        return False
