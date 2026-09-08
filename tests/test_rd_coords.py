import unittest

from zephyrlink.remotedesktop.coords import abs_to_ratio, clamp01, ratio_to_abs


class ClampTest(unittest.TestCase):
    def test_clamp01(self) -> None:
        self.assertEqual(clamp01(-0.5), 0.0)
        self.assertEqual(clamp01(0.3), 0.3)
        self.assertEqual(clamp01(1.7), 1.0)


class RatioToAbsTest(unittest.TestCase):
    def test_corners_single_monitor(self) -> None:
        region = (0, 0, 1920, 1080)
        self.assertEqual(ratio_to_abs(0.0, 0.0, *region), (0, 0))
        self.assertEqual(ratio_to_abs(1.0, 1.0, *region), (1919, 1079))

    def test_clamps_out_of_range(self) -> None:
        region = (0, 0, 1920, 1080)
        self.assertEqual(ratio_to_abs(-1.0, 2.0, *region), (0, 1079))

    def test_offset_virtual_screen(self) -> None:
        # Monitor à esquerda do primário: origem negativa.
        region = (-1920, 0, 3840, 1080)
        self.assertEqual(ratio_to_abs(0.0, 0.0, *region), (-1920, 0))
        self.assertEqual(ratio_to_abs(1.0, 0.0, *region), (1919, 0))

    def test_roundtrip_with_abs_to_ratio(self) -> None:
        region = (100, 50, 800, 600)
        for xr, yr in ((0.0, 0.0), (0.5, 0.5), (1.0, 1.0), (0.25, 0.9)):
            x, y = ratio_to_abs(xr, yr, *region)
            rx, ry = abs_to_ratio(x, y, *region)
            self.assertAlmostEqual(rx, xr, places=2)
            self.assertAlmostEqual(ry, yr, places=2)

    def test_degenerate_region(self) -> None:
        # width/height <= 1 não deve dividir por zero.
        self.assertEqual(ratio_to_abs(0.5, 0.5, 10, 20, 1, 1), (10, 20))
        self.assertEqual(abs_to_ratio(10, 20, 10, 20, 1, 1), (0.0, 0.0))


if __name__ == "__main__":
    unittest.main()
