#!/usr/bin/env python3
"""Controla a sessão do cliente ZephyrLink: iniciar, parar e status.

Os logs são exibidos em tempo real no console e não são salvos em disco.

Uso:
    python client_monitor.py iniciar [args extras p/ zephyrlink client]
    python client_monitor.py parar
    python client_monitor.py status
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
PID_FILE = BASE / "client.pid"
IS_WIN = sys.platform == "win32"


def _read_pid() -> int | None:
    try:
        return int(PID_FILE.read_text().strip())
    except (FileNotFoundError, ValueError):
        return None


def _alive(pid: int) -> bool:
    if IS_WIN:
        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True, text=True,
        ).stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def iniciar(extra: list[str]) -> int:
    pid = _read_pid()
    if pid is not None and _alive(pid):
        print(f"Cliente já está rodando (PID {pid}).")
        return 1

    cmd = [sys.executable, "-m", "zephyrlink", "client", *extra]
    proc = subprocess.Popen(cmd, cwd=BASE)
    PID_FILE.write_text(str(proc.pid))
    print(f"Cliente iniciado (PID {proc.pid}). Ctrl+C para parar.")
    try:
        return proc.wait()
    except KeyboardInterrupt:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        return 0
    finally:
        PID_FILE.unlink(missing_ok=True)


def parar() -> int:
    pid = _read_pid()
    if pid is None or not _alive(pid):
        print("Cliente não está rodando.")
        PID_FILE.unlink(missing_ok=True)
        return 1
    if IS_WIN:
        try:
            os.kill(pid, signal.CTRL_BREAK_EVENT)
            time.sleep(1.0)
        except OSError:
            pass
        if _alive(pid):
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                           capture_output=True)
    else:
        os.kill(pid, signal.SIGTERM)
    PID_FILE.unlink(missing_ok=True)
    print(f"Cliente parado (PID {pid}).")
    return 0


def status() -> int:
    pid = _read_pid()
    if pid is not None and _alive(pid):
        print(f"Rodando (PID {pid}).")
        return 0
    print("Parado.")
    return 1


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    action, extra = argv[0], argv[1:]
    match action:
        case "iniciar" | "start":
            return iniciar(extra)
        case "parar" | "stop":
            return parar()
        case "status":
            return status()
        case _:
            print(__doc__)
            return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
