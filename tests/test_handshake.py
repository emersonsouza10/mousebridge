"""Integração: handshake de autenticação sobre TCP real (loopback)."""

import asyncio
import unittest

from mousebridge.transport import Message, MessageStream, MsgType
from mousebridge.transport.security import make_challenge, sign_challenge, verify_challenge

KEY = "chave-de-teste"


async def _server_side(stream: MessageStream, shared_key: str) -> bool:
    nonce = make_challenge()
    await stream.send(Message(MsgType.AUTH_CHALLENGE, {"nonce": nonce}))
    response = await stream.receive()
    ok = response.type == MsgType.AUTH_RESPONSE and verify_challenge(
        shared_key, nonce, response.data.get("digest", "")
    )
    await stream.send(Message(MsgType.AUTH_OK if ok else MsgType.AUTH_FAIL, {}))
    return ok


async def _client_side(stream: MessageStream, shared_key: str) -> bool:
    challenge = await stream.receive()
    digest = sign_challenge(shared_key, challenge.data["nonce"])
    await stream.send(Message(MsgType.AUTH_RESPONSE, {"digest": digest}))
    result = await stream.receive()
    return result.type == MsgType.AUTH_OK


async def _run_handshake(server_key: str, client_key: str) -> tuple[bool, bool]:
    server_result: asyncio.Future[bool] = asyncio.get_running_loop().create_future()

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        stream = MessageStream(reader, writer)
        server_result.set_result(await _server_side(stream, server_key))
        await stream.close()

    server = await asyncio.start_server(handle, host="127.0.0.1", port=0)
    port = server.sockets[0].getsockname()[1]
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    client_stream = MessageStream(reader, writer)
    client_ok = await asyncio.wait_for(_client_side(client_stream, client_key), timeout=5.0)
    server_ok = await asyncio.wait_for(server_result, timeout=5.0)
    await client_stream.close()
    server.close()
    await server.wait_closed()
    return server_ok, client_ok


class HandshakeTest(unittest.TestCase):
    def test_matching_keys_authenticate(self) -> None:
        server_ok, client_ok = asyncio.run(_run_handshake(KEY, KEY))
        self.assertTrue(server_ok)
        self.assertTrue(client_ok)

    def test_wrong_key_rejected(self) -> None:
        server_ok, client_ok = asyncio.run(_run_handshake(KEY, "chave-errada"))
        self.assertFalse(server_ok)
        self.assertFalse(client_ok)


if __name__ == "__main__":
    unittest.main()
