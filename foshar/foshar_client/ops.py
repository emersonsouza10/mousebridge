"""Operações de filesystem, executadas no dono dos arquivos.

Cada função recebe o ``Sandbox`` (que resolve e contém o caminho) e os dados do
pedido, e devolve o dicionário que vira o ``FS_REPLY``. Escrita/criação/remoção
exigem share ``rw`` (``resolve_writable``). A escrita é atômica.
"""

from __future__ import annotations

import base64
import os
import shutil
from typing import Any

from foshar.foshar_security.sandbox import Sandbox
from foshar.foshar_sync.manifest import scan
from foshar.util import hash_bytes, hash_file, write_atomic

# Leitura/escrita inline (base64) tem teto; arquivos maiores usam a transferência
# por pedaços (FS_READ com offset/length e FS_WRITE_CHUNK).
MAX_INLINE_BYTES = 8 * 1024 * 1024
_UPLOAD_SUFFIX = ".foshar-upload"
# Temporários do Foshar nunca entram no manifesto nem na listagem.
_HIDDEN_SUFFIXES = (".foshar-tmp", _UPLOAD_SUFFIX)


class OpError(Exception):
    """Operação inválida (alvo inexistente, tipo errado, grande demais)."""


def op_list(sandbox: Sandbox, data: dict[str, Any]) -> dict[str, Any]:
    base = sandbox.resolve(data["share"], data.get("path", "."))
    if not base.is_dir():
        raise OpError("não é um diretório")
    entries = []
    for child in sorted(base.iterdir(), key=lambda c: c.name):
        if child.name.endswith(_HIDDEN_SUFFIXES):
            continue
        st = child.stat()
        entries.append(
            {
                "name": child.name,
                "type": "dir" if child.is_dir() else "file",
                "size": st.st_size,
                "mtime": st.st_mtime,
            }
        )
    return {"entries": entries}


def op_stat(sandbox: Sandbox, data: dict[str, Any]) -> dict[str, Any]:
    target = sandbox.resolve(data["share"], data.get("path", "."))
    if not target.exists():
        return {"exists": False}
    st = target.stat()
    return {
        "exists": True,
        "type": "dir" if target.is_dir() else "file",
        "size": st.st_size,
        "mtime": st.st_mtime,
    }


def op_read(sandbox: Sandbox, data: dict[str, Any]) -> dict[str, Any]:
    target = sandbox.resolve(data["share"], data["path"])
    if not target.is_file():
        raise OpError("não é um arquivo")
    total = target.stat().st_size
    if "offset" in data or "length" in data:  # leitura por pedaço (arquivos grandes)
        offset = int(data.get("offset", 0))
        length = int(data.get("length", MAX_INLINE_BYTES))
        with target.open("rb") as handle:
            handle.seek(offset)
            chunk = handle.read(length)
        return {"data_b64": base64.b64encode(chunk).decode("ascii"), "size": len(chunk), "total": total}
    raw = target.read_bytes()  # leitura inline (arquivos pequenos)
    if len(raw) > MAX_INLINE_BYTES:
        raise OpError("arquivo grande demais para leitura inline; use transferência por pedaços")
    return {"data_b64": base64.b64encode(raw).decode("ascii"), "hash": hash_bytes(raw), "size": len(raw), "total": total}


def op_write(sandbox: Sandbox, data: dict[str, Any]) -> dict[str, Any]:
    target = sandbox.resolve_writable(data["share"], data["path"])
    raw = base64.b64decode(data.get("data_b64", ""))
    if len(raw) > MAX_INLINE_BYTES:
        raise OpError("conteúdo grande demais para escrita inline")
    write_atomic(target, raw)
    return {"hash": hash_bytes(raw), "size": len(raw)}


def op_write_chunk(sandbox: Sandbox, data: dict[str, Any]) -> dict[str, Any]:
    """Escreve um pedaço de arquivo grande em ``<alvo>.foshar-upload``.

    ``begin`` cria/zera o temporário; ``final`` calcula o hash, sincroniza em
    disco e move atomicamente para o alvo. Pedaços chegam em ordem (o motor de
    sync é sequencial)."""
    target = sandbox.resolve_writable(data["share"], data["path"])
    tmp = target.with_name(target.name + _UPLOAD_SUFFIX)
    raw = base64.b64decode(data.get("data_b64", ""))
    begin = bool(data.get("begin"))
    final = bool(data.get("final"))
    offset = int(data.get("offset", 0))
    target.parent.mkdir(parents=True, exist_ok=True)
    with tmp.open("wb" if begin else "r+b") as handle:
        if not begin:
            handle.seek(offset)
        handle.write(raw)
        if final:
            handle.flush()
            os.fsync(handle.fileno())
    if final:
        digest = hash_file(tmp)
        size = tmp.stat().st_size
        os.replace(tmp, target)
        return {"hash": digest, "size": size}
    return {}


def op_create(sandbox: Sandbox, data: dict[str, Any]) -> dict[str, Any]:
    target = sandbox.resolve_writable(data["share"], data["path"])
    if target.exists():
        raise OpError("já existe")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.touch()
    return {}


def op_delete(sandbox: Sandbox, data: dict[str, Any]) -> dict[str, Any]:
    target = sandbox.resolve_writable(data["share"], data["path"])
    if target.is_dir():
        raise OpError("é um diretório; use rmdir")
    target.unlink()
    return {}


def op_rename(sandbox: Sandbox, data: dict[str, Any]) -> dict[str, Any]:
    src = sandbox.resolve_writable(data["share"], data["src"])
    dst = sandbox.resolve_writable(data["share"], data["dst"])
    if not src.exists():
        raise OpError("origem inexistente")
    dst.parent.mkdir(parents=True, exist_ok=True)
    src.rename(dst)
    return {}


def op_mkdir(sandbox: Sandbox, data: dict[str, Any]) -> dict[str, Any]:
    target = sandbox.resolve_writable(data["share"], data["path"])
    target.mkdir(parents=True, exist_ok=True)
    return {}


def op_rmdir(sandbox: Sandbox, data: dict[str, Any]) -> dict[str, Any]:
    target = sandbox.resolve_writable(data["share"], data["path"])
    if not target.is_dir():
        raise OpError("não é um diretório")
    if data.get("recursive"):
        shutil.rmtree(target)
    else:
        target.rmdir()
    return {}


def op_manifest(sandbox: Sandbox, data: dict[str, Any]) -> dict[str, Any]:
    base = sandbox.resolve(data["share"], data.get("path", "."))
    entries = [e for e in scan(base) if not e["path"].endswith(_HIDDEN_SUFFIXES)]
    return {"entries": entries}
