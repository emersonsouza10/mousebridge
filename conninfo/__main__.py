"""CLI da connInfo: ``python -m conninfo {host|status|connections|query}``.

``host``         lado DONO do banco (cliente): publica conexões e escuta.
``status``       lista as conexões configuradas nesta máquina.
``connections``  (lado agente) lista o que um host publica.
``query``        (lado agente) executa uma SQL e imprime o resultado.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from dataclasses import replace

from conninfo.config import DEFAULT_PORT, ConnInfoConfig, ConnInfoConfigError, load_conninfo_config


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="conninfo", description="Execução remota de banco sobre o canal do MouseBridge")
    sub = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("-c", "--config", default=None, help="caminho do config.yaml")
    common.add_argument("--key", default=None, help="chave compartilhada (sobrepõe o YAML)")

    sub.add_parser("host", parents=[common], help="lado dono do banco (publica conexões e escuta)")
    sub.add_parser("status", parents=[common], help="lista o catálogo efetivo (manual + descoberto)")
    sub.add_parser("discover", parents=[common], help="só as conexões descobertas no ambiente (tnsnames…)")

    agent = argparse.ArgumentParser(add_help=False)
    agent.add_argument("--host", required=True, help="IP da máquina cliente (ProviderHost)")
    agent.add_argument("--port", type=int, default=DEFAULT_PORT)

    sub.add_parser("connections", parents=[common, agent], help="lista as conexões que o host publica")

    q = sub.add_parser("query", parents=[common, agent], help="executa uma SQL no host e imprime o resultado")
    q.add_argument("--conn", required=True, help="id da conexão a usar")
    q.add_argument("--sql", required=True, help="SQL a executar")
    q.add_argument("--max-rows", type=int, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    try:
        config = load_conninfo_config(args.config)
    except ConnInfoConfigError as exc:
        print(f"Erro de configuração: {exc}", file=sys.stderr)
        return 2
    if args.key:
        config = replace(config, security=replace(config.security, shared_key=args.key))

    if args.command == "host":
        return _run_host(config)
    if args.command == "status":
        return _run_status(config)
    if args.command == "discover":
        return _run_discover(config)
    if args.command == "connections":
        return _run_connections(config, args.host, args.port)
    if args.command == "query":
        return _run_query(config, args.host, args.port, args.conn, args.sql, args.max_rows)
    return 2


def _run_host(config: ConnInfoConfig) -> int:
    from conninfo.ci_host import ProviderHost

    if not config.connections:
        print("Nenhuma conexão configurada (conninfo.connections no config.yaml).", file=sys.stderr)
        return 2
    try:
        asyncio.run(ProviderHost(config).run())
    except KeyboardInterrupt:
        pass
    return 0


def _run_status(config: ConnInfoConfig) -> int:
    from conninfo.ci_discovery import merged_connections

    conns = merged_connections(config)
    if not conns:
        print("Nenhuma conexão (cadastro manual nem descoberta).")
        return 0
    for c in conns:
        ro = "ro" if c.read_only else "rw"
        print(f"{c.id:<22} {c.engine:<10} {ro:<4} {c.source:<10} {c.name}")
    return 0


def _run_discover(config: ConnInfoConfig) -> int:
    from conninfo.ci_discovery import discover

    found = discover(config)
    if not found:
        print("Nada descoberto (verifique conninfo.discovery e os caminhos do tnsnames).")
        return 0
    for c in found:
        print(f"{c.id:<22} {c.engine:<10} {c.source:<10} {c.name}")
    return 0


def _run_connections(config: ConnInfoConfig, host: str, port: int) -> int:
    from conninfo.ci_gateway.api import list_connections

    try:
        conns = list_connections(host=host, port=port, security=config.security)
    except Exception as exc:  # noqa: BLE001
        print(f"Falha ao conectar em {host}:{port}: {exc}", file=sys.stderr)
        return 1
    for c in conns:
        print(f"{c.get('id'):<20} {c.get('engine'):<10} ro={c.get('read_only')}  {c.get('name')}")
    return 0


def _run_query(config: ConnInfoConfig, host: str, port: int, conn_id: str, sql: str, max_rows: int | None) -> int:
    from conninfo.ci_gateway.api import connect

    try:
        conn = connect(conn_id, host=host, port=port, security=config.security)
    except Exception as exc:  # noqa: BLE001
        print(f"Falha ao abrir sessão: {exc}", file=sys.stderr)
        return 1
    try:
        cur = conn.execute(sql, max_rows=max_rows)
        print(" | ".join(cur.columns))
        n = 0
        for row in cur:
            print(" | ".join("" if v is None else str(v) for v in row))
            n += 1
        print(f"({n} linha(s))")
    except Exception as exc:  # noqa: BLE001
        print(f"Erro: {exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
