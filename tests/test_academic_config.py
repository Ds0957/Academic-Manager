"""Ruta de datos y de Drive configurables: precedencia, archivo por usuario y comportamiento previo."""
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from academic_config import paths

ROOT = Path(__file__).resolve().parents[1]
WINDOWS_DATA = r"C:\AcademicManagerData"
VARIABLES = ("ACADEMIC_MANAGER_DATA", "ACADEMIC_MANAGER_RUNTIME", "ACADEMIC_MANAGER_CONFIG")


class ConfigCase(unittest.TestCase):
    """Cada prueba usa su propio archivo de configuración; nunca el %APPDATA% o ~/.config real."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.config = self.root / "config" / "config.json"
        clean = {k: v for k, v in os.environ.items() if k not in VARIABLES}
        clean["ACADEMIC_MANAGER_CONFIG"] = str(self.config)
        self.env = patch.dict(os.environ, clean, clear=True)
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def write(self, content):
        self.config.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, (dict, list)):
            content = json.dumps(content)
        self.config.write_text(content, encoding="utf-8")


class ResolverTests(ConfigCase):
    def test_without_file_or_variable_returns_the_module_default(self):
        self.assertEqual(paths.runtime(default=WINDOWS_DATA), WINDOWS_DATA)
        self.assertIsNone(paths.runtime(default=None))
        self.assertEqual(paths.drive_root(Path("G:/x")), Path("G:/x"))
        self.assertEqual(paths.load_config(), {})

    def test_precedence_explicit_then_variable_then_file_then_default(self):
        self.write({"runtime": str(self.root / "desde-archivo")})
        self.assertEqual(paths.runtime(default=WINDOWS_DATA), str(self.root / "desde-archivo"))
        os.environ["ACADEMIC_MANAGER_DATA"] = "/var/desde-variable"
        self.assertEqual(paths.runtime(env=("ACADEMIC_MANAGER_DATA",), default=WINDOWS_DATA),
                         "/var/desde-variable")
        # Una variable que el módulo no lee no cambia nada.
        self.assertEqual(paths.runtime(env=("ACADEMIC_MANAGER_RUNTIME",), default=WINDOWS_DATA),
                         str(self.root / "desde-archivo"))
        self.assertEqual(paths.runtime("/explicita", env=("ACADEMIC_MANAGER_DATA",)), "/explicita")
        # Como os.environ.get: una variable vacía sigue contando como indicada.
        os.environ["ACADEMIC_MANAGER_DATA"] = ""
        self.assertEqual(paths.runtime(env=("ACADEMIC_MANAGER_DATA",), default=WINDOWS_DATA), "")

    def test_drive_root_from_file(self):
        self.write({"drive_root": str(self.root / "drive")})
        self.assertEqual(paths.drive_root(Path("G:/x")), self.root / "drive")
        self.assertEqual(paths.runtime(default=None), None)

    def test_config_file_location_per_user(self):
        del os.environ["ACADEMIC_MANAGER_CONFIG"]
        os.environ["APPDATA"] = str(self.root / "Roaming")
        os.environ["HOME"] = str(self.root / "home")
        with patch.object(paths, "_is_windows", return_value=True):
            self.assertEqual(paths.windows_default(), WINDOWS_DATA)
            self.assertEqual(paths.config_path(), self.root / "Roaming" / "AcademicManager" / "config.json")
        with patch.object(paths, "_is_windows", return_value=False):
            self.assertIsNone(paths.windows_default())
            self.assertEqual(paths.config_path(),
                             self.root / "home" / ".config" / "academic-manager" / "config.json")
        os.environ["ACADEMIC_MANAGER_CONFIG"] = str(self.config)
        self.assertEqual(paths.config_path(), self.config)

    def test_invalid_file_fails_closed_naming_the_file(self):
        cases = {"{no es json": "JSON", "[1, 2]": "objeto",
                 json.dumps({"runtime": "/x", "otra": 1}): "otra",
                 json.dumps({"runtime": "relativa/datos"}): "absoluta",
                 json.dumps({"drive_root": 7}): "absoluta",
                 json.dumps({"runtime": ""}): "absoluta"}
        for content, reason in cases.items():
            with self.subTest(content=content):
                self.write(content)
                for call in (lambda: paths.runtime(default=WINDOWS_DATA),
                             lambda: paths.runtime("/explicita"),
                             lambda: paths.drive_root(Path("G:/x"))):
                    with self.assertRaises(paths.ConfigError) as caught:
                        call()
                    self.assertIn(str(self.config), str(caught.exception))
                    self.assertIn(reason, str(caught.exception))

    def test_unreadable_file_fails_closed(self):
        self.config.mkdir(parents=True)  # Existe pero no puede leerse como archivo.
        with self.assertRaisesRegex(paths.ConfigError, "no puede leerse"):
            paths.runtime(default=WINDOWS_DATA)

    def test_byte_order_mark_from_notepad_is_accepted(self):
        self.config.parent.mkdir(parents=True)
        self.config.write_bytes(b"\xef\xbb\xbf" + json.dumps({"runtime": "/datos"}).encode())
        self.assertEqual(paths.runtime(), "/datos")


class CliTests(ConfigCase):
    """Cada CLI: por defecto igual que antes, la variable de siempre gana al archivo y --runtime a todo."""

    def file_runtime(self):
        target = self.root / "datos-archivo"
        self.write({"runtime": str(target)})
        return target

    def access(self, *argv):
        from academic_access import __main__ as cli
        seen = []
        out = io.StringIO()
        with (patch.object(cli, "dispatch", side_effect=lambda runtime, a: seen.append(runtime) or {}),
              redirect_stdout(out)):
            code = cli.main(list(argv))
        return code, json.loads(out.getvalue()), seen

    def test_access(self):
        code, _, seen = self.access("health")
        self.assertEqual((code, seen), (0, [Path(WINDOWS_DATA).resolve()]))
        target = self.file_runtime()
        self.assertEqual(self.access("health")[2], [target])
        os.environ["ACADEMIC_MANAGER_DATA"] = str(self.root / "var")
        self.assertEqual(self.access("health")[2], [self.root / "var"])
        self.write("{roto")
        code, payload, seen = self.access("health")
        self.assertEqual((code, payload["ok"], payload["error"]["code"], seen), (2, False, "INVALID_CONFIG", []))
        self.assertIn(str(self.config), payload["error"]["message"])
        self.assertIn("suggested_action", payload["error"])

    def operations(self, *argv, chat_action=False):
        from academic_operations import __main__ as cli
        seen = []
        record = lambda runtime, *a, **k: seen.append(runtime) or {}
        target = (patch.object(cli.chat, "preview", side_effect=record) if chat_action
                  else patch.object(cli, "preview", side_effect=record))
        out = io.StringIO()
        with target, redirect_stdout(out):
            code = cli.main(list(argv))
        return code, json.loads(out.getvalue()), seen

    def test_operations(self):
        for chat_action, argv in ((False, ("preview", "--job", "J1")),
                                  (True, ("note", "--materia", "MAT-A", "--texto", "x"))):
            with self.subTest(chat=chat_action):
                code, payload, seen = self.operations(*argv, chat_action=chat_action)
                self.assertEqual((code, payload["error"]["message"], seen), (2, "Indique --runtime.", []))
                with patch.object(paths, "_is_windows", return_value=True):
                    seen = self.operations(*argv, chat_action=chat_action)[2]
                self.assertEqual(seen, [Path(WINDOWS_DATA).resolve()])
                target = self.file_runtime()
                self.assertEqual(self.operations(*argv, chat_action=chat_action)[2], [target])
                os.environ["ACADEMIC_MANAGER_DATA"] = str(self.root / "var")
                self.assertEqual(self.operations(*argv, chat_action=chat_action)[2], [self.root / "var"])
                explicit = self.operations(*argv, "--runtime", str(self.root / "arg"),
                                           chat_action=chat_action)[2]
                self.assertEqual(explicit, [self.root / "arg"])
                self.write({"runtime": "relativa"})
                code, payload, seen = self.operations(*argv, chat_action=chat_action)
                self.assertEqual((code, payload["error"]["code"], seen), (2, "INVALID_CONFIG", []))
                self.assertIn(str(self.config), payload["error"]["message"])
                del os.environ["ACADEMIC_MANAGER_DATA"]
                self.config.unlink()

    def run_text_cli(self, module, target, argv, result):
        seen = []
        out, err = io.StringIO(), io.StringIO()
        with (patch.object(module, target, side_effect=lambda first, *a, **k: seen.append(first) or result),
              redirect_stdout(out), redirect_stderr(err)):
            code = module.main(list(argv))
        return code, seen, err.getvalue()

    def test_store(self):
        from academic_store import __main__ as cli
        call = lambda: self.run_text_cli(cli, "info", ["info"], {"ok": True})
        self.assertEqual(call()[:2], (0, [Path(WINDOWS_DATA).resolve() / cli.DB_NAME]))
        target = self.file_runtime()
        self.assertEqual(call()[1], [target / cli.DB_NAME])
        os.environ["ACADEMIC_MANAGER_DATA"] = str(self.root / "var")
        self.assertEqual(call()[1], [self.root / "var" / cli.DB_NAME])
        self.write([])
        code, seen, err = call()
        self.assertEqual((code, seen), (1, []))
        self.assertIn(f"Academic Store: Configuración inválida en {self.config}", err)

    def test_knowledge(self):
        from academic_knowledge import __main__ as cli
        call = lambda: self.run_text_cli(cli, "overview", ["status"], {})
        self.assertEqual(call()[:2], (0, [Path(WINDOWS_DATA).resolve()]))
        target = self.file_runtime()
        self.assertEqual(call()[1], [target])
        os.environ["ACADEMIC_MANAGER_DATA"] = str(self.root / "var")
        self.assertEqual(call()[1], [self.root / "var"])
        self.write({"runtime": 3})
        code, seen, err = call()
        self.assertEqual((code, seen), (1, []))
        self.assertIn(f"Academic Knowledge: Configuración inválida en {self.config}", err)

    def test_canvas_sync(self):
        from canvas_sync import __main__ as cli
        call = lambda *extra: self.run_text_cli(cli, "status", ["status", *extra], True)
        code, seen, err = call()
        self.assertEqual((code, seen), (1, []))
        self.assertIn("Indique runtime local con --runtime.", err)
        with patch.object(paths, "_is_windows", return_value=True):
            self.assertEqual(call()[1], [cli.runtime_root(WINDOWS_DATA)])
        target = self.file_runtime()
        self.assertEqual(call()[1], [target])
        # Como los .cmd, ACADEMIC_MANAGER_DATA manda sobre el archivo de configuración.
        os.environ["ACADEMIC_MANAGER_DATA"] = str(self.root / "var")
        self.assertEqual(call()[1], [self.root / "var"])
        self.assertEqual(call("--runtime", str(self.root / "arg"))[1], [self.root / "arg"])
        self.write({"runtime": str(target), "token": "x"})
        code, seen, err = call()
        self.assertEqual((code, seen), (1, []))
        self.assertIn(f"Canvas Sync: Configuración inválida en {self.config}", err)

    def test_projects(self):
        from academic_projects import __main__ as cli
        materia = {"materia_id": "MAT-SER", "nombre": "SER", "semestre_id": "2026-2", "bloques": [1]}
        call = lambda *extra: self.run_text_cli(cli, "subjects", ["materia", "--materia", "MAT-SER", *extra],
                                                {"materias": [materia]})
        self.assertEqual(call()[:2], (0, [Path(WINDOWS_DATA)]))
        target = self.file_runtime()
        self.assertEqual(call()[1], [target])
        os.environ["ACADEMIC_MANAGER_DATA"] = str(self.root / "var")  # Manda sobre el archivo.
        self.assertEqual(call()[1], [self.root / "var"])
        self.assertEqual(call("--runtime", str(self.root / "arg"))[1], [self.root / "arg"])
        self.assertEqual(cli.render("coordinador"), cli.render("coordinador", runtime=None))
        self.write("{")
        code, seen, err = call()
        self.assertEqual((code, seen), (2, []))
        self.assertIn(f"NO_GENERADO: Configuración inválida en {self.config}", err)

    def test_capture(self):
        from capture import capture as cli
        def call(*extra):
            err = io.StringIO()
            with redirect_stdout(io.StringIO()), redirect_stderr(err):
                return cli.main(["--status", "all", *extra]), err.getvalue()
        self.assertIsNone(cli.parser().parse_args(["--status", "all"]).runtime)
        code, err = call()
        self.assertEqual(code, 1)
        self.assertIn("Indique --runtime con una carpeta absoluta local.", err)
        target = self.file_runtime()
        self.assertEqual(call()[0], 0)
        self.assertTrue((target / "procesamiento").is_dir())
        os.environ["ACADEMIC_MANAGER_DATA"] = str(self.root / "datos")  # Manda sobre el archivo.
        self.assertEqual(call()[0], 0)
        self.assertTrue((self.root / "datos" / "procesamiento").is_dir())
        os.environ["ACADEMIC_MANAGER_RUNTIME"] = str(self.root / "var")
        os.environ["ACADEMIC_MANAGER_DATA"] = str(self.root / "otra")  # RUNTIME sigue primero.
        self.assertEqual(call()[0], 0)
        self.assertTrue((self.root / "var" / "procesamiento").is_dir())
        self.assertFalse((self.root / "otra").exists())
        self.assertEqual(call("--runtime", str(self.root / "arg"))[0], 0)
        self.assertTrue((self.root / "arg" / "procesamiento").is_dir())
        self.write({"runtime": "nada"})
        code, err = call()
        self.assertEqual(code, 1)
        self.assertIn(f"Capture: Configuración inválida en {self.config}", err)

    def test_capture_windows_default(self):
        from capture import capture as cli
        seen = []
        with (patch.object(paths, "_is_windows", return_value=True),
              patch.object(cli, "Config", side_effect=lambda runtime: seen.append(runtime) or 1 / 0),
              redirect_stderr(io.StringIO())):
            cli.main(["--status", "all"])
        self.assertEqual(seen, [Path(WINDOWS_DATA)])

    def test_capture_config_default(self):
        from capture.config import Config
        from capture.models import CaptureError
        with self.assertRaisesRegex(CaptureError, "Fuera de Windows"):
            Config.default()
        target = self.file_runtime()
        self.assertEqual(Config.default().runtime, target)
        os.environ["ACADEMIC_MANAGER_RUNTIME"] = str(self.root / "var")
        self.assertEqual(Config.default().runtime, self.root / "var")
        self.write("[]")
        with self.assertRaisesRegex(CaptureError, "Configuración inválida"):
            Config.default()


class DriveRootTests(ConfigCase):
    def drive_root_in_new_process(self):
        code = "from academic_knowledge import index; print(index.DRIVE_ROOT)"
        done = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=dict(os.environ),
                              capture_output=True, text=True, check=True)
        return done.stdout.strip()

    def test_drive_root_default_file_and_invalid_file(self):
        from academic_knowledge import index
        self.assertEqual(index.DEFAULT_DRIVE_ROOT, Path(r"G:\Mi unidad\03 - Universidad\Academic Manager"))
        self.assertEqual(self.drive_root_in_new_process(), str(index.DEFAULT_DRIVE_ROOT))
        self.write({"drive_root": str(self.root / "drive")})
        self.assertEqual(self.drive_root_in_new_process(), str(self.root / "drive"))
        self.assertEqual(index.configured_drive_root(), self.root / "drive")
        # Importar no falla; los comandos vuelven a leer el archivo y se detienen.
        self.write("{")
        self.assertEqual(self.drive_root_in_new_process(), str(index.DEFAULT_DRIVE_ROOT))
        with self.assertRaises(paths.ConfigError):
            index.configured_drive_root()


class SaveConfigTests(ConfigCase):
    """Escritura del archivo por el asistente: misma validación que la lectura, atómica."""

    def test_creates_file_and_merges_keys(self):
        data = str(self.root / "datos")
        self.assertEqual(paths.save_config(runtime=data), {"runtime": data})
        drive = str(self.root / "drive")
        self.assertEqual(paths.save_config(drive_root=drive), {"runtime": data, "drive_root": drive})
        self.assertEqual(paths.load_config(), {"runtime": data, "drive_root": drive})
        self.assertEqual(paths.runtime(), data)
        self.assertEqual([p.name for p in self.config.parent.iterdir()], ["config.json"])

    def test_invalid_value_or_key_fails_closed_without_writing(self):
        self.write({"runtime": str(self.root / "datos")})
        before = self.config.read_bytes()
        for values in ({"runtime": "relativa"}, {"drive_root": ""}, {"otra": str(self.root)},
                       {"runtime": 5}):
            with self.subTest(values=values), self.assertRaises(paths.ConfigError):
                paths.save_config(**values)
        self.assertEqual(self.config.read_bytes(), before)
        self.assertEqual([p.name for p in self.config.parent.iterdir()], ["config.json"])

    def test_existing_invalid_file_is_not_overwritten(self):
        self.write("{")
        with self.assertRaises(paths.ConfigError) as caught:
            paths.save_config(runtime=str(self.root / "datos"))
        self.assertIn(str(self.config), str(caught.exception))
        self.assertEqual(self.config.read_text(encoding="utf-8"), "{")


if __name__ == "__main__":
    unittest.main()
