"""Tradução de eventos de input do Tk (viewer) para o protocolo ZephyrLink.

O viewer roda em Tk; o alvo injeta com pynput. Estas funções puras convertem
``keysym``/``char`` do Tk e números de botão no formato de payload que o
``KeyboardInjector`` já entende (ver keyboard/keymap.py) — sem depender de Tk,
para serem testáveis sem display.
"""

from __future__ import annotations

from typing import Any

# keysym do Tk -> nome de tecla pynput (keyboard.Key[...]). Cobre modificadores,
# navegação e teclas de edição comuns; caracteres imprimíveis vão pela via
# ``char``. Teclas exóticas não mapeadas são ignoradas (v1).
_NAMED: dict[str, str] = {
    "Return": "enter",
    "KP_Enter": "enter",
    "Escape": "esc",
    "Tab": "tab",
    "ISO_Left_Tab": "tab",
    "BackSpace": "backspace",
    "space": "space",
    "Up": "up",
    "Down": "down",
    "Left": "left",
    "Right": "right",
    "Delete": "delete",
    "Insert": "insert",
    "Home": "home",
    "End": "end",
    "Prior": "page_up",
    "Next": "page_down",
    "Caps_Lock": "caps_lock",
    "Num_Lock": "num_lock",
    "Scroll_Lock": "scroll_lock",
    "Print": "print_screen",
    "Pause": "pause",
    "Menu": "menu",
    "Control_L": "ctrl_l",
    "Control_R": "ctrl_r",
    "Shift_L": "shift",
    "Shift_R": "shift_r",
    "Alt_L": "alt_l",
    "Alt_R": "alt_gr",
    "ISO_Level3_Shift": "alt_gr",
    "Super_L": "cmd",
    "Super_R": "cmd_r",
    "Win_L": "cmd",
    "Win_R": "cmd_r",
    "Meta_L": "cmd",
    "Meta_R": "cmd_r",
    "Command": "cmd",
}

# Número de botão do Tk -> nome de botão do ZephyrLink (ver mouse/capture.py).
_BUTTONS: dict[int, str] = {1: "left", 2: "middle", 3: "right"}


def tk_key_payload(keysym: str, char: str) -> dict[str, Any] | None:
    """Payload de tecla a partir de ``event.keysym`` e ``event.char`` do Tk.

    Nomeadas (setas, Enter, modificadores...) viram ``{"kind":"named"}``; teclas
    de caractere viram ``{"kind":"char"}`` com o caractere imprimível — sem
    ``vk``, pois o operador pode ser de outra plataforma que o alvo (mesma via
    segura usada pelo servidor macOS). Devolve ``None`` se não reconhecer.
    """
    if keysym in _NAMED:
        return {"kind": "named", "name": _NAMED[keysym]}
    if len(keysym) == 2 and keysym[0] in "fF" and keysym[1].isdigit():
        return {"kind": "named", "name": keysym.lower()}
    if len(keysym) == 3 and keysym[0] in "fF" and keysym[1:].isdigit():
        return {"kind": "named", "name": keysym.lower()}
    if char and len(char) == 1 and char.isprintable():
        return {"kind": "char", "char": char}
    if len(keysym) == 1 and keysym.isprintable():
        return {"kind": "char", "char": keysym}
    return None


def tk_button_name(num: int) -> str | None:
    """Nome do botão a partir de ``event.num`` do Tk (1=esq, 2=meio, 3=dir)."""
    return _BUTTONS.get(num)


def wheel_delta_to_scroll(delta: int) -> tuple[int, int]:
    """``event.delta`` do <MouseWheel> -> ``(dx, dy)`` em passos de scroll.

    Windows entrega múltiplos de 120; macOS entrega valores pequenos. Só o sinal
    importa para um passo por vez: rola +1 (cima) ou -1 (baixo)."""
    if delta == 0:
        return 0, 0
    return 0, 1 if delta > 0 else -1
