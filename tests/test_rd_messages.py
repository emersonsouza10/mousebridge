import unittest

from zephyrlink.transport.messages import Message, MsgType


class RemoteDesktopMessagesTest(unittest.TestCase):
    def test_new_types_exist(self) -> None:
        for name in (
            "RD_START", "RD_ACCEPT", "RD_REJECT", "RD_STOP",
            "VIDEO_CONFIG", "VIDEO_FRAME", "INPUT_MOVE_ABS",
        ):
            self.assertTrue(hasattr(MsgType, name), name)

    def test_rd_start_roundtrip(self) -> None:
        msg = Message(MsgType.RD_START, {"quality": 70, "fps": 15})
        decoded = Message.decode(msg.encode())
        self.assertEqual(decoded.type, MsgType.RD_START)
        self.assertEqual(decoded.data["quality"], 70)

    def test_input_move_abs_roundtrip(self) -> None:
        msg = Message(MsgType.INPUT_MOVE_ABS, {"x": 0.25, "y": 0.75})
        decoded = Message.decode(msg.encode())
        self.assertAlmostEqual(decoded.data["x"], 0.25)
        self.assertAlmostEqual(decoded.data["y"], 0.75)

    def test_rd_reject_reason(self) -> None:
        decoded = Message.decode(Message(MsgType.RD_REJECT, {"reason": "sem_tls"}).encode())
        self.assertEqual(decoded.data["reason"], "sem_tls")


if __name__ == "__main__":
    unittest.main()
