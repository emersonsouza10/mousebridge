"""Ponto de entrada: ``python -m zephyrlink {server|client|gui}``."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from dataclasses import replace
from pathlib import Path

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

    sub.add_parser("server", parents=[common], help="máquina principal (tem mouse/teclado)")

    client = sub.add_parser("client", parents=[common], help="máquina secundária (controlada)")
    client.add_argument("--host", default=None, help="IP do servidor (pula a descoberta UDP)")
    client.add_argument("--edge", choices=VALID_EDGES, default=None,
                        help="borda do servidor onde esta máquina fica "
                             "(right=à direita, left=à esquerda, top=acima, bottom=abaixo)")

    sub.add_parser("gui", parents=[common], help="interface gráfica")

    launch = sub.add_parser("launch", parents=[common],
                            help="dispara a abertura de um app num cliente (fala com o servidor local)")
    launch.add_argument("--client", required=True, help="IP ou borda do cliente alvo")
    launch.add_argument("--app", required=True, help="id ou label do app no catálogo do cliente")
    launch.add_argument("--arg", action="append", default=[], dest="arg",
                        help="parâmetro do app (repita para vários)")

    apps = sub.add_parser("apps", parents=[common],
                          help="lista os apps publicados pelos clientes (fala com o servidor local)")
    apps.add_argument("--client", default=None, help="filtra por IP ou borda (opcional)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    # Antes de qualquer consulta de tela ou criação de janela.
    from zephyrlink.mouse import enable_dpi_awareness

    enable_dpi_awareness()
    # macOS: pré-computa o layout de teclado na main thread para o pynput não
    # abortar (SIGTRAP) ao consultá-lo na thread do núcleo (ver macos_compat).
    from zephyrlink.keyboard.macos_compat import install_macos_pynput_layout_fix

    install_macos_pynput_layout_fix()
    config_path = args.config
    if config_path is None and Path("config.yaml").exists():
        config_path = "config.yaml"
    try:
        config = load_config(config_path)
    except ConfigError as exc:
        print(f"Erro de configuração: {exc}", file=sys.stderr)
        return 2

    if args.key:
        config = replace(config, security=replace(config.security, shared_key=args.key))
    if args.port:
        config = replace(config, network=replace(config.network, tcp_port=args.port))
    if args.verbose:
        config = replace(config, log_level="DEBUG")
    if args.command == "client" and getattr(args, "edge", None):
        config = replace(config, layout=replace(config.layout, edge=args.edge))
    if args.command == "client" and getattr(args, "host", None):
        config = replace(config, network=replace(config.network, manual_host=args.host))
    config = replace(config, role=args.command if args.command in ("server", "client") else config.role)

    if args.command == "gui":
        from zephyrlink.gui import run_gui

        run_gui(config, config_path or "config.yaml")
        return 0

    if args.command == "launch":
        from zephyrlink.control import run_launch_cli

        setup_logging(config.log_level, config.log_json)
        return run_launch_cli(config, args.client, args.app, args.arg)

    if args.command == "apps":
        from zephyrlink.control import run_apps_cli

        setup_logging(config.log_level, config.log_json)
        return run_apps_cli(config, args.client)

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
