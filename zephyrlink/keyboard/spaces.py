"""Ações de navegação de Spaces/Mission Control do macOS.

Executa os atalhos nativos do macOS por injeção de teclado (a mesma biblioteca
já usada pelo projeto, ``pynput``), para serem acionados por botões do mouse.
No-op fora do macOS — o comportamento de Windows/Linux não é afetado.

Atalhos (macOS):

* ``mac_space_left``      → Control + ←   (Space anterior)
* ``mac_space_right``     → Control + →   (próximo Space)
* ``mac_mission_control`` → Control + ↑   (Mission Control)
* ``mac_space_1..4``      → Control + 1..4 (ir para a Mesa N)

Os atalhos Control + número dependem de estarem habilitados em Ajustes do
Sistema → Teclado → Atalhos de Teclado → Mission Control.

Requer permissão de Acessibilidade (Ajustes → Privacidade e Segurança →
Acessibilidade). Sem ela, a injeção não surte efeito; a falha é tratada e
registrada (sem travar nem encerrar), e o Control é SEMPRE liberado.
"""

from __future__ import annotations

import logging
from typing import Any

from pynput import keyboard

from zephyrlink.config.settings import VALID_MOUSE_ACTIONS as SPACE_ACTIONS
from zephyrlink.platform_info import IS_MACOS

logger = logging.getLogger(__name__)

_controller: keyboard.Controller | None = None


def _get_controller() -> keyboard.Controller:
    global _controller
    if _controller is None:
        _controller = keyboard.Controller()
    return _controller


def is_space_action(name: str | None) -> bool:
    return name in SPACE_ACTIONS


def _digit_key(digit: str) -> Any:
    """Tecla do dígito na FILEIRA PRINCIPAL. O macOS usa ^1..^4 da fileira (não
    o numpad, onde o atalho não vale) para trocar de Mesa."""
    from zephyrlink.keyboard.injector import _mac_char_keycode

    kc = _mac_char_keycode(digit)
    if kc is not None:
        return keyboard.KeyCode.from_vk(kc)
    return keyboard.KeyCode.from_char(digit)


def _keys_for(name: str) -> list[Any] | None:
    """Sequência de teclas (Control primeiro) para a ação, ou ``None`` se inválida."""
    key = keyboard.Key
    arrows = {
        "mac_space_left": key.left,
        "mac_space_right": key.right,
        "mac_mission_control": key.up,
    }
    if name in arrows:
        return [key.ctrl, arrows[name]]
    if name in ("mac_space_1", "mac_space_2", "mac_space_3", "mac_space_4"):
        return [key.ctrl, _digit_key(name[-1])]
    return None


def run_space_action(name: str) -> bool:
    """Executa o atalho de Space correspondente. Retorna ``True`` se enviado.

    macOS apenas (no-op nas demais plataformas). As teclas pressionadas são
    SEMPRE liberadas em ordem inversa, inclusive se ocorrer exceção — o Control
    nunca fica virtualmente preso. Falhas (ex.: sem permissão de Acessibilidade)
    são registradas, sem travar nem encerrar o programa."""
    if not IS_MACOS:
        logger.debug("Ação de Space ignorada fora do macOS: %s", name)
        return False
    keys = _keys_for(name)
    if keys is None:
        logger.warning("Ação de Space desconhecida: %s", name)
        return False
    logger.info("[MouseBridge] Action: %s", name)
    controller = _get_controller()
    pressed: list[Any] = []
    try:
        for k in keys:
            controller.press(k)
            pressed.append(k)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Falha ao executar %s — a permissão de Acessibilidade foi concedida? (%s)",
            name, exc,
        )
        return False
    finally:
        # Solta tudo que foi pressionado (ordem inversa) — sempre, mesmo em erro.
        for k in reversed(pressed):
            try:
                controller.release(k)
            except Exception:  # noqa: BLE001
                pass
