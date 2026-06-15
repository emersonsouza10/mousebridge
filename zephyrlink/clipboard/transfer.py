"""Transferência de arquivos do clipboard sobre o canal de mensagens.

Arquivos não cabem numa mensagem de clipboard de texto, então vão em
mensagens próprias: um ``FILE_OFFER`` com o manifesto (caminhos relativos +
tamanhos), vários ``FILE_DATA`` com os bytes em base64 e pedaços, e um
``FILE_END``. Pastas são expandidas preservando a estrutura relativa, então
copiar múltiplos arquivos e/ou diretórios funciona de forma transparente.

Esta camada é pura (não toca no clipboard do SO nem em pynput), o que a
torna testável em qualquer plataforma; a ponte com o clipboard fica em
``sync.py``/``winfiles.py``.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import secrets
from collections.abc import Awaitable, Callable

from zephyrlink.transport.messages import Message, MsgType

logger = logging.getLogger(__name__)

CHUNK_SIZE = 512 * 1024  # bytes lidos por FILE_DATA (antes do base64)

Send = Callable[[Message], Awaitable[None]]


class FileTooLarge(Exception):
    """Seleção excede o limite total configurado."""


def _iter_files(paths: list[str]) -> "list[tuple[str, str]]":
    """Expande a seleção em ``(caminho_absoluto, caminho_relativo)``.

    O caminho relativo preserva o nome de topo do item selecionado (arquivo
    ou pasta), para o destino recriar a mesma estrutura.
    """
    out: list[tuple[str, str]] = []
    for raw in paths:
        full = os.path.abspath(raw)
        if os.path.isdir(full):
            base = os.path.dirname(full)
            for root, _dirs, files in os.walk(full):
                for name in files:
                    fp = os.path.join(root, name)
                    out.append((fp, os.path.relpath(fp, base)))
        elif os.path.isfile(full):
            out.append((full, os.path.basename(full)))
    return out


def build_manifest(paths: list[str], max_total: int) -> "list[tuple[str, str, int]]":
    """Lista ``(absoluto, relativo_normalizado, tamanho)`` respeitando o teto."""
    files: list[tuple[str, str, int]] = []
    total = 0
    for full, rel in _iter_files(paths):
        try:
            size = os.path.getsize(full)
        except OSError:
            continue
        total += size
        if total > max_total:
            raise FileTooLarge(f"seleção excede {max_total} bytes")
        files.append((full, rel.replace(os.sep, "/"), size))
    return files


async def send_files(paths: list[str], send: Send, *, max_total: int, chunk_size: int = CHUNK_SIZE) -> bool:
    """Lê a seleção e a emite como FILE_OFFER + FILE_DATA* + FILE_END.

    ``send`` entrega uma mensagem ao(s) destino(s); cada chunk é lido uma vez
    e a mesma mensagem é repassada (o ``send`` do servidor faz fan-out).
    """
    loop = asyncio.get_running_loop()
    try:
        files = await loop.run_in_executor(None, build_manifest, paths, max_total)
    except FileTooLarge as exc:
        logger.warning("Transferência de arquivos ignorada: %s", exc)
        return False
    if not files:
        return False

    transfer_id = secrets.token_hex(8)
    manifest = [{"path": rel, "size": size} for _, rel, size in files]
    await send(Message(MsgType.FILE_OFFER, {"id": transfer_id, "files": manifest}))
    logger.info("Enviando %d arquivo(s) pelo clipboard", len(files))

    for index, (full, _rel, _size) in enumerate(files):
        handle = await loop.run_in_executor(None, open, full, "rb")
        try:
            while True:
                chunk = await loop.run_in_executor(None, handle.read, chunk_size)
                if not chunk:
                    break
                await send(
                    Message(
                        MsgType.FILE_DATA,
                        {"id": transfer_id, "index": index, "data": base64.b64encode(chunk).decode("ascii")},
                    )
                )
        finally:
            await loop.run_in_executor(None, handle.close)

    await send(Message(MsgType.FILE_END, {"id": transfer_id}))
    return True


def _safe_parts(rel: str) -> list[str]:
    parts = [p for p in rel.split("/") if p not in ("", ".", "..")]
    if not parts:
        raise ValueError(f"caminho inválido: {rel!r}")
    return parts


class FileReceiver:
    """Remonta os arquivos recebidos sob um diretório temporário.

    Mantém uma transferência ativa por vez (a ordem é OFFER → DATA* → END).
    ``feed`` devolve a lista de raízes (arquivos/pastas de topo) quando a
    transferência termina, para serem colocadas no clipboard local.
    """

    def __init__(self, dest_base: str) -> None:
        self._dest_base = dest_base
        self._id: str | None = None
        self._targets: list[str] = []
        self._roots: list[str] = []
        self._open_index: int | None = None
        self._handle: object | None = None

    async def feed(self, message: Message) -> list[str] | None:
        loop = asyncio.get_running_loop()
        if message.type is MsgType.FILE_OFFER:
            await loop.run_in_executor(None, self._begin, message.data)
            return None
        if message.type is MsgType.FILE_DATA:
            if str(message.data.get("id")) != self._id:
                return None
            data = base64.b64decode(message.data["data"])
            await loop.run_in_executor(None, self._write, int(message.data["index"]), data)
            return None
        if message.type is MsgType.FILE_END:
            if str(message.data.get("id")) != self._id:
                return None
            return await loop.run_in_executor(None, self._finish)
        return None

    def _begin(self, data: dict) -> None:
        self._id = str(data["id"])
        dest = os.path.join(self._dest_base, self._id)
        os.makedirs(dest, exist_ok=True)
        self._targets = []
        roots: list[str] = []
        for entry in data.get("files", []):
            parts = _safe_parts(str(entry["path"]))
            target = os.path.join(dest, *parts)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "wb"):  # cria/zera (cobre arquivos vazios)
                pass
            self._targets.append(target)
            root = os.path.join(dest, parts[0])
            if root not in roots:
                roots.append(root)
        self._roots = roots
        self._open_index = None
        self._handle = None

    def _write(self, index: int, data: bytes) -> None:
        if index < 0 or index >= len(self._targets):
            return
        if index != self._open_index:
            self._close()
            self._handle = open(self._targets[index], "ab")
            self._open_index = index
        assert self._handle is not None
        self._handle.write(data)  # type: ignore[attr-defined]

    def _finish(self) -> list[str]:
        self._close()
        roots, self._roots = self._roots, []
        self._id = None
        self._targets = []
        self._open_index = None
        logger.info("Recebidos %d item(ns) de topo pelo clipboard", len(roots))
        return roots

    def _close(self) -> None:
        if self._handle is not None:
            self._handle.close()  # type: ignore[attr-defined]
            self._handle = None
        self._open_index = None
