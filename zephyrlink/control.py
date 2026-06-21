"""Cliente do canal de controle local: subcomandos ``launch`` e ``apps``.

Conecta ao servidor que já roda nesta máquina (127.0.0.1, porta de controle),
autentica com a chave compartilhada e envia um pedido de controle. Imprime o
resultado e sai com código 0 em sucesso.
"""

from __future__ import annotations

import asyncio
import sys

from zephyrlink.config import AppConfig
from zephyrlink.transport import Message, MessageStream, MsgType
from zephyrlink.transport.security import sign_challenge


async def _control(config: AppConfig, request: Message) -> dict:
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection("127.0.0.1", config.network.control_port), timeout=5.0
    )
    stream = MessageStream(reader, writer)
    try:
        challenge = await asyncio.wait_for(stream.receive(), timeout=10.0)
        if challenge.type != MsgType.AUTH_CHALLENGE:
            return {"error": "resposta inesperada do servidor"}
        digest = sign_challenge(config.security.shared_key, str(challenge.data["nonce"]))
        await stream.send(Message(MsgType.AUTH_RESPONSE, {"digest": digest}))
        ok = await asyncio.wait_for(stream.receive(), timeout=10.0)
        if ok.type != MsgType.AUTH_OK:
            return {"error": "chave compartilhada inválida"}
        await stream.send(request)
        reply = await asyncio.wait_for(stream.receive(), timeout=20.0)
        return reply.data if reply.type == MsgType.CTRL_REPLY else {"error": "sem resposta"}
    finally:
        await stream.close()


def _send(config: AppConfig, request: Message) -> dict:
    return asyncio.run(_control(config, request))


def _unreachable(config: AppConfig, exc: Exception) -> None:
    print(
        f"Não foi possível falar com o servidor local em 127.0.0.1:{config.network.control_port}: {exc}\n"
        "O servidor (zephyrlink server ou a GUI em modo Servidor) precisa estar rodando.",
        file=sys.stderr,
    )


def run_launch_cli(config: AppConfig, target: str, app_id: str, args: list[str]) -> int:
    request = Message(MsgType.CTRL_LAUNCH, {"client": target, "app": app_id, "args": args})
    try:
        data = _send(config, request)
    except (ConnectionError, OSError, asyncio.TimeoutError) as exc:
        _unreachable(config, exc)
        return 2
    if "ok" not in data:
        print(f"Erro: {data.get('error', 'falha desconhecida')}", file=sys.stderr)
        return 2
    if data["ok"]:
        pid = data.get("pid")
        print(f"OK: '{app_id}' aberto em {target}" + (f" (pid={pid})" if pid else ""))
        return 0
    print(f"Falhou ({data.get('state') or 'erro'}): {data.get('error') or ''}".strip(), file=sys.stderr)
    return 1


def run_apps_cli(config: AppConfig, target: str | None) -> int:
    try:
        data = _send(config, Message(MsgType.CTRL_LIST, {"client": target or ""}))
    except (ConnectionError, OSError, asyncio.TimeoutError) as exc:
        _unreachable(config, exc)
        return 2
    if "clients" not in data:
        print(f"Erro: {data.get('error', 'falha desconhecida')}", file=sys.stderr)
        return 2
    clients = data["clients"]
    if not clients:
        print(f"Cliente '{target}' não conectado." if target else "Nenhum cliente conectado.")
        return 1
    for client in clients:
        apps = client.get("apps") or []
        edge = client.get("edge") or "sem borda"
        print(f"\nCliente {client.get('host')} ({edge}) — {len(apps)} app(s):")
        if not apps:
            print("  (catálogo vazio — launcher desligado ou sem apps neste cliente)")
        for app in apps:
            params = "sim" if app.get("accepts_args") else "não"
            print(f"  {str(app.get('id', '')):<20} {str(app.get('label', '')):<28} params={params}")
    return 0
