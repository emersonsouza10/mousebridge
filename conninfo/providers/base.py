"""Interface única que todo banco implementa (SPEC §12).

Cada método recebe um handle de conexão DBAPI vivo e devolve estruturas neutras
(dict/list), nunca objetos específicos do driver. Adicionar um banco = criar um
módulo, implementar a ABC e registrar — sem tocar protocolo/gateway/host.

Os métodos DBAPI são **bloqueantes**; o ProviderHost os roda num thread pool, fora
do event loop (ver ci_host/executor.py).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


class DbError(Exception):
    """Erro de banco já normalizado (sem vazar credencial/connection string)."""

    code = "DB_EXECUTION_ERROR"


@dataclass(frozen=True, slots=True)
class Column:
    name: str
    type: str | None = None


@dataclass(slots=True)
class ResultCursor:
    """Cursor de streaming aberto no host: segura o cursor DBAPI vivo."""

    raw: Any  # cursor DBAPI
    columns: list[Column]
    has_more: bool = True
    extra: dict[str, Any] = field(default_factory=dict)


class DatabaseProvider(ABC):
    engine: str

    @abstractmethod
    def connect(self, dsn: dict[str, Any], secret: str | None = None) -> Any:
        """Abre a conexão real, usando o ambiente local do cliente."""

    @abstractmethod
    def disconnect(self, conn: Any) -> None: ...

    @abstractmethod
    def execute(self, conn: Any, sql: str, params: Any = None) -> ResultCursor:
        """Executa e devolve um ``ResultCursor`` (cursor aberto + colunas)."""

    @abstractmethod
    def fetchmany(self, cursor: ResultCursor, n: int) -> tuple[list[list[Any]], bool]:
        """Devolve ``(linhas, has_more)``. Atualiza ``cursor.has_more``."""

    def close_cursor(self, cursor: ResultCursor) -> None:
        try:
            cursor.raw.close()
        except Exception:  # noqa: BLE001 — fechar cursor nunca deve propagar
            pass

    def cancel(self, conn: Any) -> None:  # melhor-esforço; override por banco
        pass

    # --- transação (SPEC RF08) ---

    def begin(self, conn: Any) -> None:  # Oracle inicia tx implicitamente no 1º DML
        pass

    def commit(self, conn: Any) -> None:
        conn.commit()

    def rollback(self, conn: Any) -> None:
        conn.rollback()

    # --- metadata (override por banco quando necessário) ---

    @abstractmethod
    def version(self, conn: Any) -> str: ...

    @abstractmethod
    def list_tables(self, conn: Any, schema: str | None = None) -> list[str]: ...

    @abstractmethod
    def describe(self, conn: Any, table: str) -> list[dict[str, Any]]: ...

    @abstractmethod
    def list_indexes(self, conn: Any, table: str) -> list[dict[str, Any]]: ...

    @abstractmethod
    def list_constraints(self, conn: Any, table: str) -> list[dict[str, Any]]: ...

    @abstractmethod
    def list_pk(self, conn: Any, table: str) -> list[str]: ...

    @abstractmethod
    def list_fk(self, conn: Any, table: str) -> list[dict[str, Any]]: ...

    @abstractmethod
    def explain(self, conn: Any, sql: str) -> str: ...
