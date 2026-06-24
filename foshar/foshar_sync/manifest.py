"""Manifesto por hash e diff incremental.

``scan`` roda no dono dos arquivos e produz o manifesto (caminho + hash de cada
arquivo). ``diff`` roda no requisitante e compara o manifesto remoto com o índice
local: só o que mudou é buscado; o que sumiu no remoto é apagado no espelho.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from foshar.foshar_cache.index import IndexEntry
from foshar.util import hash_file


def scan(root: str | Path) -> list[dict[str, Any]]:
    """Varredura recursiva: ``[{path, size, mtime, hash}]`` com ``path`` POSIX relativo."""
    root = Path(root)
    entries: list[dict[str, Any]] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for name in sorted(filenames):
            full = Path(dirpath) / name
            try:
                st = full.stat()
                digest = hash_file(full)
            except OSError:
                continue
            entries.append(
                {
                    "path": full.relative_to(root).as_posix(),
                    "size": st.st_size,
                    "mtime": st.st_mtime,
                    "hash": digest,
                }
            )
    return entries


@dataclass(frozen=True, slots=True)
class SyncPlan:
    fetch: list[str]
    delete: list[str]


def diff(remote_entries: list[dict[str, Any]], index: dict[str, IndexEntry]) -> SyncPlan:
    """O que buscar (ausente ou hash divergente) e o que apagar (sumiu no remoto)."""
    remote = {entry["path"]: entry for entry in remote_entries}
    fetch = [
        path
        for path, entry in remote.items()
        if path not in index or index[path].base_hash != entry["hash"]
    ]
    delete = [path for path in index if path not in remote]
    return SyncPlan(fetch=sorted(fetch), delete=sorted(delete))
