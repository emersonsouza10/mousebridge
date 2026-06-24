"""Persistência da seção foshar no config.yaml (usada pelo formulário da GUI)."""

import tempfile
import unittest
from pathlib import Path

import yaml

from foshar.config import load_foshar_config, read_foshar_section, save_shares


class PersistTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "config.yaml"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_save_then_read_roundtrip(self) -> None:
        shares = [
            {"id": "projeto", "path": "~/dev/projeto", "mode": "rw"},
            {"id": "docs", "path": "~/docs", "mode": "ro"},
        ]
        save_shares(self.path, enabled=True, port=50513, cache_dir="~/.foshar/cache", shares=shares)
        read, port, cache = read_foshar_section(self.path)
        self.assertEqual(read, shares)  # caminhos preservados como digitados
        self.assertEqual(port, 50513)
        self.assertEqual(cache, "~/.foshar/cache")

    def test_save_preserves_other_sections(self) -> None:
        self.path.write_text(
            yaml.safe_dump({"security": {"shared_key": "k", "allowed_hosts": ["192.168.1.*"]}}),
            encoding="utf-8",
        )
        save_shares(self.path, enabled=True, port=50513, cache_dir="c", shares=[{"id": "a", "path": "/a", "mode": "ro"}])
        raw = yaml.safe_load(self.path.read_text(encoding="utf-8"))
        self.assertEqual(raw["security"]["shared_key"], "k")
        self.assertEqual(raw["security"]["allowed_hosts"], ["192.168.1.*"])
        self.assertEqual(raw["foshar"]["shares"][0]["id"], "a")

    def test_empty_shares_disables(self) -> None:
        save_shares(self.path, enabled=False, port=50513, cache_dir="c", shares=[])
        raw = yaml.safe_load(self.path.read_text(encoding="utf-8"))
        self.assertFalse(raw["foshar"]["enabled"])

    def test_saved_config_loads_into_sandbox(self) -> None:
        share_dir = Path(self._tmp.name) / "shared"
        share_dir.mkdir()
        save_shares(
            self.path, enabled=True, port=50513, cache_dir="c",
            shares=[{"id": "s", "path": str(share_dir), "mode": "rw"}],
        )
        config = load_foshar_config(self.path)
        self.assertEqual(len(config.shares), 1)
        self.assertEqual(config.shares[0].id, "s")
        self.assertTrue(config.shares[0].writable)

    def test_read_missing_file_returns_defaults(self) -> None:
        shares, port, cache = read_foshar_section(self.path)
        self.assertEqual(shares, [])
        self.assertEqual(port, 50513)


if __name__ == "__main__":
    unittest.main()
