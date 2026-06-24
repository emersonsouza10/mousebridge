"""Mensagens do protocolo Foshar.

Mesmo envelope JSON do ZephyrLink (``{"t": tipo, "d": dados}``), mas com um enum
próprio: Foshar é um módulo independente e roda numa conexão separada, então não
precisa (nem deve) poluir o ``MsgType`` do canal de input.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

PROTOCOL_VERSION = 1


class FsMsgType(StrEnum):
    AUTH_CHALLENGE = "auth_challenge"
    AUTH_RESPONSE = "auth_response"
    AUTH_OK = "auth_ok"
    AUTH_FAIL = "auth_fail"
    FS_LIST = "fs_list"
    FS_STAT = "fs_stat"
    FS_READ = "fs_read"
    FS_WRITE = "fs_write"
    FS_WRITE_CHUNK = "fs_write_chunk"
    FS_CREATE = "fs_create"
    FS_DELETE = "fs_delete"
    FS_RENAME = "fs_rename"
    FS_MKDIR = "fs_mkdir"
    FS_RMDIR = "fs_rmdir"
    FS_MANIFEST = "fs_manifest"
    FS_REPLY = "fs_reply"


class ProtocolError(Exception):
    """Mensagem malformada ou de tipo desconhecido."""


@dataclass(frozen=True, slots=True)
class FsMessage:
    type: FsMsgType
    data: dict[str, Any] = field(default_factory=dict)

    def encode(self) -> bytes:
        return json.dumps({"t": self.type.value, "d": self.data}, separators=(",", ":")).encode("utf-8")

    @classmethod
    def decode(cls, payload: bytes) -> "FsMessage":
        try:
            raw = json.loads(payload.decode("utf-8"))
            return cls(type=FsMsgType(raw["t"]), data=raw.get("d") or {})
        except (ValueError, KeyError, TypeError) as exc:
            raise ProtocolError(f"mensagem inválida: {exc}") from exc
