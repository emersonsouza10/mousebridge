"""Navegador das pastas publicadas pelos clientes (lado que quer acessar).

Análogo ao painel "Aplicações remotas": para cada cliente conectado, consulta —
pela conexão dedicada do Foshar — o catálogo de pastas que ele compartilha e
permite sincronizar uma delas para o espelho local, abrindo no VSCode.

As consultas/clone rodam em thread (são ``asyncio``/rede); o resultado volta
para o Tk via ``after`` — a GUI só é tocada pela thread principal.
"""

from __future__ import annotations

import asyncio
import threading
import tkinter as tk
from tkinter import messagebox, ttk

from zephyrlink.config import SecurityConfig

from foshar.foshar_server.rpc import FosharClient, fetch_shares
from foshar.foshar_server.workspace import clone, open_in_vscode


class RemoteSharesBrowser:
    def __init__(
        self,
        parent: tk.Misc,
        hosts: list[str],
        port: int,
        security: SecurityConfig,
        cache_dir: str,
    ) -> None:
        self._port = port
        self._security = security
        self._cache_dir = cache_dir

        self._win = tk.Toplevel(parent)
        self._win.title("Pastas compartilhadas pelos clientes")
        self._win.minsize(520, 340)
        self._win.transient(parent)  # type: ignore[arg-type]

        frame = ttk.Frame(self._win, padding=10)
        frame.pack(fill=tk.BOTH, expand=True)

        top = ttk.Frame(frame)
        top.pack(fill=tk.X)
        ttk.Label(top, text="Cliente:").pack(side=tk.LEFT)
        self._host_var = tk.StringVar()
        self._host_combo = ttk.Combobox(top, textvariable=self._host_var, values=hosts, width=22, state="readonly")
        self._host_combo.pack(side=tk.LEFT, padx=6)
        self._host_combo.bind("<<ComboboxSelected>>", lambda _e: self._load())
        ttk.Button(top, text="Atualizar", command=self._load).pack(side=tk.LEFT)

        cols = ("id", "acesso")
        self._tree = ttk.Treeview(frame, columns=cols, show="headings", height=9)
        for col, width in zip(cols, (300, 90)):
            self._tree.heading(col, text=col.capitalize())
            self._tree.column(col, width=width, anchor="w")
        self._tree.pack(fill=tk.BOTH, expand=True, pady=8)
        self._tree.bind("<Double-1>", lambda _e: self._open())

        bottom = ttk.Frame(frame)
        bottom.pack(fill=tk.X)
        self._status = tk.StringVar(value="Selecione um cliente." if hosts else "Nenhum cliente conectado.")
        ttk.Label(bottom, textvariable=self._status).pack(side=tk.LEFT)
        ttk.Button(bottom, text="Abrir no VSCode", command=self._open).pack(side=tk.RIGHT)

        if hosts:
            self._host_var.set(hosts[0])
            self._load()

    def _load(self) -> None:
        host = self._host_var.get()
        if not host:
            return
        self._tree.delete(*self._tree.get_children())
        self._status.set(f"Consultando {host}…")
        threading.Thread(target=self._query, args=(host,), daemon=True).start()

    def _query(self, host: str) -> None:
        try:
            shares = asyncio.run(fetch_shares(host, self._port, self._security))
            self._win.after(0, self._fill, host, shares, None)
        except Exception as exc:  # rede/auth: surge no rótulo de status
            self._win.after(0, self._fill, host, [], exc)

    def _fill(self, host: str, shares: list[dict[str, str]], error: Exception | None) -> None:
        if error is not None:
            self._status.set(f"{host}: indisponível ({error})")
            return
        for share in shares:
            self._tree.insert("", "end", values=(share.get("id"), share.get("mode")))
        self._status.set(
            f"{host}: {len(shares)} pasta(s)." if shares else f"{host}: nenhuma pasta compartilhada."
        )

    def _open(self) -> None:
        host = self._host_var.get()
        sel = self._tree.selection()
        if not host or not sel:
            return
        share_id = str(self._tree.item(sel[0], "values")[0])
        self._status.set(f"Sincronizando '{share_id}' de {host}…")
        threading.Thread(target=self._do_open, args=(host, share_id), daemon=True).start()

    def _do_open(self, host: str, share_id: str) -> None:
        try:
            root = asyncio.run(self._clone(host, share_id))
            self._win.after(0, self._opened, root, share_id, None)
        except Exception as exc:
            self._win.after(0, self._opened, None, share_id, exc)

    async def _clone(self, host: str, share_id: str):
        client = FosharClient(host, self._port, self._security)
        await client.connect()
        try:
            return await clone(client, share_id, self._cache_dir)
        finally:
            await client.close()

    def _opened(self, root, share_id: str, error: Exception | None) -> None:
        if error is not None:
            self._status.set(f"Falha ao sincronizar '{share_id}'")
            messagebox.showerror("Erro", str(error), parent=self._win)
            return
        self._status.set(f"Sincronizado em {root}")
        open_in_vscode(root)


def open_remote_browser(
    parent: tk.Misc, hosts: list[str], port: int, security: SecurityConfig, cache_dir: str
) -> None:
    RemoteSharesBrowser(parent, hosts, port, security, cache_dir)
