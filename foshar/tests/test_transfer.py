"""Transferência de arquivos grandes por pedaços (B9), sobre TCP loopback."""

import asyncio
import os
import tempfile
import unittest
from pathlib import Path

from zephyrlink.config import SecurityConfig

from foshar.config import FosharConfig
from foshar.foshar_cache.index import SyncIndex
from foshar.foshar_cache.mirror import Mirror
from foshar.foshar_client.service import FosharService
from foshar.foshar_security.shares import Share
from foshar.foshar_server.rpc import FosharClient
from foshar.foshar_server.transfer import download, upload
from foshar.foshar_sync.engine import SyncEngine
from foshar.util import hash_bytes

KEY = "chave-de-teste"
# Maior que o teto inline de 8 MB e que vários blocos de 1 MB.
BIG = 9_000_000


class TransferE2ETest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._remote = tempfile.TemporaryDirectory()
        self._cache = tempfile.TemporaryDirectory()
        self.remote = Path(self._remote.name).resolve()
        self.config = FosharConfig(
            shares=(Share(id="proj", path=self.remote, mode="rw"),),
            security=SecurityConfig(shared_key=KEY),
        )
        service = FosharService(self.config)
        self.server = await asyncio.start_server(service._handle, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]
        self.client = FosharClient("127.0.0.1", self.port, self.config.security)
        await self.client.connect()
        cache = Path(self._cache.name)
        self.mirror = Mirror(cache / "proj")
        self.index = SyncIndex(cache / ".foshar" / "proj.index.db")
        self.engine = SyncEngine(self.client, "proj", self.mirror, self.index)

    async def asyncTearDown(self) -> None:
        self.index.close()
        await self.client.close()
        self.server.close()
        await self.server.wait_closed()
        self._remote.cleanup()
        self._cache.cleanup()

    async def test_direct_upload_then_download(self) -> None:
        data = os.urandom(BIG)
        src = self.mirror.root / "big.bin"
        src.write_bytes(data)
        up_hash = await upload(self.client, "proj", "big.bin", src)
        self.assertEqual((self.remote / "big.bin").read_bytes(), data)
        self.assertEqual(up_hash, hash_bytes(data))

        dest = self.mirror.root / "copy.bin"
        down_hash = await download(self.client, "proj", "big.bin", dest)
        self.assertEqual(dest.read_bytes(), data)
        self.assertEqual(down_hash, up_hash)

    async def test_large_file_roundtrip_via_engine(self) -> None:
        await self.engine.sync_once()
        data = os.urandom(BIG)
        (self.mirror.root / "big.bin").write_bytes(data)
        await self.engine.sync_once()  # push grande
        self.assertEqual((self.remote / "big.bin").read_bytes(), data)

        data2 = os.urandom(BIG)
        (self.remote / "big.bin").write_bytes(data2)
        await self.engine.sync_once()  # pull grande
        self.assertEqual((self.mirror.root / "big.bin").read_bytes(), data2)
        self.assertEqual(await self.engine.sync_once(), [])  # convergiu

    async def test_empty_file_roundtrip(self) -> None:
        await self.engine.sync_once()
        (self.mirror.root / "vazio.txt").write_bytes(b"")
        await self.engine.sync_once()
        self.assertTrue((self.remote / "vazio.txt").exists())
        self.assertEqual((self.remote / "vazio.txt").read_bytes(), b"")

    async def test_upload_temp_not_in_manifest(self) -> None:
        # Um .foshar-upload órfão no dono não pode vazar para o manifesto.
        (self.remote / "sobra.foshar-upload").write_bytes(b"lixo")
        (self.remote / "real.txt").write_text("ok\n")
        entries = await self.client.manifest("proj")
        names = {e["path"] for e in entries}
        self.assertIn("real.txt", names)
        self.assertNotIn("sobra.foshar-upload", names)


if __name__ == "__main__":
    unittest.main()
