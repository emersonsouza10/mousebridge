"""Varredura e diff incremental por hash."""

import tempfile
import unittest
from pathlib import Path

from foshar.foshar_cache.index import IndexEntry
from foshar.foshar_sync.manifest import diff, scan


class ManifestTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "sub").mkdir()
        (self.root / "a.txt").write_text("aaa")
        (self.root / "sub" / "b.txt").write_text("bbb")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_scan_paths_are_posix_relative(self) -> None:
        paths = {e["path"] for e in scan(self.root)}
        self.assertEqual(paths, {"a.txt", "sub/b.txt"})

    def test_diff_empty_index_fetches_all(self) -> None:
        plan = diff(scan(self.root), {})
        self.assertEqual(plan.fetch, ["a.txt", "sub/b.txt"])
        self.assertEqual(plan.delete, [])

    def test_diff_only_changed(self) -> None:
        entries = scan(self.root)
        index = {e["path"]: IndexEntry(e["path"], e["size"], e["mtime"], e["hash"]) for e in entries}
        self.assertEqual(diff(entries, index).fetch, [])  # nada mudou
        (self.root / "a.txt").write_text("AAA-mudou")
        plan = diff(scan(self.root), index)
        self.assertEqual(plan.fetch, ["a.txt"])

    def test_diff_detects_deletion(self) -> None:
        entries = scan(self.root)
        index = {e["path"]: IndexEntry(e["path"], e["size"], e["mtime"], e["hash"]) for e in entries}
        index["gone.txt"] = IndexEntry("gone.txt", 1, 0.0, "deadbeef")
        self.assertEqual(diff(entries, index).delete, ["gone.txt"])


if __name__ == "__main__":
    unittest.main()
