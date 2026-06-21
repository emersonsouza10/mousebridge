"""Editor gráfico do catálogo de apps permitidos (lado do cliente).

Lista os apps que esta máquina autoriza o servidor a abrir e permite
adicionar/editar/remover, com um atalho para o caso comum de "abrir um site
no navegador". Grava no ``config.yaml`` e liga ``launcher.enabled``.
"""

from __future__ import annotations

import re
import shutil
import sys
import tkinter as tk
from collections.abc import Callable
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any

from zephyrlink.config.persist import save_launcher

_FIREFOX_GUESSES = (
    r"C:\Program Files\Mozilla Firefox\firefox.exe",
    r"C:\Program Files (x86)\Mozilla Firefox\firefox.exe",
)
_PLATFORMS = ("(qualquer)", "windows", "linux", "macos")


def _current_platform() -> str:
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def _default_browser() -> str:
    for path in _FIREFOX_GUESSES:
        if Path(path).exists():
            return path
    return shutil.which("firefox") or "firefox.exe"


def _is_site(app: dict[str, Any]) -> bool:
    cmd = app["command"]
    return len(cmd) >= 2 and str(cmd[1]).lower().startswith(("http://", "https://"))


def _slug(label: str, taken: set[str]) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-") or "app"
    candidate = base
    n = 2
    while candidate in taken:
        candidate = f"{base}-{n}"
        n += 1
    return candidate


class LauncherEditor:
    def __init__(
        self,
        parent: tk.Misc,
        config_path: str,
        apps: list[dict[str, Any]],
        on_saved: Callable[[], None],
    ) -> None:
        self._config_path = config_path
        self._apps = [dict(a) for a in apps]
        self._on_saved = on_saved

        self._win = tk.Toplevel(parent)
        self._win.title("Apps permitidos neste computador")
        self._win.minsize(520, 320)
        self._win.transient(parent)  # type: ignore[arg-type]
        self._win.grab_set()

        frame = ttk.Frame(self._win, padding=10)
        frame.pack(fill=tk.BOTH, expand=True)
        ttk.Label(
            frame,
            text="Estes são os apps que o servidor pode abrir nesta máquina.",
        ).pack(anchor="w")

        cols = ("label", "tipo", "detalhe")
        self._tree = ttk.Treeview(frame, columns=cols, show="headings", height=8)
        for col, width in zip(cols, (180, 70, 240)):
            self._tree.heading(col, text=col.capitalize())
            self._tree.column(col, width=width, anchor="w")
        self._tree.pack(fill=tk.BOTH, expand=True, pady=(8, 8))
        self._tree.bind("<Double-1>", lambda _e: self._edit())

        buttons = ttk.Frame(frame)
        buttons.pack(fill=tk.X)
        ttk.Button(buttons, text="Adicionar", command=self._add).pack(side=tk.LEFT)
        ttk.Button(buttons, text="Editar", command=self._edit).pack(side=tk.LEFT, padx=6)
        ttk.Button(buttons, text="Remover", command=self._remove).pack(side=tk.LEFT)
        ttk.Button(buttons, text="Salvar", command=self._save).pack(side=tk.RIGHT)
        self._refresh()

    def _refresh(self) -> None:
        self._tree.delete(*self._tree.get_children())
        for i, app in enumerate(self._apps):
            if _is_site(app):
                tipo, detalhe = "site", app["command"][1]
            else:
                tipo, detalhe = "app", app["command"][0]
            self._tree.insert("", "end", iid=str(i), values=(app["label"], tipo, detalhe))

    def _selected_index(self) -> int | None:
        sel = self._tree.selection()
        return int(sel[0]) if sel else None

    def _add(self) -> None:
        result = _ItemDialog(self._win, None).result
        if result is not None:
            result["id"] = _slug(result["label"], {a["id"] for a in self._apps})
            self._apps.append(result)
            self._refresh()

    def _edit(self) -> None:
        index = self._selected_index()
        if index is None:
            return
        result = _ItemDialog(self._win, self._apps[index]).result
        if result is not None:
            result["id"] = self._apps[index]["id"]
            self._apps[index] = result
            self._refresh()

    def _remove(self) -> None:
        index = self._selected_index()
        if index is None:
            return
        del self._apps[index]
        self._refresh()

    def _save(self) -> None:
        try:
            save_launcher(self._config_path, enabled=True, apps=self._apps)
        except OSError as exc:
            messagebox.showerror("Erro ao salvar", str(exc), parent=self._win)
            return
        self._on_saved()
        messagebox.showinfo(
            "Salvo",
            f"Gravado em {self._config_path}.\n"
            "Reinicie o cliente para o servidor ver a lista atualizada.",
            parent=self._win,
        )
        self._win.destroy()


class _ItemDialog:
    def __init__(self, parent: tk.Misc, app: dict[str, Any] | None) -> None:
        self.result: dict[str, Any] | None = None
        site = app is None or _is_site(app)

        self._win = tk.Toplevel(parent)
        self._win.title("Editar app" if app else "Adicionar app")
        self._win.transient(parent)  # type: ignore[arg-type]
        self._win.grab_set()
        frame = ttk.Frame(self._win, padding=10)
        frame.pack(fill=tk.BOTH, expand=True)

        ttk.Label(frame, text="Nome (exibido no servidor):").grid(row=0, column=0, sticky="w")
        self._label = tk.StringVar(value=app["label"] if app else "")
        ttk.Entry(frame, textvariable=self._label, width=36).grid(
            row=0, column=1, columnspan=2, sticky="we", pady=2)

        self._tipo = tk.StringVar(value="site" if site else "app")
        ttk.Radiobutton(frame, text="Site (abre no navegador)", variable=self._tipo,
                        value="site", command=self._toggle).grid(row=1, column=0, columnspan=2, sticky="w")
        ttk.Radiobutton(frame, text="Aplicativo", variable=self._tipo,
                        value="app", command=self._toggle).grid(row=1, column=2, sticky="w")

        self._url = tk.StringVar(value=app["command"][1] if app and _is_site(app) else "")
        self._browser = tk.StringVar(
            value=(app["command"][0] if app and _is_site(app) else _default_browser()))
        self._exe = tk.StringVar(value=app["command"][0] if app and not _is_site(app) else "")

        self._url_label = ttk.Label(frame, text="URL:")
        self._url_entry = ttk.Entry(frame, textvariable=self._url, width=36)
        self._browser_label = ttk.Label(frame, text="Navegador:")
        self._browser_entry = ttk.Entry(frame, textvariable=self._browser, width=28)
        self._browser_btn = ttk.Button(frame, text="Procurar",
                                       command=lambda: self._pick(self._browser))
        self._exe_label = ttk.Label(frame, text="Executável:")
        self._exe_entry = ttk.Entry(frame, textvariable=self._exe, width=28)
        self._exe_btn = ttk.Button(frame, text="Procurar", command=lambda: self._pick(self._exe))

        self._url_label.grid(row=2, column=0, sticky="w", pady=2)
        self._url_entry.grid(row=2, column=1, columnspan=2, sticky="we", pady=2)
        self._browser_label.grid(row=3, column=0, sticky="w", pady=2)
        self._browser_entry.grid(row=3, column=1, sticky="we", pady=2)
        self._browser_btn.grid(row=3, column=2, sticky="w", padx=4)
        self._exe_label.grid(row=4, column=0, sticky="w", pady=2)
        self._exe_entry.grid(row=4, column=1, sticky="we", pady=2)
        self._exe_btn.grid(row=4, column=2, sticky="w", padx=4)

        ttk.Label(frame, text="Sistema:").grid(row=5, column=0, sticky="w", pady=2)
        self._platform = tk.StringVar(
            value=(app["platform"] if app and app.get("platform") else _current_platform()))
        ttk.Combobox(frame, textvariable=self._platform, values=_PLATFORMS, width=14,
                     state="readonly").grid(row=5, column=1, sticky="w", pady=2)

        actions = ttk.Frame(frame)
        actions.grid(row=6, column=0, columnspan=3, sticky="e", pady=(10, 0))
        ttk.Button(actions, text="Cancelar", command=self._win.destroy).pack(side=tk.RIGHT)
        ttk.Button(actions, text="OK", command=self._ok).pack(side=tk.RIGHT, padx=6)

        frame.columnconfigure(1, weight=1)
        self._toggle()
        self._win.wait_window()

    def _toggle(self) -> None:
        site = self._tipo.get() == "site"
        for widget in (self._url_label, self._url_entry, self._browser_label,
                       self._browser_entry, self._browser_btn):
            widget.grid() if site else widget.grid_remove()
        for widget in (self._exe_label, self._exe_entry, self._exe_btn):
            widget.grid_remove() if site else widget.grid()

    def _pick(self, var: tk.StringVar) -> None:
        path = filedialog.askopenfilename(
            parent=self._win,
            title="Selecione o executável",
            filetypes=[("Executáveis", "*.exe"), ("Todos", "*.*")],
        )
        if path:
            var.set(path)

    def _ok(self) -> None:
        label = self._label.get().strip()
        if not label:
            messagebox.showerror("Faltando", "Informe o nome.", parent=self._win)
            return
        platform = None if self._platform.get() == "(qualquer)" else self._platform.get()
        if self._tipo.get() == "site":
            url = self._url.get().strip()
            browser = self._browser.get().strip()
            if not browser:
                messagebox.showerror("Faltando", "Informe o navegador.", parent=self._win)
                return
            if not url.lower().startswith(("http://", "https://")):
                messagebox.showerror("URL inválida", "A URL deve começar com http:// ou https://.",
                                     parent=self._win)
                return
            command = [browser, url]
        else:
            exe = self._exe.get().strip()
            if not exe:
                messagebox.showerror("Faltando", "Informe o executável.", parent=self._win)
                return
            command = [exe]
        self.result = {"id": "", "label": label, "command": command, "platform": platform}
        self._win.destroy()
