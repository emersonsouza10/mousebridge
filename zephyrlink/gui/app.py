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
import time
import tkinter as tk
from dataclasses import replace
from tkinter import scrolledtext, ttk
from typing import Any

from zephyrlink.client import ZephyrLinkClient
from zephyrlink.config import AppConfig
from zephyrlink.discovery.beacon import get_local_ip
from zephyrlink.mouse import get_monitors
from zephyrlink.logging_setup import setup_logging

logger = logging.getLogger(__name__)

POLL_MS = 100

_STATE_LABELS = {
    "sent": "Enviado",
    "received": "Recebido",
    "launching": "Executando",
    "completed": "Concluído",
    "failed": "Falhou",
    "rejected": "Rejeitado",
    "timeout": "Falhou (timeout)",
}


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

    @property
    def core(self) -> Any:
        return self._core


class ZephyrLinkGUI:
    def __init__(self, config: AppConfig, config_path: str = "config.yaml") -> None:
        self._config = config
        self._config_path = config_path
        self._core_thread: _CoreThread | None = None
        self._monitors = get_monitors()
        self._clients_status: list[dict[str, Any]] = []
        self._client_boxes: list[tuple[int, float, float, float, float]] = []
        self._edge_zones: dict[str, tuple[float, float, float, float]] = {}
        self._principal_rect: tuple[float, float, float, float] = (0, 0, 0, 0)
        self._drag: dict[str, Any] | None = None
        self._active: str | None = None
        self._launches: list[dict[str, Any]] = []
        self._client_cid: dict[str, int] = {}
        self._app_label_id: dict[str, str] = {}
        self._app_accepts: dict[str, bool] = {}
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
        ttk.Label(controls, text="IP manual (cliente):").grid(row=1, column=0, sticky="w", pady=(6, 0))
        # Ao abrir, sempre mostra o IP atual desta máquina (o endereço do servidor
        # nesta rede), evitando exibir um IP salvo desatualizado.
        self._host_var = tk.StringVar(value=get_local_ip())
        ttk.Entry(controls, textvariable=self._host_var, width=18).grid(
            row=1, column=1, sticky="w", padx=8, pady=(6, 0))
        ttk.Label(controls, text="Chave compartilhada:").grid(row=2, column=0, sticky="w", pady=(6, 0))
        self._key_var = tk.StringVar(value=self._config.security.shared_key)
        key_box = ttk.Frame(controls)
        key_box.grid(row=2, column=1, sticky="w", padx=8, pady=(6, 0))
        self._key_entry = ttk.Entry(key_box, textvariable=self._key_var, width=18, show="•")
        self._key_entry.pack(side=tk.LEFT)
        self._key_shown = False
        self._key_toggle_btn = ttk.Button(key_box, text="Mostrar", width=8,
                                           command=self._toggle_key_visibility)
        self._key_toggle_btn.pack(side=tk.LEFT, padx=(6, 0))
        self._start_btn = ttk.Button(controls, text="Iniciar", command=self._on_start)
        self._start_btn.grid(row=0, column=2, rowspan=2, padx=12)
        self._stop_btn = ttk.Button(controls, text="Parar", command=self._on_stop, state=tk.DISABLED)
        self._stop_btn.grid(row=2, column=2, padx=12)
        ttk.Button(controls, text="Apps permitidos…", command=self._open_app_editor).grid(
            row=0, column=3, rowspan=3, padx=(16, 0))
        ttk.Button(controls, text="Pastas compartilhadas…", command=self._open_shares_editor).grid(
            row=0, column=4, rowspan=3, padx=(8, 0))
        ttk.Button(controls, text="Pastas dos clientes…", command=self._open_remote_shares).grid(
            row=0, column=5, rowspan=3, padx=(8, 0))

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

        layout_frame = ttk.LabelFrame(main, text="Posição das telas (arraste os clientes)", padding=8)
        layout_frame.pack(fill=tk.X, pady=(8, 0))
        self._canvas = tk.Canvas(layout_frame, height=210, bg="#1e1e1e", highlightthickness=0)
        self._canvas.pack(fill=tk.X)
        self._canvas.bind("<Button-1>", self._on_canvas_press)
        self._canvas.bind("<B1-Motion>", self._on_canvas_drag)
        self._canvas.bind("<ButtonRelease-1>", self._on_canvas_release)
        self._canvas.bind("<Configure>", lambda _e: self._draw_layout())
        self._draw_layout()

        launcher = ttk.LabelFrame(main, text="Aplicações remotas", padding=8)
        launcher.pack(fill=tk.X, pady=(8, 0))
        ttk.Label(launcher, text="Cliente:").grid(row=0, column=0, sticky="w")
        self._app_client_var = tk.StringVar()
        self._app_client_combo = ttk.Combobox(launcher, textvariable=self._app_client_var,
                                               width=20, state="readonly")
        self._app_client_combo.grid(row=0, column=1, sticky="w", padx=6)
        self._app_client_combo.bind("<<ComboboxSelected>>", lambda _e: self._refresh_app_list())
        ttk.Label(launcher, text="App:").grid(row=0, column=2, sticky="w")
        self._app_var = tk.StringVar()
        self._app_combo = ttk.Combobox(launcher, textvariable=self._app_var, width=20, state="readonly")
        self._app_combo.grid(row=0, column=3, sticky="w", padx=6)
        self._app_combo.bind("<<ComboboxSelected>>", lambda _e: self._update_args_state())
        ttk.Label(launcher, text="Parâmetro:").grid(row=1, column=0, sticky="w", pady=(6, 0))
        self._app_args_var = tk.StringVar()
        self._app_args_entry = ttk.Entry(launcher, textvariable=self._app_args_var,
                                         width=36, state=tk.DISABLED)
        self._app_args_entry.grid(row=1, column=1, columnspan=2, sticky="we", padx=6, pady=(6, 0))
        ttk.Button(launcher, text="Abrir no cliente", command=self._on_launch).grid(
            row=1, column=3, padx=6, pady=(6, 0))
        cols = ("hora", "cliente", "app", "estado")
        self._history = ttk.Treeview(launcher, columns=cols, show="headings", height=5)
        for col, width in zip(cols, (70, 130, 150, 130)):
            self._history.heading(col, text=col.capitalize())
            self._history.column(col, width=width, anchor="w")
        self._history.grid(row=2, column=0, columnspan=4, sticky="we", pady=(8, 0))
        launcher.columnconfigure(1, weight=1)

        logs = ttk.LabelFrame(main, text="Logs", padding=8)
        logs.pack(fill=tk.BOTH, expand=True, pady=(8, 0))
        self._log_text = scrolledtext.ScrolledText(logs, height=10, state=tk.DISABLED,
                                                   font=("Consolas", 9))
        self._log_text.pack(fill=tk.BOTH, expand=True)

    def _draw_layout(self, active: str | None = None) -> None:
        active = active if active is not None else self._active
        canvas = self._canvas
        canvas.delete("all")
        self._client_boxes = []
        self._edge_zones = {}
        width = max(canvas.winfo_width(), 420)
        height = max(canvas.winfo_height(), 160)
        is_server = self._role_var.get() == "server"

        # Caixa que envolve todos os monitores físicos desta máquina.
        monitors = self._monitors
        minx = min(m.x for m in monitors)
        miny = min(m.y for m in monitors)
        maxx = max(m.right for m in monitors)
        maxy = max(m.bottom for m in monitors)
        bw, bh = maxx - minx + 1, maxy - miny + 1
        prim = next((m for m in monitors if m.x == 0 and m.y == 0), monitors[0])

        # Uma "zona" por borda do servidor, em coordenadas de mundo.
        gx, gy = max(bw // 12, 1), max(bh // 12, 1)
        zones = {
            "right": (maxx + gx, miny, prim.width, bh),
            "left": (minx - gx - prim.width, miny, prim.width, bh),
            "top": (minx, miny - gy - prim.height, bw, prim.height),
            "bottom": (minx, maxy + gy, bw, prim.height),
        }

        # Mundo = monitores + zonas (mantém a escala estável ao arrastar).
        xs, ys = [minx, maxx], [miny, maxy]
        if is_server:
            for ex, ey, ew, eh in zones.values():
                xs += [ex, ex + ew - 1]
                ys += [ey, ey + eh - 1]
        wx0, wy0, wx1, wy1 = min(xs), min(ys), max(xs), max(ys)
        ww, wh = wx1 - wx0 + 1, wy1 - wy0 + 1
        pad = 12
        tray_h = 38 if is_server else 0
        avail_h = height - tray_h
        scale = min((width - 2 * pad) / ww, (avail_h - 2 * pad) / wh)
        ox = (width - ww * scale) / 2 - wx0 * scale
        oy = (avail_h - wh * scale) / 2 - wy0 * scale

        def to_canvas(x: float, y: float) -> tuple[float, float]:
            return (ox + x * scale, oy + y * scale)

        def box(x: int, y: int, w: int, h: int, fill: str, text: str,
                cid: int | None = None) -> None:
            x0, y0 = to_canvas(x, y)
            x1, y1 = to_canvas(x + w, y + h)
            canvas.create_rectangle(x0, y0, x1, y1, fill=fill, outline="#cccccc")
            canvas.create_text((x0 + x1) / 2, (y0 + y1) / 2, text=text, fill="white",
                               justify="center", font=("Segoe UI", 8))
            if cid is not None:
                self._client_boxes.append((cid, x0, y0, x1, y1))

        self._principal_rect = (*to_canvas(minx, miny), *to_canvas(maxx + 1, maxy + 1))
        active_edge = active.split("'")[1] if active and "'" in active else None
        primary_fill = "#3a86ff" if active in (None, "local") else "#2a4a6e"
        for i, m in enumerate(monitors):
            tag = "principal" if m is prim else f"monitor {i + 1}"
            box(m.x, m.y, m.width, m.height, primary_fill, f"{tag}\n{m.width}×{m.height}")

        if not is_server:
            return

        assigned = {c["edge"]: c for c in self._clients_status if c.get("edge")}
        for edge, (ex, ey, ew, eh) in zones.items():
            self._edge_zones[edge] = (*to_canvas(ex, ey), *to_canvas(ex + ew, ey + eh))
            client = assigned.get(edge)
            if client is not None:
                fill = "#3a86ff" if edge == active_edge else "#2a4a6e"
                box(ex, ey, ew, eh, fill, f"{client['host']}\n({edge})", cid=client["cid"])
            else:
                zx0, zy0, zx1, zy1 = self._edge_zones[edge]
                canvas.create_rectangle(zx0, zy0, zx1, zy1, outline="#555555", dash=(3, 3))
                canvas.create_text((zx0 + zx1) / 2, (zy0 + zy1) / 2, text=edge,
                                   fill="#777777", font=("Segoe UI", 8))

        # Bandeja: clientes conectados ainda sem borda (arraste para uma zona).
        unassigned = [c for c in self._clients_status if not c.get("edge")]
        ty0, ty1 = height - tray_h + 6, height - 6
        canvas.create_line(0, height - tray_h, width, height - tray_h, fill="#333333")
        if unassigned:
            tx = pad
            for c in unassigned:
                tx1 = tx + 130
                canvas.create_rectangle(tx, ty0, tx1, ty1, fill="#6a4a2a", outline="#cccccc")
                canvas.create_text((tx + tx1) / 2, (ty0 + ty1) / 2, text=f"{c['host']}\n(sem borda)",
                                   fill="white", justify="center", font=("Segoe UI", 8))
                self._client_boxes.append((c["cid"], tx, ty0, tx1, ty1))
                tx = tx1 + 8

    def _on_canvas_press(self, event: "tk.Event[Any]") -> None:
        self._drag = None
        if self._role_var.get() != "server":
            return
        for cid, x0, y0, x1, y1 in self._client_boxes:
            if x0 <= event.x <= x1 and y0 <= event.y <= y1:
                self._drag = {"cid": cid, "w": x1 - x0, "h": y1 - y0}
                return

    def _on_canvas_drag(self, event: "tk.Event[Any]") -> None:
        if self._drag is None:
            return
        self._canvas.delete("ghost")
        w, h = self._drag["w"], self._drag["h"]
        self._canvas.create_rectangle(event.x - w / 2, event.y - h / 2,
                                      event.x + w / 2, event.y + h / 2,
                                      outline="#ffd166", dash=(4, 2), width=2, tags="ghost")

    def _on_canvas_release(self, event: "tk.Event[Any]") -> None:
        if self._drag is None:
            return
        self._canvas.delete("ghost")
        cid = self._drag["cid"]
        self._drag = None
        edge = self._nearest_target(event.x, event.y)
        core = self._core_thread.core if self._core_thread is not None else None
        if core is not None and hasattr(core, "assign_edge"):
            core.assign_edge(cid, edge)

    def _nearest_target(self, x: float, y: float) -> str | None:
        """Borda (ou None = desencaixar) cujo centro está mais perto do ponto."""
        def center(r: tuple[float, float, float, float]) -> tuple[float, float]:
            return ((r[0] + r[2]) / 2, (r[1] + r[3]) / 2)

        targets: list[tuple[str | None, tuple[float, float]]] = [
            (edge, center(rect)) for edge, rect in self._edge_zones.items()
        ]
        targets.append((None, center(self._principal_rect)))
        return min(targets, key=lambda t: (x - t[1][0]) ** 2 + (y - t[1][1]) ** 2)[0]

    def _on_start(self) -> None:
        manual = self._host_var.get().strip() or None
        config = replace(
            self._config,
            role=self._role_var.get(),
            network=replace(self._config.network, manual_host=manual),
            security=replace(self._config.security, shared_key=self._key_var.get()),
        )
        self._config = config
        self._remember_connection(config, manual)
        self._core_thread = _CoreThread(config, self._status_queue)
        self._core_thread.start()
        self._start_btn.configure(state=tk.DISABLED)
        self._stop_btn.configure(state=tk.NORMAL)
        logger.info("Iniciado em modo %s", config.role)

    def _remember_connection(self, config: AppConfig, manual: str | None) -> None:
        from zephyrlink.config.persist import save_connection

        try:
            save_connection(self._config_path, role=config.role, manual_host=manual,
                            shared_key=config.security.shared_key)
        except OSError:
            logger.warning("Não foi possível salvar a conexão em %s", self._config_path)

    def _toggle_key_visibility(self) -> None:
        self._key_shown = not self._key_shown
        self._key_entry.configure(show="" if self._key_shown else "•")
        self._key_toggle_btn.configure(text="Ocultar" if self._key_shown else "Mostrar")

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
        self._clients_status = status.get("clients") or []
        self._launches = status.get("launches") or []
        self._active = status.get("active")
        self._draw_layout()
        self._refresh_launcher()

    def _refresh_launcher(self) -> None:
        self._client_cid = {
            f"{c['host']} ({c.get('edge') or 'sem borda'})": c["cid"]
            for c in self._clients_status
        }
        values = list(self._client_cid)
        self._app_client_combo["values"] = values
        if self._app_client_var.get() not in self._client_cid:
            self._app_client_var.set(values[0] if values else "")
        self._refresh_app_list()
        self._refresh_history()

    def _refresh_app_list(self) -> None:
        cid = self._client_cid.get(self._app_client_var.get())
        catalog: list[dict[str, Any]] = next(
            (c.get("catalog") or [] for c in self._clients_status if c["cid"] == cid), []
        )
        self._app_label_id = {}
        self._app_accepts = {}
        for a in catalog:
            disp = a["label"] if a["label"] not in self._app_label_id else f"{a['label']} ({a['id']})"
            self._app_label_id[disp] = a["id"]
            self._app_accepts[disp] = bool(a.get("accepts_args"))
        labels = list(self._app_label_id)
        self._app_combo["values"] = labels
        if self._app_var.get() not in self._app_label_id:
            self._app_var.set(labels[0] if labels else "")
        self._update_args_state()

    def _update_args_state(self) -> None:
        accepts = self._app_accepts.get(self._app_var.get(), False)
        self._app_args_entry.configure(state=tk.NORMAL if accepts else tk.DISABLED)
        if not accepts:
            self._app_args_var.set("")

    def _refresh_history(self) -> None:
        self._history.delete(*self._history.get_children())
        for rec in reversed(self._launches):
            hora = time.strftime("%H:%M:%S", time.localtime(rec.get("ts", 0)))
            estado = _STATE_LABELS.get(rec.get("state", ""), rec.get("state", ""))
            if rec.get("error"):
                estado = f"{estado}: {rec['error']}"
            self._history.insert("", "end", values=(hora, rec.get("host", "-"),
                                                     rec.get("label", "-"), estado))

    def _open_app_editor(self) -> None:
        from zephyrlink.gui.launcher_editor import LauncherEditor

        apps = [
            {"id": a.id, "label": a.label, "command": list(a.command), "platform": a.platform}
            for a in self._config.launcher.apps
        ]
        LauncherEditor(self._root, self._config_path, apps, self._reload_config)

    def _open_shares_editor(self) -> None:
        try:
            from foshar.gui.shares_editor import open_shares_editor
        except ImportError:
            from tkinter import messagebox

            messagebox.showerror(
                "Foshar indisponível",
                "O módulo foshar não está instalado nesta máquina.",
                parent=self._root,
            )
            return
        open_shares_editor(self._root, self._config_path, lambda: None)

    def _open_remote_shares(self) -> None:
        try:
            from foshar.config import read_foshar_section
            from foshar.gui.remote_browser import open_remote_browser
        except ImportError:
            from tkinter import messagebox

            messagebox.showerror(
                "Foshar indisponível",
                "O módulo foshar não está instalado nesta máquina.",
                parent=self._root,
            )
            return
        hosts = [c["host"] for c in self._clients_status]
        _, port, cache_dir = read_foshar_section(self._config_path)
        security = replace(self._config.security, shared_key=self._key_var.get())
        open_remote_browser(self._root, hosts, port, security, cache_dir)

    def _reload_config(self) -> None:
        from zephyrlink.config import ConfigError, load_config

        try:
            self._config = load_config(self._config_path)
        except ConfigError:
            logger.exception("Falha ao recarregar %s", self._config_path)

    def _on_launch(self) -> None:
        core = self._core_thread.core if self._core_thread is not None else None
        cid = self._client_cid.get(self._app_client_var.get())
        app_id = self._app_label_id.get(self._app_var.get())
        text = self._app_args_var.get().strip()
        args = [text] if text else []
        if core is not None and cid is not None and app_id and hasattr(core, "launch_app"):
            core.launch_app(cid, app_id, args)

    def run(self) -> None:
        self._root.mainloop()


def run_gui(config: AppConfig, config_path: str = "config.yaml") -> None:
    setup_logging(config.log_level, config.log_json)
    ZephyrLinkGUI(config, config_path).run()
