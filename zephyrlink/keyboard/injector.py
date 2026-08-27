"""Injeção de eventos de teclado na máquina secundária.

Rastreia teclas pressionadas para poder liberá-las todas se a conexão
cair no meio de uma combinação (evita modificador "preso").
"""

from __future__ import annotations

import logging
import sys
from typing import Any

from pynput import keyboard

from zephyrlink.keyboard.keymap import payload_to_key
from zephyrlink.keyboard.win32_input import caps_lock_active, send_unicode

logger = logging.getLogger(__name__)

_mac_char_keycodes: dict[str, int] | None = None

# Keycodes do teclado NUMÉRICO no macOS (kVK_ANSI_Keypad*). São excluídos do
# mapa: no numpad o Shift não vira símbolo (Shift+'/' = '/', não '?'), e vários
# desses caracteres também existem na fileira principal, onde o Shift funciona.
_MAC_KEYPAD_KEYCODES = frozenset(
    {65, 67, 69, 71, 75, 76, 78, 81, 82, 83, 84, 85, 86, 87, 88, 89, 91, 92}
)


def _mac_char_keycode(char: str) -> int | None:
    """Keycode da tecla PRINCIPAL (não numpad) que produz ``char`` sem modificador.

    No macOS o ``from_char`` do pynput escolhe, para dígitos, o keycode do
    teclado numérico — e no numpad o Shift não vira símbolo (Shift+2 = '2', não
    '@'). Reproduzir o caractere pela tecla principal faz o Shift encaminhado
    produzir o símbolo correto. Mapa construído uma vez (menor keycode = tecla
    principal). Retorna ``None`` fora do macOS ou se o char não estiver no mapa.
    """
    global _mac_char_keycodes
    if sys.platform != "darwin":
        return None
    if _mac_char_keycodes is None:
        _mac_char_keycodes = {}
        try:
            from pynput._util.darwin import keycode_context, keycode_to_string

            with keycode_context() as ctx:
                for kc in range(128):
                    if kc in _MAC_KEYPAD_KEYCODES:
                        continue  # numpad não vira símbolo com Shift
                    try:
                        ch = keycode_to_string(ctx, kc, 0)
                    except Exception:  # noqa: BLE001
                        ch = None
                    if ch and len(ch) == 1 and ch not in _mac_char_keycodes:
                        _mac_char_keycodes[ch] = kc
        except Exception:  # noqa: BLE001
            logger.warning("Falha ao construir o mapa de teclas do macOS", exc_info=True)
    return _mac_char_keycodes.get(char)


# A captura supressiva no servidor reporta sempre o caractere sem shift
# (teclas suprimidas não atualizam o estado assíncrono dos modificadores),
# então com modificador ativo a via Unicode nunca pode ser usada: ela
# digitaria o caractere literal sem reaplicar o modificador.
_MODIFIERS = {
    "alt",
    "alt_l",
    "alt_r",
    "alt_gr",
    "cmd",
    "cmd_l",
    "cmd_r",
    "ctrl",
    "ctrl_l",
    "ctrl_r",
    "shift",
    "shift_l",
    "shift_r",
}

# Virtual keys do teclado NUMÉRICO do Windows (VK_NUMPAD0..9 e operadores).
# Quando o servidor Windows captura o numpad de forma supressiva, o caractere
# pode não ser resolvido e a tecla chega como vk puro; num cliente não-Windows
# esse vk não é portável, então convertemos para o caractere correspondente.
_WIN_NUMPAD_VK = {
    0x60: "0", 0x61: "1", 0x62: "2", 0x63: "3", 0x64: "4",
    0x65: "5", 0x66: "6", 0x67: "7", 0x68: "8", 0x69: "9",
    0x6A: "*", 0x6B: "+", 0x6D: "-", 0x6E: ".", 0x6F: "/",
}

# Modificadores que caracterizam um ATALHO (não a produção de um caractere).
# Shift fica de fora: em payloads sem vk (servidor macOS) o caractere já vem
# resolvido com o Shift aplicado (ex.: '@'), então digitá-lo por Unicode é
# correto mesmo com Shift segurado.
_SHORTCUT_MODIFIERS = _MODIFIERS - {"shift", "shift_l", "shift_r"}

class KeyboardInjector:
    def __init__(self) -> None:
        self._controller = keyboard.Controller()
        self._held: set[Any] = set()
        self._modifiers: set[str] = set()
        self._unicode_held: set[str] = set()

    def key_event(self, payload: dict[str, Any], pressed: bool) -> None:
        kind = payload.get("kind")
        name = payload.get("name")
        char = payload.get("char")
        vk = payload.get("vk")

        # Numpad do Windows chegando como vk puro num cliente não-Windows: o vk
        # não é portável, então trata pelo caractere (a tecla principal produz
        # o dígito). Só quando não há caractere já resolvido.
        if (
            sys.platform != "win32"
            and not isinstance(char, str)
            and isinstance(vk, int)
            and vk in _WIN_NUMPAD_VK
        ):
            char = _WIN_NUMPAD_VK[vk]
            kind = "char"

        if kind == "named" and name in _MODIFIERS:
            if pressed:
                self._modifiers.add(name)
            else:
                self._modifiers.discard(name)

        if kind == "char" and isinstance(char, str):
            # Com o vk físico no payload (servidor Windows) a injeção é por
            # virtual key: o cliente aplica shift/altgr/caps e tecla morta sobre
            # o layout sincronizado. Sem vk (servidor macOS/não-Windows) o char
            # já vem resolvido, então digita-se por Unicode — inclusive com Shift
            # segurado; só um modificador de ATALHO (Ctrl/Alt/Cmd) desativa isso.
            use_unicode = (
                sys.platform == "win32"
                and (
                    char in self._unicode_held
                    or (
                        pressed
                        and not isinstance(vk, int)
                        and not (self._modifiers & _SHORTCUT_MODIFIERS)
                    )
                )
            )
            if use_unicode:
                sent = send_unicode(char, pressed)
                if sent and pressed:
                    self._unicode_held.add(char)
                elif not pressed:
                    self._unicode_held.discard(char)
                return

        # O vk capturado reproduz a tecla física; re-resolver o caractere
        # com VkKeyScan dependeria do layout da thread injetora e cairia
        # em Unicode literal quando não resolve (ex.: tecla ABNT_C1).
        mac_keycode = _mac_char_keycode(char) if kind == "char" and isinstance(char, str) else None
        if kind == "char" and sys.platform == "win32" and isinstance(vk, int):
            key: Any = keyboard.KeyCode.from_vk(vk)
        elif mac_keycode is not None:
            # macOS: injeta pela tecla principal para o Shift/AltGr encaminhado
            # produzir o símbolo correto (ex.: Shift+2 = '@').
            key = keyboard.KeyCode.from_vk(mac_keycode)
        else:
            key = payload_to_key(payload)
        if key is None:
            logger.warning("Tecla não reconhecida: %s", payload)
            return
        try:
            if pressed:
                self._controller.press(key)
                self._held.add(key)
            else:
                self._controller.release(key)
                self._held.discard(key)
        except self._controller.InvalidKeyException:
            logger.warning("Tecla inválida para esta plataforma: %s", payload)

    def release_all(self) -> None:
        """Libera teclas presas (chamado em desconexão/saída do modo remoto)."""
        for key in list(self._held):
            try:
                self._controller.release(key)
            except Exception:  # noqa: BLE001
                pass
        self._held.clear()
        for char in list(self._unicode_held):
            send_unicode(char, False)
        self._unicode_held.clear()
        self._modifiers.clear()
