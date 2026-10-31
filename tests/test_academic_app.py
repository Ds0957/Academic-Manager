"""Asistente de configuración: servidor local endurecido y pasos reutilizando el código existente.

Todo es sintético: DPAPI, Canvas, Google, navegador y carpetas se simulan; el archivo de
configuración vive en una carpeta temporal (ACADEMIC_MANAGER_CONFIG), nunca en el perfil real.
"""
import contextlib
import http.client
import io
import json
import os
import socket
import stat
import sys
import tempfile
import threading
import time
import types
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.parse import urlencode

from academic_app import version as app_version
from academic_config import paths
from canvas_sync import auth as canvas_auth
from canvas_sync.common import SyncError
from capture.models import CaptureError
from capture.sources import auth as drive_auth

TOKEN = "synthetic-canvas-secret-7f3a"
CLIENT_SECRET = "synthetic-client-secret-91bd"
VARIABLES = ("ACADEMIC_MANAGER_DATA", "ACADEMIC_MANAGER_RUNTIME", "ACADEMIC_MANAGER_CONFIG")


def desktop_client(**installed):
    data = {"client_id": "synthetic.apps.googleusercontent.com", "client_secret": CLIENT_SECRET,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token", "redirect_uris": ["http://localhost"]}
    data.update(installed)
    return {"installed": data}


class FakeCanvas:
    def __init__(self, token, identity=123):
        self.token, self.identity = token, identity

    def profile(self):
        if self.token == "bad-token":
            raise SyncError("Canvas rechazó el token (401).")
        return self.identity


def fake_dpapi(data, decrypt=False):
    return bytes(reversed(data))  # reversible y distinto del texto, como un cifrado


COURSES = [{"course_id": 990001, "name": "Cálculo <b>I</b>", "course_code": "CAL1"},
           {"course_id": 990002, "name": "Física", "course_code": "FIS"}]
REGISTRY = {"schema_version": 2, "active_semester": "2026-2", "active_block": 2,
            "courses": [{"course_id": 82862, "subject_id": "MAT-GBD", "semester": "2026-2",
                         "blocks": [2], "provenance": "workbook"}]}


class WindowsCase(unittest.TestCase):
    """Carpeta y configuración temporales; DPAPI de Windows simulado en canvas_sync.auth."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.config = self.root / "perfil" / "config.json"
        clean = {k: v for k, v in os.environ.items() if k not in VARIABLES}
        clean["ACADEMIC_MANAGER_CONFIG"] = str(self.config)
        for patcher in (patch.dict(os.environ, clean, clear=True),
                        patch.object(canvas_auth, "os", types.SimpleNamespace(name="nt")),
                        patch.object(canvas_auth, "dpapi", fake_dpapi)):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)
        self.data = self.root / "datos"
        self.now = datetime.now().astimezone()
        self.expiry = (self.now + timedelta(days=60)).strftime("%Y-%m-%d %H:%M")


class SaveTokenTests(WindowsCase):
    """canvas_sync.auth.save_token: la entrada del asistente con el mismo guardado que login."""

    def setUp(self):
        super().setUp()
        self.data.mkdir()

    def save(self, token=TOKEN, identity=123):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            canvas_auth.save_token(self.data, token, self.expiry, now=self.now,
                                   client_factory=lambda t: FakeCanvas(t, identity))
        return out.getvalue()

    def test_validates_encrypts_and_records_expiry(self):
        out = self.save()
        self.assertNotIn(TOKEN, out)
        self.assertNotIn(TOKEN.encode(), canvas_auth.token_path(self.data).read_bytes())
        data, expiry = canvas_auth.load(self.data)
        self.assertEqual((data["token"], data["account_id"]), (TOKEN, 123))
        self.assertEqual(canvas_auth.read_meta(self.data), expiry)

    def test_existing_token_is_replaced_only_by_the_same_account(self):
        self.save()
        self.save("replacement-token")
        self.assertEqual(canvas_auth.load(self.data)[0]["token"], "replacement-token")
        before = canvas_auth.token_path(self.data).read_bytes()
        with self.assertRaises(SyncError):
            self.save("other-account-token", identity=999)
        self.assertEqual(canvas_auth.token_path(self.data).read_bytes(), before)

    def test_rejected_or_badly_copied_token_writes_nothing(self):
        for token in ("bad-token", "", " " + TOKEN, TOKEN + "\n"):
            with self.subTest(token=token), self.assertRaises(SyncError):
                self.save(token)
        self.assertFalse(canvas_auth.token_path(self.data).exists())

    def test_interactive_login_keeps_its_behavior(self):
        """Regresión: login por consola sigue igual (no sustituye un token existente)."""
        def login():
            with contextlib.redirect_stdout(io.StringIO()):
                canvas_auth.authorize(self.data, now=self.now, input_fn=lambda prompt: self.expiry,
                                      secret_fn=lambda: TOKEN, client_factory=FakeCanvas)
        login()
        with self.assertRaises(SyncError) as caught:
            login()
        self.assertEqual(str(caught.exception), "Ya existe token. Para sustituirlo utilice renew.")
        with patch.object(sys, "stdin", io.StringIO()), self.assertRaises(SyncError) as caught:
            canvas_auth.hidden_token()
        self.assertEqual(str(caught.exception), "Introduzca el token desde una terminal local interactiva.")


class SaveClientTests(unittest.TestCase):
    """capture.sources.auth.save_client: el JSON del cliente se valida como en --login-drive."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name) / "datos"

    def test_valid_desktop_client_is_stored_privately_next_to_the_token(self):
        path = drive_auth.save_client(self.data, json.dumps(desktop_client()).encode())
        self.assertEqual(path, drive_auth.client_path(self.data))
        self.assertEqual(path.parent, drive_auth.token_path(self.data).parent)
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), desktop_client())
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(sorted(p.name for p in path.parent.iterdir()), [path.name])

    def test_invalid_client_is_rejected_without_writing(self):
        cases = {b"{": "No se pudo leer el JSON del cliente OAuth",
                 json.dumps({"web": desktop_client()["installed"]}).encode(): "tipo Desktop",
                 b"[1]": "tipo Desktop",
                 json.dumps({"installed": {"client_id": 5}}).encode(): "tipo Desktop",
                 json.dumps(desktop_client(token_uri="https://evil.example/token")).encode():
                     "endpoints oficiales"}
        for raw, message in cases.items():
            with self.subTest(message=message), self.assertRaises(CaptureError) as caught:
                drive_auth.save_client(self.data, raw)
            self.assertIn(message, str(caught.exception))
            self.assertNotIn(CLIENT_SECRET, str(caught.exception))
        self.assertFalse(drive_auth.client_path(self.data).exists())

    def test_console_login_keeps_its_messages(self):
        """Regresión: --login-drive sin cliente o con cliente web falla igual que antes."""
        with self.assertRaises(CaptureError) as caught:
            drive_auth.login(self.data, None)
        self.assertEqual(str(caught.exception),
                         "Indique --oauth-client con el JSON de un cliente Google de tipo Desktop.")
        web = Path(self.tmp.name) / "web.json"
        web.write_text(json.dumps({"web": desktop_client()["installed"]}), encoding="utf-8")
        with self.assertRaises(CaptureError) as caught:
            drive_auth.login(self.data, web)
        self.assertEqual(str(caught.exception),
                         "El JSON debe corresponder a un cliente OAuth de tipo Desktop (installed).")


class ServerCase(WindowsCase):
    """Servidor real en un puerto efímero de 127.0.0.1 y peticiones HTTP reales."""

    idle_seconds = 60

    def setUp(self):
        super().setUp()
        from academic_app import server as app_server
        from academic_app.wizard import Wizard
        self.drive_login = MagicMock(side_effect=self.fake_login)
        self.publish = MagicMock(return_value={"mode": "dry_run", "changes": [("01", 2, 1, None, "x")],
                                               "sha256": "abc"})
        self.check = MagicMock(return_value=({"veredicto": "solo_cpu"},
                                             "Resultado: SOLO CON EL PROCESADOR <lento>"))
        self.courses = MagicMock(return_value=[dict(c) for c in COURSES])
        self.index = MagicMock(return_value={})
        self.wizard = Wizard(canvas_client=FakeCanvas, canvas_courses=self.courses,
                             drive_login=self.drive_login, check=self.check,
                             publish_enrollment=self.publish, build_index=self.index)
        self.log = io.StringIO()
        self.server = app_server.create(self.wizard, idle_seconds=self.idle_seconds, log_stream=self.log)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)
        self.port = self.server.server_address[1]
        self.origin = f"http://127.0.0.1:{self.port}"
        self.bodies = []

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)

    def fake_login(self, runtime, client_file):
        # Como el login del asistente (write=True): token de Capture y token de escritura.
        Path(client_file).read_bytes()
        drive_auth.token_path(runtime).write_text("{}", encoding="utf-8")
        drive_auth.write_token_path(runtime).write_text("{}", encoding="utf-8")

    def request(self, method, path, body=None, headers=None, cookie=True):
        sent = {"Host": f"127.0.0.1:{self.port}"}
        if cookie:
            sent["Cookie"] = f"am_sesion={self.server.secret}"
        if method == "POST":
            sent["Origin"] = self.origin
            sent.setdefault("Content-Type", "application/x-www-form-urlencoded")
        sent.update(headers or {})
        sent = {k: v for k, v in sent.items() if v is not None}
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            # skip_host: sin cabecera Host cuando la prueba la quita.
            connection.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
            data = body.encode() if isinstance(body, str) else body
            if data is not None:
                sent.setdefault("Content-Length", str(len(data)))
            for name, value in sent.items():
                connection.putheader(name, value)
            connection.endheaders(data)
            response = connection.getresponse()
            text = response.read().decode("utf-8")
        finally:
            connection.close()
        self.bodies.append(text)
        return response.status, response, text

    def get(self, path, **kw):
        return self.request("GET", path, **kw)

    def post(self, path, fields=None, csrf=True, **kw):
        fields = dict(fields or {})
        if csrf:
            fields.setdefault("csrf", self.server.csrf)
        return self.request("POST", path, body=urlencode(fields, doseq=True), **kw)

    def upload(self, path, content, csrf=True):
        boundary = "----amtest"
        parts = []
        if csrf:
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="csrf"\r\n\r\n'
                         f"{self.server.csrf}\r\n".encode())
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="cliente"; '
                     f'filename="client_secret.json"\r\nContent-Type: application/json\r\n\r\n'.encode()
                     + content + b"\r\n")
        body = b"".join(parts) + f"--{boundary}--\r\n".encode()
        return self.request("POST", path, body=body,
                            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})

    def choose_data_folder(self):
        status, _, _ = self.post("/paso/datos", {"ruta": str(self.data)})
        self.assertEqual(status, 303)

    def assert_no_secrets(self):
        for text in self.bodies + [self.log.getvalue()]:
            for secret in (TOKEN, CLIENT_SECRET, self.server.secret):
                self.assertNotIn(secret, text)


class SecurityTests(ServerCase):
    def test_binds_to_loopback_with_a_random_port(self):
        self.assertEqual(self.server.server_address[0], "127.0.0.1")
        self.assertNotEqual(self.port, 0)
        self.assertTrue(self.server.url.startswith(f"http://127.0.0.1:{self.port}/?clave="))

    def test_entry_link_sets_strict_cookie_and_hides_the_secret(self):
        status, response, body = self.get(f"/?clave={self.server.secret}", cookie=False)
        self.assertEqual(status, 303)
        self.assertEqual(response.getheader("Location"), "/")
        cookie = response.getheader("Set-Cookie")
        self.assertIn(f"am_sesion={self.server.secret}", cookie)
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)
        status, response, body = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn("Carpeta de datos", body)
        self.assertIn("default-src 'none'", response.getheader("Content-Security-Policy"))
        self.assertEqual(response.getheader("Cache-Control"), "no-store")
        self.assertEqual(response.getheader("X-Frame-Options"), "DENY")
        self.assert_no_secrets()

    def test_missing_or_wrong_secret_is_forbidden(self):
        for path, cookie in (("/", False), ("/?clave=incorrecta", False), ("/paso/datos", False)):
            with self.subTest(path=path):
                self.assertEqual(self.get(path, cookie=cookie)[0], 403)
        status, _, _ = self.get("/", headers={"Cookie": "am_sesion=incorrecta"}, cookie=False)
        self.assertEqual(status, 403)
        self.assertEqual(self.post("/paso/datos", {"ruta": str(self.data)}, cookie=False)[0], 403)
        self.assertFalse(self.config.exists())

    def test_other_host_is_rejected(self):
        for host in ("evil.example", f"localhost:{self.port}", f"evil.example:{self.port}", None):
            with self.subTest(host=host):
                self.assertEqual(self.get("/", headers={"Host": host})[0], 403)

    def test_cross_origin_requests_are_rejected(self):
        self.assertEqual(self.get("/", headers={"Origin": "http://evil.example"})[0], 403)
        self.assertEqual(self.get("/", headers={"Sec-Fetch-Site": "cross-site"})[0], 403)
        self.assertEqual(self.get("/", headers={"Sec-Fetch-Site": "same-origin"})[0], 200)
        status, _, _ = self.post("/paso/datos", {"ruta": str(self.data)},
                                 headers={"Origin": "http://evil.example"})
        self.assertEqual(status, 403)
        status, _, _ = self.post("/paso/datos", {"ruta": str(self.data)},
                                 headers={"Sec-Fetch-Site": "same-site"})
        self.assertEqual(status, 403)
        self.assertFalse(self.config.exists())

    def test_post_requires_the_csrf_token(self):
        self.assertEqual(self.post("/paso/datos", {"ruta": str(self.data)}, csrf=False)[0], 403)
        self.assertEqual(self.post("/paso/datos", {"ruta": str(self.data), "csrf": "x"})[0], 403)
        self.assertEqual(self.upload("/paso/google/cliente", json.dumps(desktop_client()).encode(),
                                     csrf=False)[0], 403)
        self.assertFalse(self.config.exists())

    def test_body_too_large_is_refused(self):
        from academic_app import server as app_server
        status, _, _ = self.post("/paso/datos", {"ruta": "x" * (app_server.MAX_BODY + 1)})
        self.assertEqual(status, 413)
        self.assertFalse(self.config.exists())

    def test_only_get_and_post(self):
        self.assertEqual(self.request("PUT", "/paso/datos", body="")[0], 501)
        self.assertEqual(self.get("/no-existe")[0], 404)

    def test_hostile_values_are_escaped(self):
        hostile = "relativa<script>alert(1)</script>"
        status, _, body = self.post("/paso/datos", {"ruta": hostile})
        self.assertEqual(status, 400)
        self.assertNotIn("<script>alert(1)</script>", body)
        self.assertIn("&lt;script&gt;", body)

    def test_close_action_stops_the_server(self):
        status, _, body = self.post("/cerrar")
        self.assertEqual(status, 200)
        self.assertIn("Puede cerrar esta pestaña", body)
        self.thread.join(5)
        self.assertFalse(self.thread.is_alive())


    def test_close_works_while_another_step_is_running(self):
        # p. ej. el inicio de sesión de Google espera hasta 3 minutos al navegador
        with self.server.action_lock:
            started = time.monotonic()
            status, _, _ = self.post("/cerrar")
            self.assertEqual(status, 200)
            self.assertLess(time.monotonic() - started, 3)
        self.thread.join(5)
        self.assertFalse(self.thread.is_alive())

class SlowBodyTests(ServerCase):
    def test_body_shorter_than_announced_times_out_and_frees_the_server(self):
        from academic_app import server as app_server
        self.assertTrue(0 < app_server.Handler.timeout <= 30)  # por defecto un envío incompleto no cuelga
        with patch.object(app_server.Handler, "timeout", 0.5):
            started = time.monotonic()
            status, _, _ = self.request("POST", "/paso/datos", body="ruta=x",
                                        headers={"Content-Length": "500"})
        self.assertEqual(status, 408)
        self.assertLess(time.monotonic() - started, 5)
        self.assertFalse(self.config.exists())
        deadline = time.monotonic() + 2
        while self.server._active and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertEqual(self.server._active, 0)


class SilentConnectionTests(ServerCase):
    def test_connection_that_sends_nothing_is_logged_without_traceback(self):
        # El navegador abre conexiones de reserva que no envían nada y vencen por tiempo.
        from academic_app import server as app_server
        with patch.object(app_server.Handler, "timeout", 0.3), patch.object(
                self.server, "handle_error") as handle_error:
            with socket.create_connection(("127.0.0.1", self.port), timeout=5) as sock:
                self.assertEqual(sock.recv(1), b"")  # el servidor cierra al vencer el tiempo
        handle_error.assert_not_called()
        self.assertIn("- - aviso HTTP", self.log.getvalue())


class IdleTests(ServerCase):
    idle_seconds = 0.3

    def test_idle_timeout_stops_the_server(self):
        self.thread.join(5)
        self.assertFalse(self.thread.is_alive())


class DataFolderTests(ServerCase):
    def test_choose_folder_writes_config_and_is_resumable(self):
        status, _, body = self.get("/paso/datos")
        self.assertIn("falta", body)
        self.assertIn("AcademicManagerData", body)  # propuesta por defecto
        self.choose_data_folder()
        self.assertEqual(paths.load_config(), {"runtime": str(self.data)})
        self.assertTrue(self.data.is_dir())
        from academic_app.wizard import Wizard
        fresh = Wizard()  # otra apertura: el estado sale de los archivos reales
        self.assertEqual(fresh.status()["datos"][0], "hecho")
        status, _, body = self.get("/")
        self.assertIn("hecho", body)
        self.assertIn(str(self.data), body)

    def test_folder_is_normalized_before_creating_and_saving(self):
        raw = str(self.root / "sub" / ".." / "datos2")
        self.assertEqual(self.post("/paso/datos", {"ruta": raw})[0], 303)
        self.assertEqual(paths.load_config(), {"runtime": str(self.root / "datos2")})
        self.assertFalse((self.root / "sub").exists())
        drive = self.root / "drive"
        drive.mkdir()
        self.assertEqual(self.post("/paso/drive", {"ruta": str(self.root / "x" / ".." / "drive")})[0], 303)
        self.assertEqual(paths.load_config()["drive_root"], str(drive))

    def test_relative_folder_is_an_error_and_writes_nothing(self):
        status, _, body = self.post("/paso/datos", {"ruta": "datos"})
        self.assertEqual(status, 400)
        self.assertIn("ruta completa", body)
        self.assertFalse(self.config.exists())

    def test_invalid_config_file_is_reported(self):
        self.config.parent.mkdir(parents=True)
        self.config.write_text("{", encoding="utf-8")
        status, _, body = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn("error", body)
        self.assertIn("no es JSON válido", body)
        self.assertEqual(self.post("/paso/datos", {"ruta": str(self.data)})[0], 400)
        self.assertEqual(self.config.read_text(encoding="utf-8"), "{")


class CheckTests(ServerCase):
    def test_check_shows_the_plain_summary(self):
        self.choose_data_folder()
        status, _, body = self.get("/paso/equipo")
        self.assertEqual(status, 200)
        self.assertIn("SOLO CON EL PROCESADOR &lt;lento&gt;", body)
        self.check.assert_called_once_with(self.data)
        self.assertEqual(self.wizard.status()["equipo"][0], "hecho")

    def test_check_failure_is_an_error(self):
        self.choose_data_folder()
        self.check.side_effect = OSError("disco")
        status, _, body = self.get("/paso/equipo")
        self.assertIn("No se pudo comprobar", body)
        self.assertEqual(self.wizard.status()["equipo"][0], "error")

    def test_default_check_reuses_academic_setup(self):
        from academic_app import wizard
        from academic_setup import check
        equipo = {"sistema": {"valor": "Windows"}, "nucleos_cpu": {"valor": 8},
                  "ram_total_gb": {"valor": 16}, "disco_libre_gb": {"valor": 50, "unidad": "C:"},
                  "gpu_nvidia": {"valor": []}, "torch_cuda": {"valor": False},
                  "programas": {"whisperx": {"valor": False}, "ffmpeg": {"valor": True},
                                "yt_dlp": {"valor": True}}}
        with patch("academic_setup.hardware.collect", return_value=dict(equipo)) as collect, \
                patch.object(check, "evaluate", return_value=("solo_cpu", ["motivo"], None)):
            data, text = wizard.run_check(self.data)
        collect.assert_called_once_with(self.data)
        self.assertEqual(data["veredicto"], "solo_cpu")
        self.assertIn("Comprobación del equipo para Capture", text)


class CanvasStepTests(ServerCase):
    def save(self, token=TOKEN, vence=None):
        vence = vence or (self.now + timedelta(days=60)).strftime("%Y-%m-%dT%H:%M")
        return self.post("/paso/canvas", {"token": token, "vence": vence})

    def test_needs_the_data_folder_first(self):
        status, _, body = self.get("/paso/canvas")
        self.assertIn("Primero elija la carpeta de datos", body)
        self.assertEqual(self.save()[0], 400)

    def test_token_is_validated_stored_and_never_echoed(self):
        self.choose_data_folder()
        status, _, body = self.get("/paso/canvas")
        self.assertIn('type="password"', body)
        self.assertIn("Nuevo token de acceso", body)
        status, response, _ = self.save()
        self.assertEqual(status, 303)
        self.assertEqual(canvas_auth.load(self.data)[0]["token"], TOKEN)
        status, _, body = self.get("/paso/canvas")
        self.assertIn("hecho", body)
        self.assertIn((self.now + timedelta(days=60)).date().isoformat(), body)
        self.assertEqual(self.wizard.status()["canvas"][0], "hecho")
        self.assert_no_secrets()

    def test_rejected_token_or_bad_date_is_an_error(self):
        self.choose_data_folder()
        status, _, body = self.save("bad-token")
        self.assertEqual(status, 400)
        self.assertIn("Canvas rechazó el token", body)
        status, _, body = self.save(vence="2020-01-01T10:00")
        self.assertEqual(status, 400)
        self.assertIn("futuro", body)
        self.assertFalse(canvas_auth.token_path(self.data).exists())
        self.assertEqual(self.wizard.status()["canvas"][0], "falta")
        self.assert_no_secrets()

    def test_expired_token_shows_error(self):
        self.choose_data_folder()
        self.assertEqual(self.save()[0], 303)
        past = datetime.now().astimezone() - timedelta(days=1)
        canvas_auth.save_meta(self.data, past)
        self.assertEqual(self.wizard.status()["canvas"][0], "error")


class GoogleStepTests(ServerCase):
    def test_guide_upload_and_login(self):
        self.choose_data_folder()
        status, _, body = self.get("/paso/google")
        for text in ("Google Drive API", "usuario de prueba", "App de escritorio", "7 días"):
            self.assertIn(text, body)
        status, _, _ = self.upload("/paso/google/cliente", json.dumps(desktop_client()).encode())
        self.assertEqual(status, 303)
        self.assertTrue(drive_auth.client_path(self.data).is_file())
        self.assertEqual(self.wizard.status()["google"][0], "falta")
        status, _, _ = self.post("/paso/google/login")
        self.assertEqual(status, 303)
        self.drive_login.assert_called_once_with(self.data, drive_auth.client_path(self.data))
        self.assertEqual(self.wizard.status()["google"][0], "hecho")
        status, _, body = self.get("/paso/google")
        self.assertIn("Volver a iniciar sesión", body)
        self.assert_no_secrets()

    def test_invalid_client_or_failed_login_is_an_error(self):
        self.choose_data_folder()
        status, _, body = self.upload("/paso/google/cliente", b'{"web": {}}')
        self.assertEqual(status, 400)
        self.assertIn("App de escritorio", body)
        self.assertFalse(drive_auth.client_path(self.data).exists())
        status, _, body = self.post("/paso/google/login")
        self.assertEqual(status, 400)
        self.assertIn("Primero suba", body)
        self.upload("/paso/google/cliente", json.dumps(desktop_client()).encode())
        self.drive_login.side_effect = CaptureError("La autorización no se completó.")
        status, _, body = self.post("/paso/google/login")
        self.assertEqual(status, 400)
        self.assertIn("La autorización no se completó.", body)
        self.assertEqual(self.wizard.status()["google"][0], "falta")
        self.assert_no_secrets()


class GoogleWriteLoginTests(ServerCase):
    def test_wizard_login_asks_for_write_permission(self):
        from academic_app.wizard import Wizard
        login = Wizard().deps["drive_login"]
        self.assertIs(login.func, drive_auth.login)
        self.assertEqual(login.keywords, {"write": True})
        self.choose_data_folder()
        self.upload("/paso/google/cliente", json.dumps(desktop_client()).encode())
        status, _, body = self.get("/paso/google")
        self.assertIn("guardar archivos de Drive", body)

    def test_read_only_session_must_log_in_again_to_publish(self):
        self.choose_data_folder()
        self.upload("/paso/google/cliente", json.dumps(desktop_client()).encode())
        drive_auth.token_path(self.data).write_text("{}", encoding="utf-8")
        state, detail = self.wizard.status()["google"]
        self.assertEqual(state, "falta")
        self.assertIn("solo permite leer", detail)
        status, _, body = self.get("/paso/google")
        self.assertIn("Volver a iniciar sesión", body)

    def test_service_account_install_keeps_read_only_session_done(self):
        """Regresión: con cuenta de servicio, la sesión de Capture solo de lectura sigue «hecho»."""
        self.choose_data_folder()
        self.upload("/paso/google/cliente", json.dumps(desktop_client()).encode())
        drive_auth.token_path(self.data).write_text("{}", encoding="utf-8")
        (self.data / "control" / "service-account-drive.json").write_text("{}", encoding="utf-8")
        self.assertEqual(self.wizard.status()["google"][0], "hecho")


class DriveFolderTests(ServerCase):
    def test_existing_folder_is_saved(self):
        self.choose_data_folder()
        folder = self.root / "Mi unidad" / "Academic Manager"
        folder.mkdir(parents=True)
        status, _, _ = self.post("/paso/drive", {"ruta": str(folder)})
        self.assertEqual(status, 303)
        self.assertEqual(paths.load_config(), {"runtime": str(self.data), "drive_root": str(folder)})
        self.assertEqual(self.wizard.status()["drive"][0], "hecho")

    def test_missing_folder_is_an_error(self):
        status, _, body = self.post("/paso/drive", {"ruta": str(self.root / "no-existe")})
        self.assertEqual(status, 400)
        self.assertIn("no existe", body)
        self.assertFalse(self.config.exists())


class CoursesStepTests(ServerCase):
    def setUp(self):
        super().setUp()
        self.choose_data_folder()
        self.registry = self.data / "control" / "canvas-sync" / "course-registry.json"

    def write_registry(self):
        self.registry.parent.mkdir(parents=True, exist_ok=True)
        self.registry.write_text(json.dumps(REGISTRY), encoding="utf-8")

    def selection(self, **extra):
        fields = {"semestre": "2027-1", "curso": ["990001"], "bloques-990001": ["1", "2"]}
        fields.update(extra)
        return fields

    def test_lists_current_canvas_courses_with_checkboxes(self):
        self.write_registry()
        status, _, body = self.get("/paso/materias")
        self.assertEqual(status, 200)
        self.assertIn('type="checkbox" name="curso" value="990001"', body)
        self.assertIn("Cálculo &lt;b&gt;I&lt;/b&gt;", body)
        self.courses.assert_called_once_with(self.data)

    def test_new_user_without_database_stops_at_the_preview(self):
        status, _, body = self.post("/paso/materias/vista-previa", self.selection())
        self.assertEqual(status, 200)
        self.assertIn("Cálculo", body)
        self.assertIn("todavía no tiene la base de datos", body)
        self.assertNotIn('action="/paso/materias/confirmar"', body)
        self.assertFalse(self.registry.exists())
        self.assertEqual(self.wizard.status()["materias"][0], "falta")

    def test_preview_confirm_and_registry_update(self):
        self.write_registry()
        status, _, body = self.post("/paso/materias/vista-previa", self.selection())
        self.assertIn('action="/paso/materias/confirmar"', body)
        self.assertIn("2027-1", body)
        status, _, body = self.post("/paso/materias/confirmar", self.selection())
        self.assertEqual(status, 200)
        saved = json.loads(self.registry.read_text(encoding="utf-8"))
        added = [c for c in saved["courses"] if c["course_id"] == 990001]
        self.assertEqual(added[0]["blocks"], [1, 2])
        self.assertEqual(added[0]["subject_id"], "MAT-20271-C990001")
        self.assertEqual(saved["active_semester"], "2027-1")
        self.publish.assert_called_once()
        self.assertTrue(self.publish.call_args.kwargs["dry_run"])
        self.assertIn("1 cambio ", body)
        self.assertIn('action="/paso/materias/publicar"', body)
        self.assertEqual(self.wizard.status()["materias"][0], "falta")
        self.publish.side_effect = self.promote
        status, _, body = self.post("/paso/materias/publicar")
        self.assertFalse(self.publish.call_args.kwargs["dry_run"])
        self.assertIn("publicad", body)
        self.assertEqual(self.wizard.status()["materias"][0], "hecho")
        status, _, body = self.get("/paso/materias")
        self.assertNotIn('action="/paso/materias/pendiente"', body)

    def promote(self, runtime, registry, *, dry_run=True):
        """Como publish_enrollment: tras publicar, las altas dejan de estar pendientes."""
        if dry_run:
            return {"mode": "dry_run", "changes": [("01", 2, 1, None, "x")], "sha256": "abc"}
        data = json.loads(self.registry.read_text(encoding="utf-8"))
        for course in data["courses"]:
            course["provenance"] = "workbook"
        self.registry.write_text(json.dumps(data), encoding="utf-8")
        return {"mode": "published", "changes": [1], "sha256": "def"}

    def test_skipped_or_failed_publication_stays_pending_and_can_resume(self):
        self.write_registry()
        self.publish.side_effect = SyncError("Drive no respondió.")
        status, _, body = self.post("/paso/materias/confirmar", self.selection())
        self.assertIn("queda pendiente", body)
        status, _, body = self.get("/")
        self.assertIn("pendientes de publicar", body)
        self.assertEqual(self.wizard.status()["materias"][0], "falta")
        status, _, body = self.get("/paso/materias")
        self.assertIn("pendientes de publicar", body)
        self.assertIn('action="/paso/materias/pendiente"', body)
        self.publish.side_effect = self.promote
        status, _, body = self.post("/paso/materias/pendiente")
        self.assertEqual(status, 200)
        self.assertTrue(self.publish.call_args.kwargs["dry_run"])
        self.assertIn('action="/paso/materias/publicar"', body)
        status, _, body = self.post("/paso/materias/publicar")
        self.assertIn("publicad", body)
        self.assertEqual(self.wizard.status()["materias"][0], "hecho")

    def test_corrupt_registry_is_a_plain_error(self):
        self.registry.parent.mkdir(parents=True, exist_ok=True)
        self.registry.write_text("{", encoding="utf-8")
        status, _, body = self.post("/paso/materias/vista-previa", self.selection())
        self.assertEqual(status, 400)
        self.assertIn("course-registry.json", body)
        self.assertIn("dañado", body)
        self.assertEqual(self.wizard.status()["materias"][0], "error")

    def test_invalid_selection_is_an_error_and_changes_nothing(self):
        self.write_registry()
        before = self.registry.read_bytes()
        cases = (({"curso": []}, "Marque al menos una materia"),
                 ({"bloques-990001": []}, "bloque"),
                 ({"semestre": "2027"}, "semestre"),
                 ({"curso": ["555"], "bloques-555": ["1"]}, "no aparece"))
        for extra, message in cases:
            with self.subTest(message=message):
                for path in ("/paso/materias/vista-previa", "/paso/materias/confirmar"):
                    status, _, body = self.post(path, self.selection(**extra))
                    self.assertEqual(status, 400)
                    self.assertIn(message, body)
        self.assertEqual(self.registry.read_bytes(), before)
        self.publish.assert_not_called()


RAW = ("El JSON debe corresponder", "cliente OAuth", "endpoints oficiales", "tipo Desktop (installed)",
       "Vencimiento inválido", "YYYY-MM-DD", "Canvas Sync V0", "DPAPI",
       "No hay base local y estado de control", "requirements-drive.txt", "[Errno", "(OSError)",
       "variable ACADEMIC_MANAGER_DATA", "No se pudo leer el JSON",
       "estado compartido", "diario de creación", "detalle técnico:", "academic-operations.json",
       "database-work-state.json", "identidad técnica", "No se pudo crear el archivo en Drive",
       "No existe autorización local", "1 materias", "estado de workbook", "identidad de publicación")


class PlainLanguageTests(ServerCase):
    """Los mensajes técnicos de los módulos se dicen en lenguaje sencillo en la página."""

    def assert_plain(self, body):
        for raw in RAW:
            self.assertNotIn(raw, body)

    def test_error_paths_use_plain_spanish(self):
        with patch.dict(os.environ, {"ACADEMIC_MANAGER_DATA": str(self.root / "otra")}):
            self.assert_plain(self.get("/paso/datos")[2])
        self.choose_data_folder()
        self.check.side_effect = OSError("disco")
        self.assert_plain(self.get("/paso/equipo")[2])
        vence = (self.now + timedelta(days=60)).strftime("%Y-%m-%dT%H:%M")
        status, _, body = self.post("/paso/canvas", {"token": TOKEN, "vence": "mañana"})
        self.assertEqual(status, 400)
        self.assertIn("Elija la fecha y la hora", body)
        self.assert_plain(body)
        with patch.object(canvas_auth, "os", types.SimpleNamespace(name="posix")):
            status, _, body = self.post("/paso/canvas", {"token": TOKEN, "vence": vence})
        self.assertIn("solo funciona en Windows", body)
        self.assert_plain(body)
        for raw in (b"{", json.dumps({"web": {}}).encode(),
                    json.dumps(desktop_client(token_uri="https://evil.example/t")).encode()):
            status, _, body = self.upload("/paso/google/cliente", raw)
            self.assertEqual(status, 400)
            self.assert_plain(body)
        self.upload("/paso/google/cliente", json.dumps(desktop_client()).encode())
        self.drive_login.side_effect = CaptureError(
            "Instale requirements-drive.txt en el Python que ejecuta Capture.")
        status, _, body = self.post("/paso/google/login")
        self.assertIn("Vuelva a ejecutar el instalador", body)
        self.assert_plain(body)
        from academic_store.store import DB_NAME
        (self.data / DB_NAME).write_bytes(b"")  # base sin estado de control publicado
        fields = {"semestre": "2027-1", "curso": ["990001"], "bloques-990001": ["1"]}
        status, _, body = self.post("/paso/materias/vista-previa", fields)
        self.assertEqual(status, 400)
        self.assertIn("todavía no tiene la base de datos", body)
        self.assert_plain(body)
        self.publish.side_effect = PermissionError(13, "Permission denied")
        with patch.object(self.wizard, "deps", {**self.wizard.deps, "load_registry": lambda r: REGISTRY}):
            status, _, body = self.post("/paso/materias/publicar")
        self.assertEqual(status, 400)
        self.assertIn("No se publicó", body)
        self.assert_plain(body)


class MainTests(unittest.TestCase):
    def test_main_opens_the_browser_with_the_entry_link(self):
        from academic_app import __main__ as entry, updates
        fake = MagicMock()
        fake.url = "http://127.0.0.1:5000/?clave=s"
        with patch.object(entry.server, "create", return_value=fake) as create, \
                patch.object(entry.webbrowser, "open") as opened, \
                patch.object(updates, "check", return_value=None) as checked, \
                contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(entry.main([]), 0)
            create.call_args.args[0].update_thread.join(5)
        create.assert_called_once()
        opened.assert_called_once_with(fake.url)
        fake.serve_forever.assert_called_once()
        self.assertIn("Asistente", out.getvalue())
        checked.assert_called_once()  # la comprobación de versión arranca al abrir la app


UPDATE_DATA = b"MZ synthetic installer " * 128


class UpdateStepTests(ServerCase):
    """Aviso de versión nueva al abrir y «Actualizar ahora»: descarga verificada y abre el instalador."""

    def setUp(self):
        super().setUp()
        import hashlib
        from academic_app import updates
        self.updates = updates
        self.downloads = Path(tempfile.mkdtemp()) / "AcademicManager-actualizacion"
        self.addCleanup(__import__("shutil").rmtree, self.downloads.parent, True)
        self.release = updates.Release(
            "9.9.0", "https://github.com/Ds0957/Academic-Manager/releases/download/v9.9.0/"
                     "AcademicManager-Setup-9.9.0.exe", hashlib.sha256(UPDATE_DATA).hexdigest(), len(UPDATE_DATA))
        self.served = UPDATE_DATA
        self.check_update = MagicMock(return_value=self.release)
        self.launch = MagicMock()
        self.download = MagicMock(side_effect=self.fake_download)
        self.wizard.deps.update(check_update=self.check_update, download_update=self.download,
                                launch_update=self.launch)

    def fake_download(self, release):
        from tests.test_academic_updates import FakeOpener
        opener = FakeOpener(data=self.served, url="https://release-assets.githubusercontent.com/x")
        return self.updates.download(release, self.downloads, opener=opener)

    def started(self):
        self.wizard.start_update_check().join(5)

    def test_double_click_downloads_and_opens_the_installer_only_once(self):
        # El navegador envía dos POST por un doble clic; action_lock los pone en fila, no descarta el segundo.
        real = self.fake_download

        def slow(release):
            time.sleep(0.3)
            return real(release)
        self.download.side_effect = slow
        self.started()
        statuses = []
        clicks = [threading.Thread(target=lambda: statuses.append(self.post("/actualizar")[0]))
                  for _ in range(2)]
        for click in clicks:
            click.start()
            time.sleep(0.05)
        for click in clicks:
            click.join(10)
        self.assertEqual(sorted(statuses), [200, 303])
        self.download.assert_called_once()
        self.launch.assert_called_once()

    def test_newer_version_shows_a_card_with_both_versions_and_the_button(self):
        self.started()
        self.check_update.assert_called_once_with(app_version())
        status, _, body = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn("9.9.0", body)
        self.assertIn(app_version(), body)
        self.assertIn('action="/actualizar"', body)
        self.assertIn(f'name="csrf" value="{self.server.csrf}"', body)
        self.assertIn("Actualizar ahora", body)
        self.assertLess(body.index("Actualizar ahora"), body.index("Carpeta de datos"))
        for raw in RAW + ("github", "sha256", "http"):
            self.assertNotIn(raw, body.split("<main>")[1])

    def test_no_update_leaves_the_home_page_exactly_as_before(self):
        before = self.get("/")[2]
        self.check_update.return_value = None
        self.started()
        self.assertEqual(self.get("/")[2], before)
        self.assertNotIn("Actualizar ahora", before)

    def test_failed_check_shows_no_card_and_home_works(self):
        self.check_update.side_effect = RuntimeError("sin red")
        self.started()
        status, _, body = self.get("/")
        self.assertEqual(status, 200)
        self.assertNotIn("Actualizar ahora", body)
        self.assertIn("Carpeta de datos", body)

    def test_update_downloads_verifies_opens_the_installer_and_closes(self):
        self.started()
        status, _, body = self.post("/actualizar")
        self.assertEqual(status, 200)
        self.assertIn("instalador", body)
        expected = self.downloads / "AcademicManager-Setup-9.9.0.exe"
        self.launch.assert_called_once_with(expected)
        self.assertEqual(expected.read_bytes(), UPDATE_DATA)
        self.thread.join(5)
        self.assertFalse(self.thread.is_alive())

    def test_digest_mismatch_shows_the_error_and_runs_nothing(self):
        self.served = UPDATE_DATA[:-1] + b"X"
        self.started()
        status, _, body = self.post("/actualizar")
        self.assertEqual(status, 400)
        self.assertIn("no se ejecutó", body)
        self.assertIn("Carpeta de datos", body)  # vuelve al resumen
        self.launch.assert_not_called()
        self.assertEqual(list(self.downloads.glob("*")), [])
        self.assertTrue(self.thread.is_alive())
        for raw in RAW:
            self.assertNotIn(raw, body)

    def test_launch_failure_is_shown_and_server_keeps_running(self):
        self.launch.side_effect = self.updates.UpdateError(self.updates.ONLY_WINDOWS)
        self.started()
        status, _, body = self.post("/actualizar")
        self.assertEqual(status, 400)
        self.assertIn("solo en Windows", body)
        self.assertTrue(self.thread.is_alive())

    def test_without_a_known_update_the_button_goes_back_home(self):
        status, response, _ = self.post("/actualizar")
        self.assertEqual(status, 303)
        self.assertEqual(response.getheader("Location"), "/")
        self.download.assert_not_called()
        self.launch.assert_not_called()

    def test_update_requires_the_form_token(self):
        self.started()
        status, _, _ = self.post("/actualizar", csrf=False)
        self.assertEqual(status, 403)
        self.download.assert_not_called()
        self.launch.assert_not_called()


class NewBaseStepTests(ServerCase):
    """Paso 6 de una persona nueva: crear la base → publicarla → materias como siempre."""

    def setUp(self):
        super().setUp()
        self.choose_data_folder()
        self.create_base = MagicMock(side_effect=self.fake_create)
        self.wizard.deps["create_base"] = self.create_base
        self.drive_folder = self.root / "drive" / "Academic Manager"

    def fake_create(self, runtime, drive_root):
        # Como academic_store.bootstrap.create: base y estado instalados localmente.
        (runtime / "academic-manager.sqlite").write_bytes(b"base")
        (runtime / "control" / "database-work-state.json").write_text("{}", encoding="utf-8")
        return {"mode": "created"}

    def ready(self):
        self.drive_folder.mkdir(parents=True)
        self.assertEqual(self.post("/paso/drive", {"ruta": str(self.drive_folder)})[0], 303)
        self.upload("/paso/google/cliente", json.dumps(desktop_client()).encode())
        self.assertEqual(self.post("/paso/google/login")[0], 303)

    def test_new_user_sees_the_create_step_with_what_is_missing(self):
        status, _, body = self.get("/paso/materias")
        self.assertEqual(status, 200)
        self.assertIn("Crear su base de datos", body)
        self.assertIn("paso 5", body)
        self.assertIn("paso 4", body)
        self.assertNotIn('action="/paso/materias/crear-base"', body)
        self.courses.assert_not_called()
        self.assertIn("Falta crear la base de datos", self.wizard.status()["materias"][1])

    def test_create_then_choose_courses(self):
        self.ready()
        status, _, body = self.get("/paso/materias")
        self.assertIn('action="/paso/materias/crear-base"', body)
        self.assertIn("Crear la base de datos", body)
        status, response, _ = self.post("/paso/materias/crear-base")
        self.assertEqual(status, 303)
        self.create_base.assert_called_once_with(self.data, str(self.drive_folder))
        status, _, body = self.get("/paso/materias")
        self.assertIn("publicada en su", body)
        self.assertIn('type="checkbox" name="curso" value="990001"', body)
        status, _, body = self.post("/paso/materias/crear-base")
        self.assertEqual(status, 400)
        self.assertIn("ya tiene la base de datos", body)
        self.assertEqual(self.create_base.call_count, 1)
        self.assert_no_secrets()

    def test_failure_is_explained_and_can_be_resumed(self):
        from academic_store import bootstrap
        self.ready()
        self.create_base.side_effect = bootstrap.BootstrapError(
            "Su carpeta de Drive ya tiene una base de Academic Manager (o parte de ella).")
        status, _, body = self.post("/paso/materias/crear-base")
        self.assertEqual(status, 400)
        self.assertIn("No se creó la base", body)
        self.assertIn("ya tiene una base de Academic Manager", body)
        journal = self.data / "procesamiento" / "bootstrap" / "journal.json"
        journal.parent.mkdir(parents=True)
        journal.write_text(json.dumps({"schema_version": 1, "step": "store_created"}), encoding="utf-8")
        self.assertIn("quedó a medias", self.wizard.status()["materias"][1])
        status, _, body = self.get("/paso/materias")
        self.assertIn("Retomar la creación", body)

    def test_missing_write_identity_is_said_plainly(self):
        self.ready()
        self.create_base.side_effect = SyncError("No hay identidad técnica local válida para publicar.")
        status, _, body = self.post("/paso/materias/crear-base")
        self.assertIn("permiso para guardar", body)

    def test_real_new_base_accepts_the_first_semester(self):
        """Con la base vacía real (Drive simulado), el alta crea el registro y su primer semestre."""
        from academic_store import bootstrap
        from tests.test_store_bootstrap import FakeDrive
        fake = FakeDrive()
        fake.files["f-am"]["parent"] = "root"
        for patcher in fake.patches() + [patch("canvas_sync.workbook_blocks._drive_service",
                                               return_value=object())]:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.wizard.deps["create_base"] = bootstrap.create
        self.ready()
        mi_unidad = self.root / "G" / "Mi unidad" / "Academic Manager"
        mi_unidad.mkdir(parents=True)
        self.assertEqual(self.post("/paso/drive", {"ruta": str(mi_unidad)})[0], 303)
        self.assertEqual(self.post("/paso/materias/crear-base")[0], 303)
        status, _, body = self.post("/paso/materias/vista-previa", self.selection())
        self.assertIn('action="/paso/materias/confirmar"', body)
        status, _, body = self.post("/paso/materias/confirmar", self.selection())
        self.assertEqual(status, 200)
        registry = json.loads((self.data / "control" / "canvas-sync" / "course-registry.json")
                              .read_text(encoding="utf-8"))
        self.assertEqual((registry["active_semester"], registry["active_block"]), ("2027-1", 1))
        self.assertEqual(registry["courses"][0]["subject_id"], "MAT-20271-C990001")
        self.assertTrue(self.publish.call_args.kwargs["dry_run"])

    @staticmethod
    def selection():
        return {"semestre": "2027-1", "curso": ["990001"], "bloques-990001": ["1", "2"]}

    def test_existing_user_keeps_the_courses_page(self):
        """Regresión: con base y registro, el paso 6 sigue mostrando las materias, sin crear nada."""
        (self.data / "academic-manager.sqlite").write_bytes(b"base")
        status, _, body = self.get("/paso/materias")
        self.assertNotIn("Crear su base de datos", body)
        self.courses.assert_called_once_with(self.data)
        self.create_base.assert_not_called()


class IndexAfterPublishTests(ServerCase):
    """Tras «Publicar» se reconstruye el índice; si falla, la publicación queda y se puede reintentar."""

    def setUp(self):
        super().setUp()
        self.choose_data_folder()
        registry = self.data / "control" / "canvas-sync" / "course-registry.json"
        registry.parent.mkdir(parents=True, exist_ok=True)
        registry.write_text(json.dumps(REGISTRY), encoding="utf-8")
        self.publish.return_value = {"mode": "published", "changes": [1], "sha256": "def"}
        self.wizard.deps["load_registry"] = lambda runtime: REGISTRY

    def test_singular_course_count(self):
        self.assertEqual(self.wizard.status()["materias"], ("hecho", "1 materia en el semestre 2026-2."))

    def test_index_is_built_after_publishing(self):
        status, _, body = self.post("/paso/materias/publicar")
        self.assertEqual(status, 200)
        self.index.assert_called_once_with(self.data)
        self.assertIn("Materias publicadas", body)
        self.assertIn("Índice de búsqueda de este PC actualizado", body)

    def test_index_failure_keeps_the_publication_and_offers_retry(self):
        self.index.side_effect = SyncError("La matrícula Canvas no coincide con Excel.")
        status, _, body = self.post("/paso/materias/publicar")
        self.assertEqual(status, 200)
        self.assertIn("Materias publicadas", body)
        self.assertIn("no se pudo actualizar", body)
        self.assertIn('action="/paso/materias/indice"', body)
        self.assertNotIn("matrícula Canvas", body)
        self.assertEqual(self.publish.call_count, 1)
        self.index.side_effect = None
        status, _, body = self.post("/paso/materias/indice")
        self.assertEqual(status, 200)
        self.assertIn("actualizado", body)
        self.assertEqual(self.publish.call_count, 1)

    def test_default_builder_uses_the_drive_folder(self):
        from academic_app import wizard
        folder = self.root / "Academic Manager"
        folder.mkdir()
        paths.save_config(drive_root=str(folder))
        with patch("academic_knowledge.index.build", return_value={}) as build:
            wizard.build_index(self.data)
        build.assert_called_once_with(self.data, drive_root=folder)


class NewUserPlainLanguageTests(ServerCase):
    """Los caminos de una persona nueva no muestran nombres de archivos ni mensajes internos."""

    def setUp(self):
        super().setUp()
        from tests.test_store_bootstrap import FakeDrive
        self.fake = FakeDrive()
        self.fake.files["f-am"]["parent"] = "root"
        for patcher in self.fake.patches() + [patch("canvas_sync.workbook_blocks._drive_service",
                                                    return_value=object())]:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.choose_data_folder()
        folder = self.root / "G" / "Mi unidad" / "Academic Manager"
        folder.mkdir(parents=True)
        self.post("/paso/drive", {"ruta": str(folder)})
        self.upload("/paso/google/cliente", json.dumps(desktop_client()).encode())
        self.post("/paso/google/login")

    def create(self):
        status, _, body = self.post("/paso/materias/crear-base")
        for raw in RAW:
            self.assertNotIn(raw, body)
        return status, body

    def test_bootstrap_failures_are_plain(self):
        self.fake.fail["create_store"] = CaptureError("No se pudo crear el archivo en Drive.")
        status, body = self.create()
        self.assertEqual(status, 400)
        self.assertIn("Google Drive no respondió", body)
        self.fake.fail["state_readback"] = True
        self.assertIn("no quedó como se esperaba", self.create()[1])
        with patch("academic_store.bootstrap._install", side_effect=PermissionError(13, "denegado")):
            status, body = self.create()
        self.assertIn("Detalle técnico: PermissionError", body)
        original = __import__("academic_store.bootstrap", fromlist=["atomic_json"]).atomic_json
        def fail_operations(path, data):
            if Path(path).name == "academic-operations.json":
                raise OSError("disco")
            return original(path, data)
        with patch("academic_store.bootstrap.atomic_json", side_effect=fail_operations):
            status, body = self.create()
        self.assertIn("copiada a este PC", body)
        self.assertEqual(self.create()[0], 303)
        journal = self.data / "procesamiento" / "bootstrap" / "journal.json"
        journal.write_text("{", encoding="utf-8")
        status, _, body = self.get("/paso/materias")
        for raw in RAW:
            self.assertNotIn(raw, body)
        self.assertIn("están dañados", body)

    def test_missing_identity_canvas_and_singular_are_plain(self):
        with patch("canvas_sync.workbook_blocks._drive_service",
                   side_effect=SyncError("No hay identidad técnica local válida para publicar.")):
            status, body = self.create()
        self.assertIn("permiso para guardar", body)
        self.assertEqual(self.create()[0], 303)
        self.courses.side_effect = SyncError("No existe autorización local: ejecute login.")
        status, _, body = self.get("/paso/materias")
        self.assertIn("Falta conectar Canvas", body)
        for raw in RAW:
            self.assertNotIn(raw, body)
        self.courses.side_effect = None
        fields = {"semestre": "2027-1", "curso": ["990001"], "bloques-990001": ["1"]}
        status, _, body = self.post("/paso/materias/confirmar", fields)
        self.assertIn("1 materia guardada en este PC", body)
        self.publish.side_effect = SyncError("La liberación del estado compartido no quedó confirmada.")
        status, _, body = self.post("/paso/materias/publicar")
        self.assertIn("No se publicó", body)
        for raw in RAW:
            self.assertNotIn(raw, body)



def check_result(verdict, whisperx=False, ffmpeg=None):
    ffmpeg = whisperx if ffmpeg is None else ffmpeg
    return ({"veredicto": verdict, "programas": {"whisperx": {"valor": whisperx}, "ffmpeg": {"valor": ffmpeg}}},
            f"Resultado: {verdict}")


class WhisperXStepTests(ServerCase):
    """Paso 2: WhisperX se instala desde el asistente solo si la comprobación lo permite."""

    def setUp(self):
        super().setUp()
        self.wx_status = MagicMock(return_value={"estado": "sin_iniciar"})
        self.wx_start = MagicMock(return_value={"estado": "instalando", "variante": "cpu"})
        self.wizard.deps.update(whisperx_status=self.wx_status, whisperx_start=self.wx_start)
        self.choose_data_folder()

    def test_apt_pc_without_whisperx_offers_install(self):
        self.check.return_value = check_result("solo_cpu")
        status, _, body = self.get("/paso/equipo")
        self.assertEqual(status, 200)
        self.assertIn('action="/paso/equipo/whisperx"', body)
        self.assertIn("Instalar WhisperX", body)
        self.assertIn("procesador", body)
        status, response, _ = self.post("/paso/equipo/whisperx")
        self.assertEqual((status, response.getheader("Location")), (303, "/paso/equipo"))
        self.wx_start.assert_called_once_with(self.data, "solo_cpu")
        _, _, body = self.get("/paso/equipo")
        self.assertIn("La instalación de WhisperX empezó", body)

    def test_gpu_verdict_installs_the_graphics_card_build(self):
        self.check.return_value = check_result("apto_gpu")
        _, _, body = self.get("/paso/equipo")
        self.assertIn("tarjeta gráfica", body)
        self.post("/paso/equipo/whisperx")
        self.wx_start.assert_called_once_with(self.data, "apto_gpu")

    def test_no_apto_is_said_plainly_and_never_installs(self):
        self.check.return_value = check_result("no_apto")
        _, _, body = self.get("/paso/equipo")
        self.assertIn("no se instala WhisperX", body)
        self.assertNotIn('action="/paso/equipo/whisperx"', body)
        status, _, body = self.post("/paso/equipo/whisperx")
        self.assertEqual(status, 400)
        self.assertIn("no se instala WhisperX", body)
        self.wx_start.assert_not_called()

    def test_the_verdict_comes_from_a_fresh_check_not_from_the_form(self):
        self.check.return_value = check_result("no_apto")
        self.post("/paso/equipo/whisperx", {"veredicto": "apto_gpu"})
        self.wx_start.assert_not_called()
        self.check.assert_called_with(self.data)

    def test_installed_running_and_failed_states(self):
        self.check.return_value = check_result("solo_cpu", whisperx=True)
        _, _, body = self.get("/paso/equipo")
        self.assertIn("WhisperX ya está instalado", body)
        self.assertNotIn('action="/paso/equipo/whisperx"', body)
        status, _, _ = self.post("/paso/equipo/whisperx")
        self.assertEqual(status, 303)
        self.wx_start.assert_not_called()
        self.check.return_value = check_result("solo_cpu")
        self.wx_status.return_value = {"estado": "instalando", "variante": "cpu"}
        _, _, body = self.get("/paso/equipo")
        self.assertIn("Instalando WhisperX", body)
        self.assertNotIn('action="/paso/equipo/whisperx"', body)
        for state in ("fallo", "interrumpida"):
            with self.subTest(state=state):
                self.wx_status.return_value = {"estado": state, "codigo": 1}
                _, _, body = self.get("/paso/equipo")
                self.assertIn("Reintentar la instalación", body)
                self.assertIn('action="/paso/equipo/whisperx"', body)

    def test_texts_are_accurate_next_to_the_install_button(self):
        """A2: la comprobación no cambia nada, pero el botón sí instala; «instalado» sin detectar no es «no terminó»."""
        self.check.return_value = check_result("solo_cpu")
        _, _, body = self.get("/paso/equipo")
        self.assertNotIn("solo mira el PC: no instala ni cambia nada.", body)
        self.assertIn("La comprobación solo mira el PC", body)
        self.wx_status.return_value = {"estado": "instalado", "codigo": 0}
        _, _, body = self.get("/paso/equipo")
        self.assertNotIn("no terminó", body)
        self.assertIn("se instaló, pero este PC todavía no lo encuentra", body)
        self.assertIn('action="/paso/equipo/whisperx"', body)

    def test_whisperx_without_ffmpeg_offers_to_complete(self):
        self.check.return_value = check_result("solo_cpu", whisperx=True, ffmpeg=False)
        _, _, body = self.get("/paso/equipo")
        self.assertIn("Completar la instalación", body)
        self.assertIn("FFmpeg", body)
        self.post("/paso/equipo/whisperx")
        self.wx_start.assert_called_once_with(self.data, "solo_cpu")

    def test_launch_failure_is_plain(self):
        self.check.return_value = check_result("solo_cpu")
        self.wx_start.side_effect = OSError("sin permiso")
        status, _, body = self.post("/paso/equipo/whisperx")
        self.assertEqual(status, 400)
        self.assertIn("No se pudo empezar la instalación de WhisperX", body)
        self.assertNotIn("sin permiso", body)

    def test_step_status_is_unchanged(self):
        from academic_setup.check import HEADLINES
        self.check.return_value = check_result("solo_cpu")
        self.get("/paso/equipo")
        self.assertEqual(self.wizard.status()["equipo"], ("hecho", HEADLINES["solo_cpu"]))

    def test_needs_the_data_folder_first(self):
        paths.save_config(runtime=str(self.root / "no-existe"))
        status, _, body = self.post("/paso/equipo/whisperx")
        self.assertEqual(status, 400)
        self.wx_start.assert_not_called()

    def test_default_dependencies_reuse_academic_setup(self):
        from academic_app.wizard import Wizard
        from academic_setup import whisperx
        wizard = Wizard()
        self.assertIs(wizard.deps["whisperx_start"], whisperx.start)
        self.assertIs(wizard.deps["whisperx_status"], whisperx.status)


class DailyTaskStepTests(ServerCase):
    """Paso 7: la tarea diaria de Canvas para esta copia, con el script de instalación de siempre."""

    MISSING = {"nombre": "tarea_diaria", "estado": "error",
               "detalle": "No existe la tarea programada AcademicManager-CanvasDaily."}
    ACTIVE = {"nombre": "tarea_diaria", "estado": "ok", "detalle": "Activa; próxima ejecución: 26/10/2026 8:00:00"}

    def setUp(self):
        super().setUp()
        self.task_status = MagicMock(return_value=dict(self.MISSING))
        self.register = MagicMock(side_effect=lambda: self.task_status.configure_mock(
            return_value=dict(self.ACTIVE)))
        self.wizard.deps.update(task_status=self.task_status, register_task=self.register)

    def test_home_keeps_the_six_steps_and_adds_the_daily_task(self):
        from academic_app import version
        _, _, body = self.get("/")
        titles = ["1. Carpeta de datos", "2. Comprobación del equipo", "3. Canvas", "4. Google Drive",
                  "5. Carpeta de Drive", "6. Materias", "7. Sincronización diaria"]
        positions = [body.index(t) for t in titles]
        self.assertEqual(positions, sorted(positions))
        self.assertIn('href="/paso/tarea"', body)
        self.assertIn(f"Versión {version()}", body)
        self.task_status.assert_not_called()  # el resumen no consulta Windows

    def test_needs_the_data_folder_first(self):
        status, _, body = self.get("/paso/tarea")
        self.assertIn("Primero elija la carpeta de datos", body)
        status, _, _ = self.post("/paso/tarea")
        self.assertEqual(status, 400)
        self.register.assert_not_called()

    def test_activate_registers_and_shows_it_active(self):
        self.choose_data_folder()
        _, _, body = self.get("/paso/tarea")
        self.assertIn("Activar la sincronización diaria", body)
        self.assertIn("08:00", body)
        self.assertEqual(self.wizard.status()["tarea"][0], "falta")
        status, response, _ = self.post("/paso/tarea")
        self.assertEqual((status, response.getheader("Location")), (303, "/paso/tarea"))
        self.register.assert_called_once_with()
        _, _, body = self.get("/paso/tarea")
        self.assertIn("Sincronización diaria activada", body)
        self.assertIn("Activa; próxima ejecución", body)
        self.assertEqual(self.wizard.status()["tarea"][0], "hecho")
        self.assertIn("Volver a activar", body)

    def test_failure_is_plain(self):
        from academic_app.daily_task import TaskError
        self.choose_data_folder()
        self.register.side_effect = TaskError("Windows no dejó crear la tarea diaria.")
        status, _, body = self.post("/paso/tarea")
        self.assertEqual(status, 400)
        self.assertIn("Windows no dejó crear la tarea diaria.", body)

    def test_other_task_problem_is_an_error(self):
        self.choose_data_folder()
        self.task_status.return_value = {"nombre": "tarea_diaria", "estado": "error",
                                         "detalle": "La tarea AcademicManager-CanvasDaily está «Deshabilitado»: no se ejecutará."}
        self.get("/paso/tarea")
        self.assertEqual(self.wizard.status()["tarea"][0], "error")

    def test_not_windows_is_said_plainly(self):
        self.choose_data_folder()
        self.task_status.return_value = {"nombre": "tarea_diaria", "estado": "no_disponible",
                                         "detalle": "Solo se comprueba en Windows."}
        _, _, body = self.get("/paso/tarea")
        self.assertIn("solo funciona en Windows", body)


class DailyTaskModuleTests(unittest.TestCase):
    def test_command_reuses_the_install_script_for_this_copy(self):
        from academic_app import daily_task
        repo, python = Path("/am/app"), Path("/am/python/python.exe")
        self.assertEqual(daily_task.command(repo, python),
                         ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                          "-File", str(repo / "scripts" / "install-canvas-daily-task.ps1"),
                          "-Repo", str(repo), "-Python", str(python)])
        self.assertEqual(daily_task.repo_root(), Path(daily_task.__file__).resolve().parents[1])

    def test_register_runs_the_script_and_reports_failures(self):
        from academic_app import daily_task
        run = MagicMock(return_value=types.SimpleNamespace(returncode=0, stdout="", stderr=""))
        with patch.object(daily_task, "os", types.SimpleNamespace(name="nt")):
            daily_task.register(repo=Path("/am/app"), python=Path("/am/python/python.exe"), run=run)
            self.assertEqual(run.call_args.args[0],
                             daily_task.command(Path("/am/app"), Path("/am/python/python.exe")))
            run.return_value = types.SimpleNamespace(returncode=1, stdout="", stderr="Acceso denegado")
            with self.assertRaises(daily_task.TaskError) as caught:
                daily_task.register(repo=Path("/am/app"), python=Path("/am/python/python.exe"), run=run)
        self.assertIn("Windows no dejó crear la tarea diaria", str(caught.exception))
        with patch.object(daily_task, "os", types.SimpleNamespace(name="posix")), \
                self.assertRaises(daily_task.TaskError):
            daily_task.register(run=run)

    def test_status_reuses_academic_access(self):
        from academic_app import daily_task
        with patch("academic_access.system._scheduled_task", return_value=None):
            item = daily_task.status()
        self.assertEqual(item["estado"], "error")
        self.assertIn("AcademicManager-CanvasDaily", item["detalle"])



class DailyTaskWindowsOutputTests(ServerCase):
    """B2: lo que devuelve schtasks de verdad cuando la tarea no existe (o cuando falla de otro modo)."""

    def setUp(self):
        super().setUp()
        from academic_access import system
        from academic_app import daily_task
        self.wizard.deps["task_status"] = daily_task.status
        self.enterContext(patch.object(system, "os", types.SimpleNamespace(name="nt")))
        self.run_mock = self.enterContext(patch.object(system.subprocess, "run"))
        self.choose_data_folder()

    def answer(self, code, stderr="", stdout=""):
        import subprocess
        self.run_mock.return_value = subprocess.CompletedProcess([], code, stdout=stdout, stderr=stderr)

    def test_missing_task_is_falta_in_spanish_and_english_windows(self):
        for stderr in ("ERROR: El sistema no puede encontrar el archivo especificado.\r\n",
                       "ERROR: The system cannot find the file specified.\r\n"):
            with self.subTest(stderr=stderr):
                self.answer(1, stderr)
                _, _, body = self.get("/paso/tarea")
                self.assertEqual(self.wizard.status()["tarea"], ("falta", "Todavía no está activada en este PC."))
                self.assertIn("Activar la sincronización diaria", body)
                self.assertNotIn("código", body)
                self.assertNotIn("ERROR:", body)
                self.assertNotIn("procesamiento", body)

    def test_other_windows_failure_is_a_plain_error(self):
        self.answer(1, "ERROR: Acceso denegado.\r\n")
        _, _, body = self.get("/paso/tarea")
        state, detail = self.wizard.status()["tarea"]
        self.assertEqual(state, "error")
        for raw in ("código", "ERROR:", "Acceso denegado", "schtasks", "AcademicManager-CanvasDaily"):
            self.assertNotIn(raw, detail)
            self.assertNotIn(raw, body)

    def test_disabled_task_is_plain(self):
        self.answer(0, stdout='"\\AcademicManager-CanvasDaily","N/A","Deshabilitado"\r\n')
        _, _, body = self.get("/paso/tarea")
        state, detail = self.wizard.status()["tarea"]
        self.assertEqual(state, "error")
        self.assertIn("está desactivada", detail)
        self.assertNotIn("AcademicManager-CanvasDaily", body)

    def test_active_task_is_done(self):
        self.answer(0, stdout='"\\AcademicManager-CanvasDaily","26/10/2026 8:00:00","Listo"\r\n')
        self.get("/paso/tarea")
        self.assertEqual(self.wizard.status()["tarea"][0], "hecho")


class ProgramFolderTests(ServerCase):
    """B1: la carpeta de datos (o la de Drive) nunca dentro de la carpeta del programa, que se borra."""

    def setUp(self):
        super().setUp()
        self.local = self.root / "Local"
        self.enterContext(patch.dict(os.environ, {"LOCALAPPDATA": str(self.local)}))
        self.program = self.local / "Programs" / "AcademicManager"

    def assert_refused(self, path, ruta):
        status, _, body = self.post(path, {"ruta": str(ruta)})
        self.assertEqual(status, 400, ruta)
        self.assertIn("carpeta del programa", body)
        self.assertFalse(self.config.exists() and str(ruta) in self.config.read_text(encoding="utf-8"))

    def test_data_folder_inside_the_install_folder_is_refused(self):
        for ruta in (self.program, self.program / "app" / "datos", self.program / "python" / "x" / ".." / "d"):
            with self.subTest(ruta=ruta):
                self.assert_refused("/paso/datos", ruta)
        self.assertFalse((self.program / "app" / "datos").exists())

    def test_data_folder_inside_the_running_code_is_refused(self):
        from academic_app import daily_task
        self.assert_refused("/paso/datos", daily_task.repo_root() / "datos-de-prueba")
        self.assertFalse((daily_task.repo_root() / "datos-de-prueba").exists())

    def test_installed_layout_protects_the_whole_app_folder(self):
        from academic_app import wizard
        app = self.root / "Otro" / "AM"
        (app / "app").mkdir(parents=True)
        (app / "python").mkdir()
        with patch.object(wizard, "_code_root", return_value=app / "app"):
            self.assert_refused("/paso/datos", app / "datos")

    def test_neighbor_folder_with_the_same_prefix_is_accepted(self):
        status, _, _ = self.post("/paso/datos", {"ruta": str(self.local / "Programs" / "AcademicManagerData")})
        self.assertEqual(status, 303)

    def test_drive_folder_inside_the_install_folder_is_refused(self):
        (self.program / "drive").mkdir(parents=True)
        self.assert_refused("/paso/drive", self.program / "drive")


class PlainTaskMessagesTests(unittest.TestCase):
    """A3: sin «código N» ni rutas internas en los mensajes de la tarea."""

    def test_register_failure_has_no_code(self):
        from academic_app import daily_task
        run = MagicMock(return_value=types.SimpleNamespace(returncode=5, stdout="", stderr="Acceso denegado"))
        with patch.object(daily_task, "os", types.SimpleNamespace(name="nt")), \
                self.assertRaises(daily_task.TaskError) as caught:
            daily_task.register(repo=Path("/am/app"), python=Path("/am/python/python.exe"), run=run)
        self.assertNotIn("código", str(caught.exception))
        self.assertNotIn("Acceso denegado", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
