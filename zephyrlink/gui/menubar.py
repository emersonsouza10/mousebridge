"""Ícone na barra de menus do macOS (app em background).

Adiciona um ``NSStatusItem`` nativo à mesma ``NSApplication`` que o Tkinter já
cria, permitindo rodar como app de barra de menus: sem ícone no Dock, com a
janela detalhada disponível sob demanda. Os cliques do menu disparam na main
thread (dentro do mainloop do Tk), então podem tocar o Tk com segurança.

No-op fora do macOS ou se o AppKit não estiver disponível — nesse caso a GUI
segue como janela normal.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

logger = logging.getLogger(__name__)


class MenuBar:
    """Guarda referências vivas do status item e atualiza o menu."""

    def __init__(self, item: Any, target: Any, status_item: Any, start_item: Any) -> None:
        self._item = item
        self._target = target
        self._status_item = status_item
        self._start_item = start_item

    def set_state(self, running: bool, status_text: str) -> None:
        """Atualiza o rótulo de status e o item Iniciar/Parar (main thread)."""
        try:
            self._status_item.setTitle_(status_text)
            self._start_item.setTitle_("Parar" if running else "Iniciar")
        except Exception:  # noqa: BLE001
            logger.debug("Falha ao atualizar o menu da barra", exc_info=True)


def install_menubar(gui: Any, icon: str = "⚡") -> MenuBar | None:
    """Instala o ícone na barra de menus e some com o ícone do Dock.

    ``gui`` precisa expor ``_menubar_toggle_start()``, ``_menubar_show_window()``
    e ``_menubar_quit()``. Retorna um ``MenuBar`` (para reter/atualizar) ou
    ``None`` se indisponível.
    """
    if sys.platform != "darwin":
        return None
    try:
        import AppKit
        import objc
        from Foundation import NSObject
    except Exception:  # noqa: BLE001
        logger.warning("AppKit indisponível; sem ícone na barra de menus", exc_info=True)
        return None

    try:

        class _Target(NSObject):
            def initWithGUI_(self, the_gui):  # noqa: N802
                this = objc.super(_Target, self).init()
                if this is None:
                    return None
                this._gui = the_gui
                return this

            def onToggleStart_(self, _sender):  # noqa: N802
                self._gui._menubar_toggle_start()

            def onShow_(self, _sender):  # noqa: N802
                self._gui._menubar_show_window()

            def onQuit_(self, _sender):  # noqa: N802
                self._gui._menubar_quit()

        target = _Target.alloc().initWithGUI_(gui)

        status_bar = AppKit.NSStatusBar.systemStatusBar()
        item = status_bar.statusItemWithLength_(AppKit.NSVariableStatusItemLength)
        button = item.button()
        if button is not None:
            button.setTitle_(icon)
        button.setToolTip_("ZephyrLink") if button is not None else None

        menu = AppKit.NSMenu.alloc().init()
        menu.setAutoenablesItems_(False)

        status_item = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Parado", "", ""
        )
        status_item.setEnabled_(False)
        menu.addItem_(status_item)
        menu.addItem_(AppKit.NSMenuItem.separatorItem())

        start_item = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Iniciar", "onToggleStart:", ""
        )
        start_item.setTarget_(target)
        menu.addItem_(start_item)

        show_item = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Mostrar janela", "onShow:", ""
        )
        show_item.setTarget_(target)
        menu.addItem_(show_item)

        menu.addItem_(AppKit.NSMenuItem.separatorItem())
        quit_item = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Sair", "onQuit:", "q"
        )
        quit_item.setTarget_(target)
        menu.addItem_(quit_item)

        item.setMenu_(menu)

        # App de barra de menus: sem ícone no Dock.
        AppKit.NSApp.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)

        logger.info("Ícone na barra de menus instalado (app em background)")
        return MenuBar(item, target, status_item, start_item)
    except Exception:  # noqa: BLE001
        logger.warning("Não foi possível instalar o ícone na barra de menus", exc_info=True)
        return None
