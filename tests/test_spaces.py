"""Testes das ações de Spaces/Mission Control do macOS e do mapa mouse_actions."""

from __future__ import annotations

import unittest
from unittest import mock

from pynput import keyboard

from zephyrlink.config.settings import ConfigError, VALID_MOUSE_ACTIONS, build_config
from zephyrlink.keyboard import spaces


class _FakeController:
    """Controlador de teclado falso que registra press/release em ordem."""

    def __init__(self, fail_on=None) -> None:
        self.events: list[tuple[str, object]] = []
        self._fail_on = fail_on

    def press(self, key) -> None:
        if self._fail_on is not None and key == self._fail_on:
            raise RuntimeError("sem permissão de acessibilidade")
        self.events.append(("press", key))

    def release(self, key) -> None:
        self.events.append(("release", key))


def _run(action, *, macos=True, fail_on=None):
    fc = _FakeController(fail_on=fail_on)
    with mock.patch.object(spaces, "_controller", fc), \
         mock.patch.object(spaces, "IS_MACOS", macos):
        ok = spaces.run_space_action(action)
    return ok, fc.events


class MouseActionsConfigTest(unittest.TestCase):
    def test_default_is_empty(self):
        self.assertEqual(build_config({}).mouse_actions, {})

    def test_parses_valid_mapping(self):
        cfg = build_config({"mouse_actions": {"button9": "mac_space_right",
                                              "button8": "mac_space_left"}})
        self.assertEqual(cfg.mouse_actions,
                         {"button9": "mac_space_right", "button8": "mac_space_left"})

    def test_action_is_case_insensitive(self):
        cfg = build_config({"mouse_actions": {"button9": "MAC_SPACE_RIGHT"}})
        self.assertEqual(cfg.mouse_actions["button9"], "mac_space_right")

    def test_rejects_unknown_action(self):
        with self.assertRaises(ConfigError):
            build_config({"mouse_actions": {"button9": "teleport"}})

    def test_rejects_non_mapping(self):
        with self.assertRaises(ConfigError):
            build_config({"mouse_actions": ["button9", "mac_space_right"]})


class SpaceActionTest(unittest.TestCase):
    def test_all_actions_are_known(self):
        for action in VALID_MOUSE_ACTIONS:
            self.assertIsNotNone(spaces._keys_for(action), action)

    def test_arrow_actions_press_ctrl_first_and_release_all(self):
        for action, arrow in (
            ("mac_space_left", keyboard.Key.left),
            ("mac_space_right", keyboard.Key.right),
            ("mac_mission_control", keyboard.Key.up),
        ):
            ok, events = _run(action)
            self.assertTrue(ok, action)
            self.assertEqual(
                events,
                [("press", keyboard.Key.ctrl), ("press", arrow),
                 ("release", arrow), ("release", keyboard.Key.ctrl)],
                action,
            )

    def test_number_actions_use_ctrl_plus_key(self):
        for action in ("mac_space_1", "mac_space_2", "mac_space_3", "mac_space_4"):
            ok, events = _run(action)
            self.assertTrue(ok, action)
            self.assertEqual(events[0], ("press", keyboard.Key.ctrl), action)
            # Control é a primeira a ser pressionada e a última a ser solta.
            self.assertEqual(events[-1], ("release", keyboard.Key.ctrl), action)
            self.assertEqual(len(events), 4, action)

    def test_noop_outside_macos(self):
        ok, events = _run("mac_space_right", macos=False)
        self.assertFalse(ok)
        self.assertEqual(events, [])

    def test_unknown_action_does_not_raise(self):
        ok, events = _run("mac_space_9")
        self.assertFalse(ok)
        self.assertEqual(events, [])

    def test_ctrl_released_even_if_press_fails(self):
        # Falha ao pressionar a seta (ex.: permissão ausente): o Control, já
        # pressionado, DEVE ser solto — nunca fica virtualmente preso.
        ok, events = _run("mac_space_right", fail_on=keyboard.Key.right)
        self.assertFalse(ok)
        self.assertIn(("press", keyboard.Key.ctrl), events)
        self.assertIn(("release", keyboard.Key.ctrl), events)
        # Nada deve continuar "pressionado" sem o release correspondente.
        pressed = [k for kind, k in events if kind == "press"]
        released = [k for kind, k in events if kind == "release"]
        for key in pressed:
            self.assertIn(key, released, key)


class ClientButtonRoutingTest(unittest.TestCase):
    def _client(self, mouse_actions):
        import zephyrlink.client.client as clientmod

        c = clientmod.ZephyrLinkClient.__new__(clientmod.ZephyrLinkClient)
        c._mouse_actions = dict(mouse_actions)
        c._mouse = mock.Mock()
        return clientmod, c

    def test_macos_mapped_button_runs_action_on_press_only(self):
        clientmod, c = self._client({"button9": "mac_space_right"})
        with mock.patch.object(clientmod, "IS_MACOS", True), \
             mock.patch.object(clientmod, "run_space_action") as rsa:
            c._handle_button("button9", True)   # press -> dispara
            c._handle_button("button9", False)  # release -> ignorado
        rsa.assert_called_once_with("mac_space_right")
        c._mouse.button.assert_not_called()

    def test_unmapped_button_is_injected(self):
        clientmod, c = self._client({"button9": "mac_space_right"})
        with mock.patch.object(clientmod, "IS_MACOS", True), \
             mock.patch.object(clientmod, "run_space_action") as rsa:
            c._handle_button("left", True)
        rsa.assert_not_called()
        c._mouse.button.assert_called_once_with("left", True)

    def test_mapping_ignored_outside_macos(self):
        clientmod, c = self._client({"button9": "mac_space_right"})
        with mock.patch.object(clientmod, "IS_MACOS", False), \
             mock.patch.object(clientmod, "run_space_action") as rsa:
            c._handle_button("button9", True)
        rsa.assert_not_called()
        c._mouse.button.assert_called_once_with("button9", True)

    def test_rapid_presses_fire_each_time(self):
        clientmod, c = self._client({"button9": "mac_space_right"})
        with mock.patch.object(clientmod, "IS_MACOS", True), \
             mock.patch.object(clientmod, "run_space_action") as rsa:
            for _ in range(5):
                c._handle_button("button9", True)
                c._handle_button("button9", False)
        self.assertEqual(rsa.call_count, 5)


if __name__ == "__main__":
    unittest.main()
