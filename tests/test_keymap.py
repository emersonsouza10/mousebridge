import unittest

from zephyrlink.keyboard.keymap import payload_from_parts, payload_is_valid


class PayloadFromPartsTest(unittest.TestCase):
    def test_char_takes_priority(self) -> None:
        self.assertEqual(payload_from_parts(char="a"), {"kind": "char", "char": "a"})

    def test_named_key(self) -> None:
        self.assertEqual(payload_from_parts(name="ctrl_l"), {"kind": "named", "name": "ctrl_l"})

    def test_vk_fallback(self) -> None:
        self.assertEqual(payload_from_parts(vk=165), {"kind": "vk", "vk": 165})

    def test_nothing_returns_none(self) -> None:
        self.assertIsNone(payload_from_parts())


class PayloadValidationTest(unittest.TestCase):
    def test_valid_payloads(self) -> None:
        self.assertTrue(payload_is_valid({"kind": "char", "char": "x"}))
        self.assertTrue(payload_is_valid({"kind": "named", "name": "f5"}))
        self.assertTrue(payload_is_valid({"kind": "vk", "vk": 13}))

    def test_invalid_payloads(self) -> None:
        self.assertFalse(payload_is_valid({}))
        self.assertFalse(payload_is_valid({"kind": "char"}))
        self.assertFalse(payload_is_valid({"kind": "named", "name": ""}))
        self.assertFalse(payload_is_valid({"kind": "vk", "vk": "abc"}))
        self.assertFalse(payload_is_valid({"kind": "outro", "x": 1}))


if __name__ == "__main__":
    unittest.main()
