"""Gate de segurança do acesso remoto no cliente (alvo).

Sem rede: monta um ZephyrLinkClient, injeta um stream falso e chama
``_handle_rd_start`` direto, verificando quando a sessão é recusada e quando é
aceita. Captura/consentimento reais são substituídos por mocks.
"""

from __future__ import annotations

import asyncio
import unittest
from typing import Any
from unittest import mock

from zephyrlink.client import client as client_module
from zephyrlink.client.client import ZephyrLinkClient
from zephyrlink.config.settings import build_config
from zephyrlink.transport import Message, MsgType


class FakeStream:
    def __init__(self, host: str = "192.168.0.9") -> None:
        self._host = host
        self.sent: list[Message] = []

    @property
    def peer_host(self) -> str:
        return self._host

    async def send(self, message: Message) -> None:
        self.sent.append(message)

    async def close(self) -> None:
        pass


class FakeSession:
    def __init__(self, loop, stream, config, host, on_idle_stop=None) -> None:  # noqa: ANN001
        self.region = (0, 0, 1920, 1080)
        self.started = False

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        pass

    def mark_input(self) -> None:
        pass


def _client(**rd: Any) -> ZephyrLinkClient:
    config = build_config({"role": "client", "remote_desktop": rd})
    c = ZephyrLinkClient(config)
    c._start_panic_hotkey = lambda: None  # type: ignore[method-assign]  # evita pynput
    return c


def _last_reason(stream: FakeStream) -> str | None:
    if not stream.sent:
        return None
    return stream.sent[-1].data.get("reason")


class ConsentGateTest(unittest.TestCase):
    def test_rejected_when_disabled(self) -> None:
        c = _client(enabled=False)
        stream = FakeStream()
        asyncio.run(c._handle_rd_start(stream, {}))
        self.assertEqual(stream.sent[-1].type, MsgType.RD_REJECT)
        self.assertEqual(_last_reason(stream), "desabilitado")
        self.assertFalse(c._rd_active)

    def test_rejected_without_tls(self) -> None:
        c = _client(enabled=True, allow_insecure=False)  # sem TLS
        stream = FakeStream()
        asyncio.run(c._handle_rd_start(stream, {}))
        self.assertEqual(stream.sent[-1].type, MsgType.RD_REJECT)
        self.assertEqual(_last_reason(stream), "sem_tls")

    def test_rejected_when_consent_denied(self) -> None:
        c = _client(enabled=True, allow_insecure=True, require_consent=True)
        stream = FakeStream()
        with mock.patch.object(client_module, "ask_consent", new=mock.AsyncMock(return_value=False)):
            asyncio.run(c._handle_rd_start(stream, {}))
        self.assertEqual(stream.sent[-1].type, MsgType.RD_REJECT)
        self.assertEqual(_last_reason(stream), "negado_pelo_usuario")

    def test_accepted_when_consent_given(self) -> None:
        c = _client(enabled=True, allow_insecure=True, require_consent=True)
        stream = FakeStream()
        with mock.patch.object(client_module, "ask_consent", new=mock.AsyncMock(return_value=True)), \
                mock.patch.object(client_module, "RemoteDesktopSession", FakeSession):
            asyncio.run(c._handle_rd_start(stream, {}))
        self.assertEqual(stream.sent[-1].type, MsgType.RD_ACCEPT)
        self.assertEqual(stream.sent[-1].data["region"], [0, 0, 1920, 1080])
        self.assertTrue(c._rd_active)

    def test_accepted_without_consent_when_not_required(self) -> None:
        c = _client(enabled=True, allow_insecure=True, require_consent=False)
        stream = FakeStream()
        with mock.patch.object(client_module, "RemoteDesktopSession", FakeSession):
            asyncio.run(c._handle_rd_start(stream, {}))
        self.assertEqual(stream.sent[-1].type, MsgType.RD_ACCEPT)
        self.assertTrue(c._rd_active)

    def test_rejected_when_already_active(self) -> None:
        c = _client(enabled=True, allow_insecure=True, require_consent=False)
        c._rd_active = True
        stream = FakeStream()
        asyncio.run(c._handle_rd_start(stream, {}))
        self.assertEqual(stream.sent[-1].type, MsgType.RD_REJECT)
        self.assertEqual(_last_reason(stream), "ja_ativo")


if __name__ == "__main__":
    unittest.main()
