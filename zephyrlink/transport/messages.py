"""Mensagens do protocolo ZephyrLink.

Serialização em JSON: legível para depuração e suficiente em throughput
para eventos de input em rede local (mensagens de ~50-120 bytes).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

PROTOCOL_VERSION = 1


class MsgType(StrEnum):
    HELLO = "hello"
    AUTH_CHALLENGE = "auth_challenge"
    AUTH_RESPONSE = "auth_response"
    AUTH_OK = "auth_ok"
    AUTH_FAIL = "auth_fail"
    SCREEN_INFO = "screen_info"
    ENTER = "enter"
    LEAVE = "leave"
    MOUSE_MOVE = "mouse_move"
    MOUSE_BUTTON = "mouse_button"
    MOUSE_SCROLL = "mouse_scroll"
    KEY_EVENT = "key_event"
    CLIPBOARD = "clipboard"
    FILE_OFFER = "file_offer"
    FILE_DATA = "file_data"
    FILE_END = "file_end"
    PING = "ping"
    PONG = "pong"


class ProtocolError(Exception):
    """Mensagem malformada ou de tipo desconhecido."""


@dataclass(frozen=True, slots=True)
class Message:
    type: MsgType
    data: dict[str, Any] = field(default_factory=dict)

    def encode(self) -> bytes:
        return json.dumps({"t": self.type.value, "d": self.data}, separators=(",", ":")).encode("utf-8")

    @classmethod
    def decode(cls, payload: bytes) -> "Message":
        try:
            raw = json.loads(payload.decode("utf-8"))
            return cls(type=MsgType(raw["t"]), data=raw.get("d") or {})
        except (ValueError, KeyError, TypeError) as exc:
            raise ProtocolError(f"mensagem inválida: {exc}") from exc


def coalesce_moves(batch: list[Message]) -> list[Message]:
    """Funde sequências de MOUSE_MOVE adjacentes somando seus deltas.

    Movimento relativo é cumulativo: somar deltas consecutivos é equivalente
    a aplicá-los um a um, mas em um único pacote. Botões, scroll e teclas são
    barreiras — nunca são reordenados em relação aos movimentos, preservando
    o comportamento de clique-no-lugar-certo.
    """
    out: list[Message] = []
    acc_dx = acc_dy = 0
    pending = False
    for msg in batch:
        if msg.type is MsgType.MOUSE_MOVE:
            acc_dx += int(msg.data["dx"])
            acc_dy += int(msg.data["dy"])
            pending = True
            continue
        if pending:
            out.append(Message(MsgType.MOUSE_MOVE, {"dx": acc_dx, "dy": acc_dy}))
            acc_dx = acc_dy = 0
            pending = False
        out.append(msg)
    if pending:
        out.append(Message(MsgType.MOUSE_MOVE, {"dx": acc_dx, "dy": acc_dy}))
    return out
