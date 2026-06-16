"""Interface gráfica do ZephyrLink (Tkinter).

Mostra status da conexão, IPs, máquina ativa, posição relativa das telas
e logs em tempo real. O núcleo (servidor/cliente) roda num loop asyncio em
thread separada; a GUI consome status e logs por filas thread-safe,
drenadas periodicamente via ``after`` — Tkinter só é tocado pela thread
principal.
"""

from __future__ import annotations

import asyncio
import logging
import queue
import threading
import tkinter as tk
from dataclasses import replace
from tkinter import scrolledtext, ttk
from typing import Any

from zephyrlink.client import ZephyrLinkClient
from zephyrlink.config import AppConfig
from zephyrlink.discovery.beacon import get_local_ip
from zephyrlink.logging_setup import setup_logging

logger = logging.getLogger(__name__)

POLL_MS = 100


class _QueueLogHandler(logging.Handler):
    def __init__(self, log_queue: "queue.Queue[str]") -> None:
        super().__init__()
        self.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%H:%M:%S"))
        self._queue = log_queue

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._queue.put_nowait(self.format(record))
        except queue.Full:
            pass


class _CoreThread(threading.Thread):
    """Roda o servidor ou cliente num event loop asyncio dedicado."""

    def __init__(self, config: AppConfig, status_queue: "queue.Queue[dict[str, Any]]") -> None:
        super().__init__(daemon=True, name="zephyrlink-core")
        self._config = config
        self._status_queue = status_queue
        self._core: Any = None

    def run(self) -> None:
        from zephyrlink.server import ZephyrLinkServer

        on_status = self._status_queue.put_nowait
        if self._config.role == "server":
            self._core = ZephyrLinkServer(self._config, on_status=on_status)
        else:
            self._core = ZephyrLinkClient(self._config, on_status=on_status)
        try:
            asyncio.run(self._core.run())
        except Exception:
            logger.exception("Núcleo encerrou com erro")

    def stop(self) -> None:
        if self._core is not None:
            self._core.stop()


class ZephyrLinkGUI:
    def __init__(self, config: AppConfig) -> None:
        self._config = config
        self._core_thread: _CoreThread | None = None
        self._status_queue: queue.Queue[dict[str, Any]] = queue.Queue()
        self._log_queue: queue.Queue[str] = queue.Queue(maxsize=1000)

        logging.getLogger().addHandler(_QueueLogHandler(self._log_queue))

        self._root = tk.Tk()
        self._root.title("ZephyrLink")
        self._root.minsize(560, 480)
        self._root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._build_widgets()
        self._root.after(POLL_MS, self._poll_queues)

    def _build_widgets(self) -> None:
        main = ttk.Frame(self._root, padding=10)
        main.pack(fill=tk.BOTH, expand=True)

        controls = ttk.LabelFrame(main, text="Controle", padding=8)
        controls.pack(fill=tk.X)
        self._role_var = tk.StringVar(value=self._config.role)
        ttk.Radiobutton(controls, text="Servidor (tem o mouse)", variable=self._role_var,
                        value="server").grid(row=0, column=0, sticky="w")
        ttk.Radiobutton(controls, text="Cliente (controlado)", variable=self._role_var,
                        value="client").grid(row=0, column=1, sticky="w", padx=8)
        ttk.Label(controls, text="Borda:").grid(row=1, column=0, sticky="w", pady=(6, 0))
        self._edge_var = tk.StringVar(value=self._config.layout.edge)
        ttk.Combobox(controls, textvariable=self._edge_var, state="readonly", width=8,
                     values=("left", "right", "top", "bottom")).grid(row=1, column=1, sticky="w",
                                                                     padx=8, pady=(6, 0))
        ttk.Label(controls, text="IP manual (cliente):").grid(row=2, column=0, sticky="w", pady=(6, 0))
        self._host_var = tk.StringVar(value=self._config.network.manual_host or "")
        ttk.Entry(controls, textvariable=self._host_var, width=18).grid(row=2, column=1, sticky="w",
                                                                        padx=8, pady=(6, 0))
        ttk.Label(controls, text="Chave compartilhada:").grid(row=3, column=0, sticky="w", pady=(6, 0))
        self._key_var = tk.StringVar(value=self._config.security.shared_key)
        ttk.Entry(controls, textvariable=self._key_var, width=18, show="•").grid(
            row=3, column=1, sticky="w", padx=8, pady=(6, 0))
        self._start_btn = ttk.Button(controls, text="Iniciar", command=self._on_start)
        self._start_btn.grid(row=0, column=2, rowspan=2, padx=12)
        self._stop_btn = ttk.Button(controls, text="Parar", command=self._on_stop, state=tk.DISABLED)
        self._stop_btn.grid(row=2, column=2, padx=12)

        status = ttk.LabelFrame(main, text="Status", padding=8)
        status.pack(fill=tk.X, pady=(8, 0))
        self._status_vars: dict[str, tk.StringVar] = {}
        rows = (
            ("Conexão", "connection"),
            ("IP local", "local_ip"),
            ("IP remoto", "remote_ip"),
            ("Computador ativo", "active"),
        )
        for i, (label, key) in enumerate(rows):
            ttk.Label(status, text=f"{label}:").grid(row=i, column=0, sticky="w")
            var = tk.StringVar(value="-")
            self._status_vars[key] = var
            ttk.Label(status, textvariable=var).grid(row=i, column=1, sticky="w", padx=8)
        self._status_vars["local_ip"].set(get_local_ip())

        layout_frame = ttk.LabelFrame(main, text="Posição das telas", padding=8)
        layout_frame.pack(fill=tk.X, pady=(8, 0))
        self._canvas = tk.Canvas(layout_frame, height=110, bg="#1e1e1e", highlightthickness=0)
        self._canvas.pack(fill=tk.X)
        self._draw_layout()
        self._edge_var.trace_add("write", lambda *_: self._draw_layout())

        logs = ttk.LabelFrame(main, text="Logs", padding=8)
        logs.pack(fill=tk.BOTH, expand=True, pady=(8, 0))
        self._log_text = scrolledtext.ScrolledText(logs, height=10, state=tk.DISABLED,
                                                   font=("Consolas", 9))
        self._log_text.pack(fill=tk.BOTH, expand=True)

    def _draw_layout(self, active: str | None = None) -> None:
        canvas = self._canvas
        canvas.delete("all")
        width = max(canvas.winfo_width(), 420)
        edge = self._edge_var.get()
        box_w, box_h = 120, 70
        cx, cy = width // 2, 55
        gap = 8
        offsets = {
            "right": (box_w + gap, 0),
            "left": (-(box_w + gap), 0),
            "top": (0, -(box_h // 2 + gap)),
            "bottom": (0, box_h // 2 + gap),
        }
        dx, dy = offsets.get(edge, (box_w + gap, 0))
        if edge in ("top", "bottom"):
            cy = 55
            box_h = 42
        primary_fill = "#3a86ff" if active in (None, "local", "servidor") else "#2a4a6e"
        secondary_fill = "#3a86ff" if active in ("remoto", "esta máquina") else "#2a4a6e"
        canvas.create_rectangle(cx - box_w // 2, cy - box_h // 2, cx + box_w // 2, cy + box_h // 2,
                                fill=primary_fill, outline="#cccccc")
        canvas.create_text(cx, cy, text="Principal", fill="white")
        canvas.create_rectangle(cx - box_w // 2 + dx, cy - box_h // 2 + dy,
                                cx + box_w // 2 + dx, cy + box_h // 2 + dy,
                                fill=secondary_fill, outline="#cccccc")
        canvas.create_text(cx + dx, cy + dy, text="Secundário", fill="white")

    def _on_start(self) -> None:
        manual = self._host_var.get().strip() or None
        config = replace(
            self._config,
            role=self._role_var.get(),
            layout=replace(self._config.layout, edge=self._edge_var.get()),
            network=replace(self._config.network, manual_host=manual),
            security=replace(self._config.security, shared_key=self._key_var.get()),
        )
        self._core_thread = _CoreThread(config, self._status_queue)
        self._core_thread.start()
        self._start_btn.configure(state=tk.DISABLED)
        self._stop_btn.configure(state=tk.NORMAL)
        logger.info("Iniciado em modo %s", config.role)

    def _on_stop(self) -> None:
        if self._core_thread is not None:
            self._core_thread.stop()
            self._core_thread = None
        self._start_btn.configure(state=tk.NORMAL)
        self._stop_btn.configure(state=tk.DISABLED)
        self._status_vars["connection"].set("parado")

    def _on_close(self) -> None:
        self._on_stop()
        self._root.destroy()

    def _poll_queues(self) -> None:
        try:
            while True:
                status = self._status_queue.get_nowait()
                self._apply_status(status)
        except queue.Empty:
            pass
        lines: list[str] = []
        try:
            while True:
                lines.append(self._log_queue.get_nowait())
        except queue.Empty:
            pass
        if lines:
            self._log_text.configure(state=tk.NORMAL)
            self._log_text.insert(tk.END, "\n".join(lines) + "\n")
            self._log_text.see(tk.END)
            self._log_text.configure(state=tk.DISABLED)
        self._root.after(POLL_MS, self._poll_queues)

    def _apply_status(self, status: dict[str, Any]) -> None:
        self._status_vars["connection"].set("conectado" if status.get("connected") else "desconectado")
        self._status_vars["local_ip"].set(status.get("local_ip") or "-")
        self._status_vars["remote_ip"].set(status.get("remote_ip") or "-")
        self._status_vars["active"].set(status.get("active") or "-")
        self._draw_layout(active=status.get("active"))

    def run(self) -> None:
        self._root.mainloop()


def run_gui(config: AppConfig) -> None:
    setup_logging(config.log_level, config.log_json)
    ZephyrLinkGUI(config).run()
