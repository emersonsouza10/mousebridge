"""Mensagens do protocolo ZephyrLink.

Serialização em JSON: legível para depuração e suficiente em throughput
para eventos de input em rede local (mensagens de ~50-120 bytes).
"""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass, field
from typing import Any
from enum import StrEnum

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
    LAUNCH_CATALOG = "launch_catalog"
    LAUNCH_REQUEST = "launch_request"
    LAUNCH_ACK = "launch_ack"
    LAUNCH_RESULT = "launch_result"
    CTRL_LAUNCH = "ctrl_launch"
    CTRL_LIST = "ctrl_list"
    CTRL_REPLY = "ctrl_reply"
    PING = "ping"
    PONG = "pong"
    # Remote desktop (tela + controle). RD_* negociam a sessão; VIDEO_CONFIG
    # ajusta parâmetros de captura; VIDEO_FRAME trafega como frame BINÁRIO (ver
    # VideoFrame abaixo), não como Message JSON; INPUT_MOVE_ABS posiciona o
    # cursor por razões [0,1] da tela virtual remota.
    RD_START = "rd_start"
    RD_ACCEPT = "rd_accept"
    RD_REJECT = "rd_reject"
    RD_STOP = "rd_stop"
    VIDEO_CONFIG = "video_config"
    VIDEO_FRAME = "video_frame"
    INPUT_MOVE_ABS = "input_move_abs"


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


# Frames de vídeo carregam bytes crus (JPEG), então NÃO cabem no envelope JSON
# de ``Message`` sem inflar ~33% em base64. Um frame binário usa o mesmo framing
# de 4 bytes do TCP, mas o payload começa com um byte-tag para diferenciá-lo do
# JSON: ``Message.encode`` sempre começa com ``{`` (0x7b), então reservar 0x01
# para vídeo é retrocompatível — nenhum bump de PROTOCOL_VERSION é necessário.
VIDEO_FRAME_TAG = 0x01
_META_HEADER = struct.Struct(">I")


def is_video_frame(payload: bytes) -> bool:
    """True se o payload é um frame binário de vídeo (e não um Message JSON)."""
    return len(payload) >= 1 and payload[0] == VIDEO_FRAME_TAG


@dataclass(frozen=True, slots=True)
class VideoFrame:
    """Um frame de tela: metadados pequenos (JSON) + bytes da imagem.

    Layout do payload: ``0x01 | uint32(len(meta)) | meta_json | blob``.
    ``meta`` traz dimensões/formato/sequência (ex.: ``{"w":1920,"h":1080,
    "fmt":"jpeg","seq":42}``); ``blob`` é a imagem codificada.
    """

    meta: dict[str, Any]
    blob: bytes

    def encode(self) -> bytes:
        meta_bytes = json.dumps(self.meta, separators=(",", ":")).encode("utf-8")
        return bytes((VIDEO_FRAME_TAG,)) + _META_HEADER.pack(len(meta_bytes)) + meta_bytes + self.blob

    @classmethod
    def decode(cls, payload: bytes) -> "VideoFrame":
        start = 1 + _META_HEADER.size
        if len(payload) < start:
            raise ProtocolError("frame de vídeo truncado (header)")
        (meta_len,) = _META_HEADER.unpack_from(payload, 1)
        if len(payload) < start + meta_len:
            raise ProtocolError("frame de vídeo truncado (meta)")
        try:
            meta = json.loads(payload[start : start + meta_len].decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise ProtocolError(f"meta de frame inválida: {exc}") from exc
        if not isinstance(meta, dict):
            raise ProtocolError("meta de frame deve ser um objeto JSON")
        return cls(meta=meta, blob=payload[start + meta_len :])


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
