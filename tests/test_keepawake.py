import unittest

from zephyrlink.config.settings import ConfigError, build_config
from zephyrlink.keepawake import (
    effective_idle_threshold,
    idle_seconds,
    lock_timeout_seconds,
    should_keep_awake,
)


class ShouldKeepAwakeTest(unittest.TestCase):
    def test_holds_when_idle_at_or_above_threshold(self) -> None:
        self.assertTrue(should_keep_awake(180.0, 180.0))
        self.assertTrue(should_keep_awake(240.0, 180.0))

    def test_releases_when_active(self) -> None:
        self.assertFalse(should_keep_awake(0.0, 180.0))
        self.assertFalse(should_keep_awake(59.9, 180.0))

    def test_holds_when_idle_unknown(self) -> None:
        # Sem detecção de ociosidade (None) => mantém acordado (comportamento seguro).
        self.assertTrue(should_keep_awake(None, 180.0))


class IdleSecondsTest(unittest.TestCase):
    def test_returns_float_or_none(self) -> None:
        val = idle_seconds()
        self.assertTrue(val is None or (isinstance(val, float) and val >= 0.0))


class LockTimeoutTest(unittest.TestCase):
    def test_returns_positive_float_or_none(self) -> None:
        val = lock_timeout_seconds()
        self.assertTrue(val is None or (isinstance(val, float) and val > 0.0))


class EffectiveThresholdTest(unittest.TestCase):
    def test_no_lock_uses_configured(self) -> None:
        self.assertEqual(effective_idle_threshold(180.0, None), 180.0)
        self.assertEqual(effective_idle_threshold(180.0, 0.0), 180.0)

    def test_caps_below_lock(self) -> None:
        # Bloqueio em 60s, folga 20 => age aos 40s (antes do bloqueio).
        self.assertEqual(effective_idle_threshold(180.0, 60.0, 20.0), 40.0)

    def test_respects_configured_when_lock_is_far(self) -> None:
        self.assertEqual(effective_idle_threshold(180.0, 600.0, 20.0), 180.0)

    def test_floor_when_lock_very_short(self) -> None:
        self.assertEqual(effective_idle_threshold(180.0, 10.0, 20.0), 5.0)


class KeepAwakeConfigTest(unittest.TestCase):
    def test_default_threshold_is_one_minute(self) -> None:
        self.assertEqual(build_config({}).network.keep_awake_idle_threshold, 60.0)

    def test_custom_threshold(self) -> None:
        net = build_config({"network": {"keep_awake_idle_threshold": 120}}).network
        self.assertEqual(net.keep_awake_idle_threshold, 120.0)

    def test_invalid_threshold_rejected(self) -> None:
        for bad in (0, -5):
            with self.subTest(bad=bad):
                with self.assertRaises(ConfigError):
                    build_config({"network": {"keep_awake_idle_threshold": bad}})


if __name__ == "__main__":
    unittest.main()
