import unittest

from zephyrlink.config.settings import ConfigError, build_config


class RemoteDesktopConfigTest(unittest.TestCase):
    def test_defaults(self) -> None:
        # Frota administrada em LAN: remoto ligado, sem consentimento e sem TLS
        # por padrão (o acesso é gated pela shared_key).
        rd = build_config({}).remote_desktop
        self.assertTrue(rd.enabled)
        self.assertFalse(rd.require_consent)
        self.assertEqual(rd.fps, 12)
        self.assertEqual(rd.quality, 60)
        self.assertEqual(rd.scale, 1.0)
        self.assertEqual(rd.monitor, 0)
        self.assertTrue(rd.allow_insecure)
        self.assertTrue(rd.indicator)
        self.assertIsNone(rd.audit_file)

    def test_can_still_opt_out(self) -> None:
        # Um cliente pode desligar explicitamente ou reexigir consentimento/TLS.
        rd = build_config(
            {"remote_desktop": {"enabled": False, "require_consent": True, "allow_insecure": False}}
        ).remote_desktop
        self.assertFalse(rd.enabled)
        self.assertTrue(rd.require_consent)
        self.assertFalse(rd.allow_insecure)

    def test_full_section(self) -> None:
        rd = build_config(
            {
                "remote_desktop": {
                    "enabled": True,
                    "require_consent": False,
                    "fps": 20,
                    "quality": 80,
                    "scale": 0.5,
                    "monitor": 1,
                    "idle_timeout": 120,
                    "allow_insecure": True,
                    "indicator": False,
                    "audit_file": "/tmp/rd.jsonl",
                }
            }
        ).remote_desktop
        self.assertTrue(rd.enabled)
        self.assertFalse(rd.require_consent)
        self.assertEqual(rd.fps, 20)
        self.assertEqual(rd.quality, 80)
        self.assertEqual(rd.scale, 0.5)
        self.assertEqual(rd.monitor, 1)
        self.assertEqual(rd.idle_timeout, 120)
        self.assertTrue(rd.allow_insecure)
        self.assertFalse(rd.indicator)
        self.assertEqual(rd.audit_file, "/tmp/rd.jsonl")

    def test_invalid_values_rejected(self) -> None:
        bad_sections = [
            {"fps": 0},
            {"fps": 61},
            {"quality": 0},
            {"quality": 96},
            {"scale": 0},
            {"scale": 1.5},
            {"monitor": -1},
            {"idle_timeout": -1},
        ]
        for section in bad_sections:
            with self.subTest(section=section):
                with self.assertRaises(ConfigError):
                    build_config({"remote_desktop": section})


if __name__ == "__main__":
    unittest.main()
