from zephyrlink.transport import Message, MsgType, coalesce_moves


def _mv(dx, dy):
    return Message(MsgType.MOUSE_MOVE, {"dx": dx, "dy": dy})


def test_burst_of_moves_collapses_into_one_summed_delta():
    out = coalesce_moves([_mv(1, 2), _mv(3, -1), _mv(0, 4)])
    assert len(out) == 1
    assert out[0].type is MsgType.MOUSE_MOVE
    assert out[0].data == {"dx": 4, "dy": 5}


def test_button_is_a_barrier_and_order_is_preserved():
    btn = Message(MsgType.MOUSE_BUTTON, {"button": "left", "pressed": True})
    out = coalesce_moves([_mv(1, 0), _mv(2, 0), btn, _mv(5, 5)])
    assert [m.type for m in out] == [
        MsgType.MOUSE_MOVE,
        MsgType.MOUSE_BUTTON,
        MsgType.MOUSE_MOVE,
    ]
    assert out[0].data == {"dx": 3, "dy": 0}
    assert out[2].data == {"dx": 5, "dy": 5}


def test_non_move_events_pass_through_untouched():
    btn = Message(MsgType.MOUSE_BUTTON, {"button": "left", "pressed": False})
    assert coalesce_moves([btn]) == [btn]


def test_empty_batch():
    assert coalesce_moves([]) == []
