import unittest

from zephyrlink.discovery.beacon import build_query, build_reply, parse_packet


class PacketTest(unittest.TestCase):
    def test_query_roundtrip(self) -> None:
        packet = parse_packet(build_query())
        self.assertIsNotNone(packet)
        self.assertEqual(packet["op"], "query")

    def test_reply_roundtrip(self) -> None:
        packet = parse_packet(build_reply("meu-pc", 50510))
        self.assertIsNotNone(packet)
        self.assertEqual(packet["op"], "reply")
        self.assertEqual(packet["name"], "meu-pc")
        self.assertEqual(packet["port"], 50510)

    def test_foreign_traffic_ignored(self) -> None:
        self.assertIsNone(parse_packet(b"random bytes \xff\xfe"))
        self.assertIsNone(parse_packet(b'{"magic": "other-protocol"}'))
        self.assertIsNone(parse_packet(b'"just a string"'))
        self.assertIsNone(parse_packet(b"{}"))


if __name__ == "__main__":
    unittest.main()
