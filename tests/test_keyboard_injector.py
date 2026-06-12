import ctypes
import unittest

from zephyrlink.keyboard.win32_input import Input, KeyboardInput, MouseInput


class WindowsInputLayoutTest(unittest.TestCase):
    def test_native_structure_sizes_match_windows_abi(self) -> None:
        pointer_size = ctypes.sizeof(ctypes.c_void_p)
        self.assertEqual(ctypes.sizeof(KeyboardInput), 24 if pointer_size == 8 else 16)
        self.assertEqual(ctypes.sizeof(MouseInput), 32 if pointer_size == 8 else 24)
        self.assertEqual(ctypes.sizeof(Input), 40 if pointer_size == 8 else 28)


if __name__ == "__main__":
    unittest.main()
