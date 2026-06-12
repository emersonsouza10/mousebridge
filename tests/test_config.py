import tempfile
import unittest
from pathlib import Path

from mousebridge.config import ConfigError, load_config
from mousebridge.config.settings import build_config


class DefaultsTest(unittest.TestCase):
    def test_no_file_uses_defaults(self) -> None:
        config = load_config(None)
        self.assertEqual(config.role, "server")
        self.assertEqual(config.network.tcp_port, 50510)
        self.assertEqual(config.layout.edge, "right")
        self.assertTrue(config.clipboard.enabled)

    def test_empty_dict_uses_defaults(self) -> None:
        config = build_config({})
        self.assertEqual(config.security.shared_key, "change-me")


class LoadYamlTest(unittest.TestCase):
    def _write(self, content: str) -> Path:
        tmp = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False, encoding="utf-8")
        tmp.write(content)
        tmp.close()
        self.addCleanup(Path(tmp.name).unlink)
        return Path(tmp.name)

    def test_partial_yaml_overrides(self) -> None:
        path = self._write(
            "role: client\n"
            "layout:\n  edge: left\n"
            "security:\n  shared_key: segredo\n  allowed_hosts: ['10.0.0.*']\n"
        )
        config = load_config(path)
        self.assertEqual(config.role, "client")
        self.assertEqual(config.layout.edge, "left")
        self.assertEqual(config.security.shared_key, "segredo")
        self.assertEqual(config.security.allowed_hosts, ("10.0.0.*",))
        # não declarado mantém o padrão
        self.assertEqual(config.network.tcp_port, 50510)

    def test_missing_file_raises(self) -> None:
        with self.assertRaises(ConfigError):
            load_config("/caminho/que/nao/existe.yaml")

    def test_invalid_yaml_raises(self) -> None:
        path = self._write("role: [unclosed")
        with self.assertRaises(ConfigError):
            load_config(path)


class ValidationTest(unittest.TestCase):
    def test_invalid_role(self) -> None:
        with self.assertRaises(ConfigError):
            build_config({"role": "peer"})

    def test_invalid_edge(self) -> None:
        with self.assertRaises(ConfigError):
            build_config({"layout": {"edge": "diagonal"}})

    def test_invalid_port(self) -> None:
        with self.assertRaises(ConfigError):
            build_config({"network": {"tcp_port": 99999}})

    def test_heartbeat_timeout_must_exceed_interval(self) -> None:
        with self.assertRaises(ConfigError):
            build_config({"network": {"heartbeat_interval": 5.0, "heartbeat_timeout": 2.0}})

    def test_tls_requires_cert_and_key(self) -> None:
        with self.assertRaises(ConfigError):
            build_config({"security": {"use_tls": True}})

    def test_empty_shared_key_rejected(self) -> None:
        with self.assertRaises(ConfigError):
            build_config({"security": {"shared_key": ""}})


if __name__ == "__main__":
    unittest.main()
