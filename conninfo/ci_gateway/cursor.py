"""Cursor de streaming do lado agente: puxa ``RESULT_CHUNK`` sob demanda.

Iteração lazy com backpressure natural — só pede o próximo lote quando o consumidor
esgota o atual. ``columns`` fica disponível desde o primeiro chunk.
"""

from __future__ import annotations

from typing import Any, Iterator

from conninfo.util import decode_rows


class Cursor:
    def __init__(self, connection: "Any", first_reply: dict[str, Any]) -> None:
        self._conn = connection
        self.columns: list[str] = list(first_reply.get("columns") or [])
        self._buffer: list[list[Any]] = decode_rows(first_reply.get("rows") or [])
        self._has_more: bool = bool(first_reply.get("has_more"))
        self._cursor_id: str = first_reply.get("cursor_id") or ""

    def __iter__(self) -> Iterator[list[Any]]:
        while True:
            for row in self._buffer:
                yield row
            self._buffer = []
            if not self._has_more:
                return
            self._pull()

    def fetchone(self) -> list[Any] | None:
        if not self._buffer and self._has_more:
            self._pull()
        return self._buffer.pop(0) if self._buffer else None

    def fetchmany(self, n: int) -> list[list[Any]]:
        out: list[list[Any]] = []
        while len(out) < n:
            if not self._buffer:
                if not self._has_more:
                    break
                self._pull()
                if not self._buffer:
                    break
            take = n - len(out)
            out.extend(self._buffer[:take])
            self._buffer = self._buffer[take:]
        return out

    def fetchall(self) -> list[list[Any]]:
        return list(self)

    def close(self) -> None:
        if self._cursor_id:
            self._conn._close_cursor(self._cursor_id)
            self._cursor_id = ""
            self._has_more = False

    def _pull(self) -> None:
        reply = self._conn._fetch(self._cursor_id)
        self._buffer = decode_rows(reply.get("rows") or [])
        self._has_more = bool(reply.get("has_more"))
        self._cursor_id = reply.get("cursor_id") or ""
