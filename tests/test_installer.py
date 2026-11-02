"""Instalador de Windows (T4): entradas fijadas, script de Inno Setup, CI y tarea diaria.

Aquí no hay Windows, Inno Setup ni PowerShell: se inspeccionan los archivos por sus invariantes
y se prueba el ayudante de construcción en Python (installer/stage.py) en carpetas temporales.
"""
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "installer"
ISS = INSTALLER / "academic-manager.iss"
BUILD = INSTALLER / "build.ps1"
PINS = INSTALLER / "pins.json"
WORKFLOW = ROOT / ".github" / "workflows" / "build-installer.yml"
DAILY = ROOT / "scripts" / "canvas-daily.ps1"
TASK = ROOT / "scripts" / "install-canvas-daily-task.ps1"

DEV_REPO = r"C:\AcademicManagerDev\Academic-Manager"
DEV_PYTHON = r"C:\AcademicManagerData\procesamiento\capture-env\Scripts\python.exe"
DEV_RUNTIME = r"C:\AcademicManagerData"

try:
    import yaml
except ImportError:  # pragma: no cover - PyYAML es opcional en este entorno
    yaml = None


def read(path):
    return path.read_text(encoding="utf-8-sig")


def iss_section(name):
    """Líneas no vacías ni comentadas de una sección [name] del .iss."""
    lines, inside = [], False
    for line in read(ISS).splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            inside = stripped[1:-1].lower() == name.lower()
            continue
        if inside and stripped and not stripped.startswith(";"):
            lines.append(stripped)
    return lines


def iss_entries(name):
    """Entradas `Clave: valor; Clave: valor` de una sección, como diccionarios."""
    entries = []
    for line in iss_section(name):
        entry = {}
        for part in re.findall(r'(\w+):\s*("(?:[^"]|"")*"|[^;]+)', line):
            key, value = part
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] == '"':
                value = value[1:-1]  # solo las comillas exteriores; "" dentro es una comilla literal
            entry[key] = value
        entries.append(entry)
    return entries


def setup_value(key):
    for line in iss_section("Setup"):
        if line.split("=", 1)[0].strip().lower() == key.lower():
            return line.split("=", 1)[1].strip()
    return None


class VersionTests(unittest.TestCase):
    def test_single_version_file_read_by_app_and_installer(self):
        from academic_app import version
        text = read(ROOT / "academic_app" / "VERSION").strip()
        self.assertRegex(text, r"^\d+\.\d+\.\d+$")
        self.assertEqual(version(), text)
        self.assertIn(r"..\academic_app\VERSION", read(ISS))
        self.assertEqual(setup_value("AppVersion"), "{#AppVersion}")


class PinsTests(unittest.TestCase):
    def setUp(self):
        self.pins = json.loads(read(PINS))

    def test_every_download_has_exact_version_https_url_and_sha256(self):
        for name in ("python", "pip", "innosetup"):
            with self.subTest(name=name):
                pin = self.pins[name]
                self.assertRegex(pin["version"], r"^\d+\.\d+\.\d+$")
                self.assertTrue(pin["url"].startswith("https://"), pin["url"])
                self.assertIn(pin["version"], pin["url"])
                self.assertRegex(pin["sha256"], r"^[0-9a-f]{64}$")
                self.assertTrue(pin["fuente"])

    def test_python_is_the_windows_x64_embeddable_package(self):
        pin = self.pins["python"]
        self.assertEqual(pin["url"], f"https://www.python.org/ftp/python/{pin['version']}/"
                                     f"python-{pin['version']}-embed-amd64.zip")
        self.assertEqual(pin["sha256"], "4acbed6dd1c744b0376e3b1cf57ce906f9dc9e95e68824584c8099a63025a3c3")
        self.assertTrue(pin["version"].startswith("3.12."))  # WhisperX 3.8 pide Python < 3.14

    def test_build_verifies_every_hash_and_refuses_placeholders(self):
        text = read(BUILD)
        self.assertIn("pins.json", text)
        self.assertIn("Get-FileHash", text)
        self.assertIn("-Algorithm SHA256", text)
        self.assertRegex(text, r"\$pin\.sha256 -notmatch '\^\[0-9a-f\]\{64\}\$'")
        for name in ("python", "pip", "innosetup"):
            self.assertIn(f"Get-Pinned $pins.{name}", text)
        self.assertIn("requirements-app.txt", text)
        self.assertIn("stage.py", text)

    def test_build_requires_inno_setup_6_3_or_later(self):
        # ISCC.exe declara FileVersion 0.0.0.0: la versión real es Ver del preprocesador, en el .iss.
        self.assertNotIn("VersionInfo", read(BUILD))
        self.assertIn("6.3 o posterior", read(BUILD))
        iss = read(ISS)
        self.assertRegex(iss, r"#if Ver < EncodeVer\(6, 3, 0\)\s+#error [^\n]*6\.3 o posterior")


class RequirementsTests(unittest.TestCase):
    @staticmethod
    def requirements(name):
        lines = [l.split("#", 1)[0].strip() for l in read(ROOT / name).splitlines()]
        return [l for l in lines if l]

    def test_app_runtime_is_pinned_exactly_without_whisperx(self):
        lines = self.requirements("requirements-app.txt")
        names = {}
        for line in lines:
            self.assertRegex(line, r"^[A-Za-z0-9_.-]+(\[[a-z,-]+\])?==[0-9][0-9A-Za-z.]*$", line)
            name, version = line.split("==")
            names[name.split("[")[0].lower()] = (name, version)
        self.assertNotIn("whisperx", names)
        self.assertNotIn("torch", names)
        self.assertEqual(set(names), {"pypdf", "yt-dlp", "google-api-python-client", "google-auth",
                                      "google-auth-oauthlib", "tzdata", "segno", "mcp"})

    def test_build_checks_the_local_time_zone_loads(self):
        # El Python portátil de Windows no trae zonas horarias: sin tzdata, today y canvas_sync fallan.
        self.assertIn("ZoneInfo('America/Bogota')", read(BUILD))

    def test_app_runtime_agrees_with_the_existing_requirement_files(self):
        app = {l.split("==")[0]: l.split("==")[1] for l in self.requirements("requirements-app.txt")}
        self.assertIn("pypdf==" + app["pypdf"], self.requirements("requirements-canvas.txt"))
        self.assertIn("yt-dlp[pin,pin-deno]==" + app["yt-dlp[pin,pin-deno]"],
                      self.requirements("requirements-capture.txt"))
        drive = self.requirements("requirements-drive.txt")
        for name in ("google-api-python-client", "google-auth", "google-auth-oauthlib"):
            spec = next(l for l in drive if re.match(re.escape(name) + r"[<>=]", l))
            low, high = re.match(re.escape(name) + r">=([\d.]+),<([\d.]+)$", spec).groups()
            version = tuple(map(int, app[name].split(".")))
            self.assertTrue(tuple(map(int, low.split("."))) <= version < tuple(map(int, high.split("."))),
                            (name, app[name], spec))


class InnoSetupTests(unittest.TestCase):
    def test_per_user_install_without_admin(self):
        self.assertEqual(setup_value("PrivilegesRequired"), "lowest")
        self.assertIsNone(setup_value("PrivilegesRequiredOverridesAllowed"))
        self.assertEqual(setup_value("DefaultDirName"), r"{localappdata}\Programs\AcademicManager")
        self.assertEqual(setup_value("ArchitecturesAllowed"), "x64compatible")

    def test_spanish_interface(self):
        self.assertEqual(iss_entries("Languages"),
                         [{"Name": "es", "MessagesFile": r"compiler:Languages\Spanish.isl"}])

    def test_bundles_the_staged_python_and_code(self):
        files = [f for f in iss_entries("Files") if f["Source"].startswith("{#StageDir}")]
        self.assertEqual({(f["Source"], f["DestDir"]) for f in files},
                         {(r"{#StageDir}\python\*", r"{app}\python"), (r"{#StageDir}\app\*", r"{app}\app")})
        for entry in files:
            self.assertIn("recursesubdirs", entry["Flags"])

    def test_shortcuts_open_the_wizard_with_the_bundled_python(self):
        icons = iss_entries("Icons")
        self.assertEqual({i["Name"] for i in icons},
                         {r"{autoprograms}\Academic Manager", r"{autodesktop}\Academic Manager"})
        for icon in icons:
            self.assertEqual(icon["Filename"], r"{app}\python\python.exe")
            self.assertEqual(icon["Parameters"], "-m academic_app")
            self.assertEqual(icon["WorkingDir"], r"{app}\app")
        desktop = next(i for i in icons if i["Name"].startswith("{autodesktop}"))
        self.assertEqual(desktop["Tasks"], "desktopicon")
        self.assertIn("desktopicon", " ".join(iss_section("Tasks")))

    def test_uninstall_removes_only_program_folders(self):
        deletes = iss_entries("UninstallDelete") + iss_entries("InstallDelete")
        allowed = {r"{app}\python", r"{app}\app", "{app}"}
        for entry in deletes:
            self.assertIn(entry["Name"], allowed, entry)
            if entry["Name"] == "{app}":
                self.assertEqual(entry["Type"], "dirifempty")
        text = read(ISS)
        for forbidden in ("AcademicManagerData", "{userappdata}", "{%APPDATA", "config.json\"",
                          "{userdocs}", "AcademicManagerDev"):
            self.assertNotIn(forbidden, text)

    def test_uninstall_removes_the_daily_task_only_when_it_uses_this_copy(self):
        runs = iss_entries("UninstallRun")
        self.assertEqual(len(runs), 1)
        run = runs[0]
        self.assertEqual(run["Filename"], "powershell.exe")
        self.assertIn(r'-File ""{app}\remove-daily-task.ps1""', run["Parameters"])
        self.assertIn(r'-Python ""{app}\python\python.exe""', run["Parameters"])
        self.assertIn("runhidden", run["Flags"])
        self.assertTrue(run["RunOnceId"])
        files = iss_entries("Files")
        self.assertIn(("remove-daily-task.ps1", "{app}"), {(f["Source"], f["DestDir"]) for f in files})
        script = read(INSTALLER / "remove-daily-task.ps1")
        self.assertIn('$taskName = "AcademicManager-CanvasDaily"', script)
        self.assertIn("""$expected = '-Python "' + $Python + '"'""", script)  # el mismo formato que install-canvas-daily-task.ps1
        self.assertIn("""' -Python "' + $Python + '"'""", read(TASK))
        self.assertIn("Unregister-ScheduledTask", script)
        self.assertIn("$ours.Count -eq @($task.Actions).Count", script)
        self.assertNotIn("AcademicManagerDev", script)
        self.assertIn("exit 0", script)  # nunca bloquea la desinstalación

    def test_uninstall_page_says_data_is_kept(self):
        messages = " ".join(iss_section("Messages"))
        self.assertIn("ConfirmUninstall=", messages)
        self.assertIn("carpeta de datos", messages)
        self.assertIn(r"AppData\Roaming\AcademicManager\config.json", messages)
        self.assertIn("no se borran", messages.lower())

    def test_version_and_stage_dir_come_from_the_build(self):
        text = read(ISS)
        self.assertIn("#ifndef StageDir", text)
        self.assertIn("FileRead(", text)
        self.assertEqual(setup_value("OutputBaseFilename"), "AcademicManager-Setup-{#AppVersion}")


class StageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def stage(self, *args):
        return subprocess.run([sys.executable, str(INSTALLER / "stage.py"), *map(str, args)],
                              capture_output=True, text=True, timeout=120)

    def test_pth_enables_site_packages_and_the_app_folder(self):
        python = self.root / "python"
        python.mkdir()
        (python / "python312._pth").write_text("python312.zip\r\n.\r\n\r\n#import site\r\n", encoding="utf-8")
        result = self.stage("pth", python)
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = (python / "python312._pth").read_text(encoding="utf-8").splitlines()
        self.assertEqual(lines, ["python312.zip", ".", r"Lib\site-packages", r"..\app", "import site"])

    def test_pth_requires_exactly_one_file(self):
        python = self.root / "python"
        python.mkdir()
        self.assertNotEqual(self.stage("pth", python).returncode, 0)

    def test_app_copies_the_code_but_not_tests_docs_or_secrets(self):
        out = self.root / "app"
        result = self.stage("app", ROOT, out)
        self.assertEqual(result.returncode, 0, result.stderr)
        top = sorted(p.name for p in out.iterdir())
        self.assertEqual(top, ["academic_access", "academic_app", "academic_config", "academic_knowledge", "academic_mcp",
                               "academic_operations", "academic_projects", "academic_setup", "academic_store",
                               "canvas_sync", "capture", "scripts"])
        self.assertEqual(sorted(p.name for p in (out / "scripts").iterdir()),
                         ["canvas-daily.ps1", "install-canvas-daily-task.ps1"])
        self.assertTrue((out / "academic_app" / "VERSION").is_file())
        self.assertTrue((out / "academic_projects" / "templates" / "materia.md").is_file())
        names = [p.name for p in out.rglob("*")]
        self.assertFalse([n for n in names if n == "__pycache__" or n.endswith(".pyc")])
        self.assertFalse([n for n in names if "credentials" in n or "token" in n.lower() and n.endswith(".json")])

    def test_app_ships_only_git_tracked_files(self):
        sys.path.insert(0, str(INSTALLER))
        self.addCleanup(sys.path.remove, str(INSTALLER))
        import stage
        repo = self.root / "repo"
        (repo / "academic_app").mkdir(parents=True)
        (repo / "scripts").mkdir()
        for name in ("academic_app/a.py", "scripts/canvas-daily.ps1", "scripts/install-canvas-daily-task.ps1"):
            (repo / name).write_text("x", encoding="utf-8")
        git = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t"]
        subprocess.run(git[:3] + ["init", "-q"], check=True)
        subprocess.run(git + ["add", "."], check=True)
        subprocess.run(git + ["commit", "-qm", "x"], check=True)
        (repo / "academic_app" / "local-notes.txt").write_text("privado", encoding="utf-8")
        (repo / "academic_app" / "client_secret.json").write_text("{}", encoding="utf-8")
        out = stage.copy_app(repo, self.root / "out", packages=("academic_app",))
        self.assertEqual(sorted(p.name for p in (out / "academic_app").iterdir()), ["a.py"])

    def test_app_refuses_an_existing_output_folder(self):
        out = self.root / "app"
        out.mkdir()
        (out / "keep.txt").write_text("x", encoding="utf-8")
        self.assertNotEqual(self.stage("app", ROOT, out).returncode, 0)
        self.assertTrue((out / "keep.txt").is_file())


@unittest.skipIf(yaml is None, "PyYAML no está instalado")
class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.text = read(WORKFLOW)
        self.data = yaml.safe_load(self.text)
        self.on = self.data.get("on", self.data.get(True))

    def test_triggers_manual_and_version_tags(self):
        self.assertIn("workflow_dispatch", self.on)
        self.assertEqual(self.on["push"], {"tags": ["v*"]})

    def test_minimal_permissions(self):
        self.assertEqual(self.data["permissions"], {"contents": "read"})
        jobs = self.data["jobs"]
        self.assertEqual(jobs["release"]["permissions"], {"contents": "write"})
        self.assertNotIn("permissions", jobs["build"])
        self.assertEqual(self.text.count("contents: write"), 1)
        self.assertNotIn("secrets.", self.text)

    def test_actions_are_pinned_to_full_commit_shas(self):
        uses = re.findall(r"uses:\s*(\S+)", self.text)
        self.assertTrue(uses)
        for ref in uses:
            self.assertRegex(ref, r"^[\w.-]+/[\w.-]+@[0-9a-f]{40}$")

    def test_build_on_windows_verifies_and_compiles(self):
        build = self.data["jobs"]["build"]
        self.assertEqual(build["runs-on"], "windows-latest")
        runs = " ".join(step.get("run", "") for step in build["steps"])
        self.assertIn("installer/build.ps1", runs)
        self.assertIn("-InstallInnoSetup", runs)
        self.assertIn("academic_app/VERSION", runs)  # la etiqueta debe coincidir con la versión
        upload = [s for s in build["steps"] if "upload-artifact" in s.get("uses", "")]
        self.assertEqual(len(upload), 1)
        self.assertEqual(upload[0]["with"]["if-no-files-found"], "error")

    def test_release_only_for_tags_and_as_draft_when_missing(self):
        release = self.data["jobs"]["release"]
        self.assertEqual(release["needs"], "build")
        self.assertEqual(release["if"], "github.event_name == 'push' && startsWith(github.ref, 'refs/tags/v')")
        runs = " ".join(step.get("run", "") for step in release["steps"])
        self.assertIn("gh release upload", runs)
        self.assertIn("--draft", runs)
        self.assertIn("--clobber", runs)

    def test_published_release_is_never_replaced(self):
        runs = " ".join(step.get("run", "") for step in self.data["jobs"]["release"]["steps"])
        self.assertIn("isDraft", runs)
        self.assertIn("ya está publicada", runs)
        self.assertLess(runs.index("ya está publicada"), runs.index("--clobber"))


class DailyScriptTests(unittest.TestCase):
    """Sin parámetros, los scripts hacen exactamente lo de siempre en el PC de Diego."""

    def test_daily_defaults_are_the_current_layout(self):
        text = read(DAILY)
        self.assertIn(f'[string]$Repo = "{DEV_REPO}"', text)
        self.assertIn(f'[string]$Python = "{DEV_PYTHON}"', text)
        self.assertIn(f'$runtime = "{DEV_RUNTIME}"', text)
        self.assertIn('$logDir = Join-Path $runtime "procesamiento\\canvas-sync\\logs"', text)

    def test_daily_runs_the_same_commands_and_logs(self):
        text = read(DAILY)
        for line in ('& $python -m canvas_sync daily --apply 2>&1 | Out-String',
                     '& $python -m canvas_sync sync-study --all-active --approved-only 2>&1 | Out-String',
                     '& $python -m academic_knowledge build 2>&1 | Out-String',
                     'Set-Location $repo', '$env:PYTHONPATH = $repo',
                     '(Join-Path $logDir ("canvas-" + (Get-Date).ToString("yyyy-MM-dd") + ".log"))',
                     '(Join-Path $logDir "latest-status.json")', "exit $code"):
            self.assertIn(line, text)

    def test_installed_copy_asks_the_shared_resolver(self):
        text = read(DAILY)
        self.assertIn('$PSBoundParameters.ContainsKey("Python")', text)
        self.assertIn("from academic_config import paths", text)
        self.assertIn("paths.runtime(env=('ACADEMIC_MANAGER_DATA',), default=paths.windows_default())", text)
        self.assertIn("ConvertFrom-Json", text)
        # El código Python no lleva comillas dobles: PowerShell 5.1 las rompe al pasarlas a un programa.
        code = re.search(r'-c "([^"]*)"', text).group(1)
        self.assertIn("json.dumps", code)

    def test_task_name_and_default_action_are_unchanged(self):
        from academic_access.system import TASK_NAME
        text = read(TASK)
        self.assertEqual(TASK_NAME, "AcademicManager-CanvasDaily")
        self.assertIn(f'$taskName = "{TASK_NAME}"', text)
        self.assertIn(f'[string]$Repo = "{DEV_REPO}"', text)
        self.assertIn('$script = "$Repo\\scripts\\canvas-daily.ps1"', text)
        self.assertIn("""$arguments = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' + $script + '"'""",
                      text)
        self.assertIn('if ($PSBoundParameters.ContainsKey("Repo"))', text)
        for line in ('New-ScheduledTaskTrigger -Daily -At "08:00"', 'New-ScheduledTaskTrigger -Daily -At "18:00"',
                     "-RunLevel Limited", "-LogonType Interactive"):
            self.assertIn(line, text)


if __name__ == "__main__":
    unittest.main()
