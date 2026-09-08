"""Janela do operador: exibe a tela remota e captura mouse/teclado.

Roda na thread principal do Tk (dentro da GUI). Os frames chegam da thread do
asyncio por uma fila thread-safe e são desenhados numa drenagem periódica
(``after``); os eventos de input do usuário viram razões ``[0,1]`` e são
repassados por callbacks — a GUI liga esses callbacks aos métodos thread-safe
do servidor, que enviam ao alvo.

O desenho mantém a proporção da tela remota (letterbox) e guarda a colocação da
imagem para converter as coordenadas do clique em razão da tela remota.
"""

from __future__ import annotations

import logging
import queue
import tkinter as tk
from collections.abc import Callable
from tkinter import ttk
from typing import Any

from zephyrlink.remotedesktop.codec import CodecError, decode_image
from zephyrlink.remotedesktop.coords import clamp01
from zephyrlink.remotedesktop.tkinput import tk_button_name, tk_key_payload, wheel_delta_to_scroll

logger = logging.getLogger(__name__)

_POLL_MS = 15  # drenagem da fila de frames (~66 Hz de teto; o FPS real vem da rede)


class ScreenViewer:
    """Janela de visualização/controle de uma sessão remota."""

    def __init__(
        self,
        parent: tk.Misc,
        title: str,
        *,
        on_move: Callable[[float, float], None],
        on_button: Callable[[str, bool], None],
        on_scroll: Callable[[int, int], None],
        on_key: Callable[[dict[str, Any], bool], None],
        on_quality: Callable[[int], None] | None = None,
        on_close: Callable[[], None] | None = None,
        initial_quality: int = 60,
    ) -> None:
        self._on_move = on_move
        self._on_button = on_button
        self._on_scroll = on_scroll
        self._on_key = on_key
        self._on_quality = on_quality
        self._on_close = on_close

        self._frames: "queue.Queue[bytes]" = queue.Queue(maxsize=2)
        self._photo: Any = None  # mantém referência viva do PhotoImage
        self._placement = (0, 0, 1, 1)  # x0, y0, disp_w, disp_h
        self._closed = False

        self.win = tk.Toplevel(parent)
        self.win.title(title)
        self.win.geometry("1024x680")
        self.win.minsize(400, 300)
        self.win.protocol("WM_DELETE_WINDOW", self._handle_close)

        self._build_toolbar(initial_quality)
        self._canvas = tk.Canvas(self.win, bg="#101014", highlightthickness=0, cursor="tcross")
        self._canvas.pack(fill="both", expand=True)

        self._bind_events()
        self.win.after(_POLL_MS, self._drain)
        # Foco no canvas para receber teclas.
        self._canvas.focus_set()

    # ---- UI -------------------------------------------------------------
    def _build_toolbar(self, initial_quality: int) -> None:
        bar = ttk.Frame(self.win, padding=(8, 4))
        bar.pack(fill="x")
        ttk.Label(bar, text="Qualidade").pack(side="left")
        self._quality_var = tk.IntVar(value=initial_quality)
        scale = ttk.Scale(
            bar, from_=10, to=95, orient="horizontal", variable=self._quality_var,
            command=self._on_quality_change, length=160,
        )
        scale.pack(side="left", padx=(6, 12))
        self._status = ttk.Label(bar, text="conectando…")
        self._status.pack(side="left")
        ttk.Button(bar, text="Desconectar", command=self._handle_close).pack(side="right")

    def _on_quality_change(self, _value: str) -> None:
        if self._on_quality is not None:
            self._on_quality(int(self._quality_var.get()))

    def set_status(self, text: str) -> None:
        if not self._closed:
            self._status.config(text=text)

    # ---- Eventos de input ----------------------------------------------
    def _bind_events(self) -> None:
        c = self._canvas
        c.bind("<Motion>", self._on_motion)
        c.bind("<B1-Motion>", self._on_motion)
        c.bind("<B2-Motion>", self._on_motion)
        c.bind("<B3-Motion>", self._on_motion)
        c.bind("<ButtonPress>", lambda e: self._on_button_event(e, True))
        c.bind("<ButtonRelease>", lambda e: self._on_button_event(e, False))
        c.bind("<MouseWheel>", self._on_wheel)
        c.bind("<Button-4>", lambda e: self._on_scroll(0, 1))   # X11 scroll up
        c.bind("<Button-5>", lambda e: self._on_scroll(0, -1))  # X11 scroll down
        # Teclas: capturadas no toplevel para pegar também modificadores.
        self.win.bind("<KeyPress>", lambda e: self._on_key_event(e, True))
        self.win.bind("<KeyRelease>", lambda e: self._on_key_event(e, False))
        c.bind("<Enter>", lambda _e: c.focus_set())

    def _event_ratio(self, event: tk.Event) -> tuple[float, float]:
        x0, y0, disp_w, disp_h = self._placement
        xr = (event.x - x0) / disp_w if disp_w else 0.0
        yr = (event.y - y0) / disp_h if disp_h else 0.0
        return clamp01(xr), clamp01(yr)

    def _on_motion(self, event: tk.Event) -> None:
        xr, yr = self._event_ratio(event)
        self._on_move(xr, yr)

    def _on_button_event(self, event: tk.Event, pressed: bool) -> None:
        name = tk_button_name(getattr(event, "num", 0))
        if name is None:
            return  # botões 4/5 são scroll no X11, tratados à parte
        # Posiciona antes de clicar (garante o clique no lugar certo mesmo sem
        # um <Motion> imediatamente anterior).
        xr, yr = self._event_ratio(event)
        self._on_move(xr, yr)
        self._on_button(name, pressed)

    def _on_wheel(self, event: tk.Event) -> None:
        dx, dy = wheel_delta_to_scroll(int(getattr(event, "delta", 0)))
        if dx or dy:
            self._on_scroll(dx, dy)

    def _on_key_event(self, event: tk.Event, pressed: bool) -> None:
        payload = tk_key_payload(event.keysym, event.char or "")
        if payload is not None:
            self._on_key(payload, pressed)
        return "break"  # não deixa o Tk agir sobre a tecla (ex.: Tab de foco)

    # ---- Frames ---------------------------------------------------------
    def push_frame(self, blob: bytes) -> None:
        """Thread-safe: entrega um frame vindo da thread do asyncio."""
        if self._frames.full():
            try:
                self._frames.get_nowait()
            except queue.Empty:
                pass
        try:
            self._frames.put_nowait(blob)
        except queue.Full:
            pass

    def _drain(self) -> None:
        if self._closed:
            return
        blob = None
        while True:
            try:
                blob = self._frames.get_nowait()
            except queue.Empty:
                break
        if blob is not None:
            self._render(blob)
        self.win.after(_POLL_MS, self._drain)

    def _render(self, blob: bytes) -> None:
        try:
            from PIL import ImageTk

            image = decode_image(blob)
        except (CodecError, ImportError) as exc:
            self.set_status(f"sem Pillow/ImageTk: {exc}")
            return
        cw = max(1, self._canvas.winfo_width())
        ch = max(1, self._canvas.winfo_height())
        iw, ih = image.size
        factor = min(cw / iw, ch / ih)
        disp_w = max(1, int(iw * factor))
        disp_h = max(1, int(ih * factor))
        if (disp_w, disp_h) != (iw, ih):
            image = image.resize((disp_w, disp_h))
        x0 = (cw - disp_w) // 2
        y0 = (ch - disp_h) // 2
        self._placement = (x0, y0, disp_w, disp_h)
        self._photo = ImageTk.PhotoImage(image)
        self._canvas.delete("frame")
        self._canvas.create_image(x0, y0, anchor="nw", image=self._photo, tags="frame")

    # ---- Ciclo de vida --------------------------------------------------
    def _handle_close(self) -> None:
        if self._on_close is not None:
            self._on_close()
        self.close()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self.win.destroy()
        except tk.TclError:
            pass
