import unittest

from zephyrlink.mouse.edge import EdgeDetector, entry_position, opposite_edge
from zephyrlink.mouse.screen import ScreenInfo, enable_dpi_awareness, primary_center

FHD = ScreenInfo(0, 0, 1920, 1080)
UHD = ScreenInfo(0, 0, 3840, 2160)


class OppositeEdgeTest(unittest.TestCase):
    def test_all_edges(self) -> None:
        self.assertEqual(opposite_edge("left"), "right")
        self.assertEqual(opposite_edge("right"), "left")
        self.assertEqual(opposite_edge("top"), "bottom")
        self.assertEqual(opposite_edge("bottom"), "top")


class EdgeDetectorTest(unittest.TestCase):
    def test_right_edge(self) -> None:
        detector = EdgeDetector("right", FHD)
        self.assertTrue(detector.hit(1919, 500))
        self.assertFalse(detector.hit(1918, 500))
        self.assertFalse(detector.hit(0, 500))

    def test_left_edge(self) -> None:
        detector = EdgeDetector("left", FHD)
        self.assertTrue(detector.hit(0, 500))
        self.assertFalse(detector.hit(1, 500))

    def test_top_edge(self) -> None:
        detector = EdgeDetector("top", FHD)
        self.assertTrue(detector.hit(960, 0))
        self.assertFalse(detector.hit(960, 1))

    def test_bottom_edge(self) -> None:
        detector = EdgeDetector("bottom", FHD)
        self.assertTrue(detector.hit(960, 1079))
        self.assertFalse(detector.hit(960, 1078))

    def test_margin_widens_trigger_zone(self) -> None:
        detector = EdgeDetector("right", FHD, margin=5)
        self.assertTrue(detector.hit(1915, 500))
        self.assertFalse(detector.hit(1914, 500))

    def test_multi_monitor_negative_origin(self) -> None:
        virtual = ScreenInfo(-1920, 0, 3840, 1080)
        detector = EdgeDetector("left", virtual)
        self.assertTrue(detector.hit(-1920, 300))
        self.assertFalse(detector.hit(0, 300))
        right = EdgeDetector("right", virtual)
        self.assertTrue(right.hit(1919, 300))

    def test_exit_ratio_vertical_edges(self) -> None:
        detector = EdgeDetector("right", FHD)
        self.assertAlmostEqual(detector.exit_ratio(1919, 0), 0.0)
        self.assertAlmostEqual(detector.exit_ratio(1919, 1079), 1.0)
        self.assertAlmostEqual(detector.exit_ratio(1919, 540), 0.5, places=2)

    def test_exit_ratio_horizontal_edges(self) -> None:
        detector = EdgeDetector("top", FHD)
        self.assertAlmostEqual(detector.exit_ratio(0, 0), 0.0)
        self.assertAlmostEqual(detector.exit_ratio(1919, 0), 1.0)


class EntryPositionTest(unittest.TestCase):
    def test_enter_from_left_with_inset(self) -> None:
        x, y = entry_position("left", 0.5, FHD, inset=2)
        self.assertEqual(x, 2)
        self.assertEqual(y, 540)

    def test_enter_from_right(self) -> None:
        x, y = entry_position("right", 0.0, FHD, inset=2)
        self.assertEqual(x, 1917)
        self.assertEqual(y, 0)

    def test_resolution_mapping_preserves_ratio(self) -> None:
        detector = EdgeDetector("right", FHD)
        ratio = detector.exit_ratio(1919, 540)
        x, y = entry_position("left", ratio, UHD, inset=2)
        self.assertEqual(x, 2)
        self.assertAlmostEqual(y / (UHD.height - 1), 0.5, places=2)

    def test_ratio_clamped(self) -> None:
        x, y = entry_position("left", 1.7, FHD)
        self.assertEqual(y, 1079)

    def test_invalid_edge_raises(self) -> None:
        with self.assertRaises(ValueError):
            entry_position("middle", 0.5, FHD)


class ScreenInfoTest(unittest.TestCase):
    def test_clamp(self) -> None:
        self.assertEqual(FHD.clamp(-10, 5000), (0, 1079))
        self.assertEqual(FHD.clamp(100, 200), (100, 200))

    def test_dict_roundtrip(self) -> None:
        screen = ScreenInfo(-1920, 0, 3840, 1080)
        self.assertEqual(ScreenInfo.from_dict(screen.to_dict()), screen)


class MultiEdgeSelectionTest(unittest.TestCase):
    """Topologia estrela: várias bordas vigiadas simultaneamente.

    Reproduz a varredura que o monitor de mouse faz — primeira borda
    atingida vence — sem depender do pynput.
    """

    def setUp(self) -> None:
        self.detectors = [
            EdgeDetector("right", FHD),
            EdgeDetector("bottom", FHD),
        ]

    def _first_hit(self, x: int, y: int) -> str | None:
        for d in self.detectors:
            if d.hit(x, y):
                return d.edge
        return None

    def test_right_border_selects_right_client(self) -> None:
        self.assertEqual(self._first_hit(1919, 400), "right")

    def test_bottom_border_selects_bottom_client(self) -> None:
        self.assertEqual(self._first_hit(900, 1079), "bottom")

    def test_interior_selects_nothing(self) -> None:
        self.assertIsNone(self._first_hit(960, 540))

    def test_untracked_border_selects_nothing(self) -> None:
        self.assertIsNone(self._first_hit(0, 400))


class PlatformHelpersTest(unittest.TestCase):
    def test_dpi_awareness_is_noop_off_windows(self) -> None:
        enable_dpi_awareness()

    def test_primary_center_none_off_windows(self) -> None:
        import sys

        if sys.platform != "win32":
            self.assertIsNone(primary_center())


if __name__ == "__main__":
    unittest.main()
