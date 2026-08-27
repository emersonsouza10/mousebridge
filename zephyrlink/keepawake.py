"""Impede o Mac de dormir/bloquear enquanto o núcleo está ativo (macOS).

Como servidor, o Mac fica ocioso do ponto de vista do sistema — o operador
controla o cliente, sem input local — então o macOS escurece e bloqueia a
tela por inatividade, derrubando a sessão. Uma "atividade" do
``NSProcessInfo`` mantém a tela e o sistema acordados enquanto o token é
retido; liberá-lo devolve o comportamento normal de energia.

No-op fora do macOS (Windows/Linux têm seus próprios mecanismos e não foram
reportados como problema aqui).
"""

from __future__ import annotations

import logging
import sys
import threading

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_token = None


def prevent_sleep(reason: str = "ZephyrLink ativo") -> None:
    """Mantém a tela e o sistema acordados (idempotente)."""
    global _token
    if sys.platform != "darwin":
        return
    with _lock:
        if _token is not None:
            return
        try:
            import Foundation

            options = (
                Foundation.NSActivityUserInitiated
                | Foundation.NSActivityIdleDisplaySleepDisabled
                | Foundation.NSActivityIdleSystemSleepDisabled
            )
            _token = Foundation.NSProcessInfo.processInfo().beginActivityWithOptions_reason_(
                options, reason
            )
            logger.info("Mac mantido ativo (sem dormir/bloquear) enquanto o núcleo roda")
        except Exception:  # noqa: BLE001
            logger.warning("Não foi possível impedir o Mac de dormir", exc_info=True)
            _token = None


def allow_sleep() -> None:
    """Libera o Mac para dormir/bloquear normalmente (idempotente)."""
    global _token
    if sys.platform != "darwin":
        return
    with _lock:
        if _token is None:
            return
        try:
            import Foundation

            Foundation.NSProcessInfo.processInfo().endActivity_(_token)
            logger.info("Mac liberado para dormir/bloquear normalmente")
        except Exception:  # noqa: BLE001
            logger.warning("Falha ao liberar o Mac para dormir", exc_info=True)
        finally:
            _token = None
