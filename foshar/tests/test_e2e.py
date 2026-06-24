"""Ponta a ponta: handshake + clone incremental sobre TCP real (loopback).

Exercita B2→B6 juntos: o serviço dono publica um share, o cliente RPC conecta,
clona para um espelho local e, depois de uma mudança remota, re-sincroniza só o
delta.
"""

import asyncio
import base64
import tempfile
import unittest
from pathlib import Path

from zephyrlink.config import SecurityConfig

from foshar.config import FosharConfig
from foshar.foshar_client.service import FosharService
from foshar.foshar_security.shares import Share
from foshar.foshar_server.rpc import FosharClient, RpcError
from foshar.foshar_server.workspace import clone

KEY = "chave-de-teste"


class E2ETest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._remote = tempfile.TemporaryDirectory()
        self._cache = tempfile.TemporaryDirectory()
        self.remote = Path(self._remote.name).resolve()
        (self.remote / "pkg").mkdir()
        (self.remote / "main.py").write_text("print('hi')\n")
        (self.remote / "pkg" / "util.py").write_text("x = 1\n")

        self.config = FosharConfig(
            shares=(Share(id="proj", path=self.remote, mode="rw"),),
            security=SecurityConfig(shared_key=KEY),
        )
        self.service = FosharService(self.config)
        self.server = await asyncio.start_server(self.service._handle, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]

    async def asyncTearDown(self) -> None:
        self.server.close()
        await self.server.wait_closed()
        self._remote.cleanup()
        self._cache.cleanup()

    async def _client(self) -> FosharClient:
        client = FosharClient("127.0.0.1", self.port, self.config.security)
        await client.connect()
        return client

    async def test_handshake_publishes_shares(self) -> None:
        client = await self._client()
        try:
            self.assertEqual([s["id"] for s in client.shares], ["proj"])
        finally:
            await client.close()

    async def test_fetch_shares(self) -> None:
        from foshar.foshar_server.rpc import fetch_shares

        shares = await fetch_shares("127.0.0.1", self.port, self.config.security)
        self.assertEqual([(s["id"], s["mode"]) for s in shares], [("proj", "rw")])

    async def test_wrong_key_rejected(self) -> None:
        client = FosharClient("127.0.0.1", self.port, SecurityConfig(shared_key="errada"))
        with self.assertRaises(ConnectionError):
            await client.connect()
        await client.close()

    async def test_clone_then_incremental(self) -> None:
        client = await self._client()
        try:
            root = await clone(client, "proj", self.config.cache_dir)
            self.assertEqual((root / "main.py").read_text(), "print('hi')\n")
            self.assertEqual((root / "pkg" / "util.py").read_text(), "x = 1\n")

            # Muda no remoto e re-sincroniza: o espelho acompanha.
            (self.remote / "main.py").write_text("print('bye')\n")
            (self.remote / "novo.txt").write_text("novo\n")
            root = await clone(client, "proj", self.config.cache_dir)
            self.assertEqual((root / "main.py").read_text(), "print('bye')\n")
            self.assertEqual((root / "novo.txt").read_text(), "novo\n")
        finally:
            await client.close()

    async def test_write_back_and_readonly(self) -> None:
        client = await self._client()
        try:
            await client.write("proj", "main.py", base64.b64encode(b"editado\n").decode())
            self.assertEqual((self.remote / "main.py").read_text(), "editado\n")

            # Caminho fora do share é recusado pelo dono.
            with self.assertRaises(RpcError):
                await client.read("proj", "../etc/passwd")
        finally:
            await client.close()

    async def test_readonly_share_rejects_write(self) -> None:
        ro_config = FosharConfig(
            shares=(Share(id="proj", path=self.remote, mode="ro"),),
            security=SecurityConfig(shared_key=KEY),
        )
        service = FosharService(ro_config)
        server = await asyncio.start_server(service._handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        client = FosharClient("127.0.0.1", port, ro_config.security)
        await client.connect()
        try:
            with self.assertRaises(RpcError):
                await client.write("proj", "main.py", base64.b64encode(b"x").decode())
        finally:
            await client.close()
            server.close()
            await server.wait_closed()


if __name__ == "__main__":
    unittest.main()
