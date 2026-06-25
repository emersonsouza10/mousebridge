"""Mensagens do protocolo connInfo.

Mesmo envelope JSON do ZephyrLink/Foshar (``{"t": tipo, "d": dados}``) e mesmo
framing, mas com enum próprio e **correlação por ``req_id``**: ao contrário do
foshar (sequencial), a connInfo multiplexa várias sessões/consultas sobre um único
socket, então toda resposta volta como ``REPLY`` casada pelo ``req_id`` do pedido.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

PROTOCOL_VERSION = 1


class CiMsgType(StrEnum):
    # handshake (reusa o desafio-resposta HMAC do core)
    AUTH_CHALLENGE = "auth_challenge"
    AUTH_RESPONSE = "auth_response"
    AUTH_OK = "auth_ok"
    AUTH_FAIL = "auth_fail"
    # catálogo
    LIST_CONNECTIONS = "list_connections"
    # ciclo de sessão
    CONNECT = "connect"
    DISCONNECT = "disconnect"
    # execução / streaming
    EXECUTE = "execute"
    FETCH = "fetch_next"
    CLOSE_CURSOR = "close_cursor"
    CANCEL = "cancel"
    # transação
    BEGIN = "begin"
    COMMIT = "commit"
    ROLLBACK = "rollback"
    # metadata
    VERSION = "version"
    LIST_TABLES = "list_tables"
    DESCRIBE = "describe_table"
    LIST_INDEXES = "list_indexes"
    LIST_CONSTRAINTS = "list_constraints"
    LIST_PK = "list_pk"
    LIST_FK = "list_fk"
    EXPLAIN = "explain"
    # resposta genérica correlacionada (ok/erro + payload), espelha FS_REPLY
    REPLY = "reply"


class ProtocolError(Exception):
    """Mensagem malformada ou de tipo desconhecido."""


@dataclass(frozen=True, slots=True)
class CiMessage:
    type: CiMsgType
    data: dict[str, Any] = field(default_factory=dict)

    def encode(self) -> bytes:
        return json.dumps({"t": self.type.value, "d": self.data}, separators=(",", ":")).encode("utf-8")

    @classmethod
    def decode(cls, payload: bytes) -> "CiMessage":
        try:
            raw = json.loads(payload.decode("utf-8"))
            return cls(type=CiMsgType(raw["t"]), data=raw.get("d") or {})
        except (ValueError, KeyError, TypeError) as exc:
            raise ProtocolError(f"mensagem inválida: {exc}") from exc
