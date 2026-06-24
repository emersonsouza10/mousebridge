"""Contenção de caminho: o coração da segurança do Foshar."""

import os
import tempfile
import unittest
from pathlib import Path

from foshar.foshar_security.sandbox import Sandbox, SandboxError
from foshar.foshar_security.shares import Share


class SandboxTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        (self.root / "sub").mkdir()
        (self.root / "sub" / "a.txt").write_text("oi")
        self.rw = Sandbox([Share(id="s", path=self.root, mode="rw")])
        self.ro = Sandbox([Share(id="s", path=self.root, mode="ro")])

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_resolves_inside(self) -> None:
        self.assertEqual(self.rw.resolve("s", "sub/a.txt"), self.root / "sub" / "a.txt")
        self.assertEqual(self.rw.resolve("s", "."), self.root)
        self.assertEqual(self.rw.resolve("s", ""), self.root)

    def test_rejects_parent_traversal(self) -> None:
        with self.assertRaises(SandboxError):
            self.rw.resolve("s", "../escape")
        with self.assertRaises(SandboxError):
            self.rw.resolve("s", "sub/../../escape")

    def test_rejects_absolute(self) -> None:
        with self.assertRaises(SandboxError):
            self.rw.resolve("s", "/etc/passwd")

    def test_rejects_backslash_and_drive(self) -> None:
        with self.assertRaises(SandboxError):
            self.rw.resolve("s", "sub\\a.txt")
        with self.assertRaises(SandboxError):
            self.rw.resolve("s", "C:/Windows")

    def test_rejects_unknown_share(self) -> None:
        with self.assertRaises(SandboxError):
            self.rw.resolve("nope", "a.txt")

    def test_symlink_escape_rejected(self) -> None:
        outside = Path(self._tmp.name).parent / "foshar-outside-secret"
        outside.write_text("segredo")
        link = self.root / "leak"
        try:
            os.symlink(outside, link)
        except (OSError, NotImplementedError):
            self.skipTest("symlink não suportado neste ambiente")
        try:
            with self.assertRaises(SandboxError):
                self.rw.resolve("s", "leak")
        finally:
            link.unlink(missing_ok=True)
            outside.unlink(missing_ok=True)

    def test_readonly_blocks_write(self) -> None:
        self.ro.resolve("s", "sub/a.txt")  # leitura ok
        with self.assertRaises(SandboxError):
            self.ro.resolve_writable("s", "sub/a.txt")
        self.rw.resolve_writable("s", "sub/a.txt")  # rw ok


if __name__ == "__main__":
    unittest.main()
