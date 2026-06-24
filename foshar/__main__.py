"""CLI do Foshar: ``python -m foshar {serve|open|status}``."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from dataclasses import replace
from pathlib import Path

from foshar.config import FosharConfig, FosharConfigError, load_foshar_config


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="foshar", description="Compartilhamento de pastas sobre o canal do ZephyrLink")
    sub = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("-c", "--config", default=None, help="caminho do config.yaml")
    common.add_argument("--key", default=None, help="chave compartilhada (sobrepõe o YAML)")

    sub.add_parser("serve", parents=[common], help="lado dono dos arquivos (publica os shares)")

    opener = sub.add_parser("open", parents=[common], help="clona um share e abre no VSCode")
    opener.add_argument("--host", required=True, help="IP da máquina dona dos arquivos")
    opener.add_argument("--share", required=True, help="id do share a clonar")
    opener.add_argument("--no-vscode", action="store_true", help="só sincroniza, não abre o VSCode")
    opener.add_argument("--watch", action="store_true",
                        help="continua sincronizando nos dois sentidos (não retorna)")

    syncer = sub.add_parser("sync", parents=[common],
                            help="sincroniza um share continuamente (bidirecional, sem VSCode)")
    syncer.add_argument("--host", required=True, help="IP da máquina dona dos arquivos")
    syncer.add_argument("--share", required=True, help="id do share a sincronizar")
    syncer.add_argument("--interval", type=float, default=2.0, help="segundos entre ciclos (padrão 2)")

    sub.add_parser("status", parents=[common], help="lista os shares configurados nesta máquina")
    sub.add_parser("gui", parents=[common], help="abre o cadastro de pastas compartilhadas")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    config_path = args.config
    if config_path is None and Path("config.yaml").exists():
        config_path = "config.yaml"
    try:
        config = load_foshar_config(config_path)
    except FosharConfigError as exc:
        print(f"Erro de configuração: {exc}", file=sys.stderr)
        return 2
    if args.key:
        config = replace(config, security=replace(config.security, shared_key=args.key))

    if args.command == "serve":
        from foshar.foshar_client import FosharService

        if not config.shares:
            print("Nenhum share configurado (seção foshar.shares no config.yaml).", file=sys.stderr)
            return 2
        try:
            asyncio.run(FosharService(config).run())
        except KeyboardInterrupt:
            pass
        return 0

    if args.command == "open":
        if args.watch:
            return _run_sync(config, args.host, args.share, vscode=not args.no_vscode, interval=2.0)
        return asyncio.run(_open(config, args.host, args.share, not args.no_vscode))

    if args.command == "sync":
        return _run_sync(config, args.host, args.share, vscode=False, interval=args.interval)

    if args.command == "gui":
        try:
            from foshar.gui.shares_editor import run_shares_gui
        except ImportError as exc:
            print(f"GUI indisponível (tkinter ausente?): {exc}", file=sys.stderr)
            return 2
        run_shares_gui(config_path or "config.yaml")
        return 0

    if args.command == "status":
        if not config.shares:
            print("Nenhum share configurado.")
            return 0
        for share in config.shares:
            print(f"{share.id:<20} {share.mode:<4} {share.path}")
        return 0
    return 2


async def _open(config: FosharConfig, host: str, share: str, vscode: bool) -> int:
    from foshar.foshar_server import FosharClient, RpcError, clone, open_in_vscode

    client = FosharClient(host, config.port, config.security)
    try:
        await client.connect()
    except (ConnectionError, OSError, asyncio.TimeoutError) as exc:
        print(f"Não foi possível conectar a {host}:{config.port}: {exc}", file=sys.stderr)
        return 2
    try:
        available = {s.get("id") for s in client.shares}
        if share not in available:
            disponiveis = ", ".join(sorted(str(s) for s in available)) or "(nenhum)"
            print(f"Share '{share}' não publicado pelo host. Disponíveis: {disponiveis}", file=sys.stderr)
            return 1
        root = await clone(client, share, config.cache_dir)
    except RpcError as exc:
        print(f"Erro: {exc}", file=sys.stderr)
        return 1
    finally:
        await client.close()

    print(f"Sincronizado em {root}")
    if vscode:
        open_in_vscode(root)
    return 0


def _run_sync(config: FosharConfig, host: str, share: str, *, vscode: bool, interval: float) -> int:
    try:
        return asyncio.run(_sync_loop(config, host, share, vscode=vscode, interval=interval))
    except KeyboardInterrupt:
        print("\nSincronização interrompida.")
        return 0


async def _sync_loop(config: FosharConfig, host: str, share: str, *, vscode: bool, interval: float) -> int:
    from foshar.foshar_cache.index import SyncIndex
    from foshar.foshar_cache.mirror import Mirror
    from foshar.foshar_server import FosharClient, open_in_vscode
    from foshar.foshar_sync import SyncEngine

    cache = Path(config.cache_dir).expanduser()
    mirror = Mirror(cache / share)
    index = SyncIndex(cache / ".foshar" / f"{share}.index.db")
    opened = False
    try:
        while True:
            client = FosharClient(host, config.port, config.security)
            try:
                await client.connect()
            except (ConnectionError, OSError, asyncio.TimeoutError) as exc:
                print(f"Sem conexão com {host}:{config.port} ({exc}); tentando de novo…", file=sys.stderr)
                await asyncio.sleep(3.0)
                continue
            if share not in {s.get("id") for s in client.shares}:
                print(f"Share '{share}' não publicado pelo host.", file=sys.stderr)
                await client.close()
                return 1
            engine = SyncEngine(client, share, mirror, index, interval=interval)
            try:
                await engine.sync_once()
                if vscode and not opened:
                    open_in_vscode(mirror.root)
                    opened = True
                print(f"Sincronizando {mirror.root} ↔ {host}:{share} (Ctrl+C para parar)")
                await engine.run()
            except (ConnectionError, OSError, asyncio.TimeoutError) as exc:
                print(f"Conexão perdida ({exc}); reconectando…", file=sys.stderr)
            finally:
                await client.close()
            await asyncio.sleep(3.0)
    finally:
        index.close()


if __name__ == "__main__":
    raise SystemExit(main())
