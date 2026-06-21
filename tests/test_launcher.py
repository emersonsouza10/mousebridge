import asyncio
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from zephyrlink.config import ConfigError, LaunchableApp, LauncherConfig, load_config
from zephyrlink.config.persist import save_launcher
from zephyrlink.config.settings import build_config
from zephyrlink.launcher.audit import AuditLog
from zephyrlink.launcher.catalog import AppCatalog, current_platform
from zephyrlink.launcher.integrity import IntegrityError, verify_executable
from zephyrlink.launcher.validate import ArgError, validate_args
from zephyrlink.transport import MsgType


class LauncherConfigTest(unittest.TestCase):
    def test_default_disabled_and_empty(self) -> None:
        config = build_config({})
        self.assertFalse(config.launcher.enabled)
        self.assertEqual(config.launcher.apps, ())

    def test_parses_apps(self) -> None:
        config = build_config(
            {"launcher": {"enabled": True, "apps": [
                {"id": "notepad", "label": "Bloco", "command": ["notepad.exe"], "platform": "windows"},
                {"id": "calc", "command": "calc.exe"},
            ]}}
        )
        self.assertTrue(config.launcher.enabled)
        self.assertEqual(len(config.launcher.apps), 2)
        notepad = config.launcher.apps[0]
        self.assertEqual(notepad.command, ("notepad.exe",))
        self.assertEqual(notepad.platform, "windows")
        self.assertEqual(config.launcher.apps[1].label, "calc")

    def test_rejects_missing_id(self) -> None:
        with self.assertRaises(ConfigError):
            build_config({"launcher": {"apps": [{"command": ["x"]}]}})

    def test_rejects_empty_command(self) -> None:
        with self.assertRaises(ConfigError):
            build_config({"launcher": {"apps": [{"id": "a", "command": []}]}})

    def test_rejects_empty_executable(self) -> None:
        with self.assertRaises(ConfigError):
            build_config({"launcher": {"apps": [{"id": "a", "command": ["", "x"]}]}})

    def test_allows_empty_arg_after_executable(self) -> None:
        config = build_config({"launcher": {"apps": [
            {"id": "a", "command": ["cmd", "/c", "start", ""]},
        ]}})
        self.assertEqual(config.launcher.apps[0].command, ("cmd", "/c", "start", ""))

    def test_rejects_duplicate_id(self) -> None:
        with self.assertRaises(ConfigError):
            build_config({"launcher": {"apps": [
                {"id": "a", "command": ["x"]}, {"id": "a", "command": ["y"]},
            ]}})

    def test_rejects_unknown_platform(self) -> None:
        with self.assertRaises(ConfigError):
            build_config({"launcher": {"apps": [{"id": "a", "command": ["x"], "platform": "bsd"}]}})


class AppCatalogTest(unittest.TestCase):
    def _catalog(self, enabled: bool = True) -> AppCatalog:
        config = build_config({"launcher": {"enabled": enabled, "apps": [
            {"id": "here", "label": "Aqui", "command": ["x"], "platform": current_platform()},
            {"id": "anywhere", "label": "Qualquer", "command": ["y"]},
            {"id": "elsewhere", "label": "Outro", "command": ["z"],
             "platform": "macos" if current_platform() != "macos" else "linux"},
        ]}})
        return AppCatalog.from_config(config.launcher)

    def test_filters_by_platform(self) -> None:
        catalog = self._catalog()
        self.assertIsNotNone(catalog.resolve("here"))
        self.assertIsNotNone(catalog.resolve("anywhere"))
        self.assertIsNone(catalog.resolve("elsewhere"))

    def test_disabled_config_yields_empty(self) -> None:
        catalog = AppCatalog.from_config(LauncherConfig(enabled=False))
        self.assertEqual(catalog.entries(), [])

    def test_catalog_message_exposes_arg_metadata(self) -> None:
        message = self._catalog().catalog_message()
        self.assertEqual(message.type, MsgType.LAUNCH_CATALOG)
        apps = message.data["apps"]
        self.assertEqual({k for app in apps for k in app}, {"id", "label", "accepts_args", "arg_kind"})
        self.assertIn("here", {a["id"] for a in apps})


class ArgConfigTest(unittest.TestCase):
    def test_rejects_accepts_args_without_kind(self) -> None:
        with self.assertRaises(ConfigError):
            build_config({"launcher": {"apps": [
                {"id": "a", "command": ["x"], "accepts_args": True},
            ]}})

    def test_path_kind_requires_dirs(self) -> None:
        with self.assertRaises(ConfigError):
            build_config({"launcher": {"apps": [
                {"id": "a", "command": ["x"], "accepts_args": True, "arg_kind": "path_in_dir"},
            ]}})

    def test_enum_kind_requires_values(self) -> None:
        with self.assertRaises(ConfigError):
            build_config({"launcher": {"apps": [
                {"id": "a", "command": ["x"], "accepts_args": True, "arg_kind": "enum"},
            ]}})

    def test_rate_limit_must_be_positive(self) -> None:
        with self.assertRaises(ConfigError):
            build_config({"launcher": {"rate_limit_per_min": 0}})


class ValidateArgsTest(unittest.TestCase):
    def test_none_rejects_args(self) -> None:
        app = LaunchableApp(id="a", label="a", command=("x",))
        self.assertEqual(validate_args(app, []), [])
        with self.assertRaises(ArgError):
            validate_args(app, ["surpresa"])

    def test_url_scheme_and_host(self) -> None:
        app = LaunchableApp(id="a", label="a", command=("x",), accepts_args=True, arg_kind="url",
                            allowed_url_schemes=("https",), allowed_url_hosts=("*.empresa.com",))
        self.assertEqual(validate_args(app, ["https://app.empresa.com/x"]),
                         ["https://app.empresa.com/x"])
        with self.assertRaises(ArgError):
            validate_args(app, ["http://app.empresa.com"])
        with self.assertRaises(ArgError):
            validate_args(app, ["https://evil.com"])
        with self.assertRaises(ArgError):
            validate_args(app, ["--proxy=1.2.3.4"])

    def test_enum(self) -> None:
        app = LaunchableApp(id="a", label="a", command=("x",), accepts_args=True, arg_kind="enum",
                            enum_values=("prod", "homolog"))
        self.assertEqual(validate_args(app, ["prod"]), ["prod"])
        with self.assertRaises(ArgError):
            validate_args(app, ["dev"])

    def test_path_in_dir_blocks_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            app = LaunchableApp(id="a", label="a", command=("x",), accepts_args=True,
                                arg_kind="path_in_dir", allowed_dirs=(str(base),))
            inside = base / "rel.txt"
            self.assertEqual(validate_args(app, [str(inside)]), [str(inside)])
            with self.assertRaises(ArgError):
                validate_args(app, [str(base / ".." / "fora.txt")])

    def test_single_arg_only(self) -> None:
        app = LaunchableApp(id="a", label="a", command=("x",), accepts_args=True, arg_kind="enum",
                            enum_values=("a", "b"))
        with self.assertRaises(ArgError):
            validate_args(app, ["a", "b"])


class IntegrityTest(unittest.TestCase):
    def _app(self, exe: str, sha256: str | None) -> LaunchableApp:
        return LaunchableApp(id="a", label="a", command=(exe,), sha256=sha256)

    def test_no_hash_skips(self) -> None:
        asyncio.run(verify_executable(self._app("/no/such/bin", None)))

    def test_matching_hash_passes(self) -> None:
        with tempfile.NamedTemporaryFile("wb", suffix=".bin", delete=False) as tmp:
            tmp.write(b"conteudo do binario")
            path = tmp.name
        self.addCleanup(Path(path).unlink)
        digest = hashlib.sha256(b"conteudo do binario").hexdigest()
        asyncio.run(verify_executable(self._app(path, digest)))

    def test_mismatched_hash_raises(self) -> None:
        with tempfile.NamedTemporaryFile("wb", suffix=".bin", delete=False) as tmp:
            tmp.write(b"trocado")
            path = tmp.name
        self.addCleanup(Path(path).unlink)
        with self.assertRaises(IntegrityError):
            asyncio.run(verify_executable(self._app(path, "0" * 64)))

    def test_missing_executable_raises(self) -> None:
        with self.assertRaises(IntegrityError):
            asyncio.run(verify_executable(self._app("/no/such/binary", "0" * 64)))

    def test_config_rejects_bad_sha(self) -> None:
        with self.assertRaises(ConfigError):
            build_config({"launcher": {"apps": [{"id": "a", "command": ["x"], "sha256": "xyz"}]}})

    def test_config_normalizes_sha(self) -> None:
        config = build_config({"launcher": {"apps": [
            {"id": "a", "command": ["x"], "sha256": "AB" * 32},
        ]}})
        self.assertEqual(config.launcher.apps[0].sha256, "ab" * 32)


class ControlConfigTest(unittest.TestCase):
    def test_control_port_default(self) -> None:
        self.assertEqual(build_config({}).network.control_port, 50512)

    def test_control_port_override_and_validation(self) -> None:
        self.assertEqual(
            build_config({"network": {"control_port": 51000}}).network.control_port, 51000
        )
        with self.assertRaises(ConfigError):
            build_config({"network": {"control_port": 70000}})


class LaunchCommandParserTest(unittest.TestCase):
    def test_parses_launch_subcommand(self) -> None:
        from zephyrlink.__main__ import build_parser

        args = build_parser().parse_args(
            ["launch", "--client", "192.168.10.121", "--app", "navegador",
             "--arg", "https://x.empresa.com"]
        )
        self.assertEqual(args.command, "launch")
        self.assertEqual(args.client, "192.168.10.121")
        self.assertEqual(args.app, "navegador")
        self.assertEqual(args.arg, ["https://x.empresa.com"])

    def test_launch_requires_client_and_app(self) -> None:
        from zephyrlink.__main__ import build_parser

        with self.assertRaises(SystemExit):
            build_parser().parse_args(["launch", "--app", "navegador"])


class PersistTest(unittest.TestCase):
    def test_round_trip_preserves_other_sections(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.yaml"
            path.write_text("role: client\nsecurity:\n  shared_key: segredo\n", encoding="utf-8")
            apps = [
                {"id": "notepad", "label": "Bloco", "command": ["notepad.exe"], "platform": "windows"},
                {"id": "dash", "label": "Dash", "command": ["firefox.exe", "https://x/#a&b"],
                 "platform": None},
            ]
            save_launcher(str(path), enabled=True, apps=apps)
            config = load_config(str(path))
            self.assertEqual(config.security.shared_key, "segredo")
            self.assertTrue(config.launcher.enabled)
            self.assertEqual(len(config.launcher.apps), 2)
            self.assertEqual(config.launcher.apps[1].command, ("firefox.exe", "https://x/#a&b"))

    def test_creates_file_when_absent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.yaml"
            save_launcher(str(path), enabled=True, apps=[
                {"id": "a", "label": "A", "command": ["x"]},
            ])
            self.assertTrue(path.exists())
            self.assertEqual(load_config(str(path)).launcher.apps[0].id, "a")


class AuditTest(unittest.TestCase):
    def test_disabled_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "audit.jsonl"
            asyncio.run(AuditLog(None).write({"decision": "completed"}))
            self.assertFalse(target.exists())

    def test_appends_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "audit.jsonl"
            log = AuditLog(str(target))

            async def go() -> None:
                await log.write({"decision": "rejected", "reason": "replay"})
                await log.write({"decision": "completed", "pid": 42})

            asyncio.run(go())
            lines = target.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 2)
            first = json.loads(lines[0])
            self.assertEqual(first["decision"], "rejected")
            self.assertIn("ts", first)
            self.assertEqual(json.loads(lines[1])["pid"], 42)


if __name__ == "__main__":
    unittest.main()
