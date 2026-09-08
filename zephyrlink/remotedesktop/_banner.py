"""Faixa "tela sendo compartilhada" — processo separado, com Tk próprio.

Roda como ``python -m zephyrlink.remotedesktop._banner <host>``. Vive em seu
próprio processo (e portanto na sua própria main thread) porque o cliente é
headless/asyncio e o Tk é instável fora da main thread — em especial no macOS.
Mostra uma barra vermelha, sem borda e sempre no topo, no alto da tela. Encerra
quando o processo pai fecha seu stdin (morte do pai) ou é terminado.
"""

from __future__ import annotations

import sys
import threading
import tkinter as tk


def _watch_stdin(root: tk.Tk) -> None:
    # EOF em stdin = o pai fechou o pipe (encerrou a sessão ou morreu).
    try:
        for _ in sys.stdin:
            pass
    except (OSError, ValueError):
        pass
    root.after(0, root.destroy)


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    host = argv[0] if argv else "?"

    root = tk.Tk()
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    try:
        root.attributes("-alpha", 0.92)
    except tk.TclError:
        pass
    width = root.winfo_screenwidth()
    bar_h = 30
    root.geometry(f"{width}x{bar_h}+0+0")
    root.configure(bg="#c1121f")
    label = tk.Label(
        root,
        text=f"🔴  Esta tela está sendo compartilhada e controlada remotamente por {host}",
        bg="#c1121f",
        fg="white",
        font=("Helvetica", 13, "bold"),
    )
    label.pack(fill="both", expand=True)

    threading.Thread(target=_watch_stdin, args=(root,), daemon=True).start()
    try:
        root.mainloop()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
