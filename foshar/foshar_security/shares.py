"""Definição de um *share*: um diretório que a máquina dona publica.

O dono dos arquivos é autoritativo (mesmo modelo do launcher): só os diretórios
declarados aqui podem ser acessados, e ``mode`` decide se a escrita é permitida.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

VALID_MODES = ("ro", "rw")


@dataclass(frozen=True, slots=True)
class Share:
    id: str
    path: Path
    mode: str

    @property
    def writable(self) -> bool:
        return self.mode == "rw"
