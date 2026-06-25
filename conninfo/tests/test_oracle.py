"""Fase 3: OracleProvider (driver mockado) e descoberta de tnsnames.ora.

Não há Oracle no ambiente, então o ``oracledb`` é substituído por um fake que
registra a SQL/binds e devolve linhas canônicas — valida o formato das queries de
metadata e o caminho de execução/streaming sem um banco real.
"""

from __future__ import annotations

import sys

import pytest

from conninfo.ci_discovery.oracle import OracleScanner, parse_tnsnames
from conninfo.providers.oracle import OracleProvider


# --- descoberta (sem banco) ---

TNSNAMES = """
# comentário de topo
HOMOL =
  (DESCRIPTION =
    (ADDRESS = (PROTOCOL = TCP)(HOST = dbhomol)(PORT = 1521))
    (CONNECT_DATA = (SERVICE_NAME = HOMOL)))

PRD, PRODUCAO =
  (DESCRIPTION =
    (ADDRESS = (PROTOCOL = TCP)(HOST = dbprd)(PORT = 1521))
    (CONNECT_DATA = (SERVICE_NAME = PRD)))
"""


def test_parse_tnsnames_extracts_aliases_including_multivalue():
    assert parse_tnsnames(TNSNAMES) == ["HOMOL", "PRD", "PRODUCAO"]


def test_parse_tnsnames_ignores_comments_and_dedups():
    text = "A = (X)\n# A = (Y)\na = (Z)\n"
    assert parse_tnsnames(text) == ["A"]


def test_oracle_scanner_reads_file(tmp_path):
    f = tmp_path / "tnsnames.ora"
    f.write_text(TNSNAMES, encoding="utf-8")
    refs = OracleScanner(paths=[f]).scan()
    assert [r.name for r in refs] == ["HOMOL", "PRD", "PRODUCAO"]
    assert refs[0].id == "oracle_homol"
    assert refs[0].dsn == {"dsn": "HOMOL"}
    assert refs[0].source == "tnsnames"
    assert refs[0].needs_secret is True


# --- provider com driver mockado ---


class _FakeType:
    def __init__(self, name):
        self.name = name


class _FakeCursor:
    def __init__(self, conn):
        self._conn = conn
        self.description = None
        self._rows = []

    def execute(self, sql, params=None):
        self._conn.calls.append((sql, params))
        self.description, self._rows = self._conn.router(sql)

    def fetchall(self):
        rows, self._rows = self._rows, []
        return rows

    def fetchmany(self, n):
        out, self._rows = self._rows[:n], self._rows[n:]
        return out

    def close(self):
        pass


class _FakeConn:
    version = "19.3.0.0.0"

    def __init__(self, router):
        self.router = router
        self.calls = []
        self.cancelled = False

    def cursor(self):
        return _FakeCursor(self)

    def cancel(self):
        self.cancelled = True

    def close(self):
        pass


class _FakeOracledb:
    Error = Exception

    def __init__(self, router):
        self.connect_kwargs = None
        self._router = router

    def connect(self, **kwargs):
        self.connect_kwargs = kwargs
        return _FakeConn(self._router)


def _router(sql):
    low = sql.lower()
    if "explain plan for" in low:
        return [], []
    if "dbms_xplan" in low:
        return [("PLAN_TABLE_OUTPUT", _FakeType("VARCHAR2"))], [
            ["SELECT STATEMENT"], ["  TABLE ACCESS FULL CLIENTE"]
        ]
    if "cons_columns" in low and "'p'" in low:
        return [("C", _FakeType("VARCHAR2"))], [["ID"]]
    if "cons_columns" in low and "'r'" in low:
        # ordem do select: constraint_name, column_name, r_constraint_name, position
        return [("C", _FakeType("VARCHAR2"))], [["FK_CLI_PED", "PEDIDO_ID", "PED", 1]]
    if "_constraints where" in low:
        return [("C", _FakeType("VARCHAR2"))], [["PK_CLI", "P", None], ["FK_CLI_PED", "R", None]]
    if "ind_columns" in low:
        return [("C", _FakeType("VARCHAR2"))], [
            ["PK_CLI", "UNIQUE", "ID"], ["IDX_NOME", "NONUNIQUE", "NOME"]
        ]
    if "_objects" in low:
        return [("OBJECT_NAME", _FakeType("VARCHAR2"))], [["CLIENTE"], ["PEDIDO"]]
    if "tab_columns" in low:
        return ([("C", _FakeType("VARCHAR2"))],
                [["ID", "NUMBER", "N"], ["NOME", "VARCHAR2", "Y"]])
    return [("DUMMY", _FakeType("VARCHAR2"))], [["X"], ["Y"], ["Z"]]


@pytest.fixture()
def oracle(monkeypatch):
    fake = _FakeOracledb(_router)
    monkeypatch.setitem(sys.modules, "oracledb", fake)
    return OracleProvider(), fake


def test_connect_passes_dsn_and_secret(oracle):
    provider, fake = oracle
    provider.connect({"user": "leitor", "dsn": "HOMOL"}, secret="senha")
    assert fake.connect_kwargs == {"user": "leitor", "dsn": "HOMOL", "password": "senha"}


def test_version(oracle):
    provider, _ = oracle
    conn = provider.connect({"dsn": "HOMOL"})
    assert provider.version(conn) == "Oracle 19.3.0.0.0"


def test_list_tables_binds_owner(oracle):
    provider, _ = oracle
    conn = provider.connect({"dsn": "HOMOL"})
    assert provider.list_tables(conn, schema="HUMASTER") == ["CLIENTE", "PEDIDO"]
    sql, binds = conn.calls[-1]
    assert "all_objects" in sql and binds == {"owner": "HUMASTER"}


def test_describe_binds_owner_and_table(oracle):
    provider, _ = oracle
    conn = provider.connect({"dsn": "HOMOL"})
    cols = provider.describe(conn, "humaster.cliente")
    assert cols == [
        {"name": "ID", "type": "NUMBER", "nullable": False},
        {"name": "NOME", "type": "VARCHAR2", "nullable": True},
    ]
    sql, binds = conn.calls[-1]
    assert binds == {"owner": "HUMASTER", "tname": "CLIENTE"}  # nomes uppercased, via bind


def test_execute_streams_in_chunks(oracle):
    provider, _ = oracle
    conn = provider.connect({"dsn": "HOMOL"})
    cur = provider.execute(conn, "select dummy from dual")
    assert [c.name for c in cur.columns] == ["DUMMY"]
    rows, has_more = provider.fetchmany(cur, 2)
    assert rows == [["X"], ["Y"]] and has_more is True
    rows, has_more = provider.fetchmany(cur, 2)
    assert rows == [["Z"]] and has_more is False


def test_cancel_calls_break(oracle):
    provider, _ = oracle
    conn = provider.connect({"dsn": "HOMOL"})
    provider.cancel(conn)
    assert conn.cancelled is True


def test_list_indexes_groups_columns(oracle):
    provider, _ = oracle
    conn = provider.connect({"dsn": "HOMOL"})
    idx = provider.list_indexes(conn, "HUMASTER.CLIENTE")
    assert idx == [
        {"name": "PK_CLI", "unique": True, "columns": ["ID"]},
        {"name": "IDX_NOME", "unique": False, "columns": ["NOME"]},
    ]


def test_list_constraints_maps_types(oracle):
    provider, _ = oracle
    conn = provider.connect({"dsn": "HOMOL"})
    cons = provider.list_constraints(conn, "CLIENTE")
    types = {c["name"]: c["type"] for c in cons}
    assert types == {"PK_CLI": "primary_key", "FK_CLI_PED": "foreign_key"}


def test_list_pk_and_fk(oracle):
    provider, _ = oracle
    conn = provider.connect({"dsn": "HOMOL"})
    assert provider.list_pk(conn, "HUMASTER.CLIENTE") == ["ID"]
    fk = provider.list_fk(conn, "HUMASTER.CLIENTE")
    assert fk == [{"name": "FK_CLI_PED", "columns": ["PEDIDO_ID"], "references": "PED"}]


def test_explain_returns_plan_text(oracle):
    provider, _ = oracle
    conn = provider.connect({"dsn": "HOMOL"})
    plan = provider.explain(conn, "select * from cliente")
    assert "TABLE ACCESS FULL CLIENTE" in plan


def test_user_scope_when_no_owner(oracle):
    provider, _ = oracle
    conn = provider.connect({"dsn": "HOMOL"})
    provider.list_pk(conn, "cliente")  # sem owner → views user_*
    sql, _ = conn.calls[-1]
    assert "user_constraints" in sql and "user_cons_columns" in sql
