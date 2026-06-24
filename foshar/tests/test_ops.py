"""Round-trip das operações de FS dentro do sandbox."""

import base64
import tempfile
import unittest
from pathlib import Path

from foshar.foshar_client import ops
from foshar.foshar_client.ops import OpError
from foshar.foshar_security.sandbox import Sandbox, SandboxError
from foshar.foshar_security.shares import Share


def _b64(text: str) -> str:
    return base64.b64encode(text.encode()).decode()


class OpsTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        self.sb = Sandbox([Share(id="s", path=self.root, mode="rw")])

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_write_then_read(self) -> None:
        ops.op_write(self.sb, {"share": "s", "path": "dir/x.txt", "data_b64": _b64("conteúdo")})
        self.assertEqual((self.root / "dir" / "x.txt").read_text(), "conteúdo")
        reply = ops.op_read(self.sb, {"share": "s", "path": "dir/x.txt"})
        self.assertEqual(base64.b64decode(reply["data_b64"]).decode(), "conteúdo")

    def test_list_and_stat(self) -> None:
        ops.op_write(self.sb, {"share": "s", "path": "a.txt", "data_b64": _b64("a")})
        ops.op_mkdir(self.sb, {"share": "s", "path": "d"})
        names = {e["name"]: e["type"] for e in ops.op_list(self.sb, {"share": "s", "path": "."})["entries"]}
        self.assertEqual(names, {"a.txt": "file", "d": "dir"})
        self.assertTrue(ops.op_stat(self.sb, {"share": "s", "path": "a.txt"})["exists"])
        self.assertFalse(ops.op_stat(self.sb, {"share": "s", "path": "nope"})["exists"])

    def test_create_rename_delete(self) -> None:
        ops.op_create(self.sb, {"share": "s", "path": "f.txt"})
        self.assertTrue((self.root / "f.txt").exists())
        ops.op_rename(self.sb, {"share": "s", "src": "f.txt", "dst": "g.txt"})
        self.assertFalse((self.root / "f.txt").exists())
        self.assertTrue((self.root / "g.txt").exists())
        ops.op_delete(self.sb, {"share": "s", "path": "g.txt"})
        self.assertFalse((self.root / "g.txt").exists())

    def test_rmdir(self) -> None:
        ops.op_mkdir(self.sb, {"share": "s", "path": "d/e"})
        ops.op_rmdir(self.sb, {"share": "s", "path": "d", "recursive": True})
        self.assertFalse((self.root / "d").exists())

    def test_readonly_write_rejected(self) -> None:
        ro = Sandbox([Share(id="s", path=self.root, mode="ro")])
        with self.assertRaises(SandboxError):
            ops.op_write(ro, {"share": "s", "path": "x.txt", "data_b64": _b64("x")})

    def test_delete_dir_rejected(self) -> None:
        ops.op_mkdir(self.sb, {"share": "s", "path": "d"})
        with self.assertRaises(OpError):
            ops.op_delete(self.sb, {"share": "s", "path": "d"})


if __name__ == "__main__":
    unittest.main()
