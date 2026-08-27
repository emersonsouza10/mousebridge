"""Correções de compatibilidade do pynput no macOS.

No macOS recente (Sequoia/Tahoe) as APIs Text Input Source (TIS/TSM) do
HIToolbox abortam o processo (``dispatch_assert_queue`` → ``SIGTRAP``) quando
são chamadas fora da *main thread*. O pynput consulta o layout de teclado por
essas APIs em dois pontos, e ambos rodam numa thread de background neste app:

* ``Listener._run`` (captura, lado servidor) entra em ``keycode_context`` na
  thread do listener do pynput;
* ``Controller.__init__`` (injeção, lado cliente) chama
  ``get_unicode_to_keycode_map`` → ``keycode_context`` na ``_CoreThread``.

A correção pré-computa o contexto de layout uma vez na *main thread* e faz o
pynput reutilizá-lo, de modo que a thread de background nunca toque TIS/TSM.
A tradução em si (``UCKeyTranslate``, feita a partir do ``layout_data`` já
carregado) é segura em qualquer thread. Deve ser chamada na *main thread*,
antes de iniciar o núcleo.
"""

from __future__ import annotations

import contextlib
import logging
import sys
import threading

logger = logging.getLogger(__name__)

_installed = False
_lock = threading.Lock()


def install_macos_pynput_layout_fix() -> None:
    """Evita o ``SIGTRAP`` do pynput ao consultar o layout fora da main thread.

    No-op fora do macOS ou se já instalada. Idempotente e segura de chamar em
    qualquer plataforma.
    """
    global _installed
    if sys.platform != "darwin":
        return
    with _lock:
        if _installed:
            return
        try:
            from pynput._util import darwin as _util_darwin
            from pynput.keyboard import _darwin as _kbd_darwin
        except Exception:  # noqa: BLE001
            logger.debug("pynput indisponível; correção de layout ignorada", exc_info=True)
            return

        if threading.current_thread() is not threading.main_thread():
            logger.warning(
                "install_macos_pynput_layout_fix() chamada fora da main thread; "
                "o contexto de layout pode ser instável"
            )

        try:
            with _util_darwin.keycode_context() as context:
                cached = context
        except Exception:  # noqa: BLE001
            logger.warning("Falha ao pré-computar o layout de teclado do macOS", exc_info=True)
            return

        @contextlib.contextmanager
        def _cached_keycode_context():
            yield cached

        # ``get_unicode_to_keycode_map`` referencia o global de _util/darwin;
        # ``Listener._run`` usa o nome importado em keyboard/_darwin. Trocar os
        # dois garante que nenhum caminho reentre nas APIs TIS/TSM.
        _util_darwin.keycode_context = _cached_keycode_context
        _kbd_darwin.keycode_context = _cached_keycode_context
        _installed = True
        logger.info("Correção de layout do pynput (macOS) instalada")


def request_macos_accessibility() -> bool:
    """Garante que o app peça a permissão de Acessibilidade ao próprio macOS.

    Retorna ``True`` se o processo já é confiável. Caso contrário, dispara o
    diálogo oficial do sistema (com o botão "Abrir Ajustes do Sistema"), que
    registra o app CORRETO na lista de Acessibilidade — evitando o erro comum de
    autorizar o app errado (ex.: o Terminal em vez do ``Python.app`` que o
    Tkinter passa a representar). No-op fora do macOS. A permissão só passa a
    valer após REINICIAR o app.
    """
    if sys.platform != "darwin":
        return True
    try:
        import HIServices
    except Exception:  # noqa: BLE001
        logger.debug("HIServices indisponível; não dá para checar Acessibilidade", exc_info=True)
        return False
    if HIServices.AXIsProcessTrusted():
        return True
    try:
        HIServices.AXIsProcessTrustedWithOptions({HIServices.kAXTrustedCheckOptionPrompt: True})
        logger.warning(
            "Permissão de Acessibilidade ausente: aceite o diálogo do macOS "
            "(ou vá em Ajustes → Privacidade e Segurança → Acessibilidade), "
            "LIGUE a entrada deste app e REINICIE o app."
        )
    except Exception:  # noqa: BLE001
        logger.warning("Falha ao solicitar a permissão de Acessibilidade", exc_info=True)
    return False
