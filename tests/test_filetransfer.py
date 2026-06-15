import asyncio
import os
import unittest

from zephyrlink.clipboard.transfer import FileReceiver, FileTooLarge, build_manifest, send_files
from zephyrlink.transport.messages import Message, MsgType


def _write(path: str, data: bytes) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)


class BuildManifestTest(unittest.TestCase):
    def setUp(self) -> None:
        import tempfile

        self.src = tempfile.mkdtemp()

    def test_single_file(self) -> None:
        p = os.path.join(self.src, "a.txt")
        _write(p, b"hello")
        files = build_manifest([p], max_total=10_000)
        self.assertEqual([(rel, size) for _, rel, size in files], [("a.txt", 5)])

    def test_directory_preserves_relative_tree(self) -> None:
        _write(os.path.join(self.src, "proj", "x.txt"), b"12")
        _write(os.path.join(self.src, "proj", "sub", "y.txt"), b"3456")
        files = build_manifest([os.path.join(self.src, "proj")], max_total=10_000)
        rels = sorted(rel for _, rel, _ in files)
        self.assertEqual(rels, ["proj/sub/y.txt", "proj/x.txt"])

    def test_total_cap_enforced(self) -> None:
        _write(os.path.join(self.src, "big.bin"), b"x" * 100)
        with self.assertRaises(FileTooLarge):
            build_manifest([os.path.join(self.src, "big.bin")], max_total=50)


class RoundTripTest(unittest.TestCase):
    def _run(self, coro):
        return asyncio.run(coro)

    def test_multiple_files_and_folder_roundtrip(self) -> None:
        import tempfile

        src = tempfile.mkdtemp()
        dest = tempfile.mkdtemp()
        # dois arquivos soltos + uma pasta com subpasta e um arquivo vazio
        _write(os.path.join(src, "a.txt"), b"AAAA")
        _write(os.path.join(src, "b.bin"), os.urandom(700_000))  # > 1 chunk
        _write(os.path.join(src, "dir", "c.txt"), b"CC")
        _write(os.path.join(src, "dir", "deep", "d.txt"), b"")  # arquivo vazio
        selection = [
            os.path.join(src, "a.txt"),
            os.path.join(src, "b.bin"),
            os.path.join(src, "dir"),
        ]

        captured: list[Message] = []

        async def send(message: Message) -> None:
            captured.append(message)

        async def go() -> list[str]:
            ok = await send_files(selection, send, max_total=10_000_000, chunk_size=256 * 1024)
            self.assertTrue(ok)
            receiver = FileReceiver(dest)
            roots: list[str] = []
            for msg in captured:
                result = await receiver.feed(msg)
                if result is not None:
                    roots = result
            return roots

        roots = self._run(go())

        # protocolo: 1 offer, vários data, 1 end
        self.assertEqual(captured[0].type, MsgType.FILE_OFFER)
        self.assertEqual(captured[-1].type, MsgType.FILE_END)
        self.assertTrue(any(m.type == MsgType.FILE_DATA for m in captured))

        # raízes de topo = os 3 itens selecionados
        self.assertEqual(sorted(os.path.basename(r) for r in roots), ["a.txt", "b.bin", "dir"])

        # conteúdo reconstruído byte a byte
        for rel, original in (
            ("a.txt", os.path.join(src, "a.txt")),
            ("b.bin", os.path.join(src, "b.bin")),
            ("dir/c.txt", os.path.join(src, "dir", "c.txt")),
            ("dir/deep/d.txt", os.path.join(src, "dir", "deep", "d.txt")),
        ):
            tid = captured[0].data["id"]
            rebuilt = os.path.join(dest, tid, *rel.split("/"))
            with open(rebuilt, "rb") as fh:
                self.assertEqual(fh.read(), open(original, "rb").read())

    def test_path_traversal_is_neutralized(self) -> None:
        dest = __import__("tempfile").mkdtemp()
        receiver = FileReceiver(dest)

        async def go() -> None:
            await receiver.feed(
                Message(MsgType.FILE_OFFER, {"id": "x", "files": [{"path": "../../evil.txt", "size": 0}]})
            )

        self._run(go())
        # os ".." são removidos: o arquivo fica contido no diretório da transferência
        self.assertTrue(os.path.exists(os.path.join(dest, "x", "evil.txt")))
        self.assertFalse(os.path.exists(os.path.join(dest, "evil.txt")))

    def test_empty_relative_path_rejected(self) -> None:
        dest = __import__("tempfile").mkdtemp()
        receiver = FileReceiver(dest)

        async def go() -> None:
            await receiver.feed(Message(MsgType.FILE_OFFER, {"id": "x", "files": [{"path": "..", "size": 0}]}))

        with self.assertRaises(ValueError):
            self._run(go())


if __name__ == "__main__":
    unittest.main()
