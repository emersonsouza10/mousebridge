"""Utilidades: hash/preview de SQL e normalização de valores para JSON.

JSON não representa nativamente ``datetime``/``Decimal``/``bytes``; sem cuidado
haveria perda silenciosa de precisão (crítico em dados financeiros). Cada célula
não-JSON vira um envelope tipado ``{"$t": ..., "v": ...}`` com round-trip exato.
"""

from __future__ import annotations

import base64
import datetime as _dt
import hashlib
from decimal import Decimal
from typing import Any


def sql_hash(sql: str) -> str:
    """Hash estável da SQL para auditoria (nunca registra a query crua inteira)."""
    return hashlib.blake2b(sql.encode("utf-8"), digest_size=16).hexdigest()


def sql_preview(sql: str, limit: int = 200) -> str:
    one_line = " ".join(sql.split())
    return one_line if len(one_line) <= limit else one_line[: limit - 1] + "…"


def to_jsonable(value: Any) -> Any:
    """Converte uma célula de resultado para algo serializável em JSON."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Decimal):
        return {"$t": "dec", "v": str(value)}
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {"$t": "b64", "v": base64.b64encode(bytes(value)).decode("ascii")}
    if isinstance(value, (_dt.datetime, _dt.date, _dt.time)):
        return {"$t": "dt", "v": value.isoformat()}
    return str(value)  # fallback seguro: representação textual


def from_jsonable(value: Any) -> Any:
    """Inverte ``to_jsonable`` no lado do agente."""
    if isinstance(value, dict) and "$t" in value:
        tag = value["$t"]
        raw = value.get("v")
        if tag == "dec":
            return Decimal(raw)
        if tag == "b64":
            return base64.b64decode(raw)
        if tag == "dt":
            return raw  # mantém ISO string; o agente decide se reparseia
    return value


def encode_rows(rows: list[list[Any]]) -> list[list[Any]]:
    return [[to_jsonable(cell) for cell in row] for row in rows]


def decode_rows(rows: list[list[Any]]) -> list[list[Any]]:
    return [[from_jsonable(cell) for cell in row] for row in rows]
