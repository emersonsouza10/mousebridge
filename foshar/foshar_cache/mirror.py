"""Espelho local em disco (Opção B): a pasta que o VSCode abre.

Mapeia caminhos do protocolo (POSIX, relativos) para arquivos sob ``root`` e
grava de forma atômica. Recusa, por segurança, qualquer caminho que escape de
``root`` (mesmo princípio do sandbox, do lado do requisitante).
"""

from __future__ import annotations

from pathlib import Path

from foshar.util import write_atomic


class Mirror:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def path_for(self, relpath: str) -> Path:
        target = (self.root / Path(*[p for p in relpath.split("/") if p])).resolve()
        if target != self.root and not target.is_relative_to(self.root):
            raise ValueError(f"caminho fora do espelho: {relpath!r}")
        return target

    def write(self, relpath: str, data: bytes) -> None:
        write_atomic(self.path_for(relpath), data)

    def delete(self, relpath: str) -> None:
        target = self.path_for(relpath)
        if target.is_file():
            target.unlink()

    def make_dir(self, relpath: str) -> None:
        self.path_for(relpath).mkdir(parents=True, exist_ok=True)
