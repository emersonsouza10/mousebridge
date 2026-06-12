import unittest

from mousebridge.transport.messages import Message, MsgType, ProtocolError


class MessageTest(unittest.TestCase):
    def test_roundtrip(self) -> None:
        original = Message(MsgType.MOUSE_MOVE, {"dx": -5, "dy": 12})
        decoded = Message.decode(original.encode())
        self.assertEqual(decoded, original)

    def test_roundtrip_without_data(self) -> None:
        decoded = Message.decode(Message(MsgType.PING).encode())
        self.assertEqual(decoded.type, MsgType.PING)
        self.assertEqual(decoded.data, {})

    def test_key_event_payload(self) -> None:
        msg = Message(MsgType.KEY_EVENT, {"key": {"kind": "named", "name": "ctrl_l"}, "pressed": True})
        decoded = Message.decode(msg.encode())
        self.assertEqual(decoded.data["key"]["name"], "ctrl_l")
        self.assertTrue(decoded.data["pressed"])

    def test_invalid_json_raises(self) -> None:
        with self.assertRaises(ProtocolError):
            Message.decode(b"not json at all")

    def test_unknown_type_raises(self) -> None:
        with self.assertRaises(ProtocolError):
            Message.decode(b'{"t":"definitely_not_a_type","d":{}}')

    def test_missing_type_raises(self) -> None:
        with self.assertRaises(ProtocolError):
            Message.decode(b'{"d":{}}')


if __name__ == "__main__":
    unittest.main()
