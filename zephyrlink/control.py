"""Cliente do canal de controle local: o subcomando ``launch``.

Conecta ao servidor que já roda nesta máquina (127.0.0.1, porta de controle),
autentica com a chave compartilhada e dispara a abertura de um app num cliente.
Imprime o resultado e sai com código 0 em sucesso.
"""

from __future__ import annotations

import asyncio
import sys

from zephyrlink.config import AppConfig
from zephyrlink.transport import Message, MessageStream, MsgType
from zephyrlink.transport.security import sign_challenge


async def _request(config: AppConfig, target: str, app_id: str, args: list[str]) -> dict:
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection("127.0.0.1", config.network.control_port), timeout=5.0
    )
    stream = MessageStream(reader, writer)
    try:
        challenge = await asyncio.wait_for(stream.receive(), timeout=10.0)
        if challenge.type != MsgType.AUTH_CHALLENGE:
            return {"ok": False, "error": "resposta inesperada do servidor"}
        digest = sign_challenge(config.security.shared_key, str(challenge.data["nonce"]))
        await stream.send(Message(MsgType.AUTH_RESPONSE, {"digest": digest}))
        result = await asyncio.wait_for(stream.receive(), timeout=10.0)
        if result.type != MsgType.AUTH_OK:
            return {"ok": False, "error": "chave compartilhada inválida"}
        await stream.send(
            Message(MsgType.CTRL_LAUNCH, {"client": target, "app": app_id, "args": args})
        )
        reply = await asyncio.wait_for(stream.receive(), timeout=20.0)
        return reply.data if reply.type == MsgType.CTRL_REPLY else {"ok": False, "error": "sem resposta"}
    finally:
        await stream.close()


def run_launch_cli(config: AppConfig, target: str, app_id: str, args: list[str]) -> int:
    try:
        data = asyncio.run(_request(config, target, app_id, args))
    except (ConnectionError, OSError, asyncio.TimeoutError) as exc:
        print(
            f"Não foi possível falar com o servidor local em 127.0.0.1:{config.network.control_port}: {exc}\n"
            "O servidor (zephyrlink server ou a GUI em modo Servidor) precisa estar rodando.",
            file=sys.stderr,
        )
        return 2
    if data.get("ok"):
        pid = data.get("pid")
        print(f"OK: '{app_id}' aberto em {target}" + (f" (pid={pid})" if pid else ""))
        return 0
    print(f"Falhou ({data.get('state') or 'erro'}): {data.get('error') or ''}".strip(), file=sys.stderr)
    return 1
