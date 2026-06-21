"""Validação do parâmetro do operador, executada no cliente (autoritativo).

Cada ``arg_kind`` tem uma regra fechada. Mesmo com ``shell=False``, um valor
livre poderia virar uma *flag* para o programa (ex.: ``--proxy``); por isso
``url`` exige um esquema permitido (não começa com ``-``) e ``path_in_dir``
resolve para um caminho absoluto contido em um diretório permitido.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

from zephyrlink.config import LaunchableApp


class ArgError(Exception):
    """Parâmetro rejeitado pela validação."""


def validate_args(app: LaunchableApp, args: list[str]) -> list[str]:
    """Devolve a lista segura para anexar ao comando, ou levanta ``ArgError``."""
    args = list(args or [])
    if not app.accepts_args or app.arg_kind == "none":
        if args:
            raise ArgError("aplicação não aceita parâmetros")
        return []
    if len(args) != 1:
        raise ArgError("esperado exatamente um parâmetro")
    value = args[0]
    match app.arg_kind:
        case "url":
            return [_validate_url(app, value)]
        case "enum":
            if value not in app.enum_values:
                raise ArgError(f"valor inválido; permitidos: {', '.join(app.enum_values)}")
            return [value]
        case "path_in_dir":
            return [_validate_path(app, value)]
    raise ArgError(f"arg_kind desconhecido: {app.arg_kind}")


def _validate_url(app: LaunchableApp, value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme.lower() not in app.allowed_url_schemes:
        raise ArgError(f"esquema não permitido; use: {', '.join(app.allowed_url_schemes)}")
    host = (parsed.hostname or "").lower()
    if not host:
        raise ArgError("URL sem host")
    if app.allowed_url_hosts and not _host_matches(host, app.allowed_url_hosts):
        raise ArgError("host de destino não permitido")
    return value


def _host_matches(host: str, allowed: tuple[str, ...]) -> bool:
    for pattern in allowed:
        if pattern == host:
            return True
        if pattern.startswith("*.") and (host == pattern[2:] or host.endswith(pattern[1:])):
            return True
    return False


def _validate_path(app: LaunchableApp, value: str) -> str:
    target = Path(value).expanduser().resolve()
    for directory in app.allowed_dirs:
        base = Path(directory).expanduser().resolve()
        if target == base or target.is_relative_to(base):
            return str(target)
    raise ArgError("caminho fora dos diretórios permitidos")
