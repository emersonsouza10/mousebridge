"""Sincronização bidirecional ponta a ponta sobre TCP loopback."""

import asyncio
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
from foshar.foshar_sync.engine import SyncEngine

KEY = "chave-de-teste"


class SyncE2ETest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._remote = tempfile.TemporaryDirectory()
        self._cache = tempfile.TemporaryDirectory()
        self.remote = Path(self._remote.name).resolve()
        (self.remote / "main.py").write_text("v0\n")

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

    async def test_initial_sync_pulls(self) -> None:
        await self.engine.sync_once()
        self.assertEqual((self.mirror.root / "main.py").read_text(), "v0\n")

    async def test_local_edit_pushes_to_owner(self) -> None:
        await self.engine.sync_once()
        (self.mirror.root / "main.py").write_text("editado-local\n")
        (self.mirror.root / "novo.py").write_text("novo\n")
        await self.engine.sync_once()
        self.assertEqual((self.remote / "main.py").read_text(), "editado-local\n")
        self.assertEqual((self.remote / "novo.py").read_text(), "novo\n")

    async def test_local_delete_removes_on_owner(self) -> None:
        await self.engine.sync_once()
        (self.mirror.root / "main.py").unlink()
        await self.engine.sync_once()
        self.assertFalse((self.remote / "main.py").exists())

    async def test_remote_edit_pulls_to_mirror(self) -> None:
        await self.engine.sync_once()
        (self.remote / "main.py").write_text("editado-remoto\n")
        await self.engine.sync_once()
        self.assertEqual((self.mirror.root / "main.py").read_text(), "editado-remoto\n")

    async def test_remote_delete_removes_from_mirror(self) -> None:
        await self.engine.sync_once()
        (self.remote / "main.py").unlink()
        await self.engine.sync_once()
        self.assertFalse((self.mirror.root / "main.py").exists())

    async def test_conflict_remote_wins_local_backed_up(self) -> None:
        await self.engine.sync_once()
        (self.mirror.root / "main.py").write_text("versao-local\n")
        (self.remote / "main.py").write_text("versao-remota\n")
        await self.engine.sync_once()
        self.assertEqual((self.mirror.root / "main.py").read_text(), "versao-remota\n")
        backup = self.mirror.root / "main.py.foshar-conflict"
        self.assertTrue(backup.exists())
        self.assertEqual(backup.read_text(), "versao-local\n")
        # O backup não deve vazar para o dono num próximo ciclo.
        await self.engine.sync_once()
        self.assertFalse((self.remote / "main.py.foshar-conflict").exists())

    async def test_idempotent_when_in_sync(self) -> None:
        await self.engine.sync_once()
        actions = await self.engine.sync_once()
        self.assertEqual(actions, [])

    async def test_readonly_share_skips_push(self) -> None:
        ro_remote = Path(tempfile.mkdtemp()).resolve()
        (ro_remote / "f.txt").write_text("ro\n")
        ro_config = FosharConfig(
            shares=(Share(id="proj", path=ro_remote, mode="ro"),),
            security=SecurityConfig(shared_key=KEY),
        )
        service = FosharService(ro_config)
        server = await asyncio.start_server(service._handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        client = FosharClient("127.0.0.1", port, ro_config.security)
        await client.connect()
        cache = Path(tempfile.mkdtemp())
        mirror = Mirror(cache / "proj")
        index = SyncIndex(cache / "proj.index.db")
        engine = SyncEngine(client, "proj", mirror, index)
        try:
            await engine.sync_once()
            (mirror.root / "f.txt").write_text("tentativa-local\n")
            await engine.sync_once()  # push é recusado (ro), mas não quebra
            self.assertEqual((ro_remote / "f.txt").read_text(), "ro\n")
        finally:
            index.close()
            await client.close()
            server.close()
            await server.wait_closed()


if __name__ == "__main__":
    unittest.main()
