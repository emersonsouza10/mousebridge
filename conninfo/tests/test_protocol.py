from conninfo.ci_protocol.messages import CiMessage, CiMsgType, ProtocolError
from conninfo.ci_security.limits import PolicyError, clamp_max_rows, ensure_read_only, first_keyword
from conninfo.util import decode_rows, encode_rows
import datetime as dt
from decimal import Decimal

import pytest


def test_message_round_trip():
    msg = CiMessage(CiMsgType.EXECUTE, {"sql": "select 1", "req_id": "7"})
    again = CiMessage.decode(msg.encode())
    assert again.type is CiMsgType.EXECUTE
    assert again.data["sql"] == "select 1"


def test_message_decode_rejects_garbage():
    with pytest.raises(ProtocolError):
        CiMessage.decode(b"{not json")
    with pytest.raises(ProtocolError):
        CiMessage.decode(b'{"t":"nao_existe","d":{}}')


def test_first_keyword_ignores_comments():
    assert first_keyword("  -- comentário\n select * from t") == "SELECT"
    assert first_keyword("/* bloco */ WITH x as (select 1) select * from x") == "WITH"


def test_read_only_allows_select_blocks_dml():
    ensure_read_only("select * from clientes")
    ensure_read_only("WITH a AS (SELECT 1) SELECT * FROM a")
    for bad in ["delete from t", "DROP TABLE t", "update t set x=1", "insert into t values (1)"]:
        with pytest.raises(PolicyError):
            ensure_read_only(bad)


def test_clamp_max_rows():
    assert clamp_max_rows(None, None, 1000) == 1000
    assert clamp_max_rows(50, 1000, 5000) == 50
    assert clamp_max_rows(99999, 1000, 5000) == 1000  # nunca acima do teto da conexão


def test_value_round_trip_preserves_types():
    rows = [[Decimal("10.50"), b"\x00\xff", dt.date(2026, 6, 25), None, "txt", 3]]
    decoded = decode_rows(encode_rows(rows))
    assert decoded[0][0] == Decimal("10.50")
    assert decoded[0][1] == b"\x00\xff"
    assert decoded[0][2] == "2026-06-25"
    assert decoded[0][3] is None
