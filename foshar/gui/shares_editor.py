"""Editor gráfico das pastas compartilháveis (lado dono dos arquivos).

Lista os *shares* que esta máquina publica e permite adicionar/editar/remover,
com seletor de pasta e modo (somente leitura / leitura e escrita). Grava na
seção ``foshar`` do ``config.yaml`` — persistente entre execuções.
"""

from __future__ import annotations

import re
import tkinter as tk
from collections.abc import Callable
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any

from foshar.config import save_shares

_MODES = ("rw", "ro")


def _slug(value: str, taken: set[str]) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-") or "pasta"
    candidate = base
    n = 2
    while candidate in taken:
        candidate = f"{base}-{n}"
        n += 1
    return candidate


class SharesEditor:
    def __init__(
        self,
        parent: tk.Misc,
        config_path: str,
        shares: list[dict[str, str]],
        on_saved: Callable[[], None],
        *,
        port: int,
        cache_dir: str,
    ) -> None:
        self._config_path = config_path
        self._shares = [dict(s) for s in shares]
        self._on_saved = on_saved
        self._port = port
        self._cache_dir = cache_dir

        self._win = tk.Toplevel(parent)
        self._win.title("Pastas compartilhadas neste computador")
        self._win.minsize(560, 320)
        self._win.transient(parent)  # type: ignore[arg-type]
        self._win.grab_set()

        frame = ttk.Frame(self._win, padding=10)
        frame.pack(fill=tk.BOTH, expand=True)
        ttk.Label(
            frame,
            text="Estas são as pastas que outra máquina pode acessar via Foshar.",
        ).pack(anchor="w")

        cols = ("id", "modo", "pasta")
        self._tree = ttk.Treeview(frame, columns=cols, show="headings", height=8)
        for col, width in zip(cols, (140, 60, 320)):
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
        for i, share in enumerate(self._shares):
            self._tree.insert("", "end", iid=str(i), values=(share["id"], share["mode"], share["path"]))

    def _selected_index(self) -> int | None:
        sel = self._tree.selection()
        return int(sel[0]) if sel else None

    def _add(self) -> None:
        result = _ShareDialog(self._win, None).result
        if result is not None:
            result["id"] = _slug(result["id"] or Path(result["path"]).name, {s["id"] for s in self._shares})
            self._shares.append(result)
            self._refresh()

    def _edit(self) -> None:
        index = self._selected_index()
        if index is None:
            return
        result = _ShareDialog(self._win, self._shares[index]).result
        if result is not None:
            taken = {s["id"] for i, s in enumerate(self._shares) if i != index}
            result["id"] = _slug(result["id"] or Path(result["path"]).name, taken)
            self._shares[index] = result
            self._refresh()

    def _remove(self) -> None:
        index = self._selected_index()
        if index is None:
            return
        del self._shares[index]
        self._refresh()

    def _save(self) -> None:
        try:
            save_shares(
                self._config_path,
                enabled=bool(self._shares),
                port=self._port,
                cache_dir=self._cache_dir,
                shares=self._shares,
            )
        except OSError as exc:
            messagebox.showerror("Erro ao salvar", str(exc), parent=self._win)
            return
        self._on_saved()
        messagebox.showinfo(
            "Salvo",
            f"Gravado em {self._config_path}.\n"
            "Reinicie o 'foshar serve' para publicar a lista atualizada.",
            parent=self._win,
        )
        self._win.destroy()


class _ShareDialog:
    def __init__(self, parent: tk.Misc, share: dict[str, str] | None) -> None:
        self.result: dict[str, str] | None = None

        self._win = tk.Toplevel(parent)
        self._win.title("Editar pasta" if share else "Adicionar pasta")
        self._win.transient(parent)  # type: ignore[arg-type]
        self._win.grab_set()
        frame = ttk.Frame(self._win, padding=10)
        frame.pack(fill=tk.BOTH, expand=True)

        ttk.Label(frame, text="Pasta:").grid(row=0, column=0, sticky="w", pady=2)
        self._path = tk.StringVar(value=share["path"] if share else "")
        ttk.Entry(frame, textvariable=self._path, width=40).grid(row=0, column=1, sticky="we", pady=2)
        ttk.Button(frame, text="Procurar", command=self._pick).grid(row=0, column=2, padx=4)

        ttk.Label(frame, text="Nome (id, opcional):").grid(row=1, column=0, sticky="w", pady=2)
        self._id = tk.StringVar(value=share["id"] if share else "")
        ttk.Entry(frame, textvariable=self._id, width=24).grid(row=1, column=1, sticky="w", pady=2)

        ttk.Label(frame, text="Acesso:").grid(row=2, column=0, sticky="w", pady=2)
        self._mode = tk.StringVar(value=share["mode"] if share else "rw")
        ttk.Combobox(frame, textvariable=self._mode, values=_MODES, width=10, state="readonly").grid(
            row=2, column=1, sticky="w", pady=2)
        ttk.Label(frame, text="rw = leitura e escrita · ro = somente leitura", foreground="#666666").grid(
            row=3, column=1, sticky="w")

        actions = ttk.Frame(frame)
        actions.grid(row=4, column=0, columnspan=3, sticky="e", pady=(10, 0))
        ttk.Button(actions, text="Cancelar", command=self._win.destroy).pack(side=tk.RIGHT)
        ttk.Button(actions, text="OK", command=self._ok).pack(side=tk.RIGHT, padx=6)

        frame.columnconfigure(1, weight=1)
        self._win.wait_window()

    def _pick(self) -> None:
        path = filedialog.askdirectory(parent=self._win, title="Selecione a pasta a compartilhar")
        if path:
            self._path.set(path)

    def _ok(self) -> None:
        path = self._path.get().strip()
        if not path:
            messagebox.showerror("Faltando", "Informe a pasta.", parent=self._win)
            return
        if not Path(path).expanduser().is_dir():
            messagebox.showerror("Pasta inválida", "O caminho não é uma pasta existente.", parent=self._win)
            return
        self.result = {"id": self._id.get().strip(), "path": path, "mode": self._mode.get()}
        self._win.destroy()


def open_shares_editor(parent: tk.Misc, config_path: str, on_saved: Callable[[], None]) -> None:
    """Ponto de entrada usado pela GUI do ZephyrLink (import tardio)."""
    from foshar.config import read_foshar_section

    shares, port, cache_dir = read_foshar_section(config_path)
    SharesEditor(parent, config_path, shares, on_saved, port=port, cache_dir=cache_dir)
