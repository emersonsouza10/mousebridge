"""Sandbox de caminhos: resolução e contenção dentro de um share.

Espelha ``zephyrlink/launcher/validate.py::_validate_path``: todo caminho é
resolvido para absoluto e precisa estar **contido** no diretório do share. Como
``resolve()`` segue symlinks, um link que aponte para fora do share resolve para
um alvo que não está contido — e é recusado, fechando o escape por symlink.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath

from foshar.foshar_security.shares import Share


class SandboxError(Exception):
    """Acesso recusado: share desconhecido, caminho fora do share, ou somente leitura."""


class Sandbox:
    """Resolve caminhos do protocolo (POSIX, relativos) para caminhos reais contidos."""

    def __init__(self, shares: tuple[Share, ...] | list[Share]) -> None:
        self._shares = {s.id: s for s in shares}

    def share(self, share_id: str) -> Share:
        share = self._shares.get(share_id)
        if share is None:
            raise SandboxError(f"share desconhecido: {share_id!r}")
        return share

    def resolve(self, share_id: str, relpath: str) -> Path:
        share = self.share(share_id)
        relpath = relpath or "."
        if "\\" in relpath or "\x00" in relpath:
            raise SandboxError("caminho com caractere inválido")
        rel = PurePosixPath(relpath)
        if rel.is_absolute() or any(part == ".." or ":" in part for part in rel.parts):
            raise SandboxError("caminho não pode ser absoluto nem conter '..'")
        target = (share.path / Path(*rel.parts)).resolve()
        if target != share.path and not target.is_relative_to(share.path):
            raise SandboxError("caminho fora do share")
        return target

    def resolve_writable(self, share_id: str, relpath: str) -> Path:
        if not self.share(share_id).writable:
            raise SandboxError(f"share '{share_id}' é somente leitura")
        return self.resolve(share_id, relpath)

    def shares_catalog(self) -> list[dict[str, str]]:
        return [{"id": s.id, "mode": s.mode} for s in self._shares.values()]
