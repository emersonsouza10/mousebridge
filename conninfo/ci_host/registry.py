"""Registro engine → provider, com import lazy.

Um provider só é importado quando uma conexão daquele engine é aberta, então a
ausência do driver (``oracledb``) só falha ao abrir uma conexão, não na carga.
"""

from __future__ import annotations

from conninfo.providers.base import DatabaseProvider, DbError

_LAZY = {
    "oracle": ("conninfo.providers.oracle", "OracleProvider"),
}


def get_provider(engine: str) -> DatabaseProvider:
    target = _LAZY.get(engine)
    if target is None:
        raise DbError(f"engine sem provider: {engine}")
    module_name, class_name = target
    import importlib

    module = importlib.import_module(module_name)
    return getattr(module, class_name)()
