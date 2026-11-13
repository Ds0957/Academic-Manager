"""Claude Desktop connection (docs/43): config location, safe merge with backup, wizard page «/claude».

Fake %APPDATA% / %LOCALAPPDATA% live in temporary folders outside the worktree; nothing touches
a real Claude Desktop installation.
"""
import html
import json
import os
import re
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from academic_app import claude_desktop as cd
from academic_app.pages import esc

from tests.test_academic_panel import PanelCase

SECRET = "sk-other-server-secret-123"
OTHER = {"desktop-commander": {"command": "npx", "args": ["-y", "@wonderwhy-er/desktop-commander"],
                               "env": {"API_KEY": SECRET}}}
NOW = datetime(2026, 11, 2, 15, 30, 0, tzinfo=timezone.utc)


class Env:
    """Temporary APPDATA / LOCALAPPDATA."""

    def __init__(self, case):
        tmp = tempfile.TemporaryDirectory()
        case.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.appdata = self.root / "Roaming"
        self.local = self.root / "Local"
        self.appdata.mkdir()
        self.local.mkdir()
        self.env = {"APPDATA": str(self.appdata), "LOCALAPPDATA": str(self.local)}

    def normal(self, *files):
        folder = self.appdata / "Claude"
        folder.mkdir(exist_ok=True)
        for name in files:
            (folder / name).write_text("x", encoding="utf-8")
        return folder / cd.CONFIG_NAME

    def store(self, *files):
        folder = self.local / "Packages" / "Claude_pzs8sxrjxfjjc" / "LocalCache" / "Roaming" / "Claude"
        folder.mkdir(parents=True, exist_ok=True)
        for name in files:
            (folder / name).write_text("x", encoding="utf-8")
        return folder / cd.CONFIG_NAME

    def data(self, *files):
        """Carpeta actual de Claude Desktop de Microsoft Store: %LOCALAPPDATA%\\Claude-Data\\claude_<editor>."""
        folder = self.local / "Claude-Data" / "claude_pzs8sxrjxfjjc"
        folder.mkdir(parents=True, exist_ok=True)
        (folder.parent / "README.txt").write_text("x", encoding="utf-8")
        for name in files:
            (folder / name).write_text("x", encoding="utf-8")
        return folder / cd.CONFIG_NAME


def backups(config):
    return sorted(config.parent.glob(cd.CONFIG_NAME + ".academic-manager-*.bak"))


class LocateTests(unittest.TestCase):
    def setUp(self):
        self.fake = Env(self)
        self.data = self.fake.root / "datos"
        self.data.mkdir()

    def test_current_data_folder_wins_over_the_folders_claude_left_behind(self):
        self.fake.store(".user-data-left.claude_pzs8sxrjxfjjc")
        self.fake.normal("old.log")
        config = self.fake.data(cd.CONFIG_NAME, "config.json")
        self.assertEqual(cd.locate(self.fake.env), config)

    def test_current_data_folder_wins_even_before_it_has_the_config_file(self):
        self.fake.store(".user-data-left.claude_pzs8sxrjxfjjc")
        config = self.fake.data("config.json")
        self.assertEqual(cd.locate(self.fake.env), config)

    def test_connect_writes_in_the_current_data_folder_and_keeps_its_settings(self):
        self.fake.store(".user-data-left.claude_pzs8sxrjxfjjc")
        config = self.fake.data()
        config.write_text(json.dumps({"preferences": {"a": 1}}), encoding="utf-8")
        result = cd.connect("C:\\Datos", env=self.fake.env, now=NOW, executable="C:\\py\\python.exe")
        self.assertEqual(result["ruta"], config)
        data = json.loads(config.read_text(encoding="utf-8"))
        self.assertEqual(data["preferences"], {"a": 1})
        self.assertIn(cd.SERVER_KEY, data["mcpServers"])
        self.assertEqual(len(backups(config)), 1)

    def test_not_installed_never_creates_claude_folder(self):
        state = cd.status(self.data, env=self.fake.env)
        self.assertEqual(state["estado"], cd.NOT_INSTALLED)
        self.assertEqual(state["mensaje"], "Claude Desktop no está instalado en este PC")
        with self.assertRaises(cd.ClaudeDesktopError) as caught:
            cd.connect(self.data, env=self.fake.env, now=NOW)
        self.assertIn("no está instalado", str(caught.exception))
        self.assertFalse((self.fake.appdata / "Claude").exists())
        self.assertFalse((self.fake.local / "Packages").exists())

    def test_no_environment_means_not_installed(self):
        self.assertEqual(cd.status(self.data, env={})["estado"], cd.NOT_INSTALLED)

    def test_normal_install(self):
        config = self.fake.normal("Local State")
        self.assertEqual(cd.locate(self.fake.env), config)

    def test_store_install_detected(self):
        config = self.fake.store("Local State")
        self.assertEqual(cd.locate(self.fake.env), config)
        self.assertEqual(cd.status(self.data, env=self.fake.env)["estado"], cd.NOT_CONNECTED)

    def test_both_prefers_the_folder_with_claude_files(self):
        self.fake.normal()  # empty leftover folder
        store = self.fake.store("Local State", "Preferences")
        self.assertEqual(cd.locate(self.fake.env), store)
        tmp = Env(self)
        normal = tmp.normal("Local State")
        tmp.store()
        self.assertEqual(cd.locate(tmp.env), normal)


class ConnectTests(unittest.TestCase):
    def setUp(self):
        self.fake = Env(self)
        self.data = self.fake.root / "datos"
        self.data.mkdir()
        self.config = self.fake.normal("Local State")

    def write(self, value):
        raw = value if isinstance(value, str) else json.dumps(value, indent=4)
        self.config.write_text(raw, encoding="utf-8")
        return self.config.read_bytes()

    def read(self):
        return json.loads(self.config.read_text(encoding="utf-8"))

    def test_server_entry(self):
        entry = cd.server_entry(self.data)
        self.assertEqual(entry, {"command": sys.executable, "args": ["-m", "academic_mcp"],
                                 "env": {"ACADEMIC_MANAGER_DATA": str(self.data)}})

    def test_connect_on_missing_file_inside_existing_folder(self):
        result = cd.connect(self.data, env=self.fake.env, now=NOW)
        self.assertTrue(result["cambio"])
        self.assertIsNone(result["copia"])
        self.assertEqual(self.read(), {"mcpServers": {cd.SERVER_KEY: cd.server_entry(self.data)}})
        self.assertEqual(backups(self.config), [])
        self.assertEqual(cd.status(self.data, env=self.fake.env)["estado"], cd.CONNECTED)

    def test_connect_preserves_everything_else_and_backs_up(self):
        before = {"globalShortcut": "Ctrl+Space", "mcpServers": dict(OTHER), "extra": [1, {"á": "ñ"}]}
        old = self.write(before)
        result = cd.connect(self.data, env=self.fake.env, now=NOW)
        after = self.read()
        self.assertEqual(after["mcpServers"][cd.SERVER_KEY], cd.server_entry(self.data))
        del after["mcpServers"][cd.SERVER_KEY]
        self.assertEqual(after, before)
        [backup] = backups(self.config)
        self.assertEqual(backup.name, "claude_desktop_config.json.academic-manager-20261102T153000Z.bak")
        self.assertEqual(Path(result["copia"]), backup)
        self.assertEqual(backup.read_bytes(), old)
        text = self.config.read_text(encoding="utf-8")
        self.assertIn('"á": "ñ"', text)  # ensure_ascii=False
        self.assertIn('\n  "', text)  # indent=2
        self.assertEqual(list(self.config.parent.glob("*.tmp")), [])

    def test_second_connect_is_a_no_op_without_a_second_backup(self):
        self.write({"mcpServers": dict(OTHER)})
        cd.connect(self.data, env=self.fake.env, now=NOW)
        written = self.config.read_bytes()
        result = cd.connect(self.data, env=self.fake.env, now=NOW.replace(minute=31))
        self.assertFalse(result["cambio"])
        self.assertEqual(self.config.read_bytes(), written)
        self.assertEqual(len(backups(self.config)), 1)

    def test_outdated_entry_is_updated(self):
        self.write({"mcpServers": {**OTHER, cd.SERVER_KEY: {"command": "C:\\old\\python.exe",
                                                             "args": ["-m", "academic_mcp"]}}})
        self.assertEqual(cd.status(self.data, env=self.fake.env)["estado"], cd.OUTDATED)
        result = cd.connect(self.data, env=self.fake.env, now=NOW)
        self.assertTrue(result["cambio"])
        self.assertEqual(self.read()["mcpServers"][cd.SERVER_KEY], cd.server_entry(self.data))
        self.assertEqual(self.read()["mcpServers"]["desktop-commander"], OTHER["desktop-commander"])
        self.assertEqual(cd.status(self.data, env=self.fake.env)["estado"], cd.CONNECTED)

    def test_other_data_folder_is_outdated(self):
        cd.connect(self.data, env=self.fake.env, now=NOW)
        other = self.fake.root / "otros"
        other.mkdir()
        self.assertEqual(cd.status(other, env=self.fake.env)["estado"], cd.OUTDATED)

    def test_unusable_files_are_left_untouched(self):
        for content in ("{not json", "[1, 2]", json.dumps({"mcpServers": []}), json.dumps({"mcpServers": "x"})):
            with self.subTest(content=content):
                old = self.write(content)
                state = cd.status(self.data, env=self.fake.env)
                self.assertEqual(state["estado"], cd.UNREADABLE)
                for action in (cd.connect, cd.disconnect):
                    with self.assertRaises(cd.ClaudeDesktopError) as caught:
                        action(self.data, env=self.fake.env, now=NOW)
                    self.assertIn("no se cambió", str(caught.exception))
                    self.assertNotIn(content, str(caught.exception))
                self.assertEqual(self.config.read_bytes(), old)
                self.assertEqual(backups(self.config), [])

    @unittest.skipIf(os.name == "nt", "symlinks need privileges on Windows")
    def test_symlinked_config_is_refused(self):
        target = self.fake.root / "elsewhere.json"
        target.write_text(json.dumps({"mcpServers": dict(OTHER)}), encoding="utf-8")
        old = target.read_bytes()
        self.config.symlink_to(target)
        self.assertEqual(cd.status(self.data, env=self.fake.env)["estado"], cd.UNREADABLE)
        for action in (cd.connect, cd.disconnect):
            with self.assertRaises(cd.ClaudeDesktopError):
                action(self.data, env=self.fake.env, now=NOW)
        self.assertTrue(self.config.is_symlink())
        self.assertEqual(target.read_bytes(), old)
        self.assertEqual(backups(self.config), [])

    def test_disconnect_removes_only_our_key(self):
        before = {"theme": "dark", "mcpServers": dict(OTHER)}
        self.write(before)
        cd.connect(self.data, env=self.fake.env, now=NOW)
        connected = self.config.read_bytes()
        result = cd.disconnect(self.data, env=self.fake.env, now=NOW.replace(minute=40))
        self.assertTrue(result["cambio"])
        self.assertEqual(self.read(), before)
        self.assertEqual(Path(result["copia"]).read_bytes(), connected)
        self.assertEqual(len(backups(self.config)), 2)
        self.assertEqual(cd.status(self.data, env=self.fake.env)["estado"], cd.NOT_CONNECTED)
        again = cd.disconnect(self.data, env=self.fake.env, now=NOW.replace(minute=50))
        self.assertFalse(again["cambio"])
        self.assertEqual(len(backups(self.config)), 2)

    def test_disconnect_without_file_does_nothing(self):
        self.assertFalse(cd.disconnect(self.data, env=self.fake.env, now=NOW)["cambio"])
        self.assertFalse(self.config.exists())

    def test_manual_snippet_is_the_exact_fragment(self):
        snippet = json.loads(cd.manual_snippet(self.data))
        self.assertEqual(snippet, {"mcpServers": {cd.SERVER_KEY: cd.server_entry(self.data)}})

    def test_pythonw_is_swapped_for_python(self):
        folder = self.fake.root / "py"
        folder.mkdir()
        (folder / "python.exe").write_text("", encoding="utf-8")
        entry = cd.server_entry(self.data, executable=str(folder / "pythonw.exe"))
        self.assertEqual(entry["command"], str(folder / "python.exe"))


class ClaudePageTests(PanelCase):
    """GET/POST /claude on the real wizard server."""

    def setUp(self):
        super().setUp()
        self.fake = Env(self)
        os.environ.update(self.fake.env)  # ServerCase patches os.environ; restored on cleanup
        self.runtime = self.wizard.data_folder()

    def body_text(self, body):
        return body.split("</header>", 1)[1]

    def assert_never_leaks(self):
        for text in self.bodies + [self.log.getvalue()]:
            self.assertNotIn(SECRET, text)
            self.assertNotIn("Ctrl+Space", text)

    def test_home_links_to_the_page(self):
        body = self.get("/")[2]
        self.assertIn('href="/claude"', body)
        self.assertIn("Claude Desktop", body)
        self.assertLess(body.index('href="/celular"'), body.index('href="/claude"'))

    def test_not_installed(self):
        status, _, body = self.get("/claude")
        self.assertEqual(status, 200)
        self.assertIn("Claude Desktop no está instalado en este PC", body)
        self.assertNotIn("Conectar con Claude Desktop</button>", body)
        self.assertIn("ponerme_al_dia", body)  # instructions are useful anyway
        status, _, body = self.post("/claude/conectar")
        self.assertEqual(status, 400)
        self.assertIn("no está instalado", body)
        self.assertFalse((self.fake.appdata / "Claude").exists())

    def test_page_offers_the_drive_transcript_button(self):
        from academic_app import drive_button
        status, _, body = self.get("/claude")
        self.assertEqual(status, 200)
        self.assertIn('class="marcador"', body)
        self.assertIn(html.escape(drive_button.href(), quote=True), body)
        self.assertIn("Importa las transcripciones descargadas", body)

    def test_connect_flow_with_other_servers(self):
        config = self.fake.normal("Local State")
        config.write_text(json.dumps({"globalShortcut": "Ctrl+Space", "mcpServers": dict(OTHER)}),
                          encoding="utf-8")
        body = self.get("/claude")[2]
        self.assertIn("Conectar con Claude Desktop", body)
        self.assertIn("suscripción", body)
        self.assertIn("Desktop Commander", body)
        self.assertIn("solo lectura", body)
        status, response, _ = self.post("/claude/conectar")
        self.assertEqual(status, 303)
        self.assertEqual(response.headers["Location"], "/claude")
        body = self.get("/claude")[2]
        self.assertIn("Listo. Cierre Claude Desktop por completo (también desde la bandeja del sistema) "
                      "y vuelva a abrirlo", body)
        self.assertIn("Quitar conexión", body)
        saved = json.loads(config.read_text(encoding="utf-8"))
        self.assertEqual(saved["mcpServers"][cd.SERVER_KEY]["env"]["ACADEMIC_MANAGER_DATA"], str(self.runtime))
        self.assertEqual(saved["mcpServers"]["desktop-commander"], OTHER["desktop-commander"])
        self.assertEqual(len(backups(config)), 1)
        status, _, _ = self.post("/claude/quitar")
        self.assertEqual(status, 303)
        self.assertNotIn(cd.SERVER_KEY, json.loads(config.read_text(encoding="utf-8"))["mcpServers"])
        self.assert_never_leaks()

    def test_outdated_offers_update(self):
        config = self.fake.normal("Local State")
        config.write_text(json.dumps({"mcpServers": {cd.SERVER_KEY: {"command": "viejo", "args": []}}}),
                          encoding="utf-8")
        body = self.get("/claude")[2]
        self.assertIn("Actualizar conexión", body)

    def test_failure_shows_plain_message_and_manual_instructions(self):
        config = self.fake.normal("Local State")
        broken = '{"mcpServers": {"x": {"env": {"API_KEY": "%s"}}' % SECRET  # truncated JSON
        config.write_text(broken, encoding="utf-8")
        status, _, body = self.post("/claude/conectar")
        self.assertEqual(status, 400)
        self.assertIn("no se cambió", body)
        self.assertIn(esc(str(config)), body)
        self.assertIn('class="copy-box"', body)
        self.assertIn("&quot;academic_mcp&quot;", body)  # the snippet, escaped in the textarea
        self.assertEqual(config.read_text(encoding="utf-8"), broken)
        self.assert_never_leaks()

    def test_every_post_requires_csrf(self):
        config = self.fake.normal("Local State")
        for path in ("/claude/conectar", "/claude/quitar"):
            status, _, _ = self.post(path, csrf=False)
            self.assertEqual(status, 403, path)
        self.assertFalse(config.exists())

    def test_without_data_folder_connect_is_unavailable(self):
        self.fake.normal("Local State")
        self.config.write_text("{}", encoding="utf-8")  # empty user config: no data folder
        body = self.get("/claude")[2]
        self.assertIn("carpeta de datos", body)
        self.assertNotIn("Conectar con Claude Desktop</button>", body)
        status, _, _ = self.post("/claude/conectar")
        self.assertEqual(status, 400)
        self.assertFalse((self.fake.appdata / "Claude" / cd.CONFIG_NAME).exists())

    def test_instructions_for_claude_box(self):
        body = self.get("/claude")[2]
        match = re.search(r'<textarea id="claude-coordinador"[^>]*>(.*?)</textarea>', body, re.S)
        self.assertIsNotNone(match)
        text = match.group(1)
        for needle in ("ponerme_al_dia", "buscar", "leer_fuente", "Desktop Commander", "nunca"):
            self.assertIn(needle, text)

    def test_not_reachable_from_the_phone(self):
        from academic_app import phone
        self.assertNotIn("/claude", phone.ALLOWED_GET)
        self.assertNotIn("/claude/conectar", phone.ALLOWED_POST)

    def test_one_instructions_box_per_claude_project(self):
        body = self.get("/claude")[2]
        self.assertIn("Proyectos de Claude", body)
        self.assertIn("Memory", body)
        self.assertLess(body.index('id="claude-coordinador"'), body.index('id="claude-MAT-GBD"'))
        self.assertLess(body.index('id="claude-MAT-GBD"'), body.index('id="claude-MAT-SO2"'))
        self.assertEqual(body.count('class="copiar"'),
                         3 + body.count('id="claude-manual"') + body.count('id="drive-button"'))
        self.assertEqual(body.count('id="drive-button"'), 1)
        self.assertIn("Este proyecto es el Coordinador", body)
        self.assertIn("Este proyecto es solo de Sistemas operativos II (MAT-SO2)", body)
        self.assertIn("ponerme_al_dia para MAT-SO2", body)
        self.assertIn("Gestión de bases &lt;datos&gt; (MAT-GBD)", body)
        self.assertNotIn("<datos>", body)

    def test_subject_box_keeps_the_shared_rules_and_adds_its_scope(self):
        from academic_app import wizard
        text = wizard.claude_project_text("Sistemas operativos II", "MAT-SO2")
        self.assertTrue(text.startswith(wizard.CLAUDE_INSTRUCTIONS))
        self.assertIn("no consultes otras materias salvo que te lo pida", text)

    def test_without_subjects_only_the_coordinator_box_is_offered(self):
        self.summary.side_effect = OSError("sin datos")
        status, _, body = self.get("/claude")
        self.assertEqual(status, 200)
        self.assertIn('id="claude-coordinador"', body)
        self.assertNotIn('id="claude-MAT-', body)
        self.assertNotIn("sin datos", body)

    def test_manual_steps_cover_a_file_with_content_but_no_servers(self):
        self.fake.data("config.json")
        body = self.get("/claude")[2]
        self.assertIn("sin «mcpServers»", body)
        self.assertIn(esc(str(self.fake.data())), body)


if __name__ == "__main__":
    unittest.main()
