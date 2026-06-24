"""Transferência de arquivos grandes por pedaços (B9).

Em vez de carregar o arquivo inteiro em memória/base64 (teto de 8 MB e de frame),
lê/grava em blocos de 1 MB, cada bloco um pedido independente sobre a conexão. O
hash é calculado em fluxo (mesmo blake2b do manifesto), e a gravação é atômica:
o destino só aparece completo (escreve em ``.foshar-tmp`` e dá ``os.replace``).
"""

from __future__ import annotations

import base64
import os
from pathlib import Path

from foshar.foshar_server.rpc import FosharClient
from foshar.util import new_hasher

CHUNK = 1 << 20  # 1 MiB


async def download(client: FosharClient, share: str, path: str, dest: Path) -> str:
    """Baixa ``path`` do dono para ``dest`` em pedaços; devolve o hash recebido."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".foshar-tmp")
    digest = new_hasher()
    offset = 0
    with tmp.open("wb") as handle:
        while True:
            reply = await client.read_chunk(share, path, offset, CHUNK)
            data = base64.b64decode(reply.get("data_b64", ""))
            if data:
                handle.write(data)
                digest.update(data)
                offset += len(data)
            if not data or offset >= int(reply.get("total", 0)):
                break
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, dest)
    return digest.hexdigest()


async def upload(client: FosharClient, share: str, path: str, src: Path) -> str:
    """Envia ``src`` para ``path`` no dono em pedaços; devolve o hash confirmado."""
    size = src.stat().st_size
    with src.open("rb") as handle:
        if size == 0:
            reply = await client.write_chunk(share, path, offset=0, data_b64="", begin=True, final=True)
            return str(reply.get("hash", ""))
        offset = 0
        reply = {}
        while offset < size:
            chunk = handle.read(CHUNK)
            if not chunk:
                break
            reply = await client.write_chunk(
                share,
                path,
                offset=offset,
                data_b64=base64.b64encode(chunk).decode("ascii"),
                begin=offset == 0,
                final=offset + len(chunk) >= size,
            )
            offset += len(chunk)
    return str(reply.get("hash", ""))
