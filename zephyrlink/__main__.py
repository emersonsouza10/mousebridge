"""Ponto de entrada: ``python -m zephyrlink {server|client|gui}``."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from dataclasses import replace

from zephyrlink import __version__
from zephyrlink.config import ConfigError, load_config
from zephyrlink.config.settings import VALID_EDGES
from zephyrlink.logging_setup import setup_logging

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="zephyrlink", description="Compartilhamento de mouse e teclado em rede local")
    parser.add_argument("--version", action="version", version=f"zephyrlink {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("-c", "--config", default=None, help="caminho do config.yaml")
    common.add_argument("--key", default=None, help="chave compartilhada (sobrepõe o YAML)")
    common.add_argument("--port", type=int, default=None, help="porta TCP (sobrepõe o YAML)")
    common.add_argument("-v", "--verbose", action="store_true", help="log em nível DEBUG")

    server = sub.add_parser("server", parents=[common], help="máquina principal (tem mouse/teclado)")
    server.add_argument("--edge", choices=VALID_EDGES, default=None,
                        help="borda onde está a tela secundária")

    client = sub.add_parser("client", parents=[common], help="máquina secundária (controlada)")
    client.add_argument("--host", default=None, help="IP do servidor (pula a descoberta UDP)")

    sub.add_parser("gui", parents=[common], help="interface gráfica")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    # Antes de qualquer consulta de tela ou criação de janela.
    from zephyrlink.mouse import enable_dpi_awareness

    enable_dpi_awareness()
    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"Erro de configuração: {exc}", file=sys.stderr)
        return 2

    if args.key:
        config = replace(config, security=replace(config.security, shared_key=args.key))
    if args.port:
        config = replace(config, network=replace(config.network, tcp_port=args.port))
    if args.verbose:
        config = replace(config, log_level="DEBUG")
    if args.command == "server" and getattr(args, "edge", None):
        config = replace(config, layout=replace(config.layout, edge=args.edge))
    if args.command == "client" and getattr(args, "host", None):
        config = replace(config, network=replace(config.network, manual_host=args.host))
    config = replace(config, role=args.command if args.command in ("server", "client") else config.role)

    if args.command == "gui":
        from zephyrlink.gui import run_gui

        run_gui(config)
        return 0

    setup_logging(config.log_level, config.log_json)
    if config.security.shared_key == "change-me":
        logger.warning("Usando shared_key padrão; defina uma chave própria em produção")

    if args.command == "server":
        from zephyrlink.server import ZephyrLinkServer

        core: object = ZephyrLinkServer(config)
    else:
        from zephyrlink.client import ZephyrLinkClient

        core = ZephyrLinkClient(config)

    try:
        asyncio.run(core.run())  # type: ignore[attr-defined]
    except KeyboardInterrupt:
        logger.info("Interrompido pelo usuário")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
