"""Utilidades compartilhadas: hashing e escrita atômica.

O hash (``blake2b``, 128 bits) é a chave de igualdade entre o original remoto e
o espelho local — base do diff incremental. A escrita é atômica (``os.replace``)
para que uma queda no meio da gravação nunca deixe um arquivo parcial.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

_DIGEST_SIZE = 16
_CHUNK = 1 << 20


def hash_bytes(data: bytes) -> str:
    return hashlib.blake2b(data, digest_size=_DIGEST_SIZE).hexdigest()


def hash_file(path: Path) -> str:
    digest = hashlib.blake2b(digest_size=_DIGEST_SIZE)
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".foshar-tmp")
    with tmp.open("wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)
