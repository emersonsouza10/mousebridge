#!/usr/bin/env python3
"""Controla a sessão do cliente ZephyrLink: iniciar, logs e parar.

Uso:
    python client_monitor.py iniciar [args extras p/ zephyrlink client]
    python client_monitor.py logs
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
LOG_FILE = BASE / "client.log"
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
    log = open(LOG_FILE, "a", buffering=1, encoding="utf-8")
    kwargs: dict = {"stdout": log, "stderr": subprocess.STDOUT, "cwd": BASE}
    if IS_WIN:
        kwargs["creationflags"] = (
            subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS  # type: ignore[attr-defined]
        )
    else:
        kwargs["start_new_session"] = True

    proc = subprocess.Popen(cmd, **kwargs)
    PID_FILE.write_text(str(proc.pid))
    print(f"Cliente iniciado (PID {proc.pid}). Logs em {LOG_FILE.name}.")
    return 0


def logs() -> int:
    if not LOG_FILE.exists():
        print("Sem log ainda. Rode 'iniciar' primeiro.")
        return 1
    with open(LOG_FILE, encoding="utf-8", errors="replace") as fh:
        tail = fh.readlines()[-50:]
        sys.stdout.write("".join(tail))
        sys.stdout.flush()
        print("--- acompanhando (Ctrl+C para sair) ---")
        try:
            while True:
                line = fh.readline()
                if line:
                    sys.stdout.write(line)
                    sys.stdout.flush()
                else:
                    time.sleep(0.4)
        except KeyboardInterrupt:
            return 0


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
        case "logs" | "log":
            return logs()
        case "parar" | "stop":
            return parar()
        case "status":
            return status()
        case _:
            print(__doc__)
            return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
