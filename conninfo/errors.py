"""Erros normalizados da connInfo, com ``code`` estável (SPEC §20).

Cada erro carrega um ``code`` que viaja no ``REPLY`` e chega ao agente como
``GatewayError.code`` — sem nunca vazar credencial ou connection string.
"""

from __future__ import annotations


class ConnInfoError(Exception):
    code = "INTERNAL_ERROR"


class ConnectionNotAllowed(ConnInfoError):
    code = "CONNECTION_NOT_ALLOWED"


class AuthDenied(ConnInfoError):
    code = "AUTHENTICATION_FAILED"


class QueryTimeout(ConnInfoError):
    code = "QUERY_TIMEOUT"


class QueryCancelled(ConnInfoError):
    code = "QUERY_CANCELLED"


class RateLimited(ConnInfoError):
    code = "RATE_LIMITED"
