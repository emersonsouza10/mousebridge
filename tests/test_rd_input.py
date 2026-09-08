import unittest

from zephyrlink.remotedesktop.tkinput import (
    tk_button_name,
    tk_key_payload,
    wheel_delta_to_scroll,
)


class KeyPayloadTest(unittest.TestCase):
    def test_letter(self) -> None:
        self.assertEqual(tk_key_payload("a", "a"), {"kind": "char", "char": "a"})

    def test_shifted_symbol_uses_char(self) -> None:
        # Shift+2 no teclado do operador chega como char '@'.
        self.assertEqual(tk_key_payload("at", "@"), {"kind": "char", "char": "@"})

    def test_ctrl_letter_recovers_base_char(self) -> None:
        # Ctrl+C: event.char é o caractere de controle \x03; usa o keysym 'c'.
        self.assertEqual(tk_key_payload("c", "\x03"), {"kind": "char", "char": "c"})

    def test_named_keys(self) -> None:
        self.assertEqual(tk_key_payload("Return", ""), {"kind": "named", "name": "enter"})
        self.assertEqual(tk_key_payload("Escape", ""), {"kind": "named", "name": "esc"})
        self.assertEqual(tk_key_payload("Left", ""), {"kind": "named", "name": "left"})
        self.assertEqual(tk_key_payload("BackSpace", ""), {"kind": "named", "name": "backspace"})

    def test_modifiers(self) -> None:
        self.assertEqual(tk_key_payload("Control_L", ""), {"kind": "named", "name": "ctrl_l"})
        self.assertEqual(tk_key_payload("Shift_L", ""), {"kind": "named", "name": "shift"})
        self.assertEqual(tk_key_payload("Alt_R", ""), {"kind": "named", "name": "alt_gr"})

    def test_function_keys(self) -> None:
        self.assertEqual(tk_key_payload("F1", ""), {"kind": "named", "name": "f1"})
        self.assertEqual(tk_key_payload("F12", ""), {"kind": "named", "name": "f12"})

    def test_unknown_returns_none(self) -> None:
        self.assertIsNone(tk_key_payload("Hyper_L", ""))


class ButtonAndWheelTest(unittest.TestCase):
    def test_buttons(self) -> None:
        self.assertEqual(tk_button_name(1), "left")
        self.assertEqual(tk_button_name(2), "middle")
        self.assertEqual(tk_button_name(3), "right")
        self.assertIsNone(tk_button_name(4))  # scroll no X11

    def test_wheel(self) -> None:
        self.assertEqual(wheel_delta_to_scroll(120), (0, 1))
        self.assertEqual(wheel_delta_to_scroll(-3), (0, -1))
        self.assertEqual(wheel_delta_to_scroll(0), (0, 0))


if __name__ == "__main__":
    unittest.main()
