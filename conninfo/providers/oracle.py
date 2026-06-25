"""Provider Oracle via ``python-oracledb`` (thin/thick). Import lazy.

DSN: ``{"user","dsn"}`` onde ``dsn`` é um alias do ``tnsnames.ora`` ou um EZConnect
``host:port/service``. A senha é resolvida no cliente (Oracle Wallet / prompt local)
e nunca trafega; ``secret`` opcional só sob TLS, usado uma vez e não persistido.

Metadata vem do dicionário de dados (``ALL_TAB_COLUMNS`` etc.) com binds — nunca
concatenação de identificador, para não abrir injeção (ARQUITETURA §15 risco 7).
"""

from __future__ import annotations

from typing import Any

from conninfo.providers.base import Column, DatabaseProvider, DbError, ResultCursor


class OracleProvider(DatabaseProvider):
    engine = "oracle"

    def _driver(self):
        try:
            import oracledb  # noqa: PLC0415
        except ImportError as exc:
            raise DbError("driver 'oracledb' não instalado no cliente") from exc
        return oracledb

    def connect(self, dsn: dict[str, Any], secret: str | None = None) -> Any:
        oracledb = self._driver()
        kwargs = {k: v for k, v in dsn.items() if v is not None}
        if secret is not None:
            kwargs["password"] = secret
        try:
            return oracledb.connect(**kwargs)
        except Exception as exc:  # noqa: BLE001 — normaliza erro do driver
            raise DbError(f"falha ao conectar no oracle: {exc}") from exc

    def disconnect(self, conn: Any) -> None:
        conn.close()

    def execute(self, conn: Any, sql: str, params: Any = None) -> ResultCursor:
        cur = conn.cursor()
        try:
            cur.execute(sql, params or [])
        except Exception as exc:  # noqa: BLE001
            cur.close()
            raise DbError(str(exc)) from exc
        columns = [Column(name=d[0], type=_type_name(d)) for d in (cur.description or [])]
        return ResultCursor(raw=cur, columns=columns)

    def fetchmany(self, cursor: ResultCursor, n: int) -> tuple[list[list[Any]], bool]:
        rows = cursor.raw.fetchmany(n)
        result = [list(r) for r in rows]
        cursor.has_more = len(rows) == n
        return result, cursor.has_more

    def cancel(self, conn: Any) -> None:
        try:
            conn.cancel()  # OCIBreak
        except Exception:  # noqa: BLE001
            pass

    def version(self, conn: Any) -> str:
        return "Oracle " + str(getattr(conn, "version", "?"))

    def list_tables(self, conn: Any, schema: str | None = None) -> list[str]:
        if schema:
            sql = (
                "select object_name from all_objects "
                "where owner = :owner and object_type in ('TABLE','VIEW') order by object_name"
            )
            rows = self._query(conn, sql, {"owner": schema.upper()})
        else:
            sql = (
                "select object_name from user_objects "
                "where object_type in ('TABLE','VIEW') order by object_name"
            )
            rows = self._query(conn, sql, {})
        return [r[0] for r in rows]

    def describe(self, conn: Any, table: str) -> list[dict[str, Any]]:
        owner, _, name = table.rpartition(".")
        if owner:
            sql = (
                "select column_name, data_type, nullable from all_tab_columns "
                "where owner = :owner and table_name = :tname order by column_id"
            )
            rows = self._query(conn, sql, {"owner": owner.upper(), "tname": name.upper()})
        else:
            sql = (
                "select column_name, data_type, nullable from user_tab_columns "
                "where table_name = :tname order by column_id"
            )
            rows = self._query(conn, sql, {"tname": name.upper()})
        return [{"name": r[0], "type": r[1], "nullable": r[2] == "Y"} for r in rows]

    def list_indexes(self, conn: Any, table: str) -> list[dict[str, Any]]:
        owner, name = self._split(table)
        sql = (
            "select i.index_name, i.uniqueness, c.column_name "
            "from {a}_indexes i join {a}_ind_columns c "
            "  on c.index_name = i.index_name {join_owner} "
            "where i.table_name = :tname {where_owner} "
            "order by i.index_name, c.column_position"
        ).format(**self._scope(owner, alias_join="and c.index_owner = i.owner", alias_where="and i.owner = :owner"))
        rows = self._query(conn, sql, self._binds(owner, name))
        by_index: dict[str, dict[str, Any]] = {}
        for index_name, uniqueness, column in rows:
            entry = by_index.setdefault(index_name, {"name": index_name, "unique": uniqueness == "UNIQUE", "columns": []})
            entry["columns"].append(column)
        return list(by_index.values())

    def list_constraints(self, conn: Any, table: str) -> list[dict[str, Any]]:
        owner, name = self._split(table)
        scope = self._scope(owner, alias_where="and owner = :owner")
        sql = (
            "select constraint_name, constraint_type, search_condition "
            "from {a}_constraints where table_name = :tname {where_owner} "
            "order by constraint_name"
        ).format(**scope)
        kinds = {"P": "primary_key", "R": "foreign_key", "U": "unique", "C": "check"}
        return [
            {"name": r[0], "type": kinds.get(r[1], r[1]), "condition": r[2]}
            for r in self._query(conn, sql, self._binds(owner, name))
        ]

    def list_pk(self, conn: Any, table: str) -> list[str]:
        owner, name = self._split(table)
        scope = self._scope(
            owner,
            alias_join="and cc.owner = c.owner",
            alias_where="and c.owner = :owner",
        )
        sql = (
            "select cc.column_name from {a}_constraints c "
            "join {a}_cons_columns cc on cc.constraint_name = c.constraint_name {join_owner} "
            "where c.table_name = :tname and c.constraint_type = 'P' {where_owner} "
            "order by cc.position"
        ).format(**scope)
        return [r[0] for r in self._query(conn, sql, self._binds(owner, name))]

    def list_fk(self, conn: Any, table: str) -> list[dict[str, Any]]:
        owner, name = self._split(table)
        scope = self._scope(
            owner,
            alias_join="and cc.owner = c.owner",
            alias_where="and c.owner = :owner",
        )
        sql = (
            "select c.constraint_name, cc.column_name, c.r_constraint_name, cc.position "
            "from {a}_constraints c "
            "join {a}_cons_columns cc on cc.constraint_name = c.constraint_name {join_owner} "
            "where c.table_name = :tname and c.constraint_type = 'R' {where_owner} "
            "order by c.constraint_name, cc.position"
        ).format(**scope)
        by_fk: dict[str, dict[str, Any]] = {}
        for name_, column, ref, _pos in self._query(conn, sql, self._binds(owner, name)):
            entry = by_fk.setdefault(name_, {"name": name_, "columns": [], "references": ref})
            entry["columns"].append(column)
        return list(by_fk.values())

    def explain(self, conn: Any, sql: str) -> str:
        cur = conn.cursor()
        try:
            cur.execute("explain plan for " + sql)
            cur.execute(
                "select plan_table_output from table(dbms_xplan.display(null, null, 'BASIC'))"
            )
            return "\n".join(str(r[0]) for r in cur.fetchall())
        except Exception as exc:  # noqa: BLE001
            raise DbError(str(exc)) from exc
        finally:
            cur.close()

    @staticmethod
    def _split(table: str) -> tuple[str | None, str]:
        owner, _, name = table.rpartition(".")
        return (owner.upper() or None), name.upper()

    @staticmethod
    def _scope(owner: str | None, *, alias_join: str = "", alias_where: str = "") -> dict[str, str]:
        """Escolhe as views ``all_*`` (com owner) ou ``user_*`` (sem) e os filtros."""
        if owner:
            return {"a": "all", "join_owner": alias_join, "where_owner": alias_where}
        return {"a": "user", "join_owner": "", "where_owner": ""}

    @staticmethod
    def _binds(owner: str | None, name: str) -> dict[str, Any]:
        binds = {"tname": name}
        if owner:
            binds["owner"] = owner
        return binds

    def _query(self, conn: Any, sql: str, binds: dict[str, Any]) -> list[Any]:
        cur = conn.cursor()
        try:
            cur.execute(sql, binds)
            return cur.fetchall()
        except Exception as exc:  # noqa: BLE001
            raise DbError(str(exc)) from exc
        finally:
            cur.close()


def _type_name(description_row: Any) -> str | None:
    type_obj = description_row[1] if len(description_row) > 1 else None
    return getattr(type_obj, "name", None) or (str(type_obj) if type_obj else None)
