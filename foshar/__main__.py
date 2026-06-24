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
        return asyncio.run(_open(config, args.host, args.share, not args.no_vscode))

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


if __name__ == "__main__":
    raise SystemExit(main())
