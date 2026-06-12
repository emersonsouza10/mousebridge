import ctypes
import sys
import unittest

from zephyrlink.keyboard.win32_input import (
    Input,
    KeyboardInput,
    MouseInput,
    caps_lock_active,
    send_unicode,
)


class WindowsInputLayoutTest(unittest.TestCase):
    def test_native_structure_sizes_match_windows_abi(self) -> None:
        pointer_size = ctypes.sizeof(ctypes.c_void_p)
        self.assertEqual(ctypes.sizeof(KeyboardInput), 24 if pointer_size == 8 else 16)
        self.assertEqual(ctypes.sizeof(MouseInput), 32 if pointer_size == 8 else 24)
        self.assertEqual(ctypes.sizeof(Input), 40 if pointer_size == 8 else 28)

    @unittest.skipIf(sys.platform == "win32", "comportamento fora do Windows")
    def test_win32_helpers_are_noops_off_windows(self) -> None:
        self.assertFalse(caps_lock_active())
        self.assertFalse(send_unicode("a", True))


if __name__ == "__main__":
    unittest.main()
