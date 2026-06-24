"""Motor de sincronização bidirecional por polling.

A cada ciclo, o acessor compara três estados de cada arquivo — local (espelho),
remoto (manifesto do dono) e base (último hash sincronizado, no índice) — e
decide o que fazer. Usa hash como verdade (não relógio), então funciona entre
máquinas sem clock sincronizado.

Polling (sem ``watchdog``): é dependência-zero, multiplataforma e robusto a
políticas corporativas. ``FS_WATCH``/``FS_EVENT`` (push de eventos do dono para
cortar latência/banda) fica como otimização futura.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from dataclasses import dataclass

from foshar.foshar_cache.index import IndexEntry, SyncIndex
from foshar.foshar_cache.mirror import Mirror
from foshar.foshar_server.rpc import FosharClient, RpcError
from foshar.foshar_server.transfer import download, upload
from foshar.foshar_sync.manifest import scan

logger = logging.getLogger(__name__)

_IGNORE_SUFFIXES = (".foshar-tmp", ".foshar-conflict", ".foshar-upload")


@dataclass(frozen=True, slots=True)
class Action:
    kind: str  # pull | push | del_local | del_remote | del_index | set_base | conflict
    path: str


def plan_sync(local: dict[str, str], remote: dict[str, str], base: dict[str, str]) -> list[Action]:
    """Decide, por caminho, a ação de sincronização a partir dos hashes.

    ``local``/``remote``: caminho → hash dos arquivos presentes de cada lado.
    ``base``: caminho → hash da última versão sincronizada (índice)."""
    actions: list[Action] = []
    for path in sorted(set(local) | set(remote) | set(base)):
        loc = local.get(path)
        rem = remote.get(path)
        bas = base.get(path)
        if loc == rem:
            if loc is None:
                if bas is not None:  # sumiu dos dois lados: limpa o índice
                    actions.append(Action("del_index", path))
            elif bas != loc:  # idênticos dos dois lados, mas o índice não sabia
                actions.append(Action("set_base", path))
            continue
        if bas is None:  # sem base comum: criação de um lado (ou dos dois)
            if loc is None:
                actions.append(Action("pull", path))
            elif rem is None:
                actions.append(Action("push", path))
            else:
                actions.append(Action("conflict", path))
            continue
        local_changed = loc != bas
        remote_changed = rem != bas
        if local_changed and not remote_changed:
            actions.append(Action("del_remote" if loc is None else "push", path))
        elif remote_changed and not local_changed:
            actions.append(Action("del_local" if rem is None else "pull", path))
        else:  # os dois mudaram para conteúdos diferentes
            actions.append(Action("conflict", path))
    return actions


class SyncEngine:
    def __init__(
        self,
        client: FosharClient,
        share: str,
        mirror: Mirror,
        index: SyncIndex,
        interval: float = 2.0,
    ) -> None:
        self._client = client
        self._share = share
        self._mirror = mirror
        self._index = index
        self._interval = interval
        self._stopping = asyncio.Event()

    def stop(self) -> None:
        self._stopping.set()

    async def run(self) -> None:
        """Laço de polling. Erros de conexão propagam (o chamador reconecta)."""
        while not self._stopping.is_set():
            await self.sync_once()
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._stopping.wait(), timeout=self._interval)

    async def sync_once(self) -> list[Action]:
        remote = {e["path"]: e["hash"] for e in await self._client.manifest(self._share)}
        local = self._local_scan()
        base = {path: entry.base_hash for path, entry in self._index.all().items()}
        actions = plan_sync(local, remote, base)
        for action in actions:
            await self._apply(action, local, remote)
        return actions

    def _local_scan(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for entry in scan(self._mirror.root):
            if entry["path"].endswith(_IGNORE_SUFFIXES):
                continue
            out[entry["path"]] = entry["hash"]
        return out

    async def _apply(self, action: Action, local: dict[str, str], remote: dict[str, str]) -> None:
        path = action.path
        try:
            if action.kind == "pull":
                await self._pull(path)
            elif action.kind == "push":
                await self._push(path)
            elif action.kind == "del_remote":
                await self._client.delete(self._share, path)
                self._index.delete(path)
            elif action.kind == "del_local":
                self._mirror.delete(path)
                self._index.delete(path)
            elif action.kind == "del_index":
                self._index.delete(path)
            elif action.kind == "set_base":
                self._index.upsert(IndexEntry(path, 0, 0.0, local[path]))
            elif action.kind == "conflict":
                await self._resolve_conflict(path, path in remote)
        except RpcError as exc:  # ex.: push em share somente leitura
            logger.warning("Operação '%s' em '%s' recusada pelo dono: %s", action.kind, path, exc)

    async def _pull(self, path: str) -> None:
        dest = self._mirror.path_for(path)
        digest = await download(self._client, self._share, path, dest)
        self._index.upsert(IndexEntry(path, dest.stat().st_size, 0.0, digest))

    async def _push(self, path: str) -> None:
        src = self._mirror.path_for(path)
        digest = await upload(self._client, self._share, path, src)
        self._index.upsert(IndexEntry(path, src.stat().st_size, 0.0, digest))

    async def _resolve_conflict(self, path: str, remote_exists: bool) -> None:
        """Last-writer-wins seguro: o remoto vence, mas a versão local é
        preservada em ``<arquivo>.foshar-conflict`` para não perder trabalho."""
        target = self._mirror.path_for(path)
        if target.exists():
            backup = target.with_name(target.name + ".foshar-conflict")
            os.replace(target, backup)
            logger.warning("Conflito em '%s': remoto venceu; local salvo em '%s'", path, backup.name)
        else:
            logger.warning("Conflito em '%s': remoto venceu", path)
        if remote_exists:
            await self._pull(path)
        else:  # remoto apagou o arquivo: a remoção vence
            self._index.delete(path)
