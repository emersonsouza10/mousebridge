"""Serialização de teclas para transporte em rede.

Uma tecla vira um payload JSON-friendly de três formas:

* ``{"kind": "char", "char": "a"}`` — tecla de caractere imprimível,
  com ``vk`` da tecla física quando conhecido;
* ``{"kind": "named", "name": "ctrl_l"}`` — tecla especial pelo nome
  pynput (``ctrl_l``, ``alt``, ``cmd``, ``f1``..., ``tab``, ``enter``...);
* ``{"kind": "vk", "vk": 165}`` — fallback por virtual key code quando o
  pynput não resolve nome nem caractere.

Nomes pynput são estáveis entre plataformas, o que permite combinações
como Ctrl+C, Alt+Tab e Win+R (``cmd`` é a tecla Windows) funcionarem
entre máquinas com layouts próximos. As funções de (de)serialização que
dependem de pynput ficam em funções separadas para que este módulo seja
importável (e testável) sem display.
"""

from __future__ import annotations

from typing import Any

VALID_KINDS = ("char", "named", "vk")


def payload_from_parts(
    char: str | None = None,
    name: str | None = None,
    vk: int | None = None,
) -> dict[str, Any] | None:
    """Monta o payload a partir dos atributos extraídos de uma tecla."""
    if char is not None:
        payload: dict[str, Any] = {"kind": "char", "char": char}
        if vk is not None:
            payload["vk"] = int(vk)
        return payload
    if name is not None:
        return {"kind": "named", "name": name}
    if vk is not None:
        return {"kind": "vk", "vk": int(vk)}
    return None


def payload_is_valid(payload: dict[str, Any]) -> bool:
    kind = payload.get("kind")
    if kind == "char":
        return isinstance(payload.get("char"), str) and len(payload["char"]) >= 1
    if kind == "named":
        return isinstance(payload.get("name"), str) and bool(payload["name"])
    if kind == "vk":
        return isinstance(payload.get("vk"), int)
    return False


def key_to_payload(key: Any) -> dict[str, Any] | None:
    """Converte uma tecla pynput (``Key`` ou ``KeyCode``) em payload."""
    from pynput import keyboard

    if isinstance(key, keyboard.Key):
        return payload_from_parts(name=key.name)
    if isinstance(key, keyboard.KeyCode):
        if key.char is not None:
            return payload_from_parts(char=key.char, vk=key.vk)
        return payload_from_parts(vk=key.vk)
    return None


def payload_to_key(payload: dict[str, Any]) -> Any | None:
    """Converte um payload de volta para uma tecla pynput injetável."""
    from pynput import keyboard

    match payload.get("kind"):
        case "char":
            return keyboard.KeyCode.from_char(payload["char"])
        case "named":
            try:
                return keyboard.Key[payload["name"]]
            except KeyError:
                return None
        case "vk":
            return keyboard.KeyCode.from_vk(int(payload["vk"]))
    return None
