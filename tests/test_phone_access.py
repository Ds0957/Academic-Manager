"""Phone access: state (PIN, pairing, devices, sessions) and the LAN listener.

Everything is synthetic: the state lives in a temporary folder outside the worktree and the
listener is bound to 127.0.0.1 with the client-address predicate replaced, because loopback
clients are rejected by design.
"""
import http.client
import io
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlencode

from academic_app import pages, phone

PIN = "482913"
UA_ANDROID = "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36"


class Clock:
    def __init__(self, now=1_800_000_000.0):
        self.now = now

    def __call__(self):
        return self.now


class StateCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.runtime = Path(self.tmp.name)
        self.clock = Clock()
        self.access = phone.PhoneAccess(self.runtime, clock=self.clock)

    def state_file(self):
        return self.runtime / "control" / "celular" / "acceso.json"

    def paired(self):
        self.access.enable(PIN)
        code = self.access.new_pairing()
        token = self.access.pair(code, "Android")
        return token, self.access.device(token)


class StateTests(StateCase):
    def test_missing_file_means_off(self):
        self.assertFalse(self.access.is_active())
        self.assertEqual(self.access.devices(), [])
        self.assertFalse(self.state_file().exists())

    def test_pin_validation(self):
        for bad in ("12345", "1234567890123", "12a456", "１２３４５６", "", " 123456", None):
            with self.assertRaises(phone.PhoneError):
                self.access.enable(bad)
        self.assertFalse(self.access.is_active())
        self.access.enable("123456")
        self.access.change_pin("123456789012")

    def test_pin_stored_as_pbkdf2_only(self):
        self.access.enable(PIN)
        raw = self.state_file().read_text(encoding="utf-8")
        self.assertNotIn(PIN, raw)
        data = json.loads(raw)
        self.assertEqual(data["schema"], 1)
        self.assertTrue(data["activo"])
        self.assertEqual(data["pin"]["iteraciones"], 200_000)
        self.assertEqual(len(bytes.fromhex(data["pin"]["salt"])), 16)
        # A fresh instance reads the state back.
        again = phone.PhoneAccess(self.runtime, clock=self.clock)
        self.assertTrue(again.is_active())

    def test_same_pin_gets_a_different_salt(self):
        self.access.enable(PIN)
        first = json.loads(self.state_file().read_text())["pin"]
        self.access.change_pin(PIN)
        second = json.loads(self.state_file().read_text())["pin"]
        self.assertNotEqual(first["salt"], second["salt"])
        self.assertNotEqual(first["hash"], second["hash"])

    def test_enable_disable_change_pin_end_sessions(self):
        token, device_id = self.paired()
        for action in (lambda: self.access.change_pin("999999"), lambda: self.access.enable("999999"),
                       self.access.disable):
            self.access.enable("999999")
            session = self.access.login(device_id, "999999")
            self.assertIsNotNone(self.access.session(session))
            action()
            self.assertIsNone(self.access.session(session))
        self.assertFalse(self.access.is_active())
        self.assertEqual(len(self.access.devices()), 1)  # disable keeps devices

    def test_disable_drops_pending_pairing(self):
        self.access.enable(PIN)
        code = self.access.new_pairing()
        self.access.disable()
        self.access.enable(PIN)
        with self.assertRaises(phone.PhoneError):
            self.access.pair(code, "x")

    def test_pairing_only_while_active(self):
        with self.assertRaises(phone.PhoneError):
            self.access.new_pairing()

    def test_pairing_single_use_and_device_token_not_stored(self):
        self.access.enable(PIN)
        code = self.access.new_pairing()
        token = self.access.pair(code, UA_ANDROID)
        self.assertIsNotNone(self.access.device(token))
        with self.assertRaises(phone.PhoneError):
            self.access.pair(code, UA_ANDROID)
        raw = self.state_file().read_text(encoding="utf-8")
        self.assertNotIn(token, raw)
        self.assertNotIn(code, raw)
        self.assertIsNone(self.access.device("not-a-token"))
        self.assertIsNone(self.access.device(""))

    def test_new_pairing_replaces_previous(self):
        self.access.enable(PIN)
        first = self.access.new_pairing()
        second = self.access.new_pairing()
        with self.assertRaises(phone.PhoneError):
            self.access.pair(first, "x")
        self.assertTrue(self.access.pair(second, "x"))

    def test_pairing_expires_after_five_minutes(self):
        self.access.enable(PIN)
        code = self.access.new_pairing()
        self.clock.now += 301
        with self.assertRaises(phone.PhoneError):
            self.access.pair(code, "x")
        code = self.access.new_pairing()
        self.clock.now += 299
        self.assertTrue(self.access.pair(code, "x"))

    def test_device_name_is_short_and_printable(self):
        self.access.enable(PIN)
        self.access.pair(self.access.new_pairing(), "a\x00b\n" + "x" * 200)
        name = self.access.devices()[0]["nombre"]
        self.assertLessEqual(len(name), 60)
        self.assertTrue(name.isprintable())
        self.assertNotIn("token_sha256", self.access.devices()[0])

    def test_seen_updated_at_most_hourly(self):
        token, _ = self.paired()
        with patch.object(phone, "atomic_json", wraps=phone.atomic_json) as writes:
            self.clock.now += 60
            self.access.device(token)
            self.assertEqual(writes.call_count, 0)
            self.clock.now += 3600
            self.access.device(token)
            self.assertEqual(writes.call_count, 1)
        self.assertEqual(self.access.devices()[0]["visto"], int(self.clock.now))

    def test_lockout_after_five_wrong_pins(self):
        _, device_id = self.paired()
        for _ in range(4):
            with self.assertRaises(phone.PhoneError):
                self.access.login(device_id, "000000")
        with self.assertRaises(phone.PhoneError) as locked:
            self.access.login(device_id, "000000")
        self.assertIn("espere", str(locked.exception).lower())
        with self.assertRaises(phone.PhoneError):  # even the right PIN is refused while locked
            self.access.login(device_id, PIN)
        self.clock.now += 901
        self.assertTrue(self.access.login(device_id, PIN))

    def test_success_resets_failures(self):
        _, device_id = self.paired()
        for _ in range(4):
            with self.assertRaises(phone.PhoneError):
                self.access.login(device_id, "000000")
        self.access.login(device_id, PIN)
        for _ in range(4):
            with self.assertRaises(phone.PhoneError):
                self.access.login(device_id, "000000")
        self.assertTrue(self.access.login(device_id, PIN))

    def test_login_unknown_device_refused(self):
        self.access.enable(PIN)
        with self.assertRaises(phone.PhoneError):
            self.access.login("deadbeef", PIN)

    def test_session_expires_after_twelve_hours(self):
        _, device_id = self.paired()
        session = self.access.login(device_id, PIN)
        found = self.access.session(session)
        self.assertEqual(found[0], device_id)
        self.assertTrue(found[1])
        self.clock.now += 12 * 3600 - 1
        self.assertIsNotNone(self.access.session(session))
        self.clock.now += 2
        self.assertIsNone(self.access.session(session))
        self.assertNotIn(session, self.state_file().read_text())

    def test_logout(self):
        _, device_id = self.paired()
        session = self.access.login(device_id, PIN)
        self.access.logout(session)
        self.assertIsNone(self.access.session(session))

    def test_revoke_removes_device_and_sessions(self):
        token, device_id = self.paired()
        session = self.access.login(device_id, PIN)
        self.access.revoke(device_id)
        self.assertIsNone(self.access.session(session))
        self.assertIsNone(self.access.device(token))
        self.assertEqual(self.access.devices(), [])
        self.assertEqual(phone.PhoneAccess(self.runtime).devices(), [])

    def write_state(self, **fields):
        good = {"salt": "00" * 16, "hash": "ab" * 32, "iteraciones": 200_000}
        device = {"id": "d1", "token_sha256": "cd" * 32, "nombre": "Android", "creado": 1, "visto": 1}
        state = {"schema": 1, "activo": True, "pin": good, "dispositivos": [device], **fields}
        self.state_file().parent.mkdir(parents=True, exist_ok=True)
        self.state_file().write_text(json.dumps(state), encoding="utf-8")
        return phone.PhoneAccess(self.runtime, clock=self.clock)

    def test_well_formed_state_is_loaded(self):
        access = self.write_state()
        self.assertTrue(access.is_active())
        self.assertEqual(len(access.devices()), 1)

    def test_malformed_pin_record_means_off_without_crash(self):
        bad_pins = ("x", 5, [], {}, {"salt": "00" * 16, "hash": "ab" * 32},
                    {"salt": "zz", "hash": "ab" * 32, "iteraciones": 200_000},
                    {"salt": "00" * 16, "hash": 7, "iteraciones": 200_000},
                    {"salt": "00" * 16, "hash": "ñ" * 64, "iteraciones": 200_000},
                    {"salt": "00" * 16, "hash": "ab" * 32, "iteraciones": "200000"},
                    {"salt": "00" * 16, "hash": "ab" * 32, "iteraciones": True},
                    {"salt": "00" * 16, "hash": "ab" * 32, "iteraciones": 0})
        for pin in bad_pins:
            access = self.write_state(pin=pin)
            self.assertFalse(access.is_active(), pin)
            self.assertEqual(access.devices(), [], pin)
            with self.assertRaises(phone.PhoneError):  # never a crash, never a session
                access.login("d1", PIN)

    def test_malformed_device_entries_mean_off_without_crash(self):
        for device in ("x", {"id": "d1"}, {"id": 5, "token_sha256": "cd", "nombre": "a", "creado": 1, "visto": 1},
                       {"id": "d1", "token_sha256": None, "nombre": "a", "creado": 1, "visto": 1},
                       {"id": "d1", "token_sha256": "ñ", "nombre": "a", "creado": 1, "visto": 1},
                       {"id": "d1", "token_sha256": "cd", "nombre": "a", "creado": "1", "visto": 1}):
            access = self.write_state(dispositivos=[device])
            self.assertFalse(access.is_active(), device)
            self.assertEqual(access.devices(), [], device)
            self.assertIsNone(access.device("anything"))

    def test_failed_writes_leave_the_state_unchanged(self):
        token, device_id = self.paired()
        before = self.access.devices()
        with patch.object(phone, "atomic_json", side_effect=OSError("disk full")):
            for action in (lambda: self.access.change_pin("999999"), lambda: self.access.enable("999999"),
                           lambda: self.access.revoke(device_id),
                           lambda: self.access.pair(self.access.new_pairing(), "otro")):
                with self.assertRaises(phone.PhoneError):
                    action()
                self.assertEqual(self.access.devices(), before)
                self.assertTrue(self.access.is_active())
            with self.assertRaises(phone.PhoneError):
                self.access.disable()
            self.assertTrue(self.access.is_active())
        self.assertTrue(self.access.login(device_id, PIN))  # the old PIN is still the one in force
        self.assertEqual(self.access.device(token), device_id)
        self.assertEqual(phone.PhoneAccess(self.runtime, clock=self.clock).devices(), before)

    def test_failed_first_enable_keeps_access_off(self):
        with patch.object(phone, "atomic_json", side_effect=OSError("disk full")):
            with self.assertRaises(phone.PhoneError):
                self.access.enable(PIN)
        self.assertFalse(self.access.is_active())
        self.assertFalse(self.state_file().exists())

    def test_symlinked_folder_refused(self):
        target = self.runtime / "elsewhere"
        target.mkdir()
        (self.runtime / "control").mkdir()
        (self.runtime / "control" / "celular").symlink_to(target, target_is_directory=True)
        with self.assertRaises(phone.PhoneError):
            self.access.enable(PIN)
        self.assertEqual(list(target.iterdir()), [])


class LanAddressTests(unittest.TestCase):
    def fake_socket(self, ip):
        class Sock:
            def __init__(self, *args):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def connect(self, addr):
                pass

            def getsockname(self):
                return (ip, 50000)

            def close(self):
                pass
        return Sock

    def test_private_address_returned(self):
        with patch.object(phone.socket, "socket", self.fake_socket("192.168.1.20")):
            self.assertEqual(phone.lan_address(), "192.168.1.20")

    def test_non_private_addresses_refused(self):
        for ip in ("127.0.0.1", "169.254.3.4", "8.8.8.8", "0.0.0.0"):
            with patch.object(phone.socket, "socket", self.fake_socket(ip)):
                self.assertIsNone(phone.lan_address(), ip)

    def test_no_network(self):
        class Broken:
            def __init__(self, *args):
                raise OSError("no network")
        with patch.object(phone.socket, "socket", Broken):
            self.assertIsNone(phone.lan_address())


class FakeWizard:
    def __init__(self, lock):
        self.lock = lock
        self.calls = []

    def handle(self, method, path, form, files, csrf, phone=False):
        self.calls.append({"method": method, "path": path, "form": form, "csrf": csrf,
                           "locked": self.lock.locked(), "phone": phone})
        return pages.Response(200, pages.layout("Panel", "<p>contenido del panel</p>"))


class ListenerCase(StateCase):
    def setUp(self):
        super().setUp()
        self.lock = threading.Lock()
        self.wizard = FakeWizard(self.lock)
        self.log = io.StringIO()
        self.activity = []
        self.access.enable(PIN)
        self.server = phone.PhoneServer(("127.0.0.1", 0), self.wizard, self.access, self.lock,
                                        lambda: self.activity.append(1), log_stream=self.log)
        self.server.client_allowed = lambda ip: True
        self.port = self.server.server_address[1]
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.cookies = {}

    def request(self, method, path, form=None, headers=None, origin=True):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        sent = {"Host": f"127.0.0.1:{self.port}", "User-Agent": UA_ANDROID}
        if method == "POST" and origin:
            sent["Origin"] = f"http://127.0.0.1:{self.port}"
        if self.cookies:
            sent["Cookie"] = "; ".join(f"{k}={v}" for k, v in self.cookies.items())
        body = None
        if form is not None:
            body = urlencode(form)
            sent["Content-Type"] = "application/x-www-form-urlencoded"
        sent.update(headers or {})
        conn.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
        for key, value in sent.items():
            conn.putheader(key, value)
        if body is not None:
            conn.putheader("Content-Length", str(len(body)))
        conn.endheaders(body.encode() if body is not None else None)
        response = conn.getresponse()
        text = response.read().decode("utf-8")
        for header in response.headers.get_all("Set-Cookie") or []:
            name, _, rest = header.partition("=")
            self.cookies[name] = rest.split(";")[0]
        conn.close()
        return response, text

    def login(self):
        code = self.access.new_pairing()
        response, _ = self.request("GET", f"/emparejar?codigo={code}")
        self.assertEqual(response.status, 303)
        self.assertEqual(response.headers["Location"], "/entrar")
        response, _ = self.request("POST", "/entrar", {"pin": PIN})
        self.assertEqual(response.status, 303)
        self.assertEqual(response.headers["Location"], "/panel")
        session = self.access.session(self.cookies[phone.SESSION_COOKIE])
        return code, session[1]


class ListenerTests(ListenerCase):
    def test_loopback_rejected_by_default(self):
        del self.server.client_allowed  # back to the class default predicate
        response, text = self.request("GET", "/entrar")
        self.assertEqual(response.status, 403)
        self.assertFalse(phone.PhoneServer.client_allowed("127.0.0.1"))
        self.assertFalse(phone.PhoneServer.client_allowed("8.8.8.8"))
        self.assertTrue(phone.PhoneServer.client_allowed("192.168.1.30"))

    def test_wrong_host_rejected(self):
        response, _ = self.request("GET", "/entrar", headers={"Host": f"localhost:{self.port}"})
        self.assertEqual(response.status, 403)

    def test_post_without_origin_rejected(self):
        response, _ = self.request("POST", "/entrar", {"pin": PIN}, origin=False)
        self.assertEqual(response.status, 403)

    def test_foreign_origin_rejected(self):
        response, _ = self.request("POST", "/entrar", {"pin": PIN},
                                   headers={"Origin": "http://evil.example"})
        self.assertEqual(response.status, 403)

    def test_inactive_access_rejects_everything(self):
        code = self.access.new_pairing()
        self.access.disable()
        response, text = self.request("GET", f"/emparejar?codigo={code}")
        self.assertEqual(response.status, 403)
        self.assertNotIn(phone.DEVICE_COOKIE, self.cookies)

    def test_unpaired_device_never_sees_pin_form(self):
        response, text = self.request("GET", "/entrar")
        self.assertEqual(response.status, 403)
        self.assertNotIn('name="pin"', text)
        self.assertIn("QR", text)
        response, _ = self.request("GET", "/panel")
        self.assertEqual(response.status, 303)
        self.assertEqual(response.headers["Location"], "/entrar")

    def test_bad_pairing_code(self):
        response, text = self.request("GET", "/emparejar?codigo=nope")
        self.assertEqual(response.status, 403)
        self.assertNotIn(phone.DEVICE_COOKIE, self.cookies)

    def test_full_flow_reaches_wizard_with_session_csrf(self):
        conn_count = len(self.activity)
        code, csrf = self.login()
        self.assertGreater(len(self.activity), conn_count)
        response, text = self.request("GET", "/entrar")
        self.assertEqual(response.status, 200)
        self.assertIn('name="pin"', text)
        response, text = self.request("GET", "/panel?x=1")
        self.assertEqual(response.status, 200)
        self.assertIn("contenido del panel", text)
        call = self.wizard.calls[-1]
        self.assertEqual((call["method"], call["path"], call["csrf"]), ("GET", "/panel", csrf))
        self.assertTrue(call["phone"])  # the wizard renders the phone navigation
        for path in ("/panel/resumen", "/panel/materias", "/panel/instrucciones", "/panel/materia/5",
                     "/panel/publicando/abc"):
            self.assertEqual(self.request("GET", path)[0].status, 200, path)

    def test_cookie_attributes(self):
        code = self.access.new_pairing()
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request("GET", f"/emparejar?codigo={code}", headers={"Host": f"127.0.0.1:{self.port}"})
        header = conn.getresponse().headers["Set-Cookie"]
        conn.close()
        for part in ("HttpOnly", "SameSite=Strict", "Path=/", "Max-Age=15552000"):
            self.assertIn(part, header)

    def test_wrong_pin_shows_form_with_message(self):
        code = self.access.new_pairing()
        self.request("GET", f"/emparejar?codigo={code}")
        response, text = self.request("POST", "/entrar", {"pin": "000000"})
        self.assertEqual(response.status, 403)
        self.assertIn('name="pin"', text)
        self.assertNotIn(phone.SESSION_COOKIE, self.cookies)

    def test_disallowed_paths(self):
        self.login()
        for method, path in (("GET", "/"), ("POST", "/cerrar"), ("GET", "/paso/google"),
                             ("GET", "/panel/configuracion")):
            form = {"csrf": "x"} if method == "POST" else None
            response, text = self.request(method, path, form)
            self.assertEqual(response.status, 403, path)
            self.assertIn("No disponible desde el celular", text)
        self.assertEqual(self.wizard.calls, [])

    def test_post_requires_session_csrf_and_runs_under_lock(self):
        _, csrf = self.login()
        for bad in ({}, {"csrf": "wrong"}):
            response, _ = self.request("POST", "/panel/registrar", {"texto": "hola", **bad})
            self.assertEqual(response.status, 403)
        self.assertEqual(self.wizard.calls, [])
        response, _ = self.request("POST", "/panel/registrar", {"texto": "hola", "csrf": csrf})
        self.assertEqual(response.status, 200)
        call = self.wizard.calls[-1]
        self.assertEqual(call["path"], "/panel/registrar")
        self.assertTrue(call["locked"])
        self.assertTrue(call["phone"])
        self.assertEqual(call["form"]["texto"], ["hola"])
        self.request("POST", "/panel/registrar/aprobar", {"csrf": csrf})
        self.assertEqual(self.wizard.calls[-1]["path"], "/panel/registrar/aprobar")

    def test_logout_and_revoked_device(self):
        self.login()
        response, _ = self.request("POST", "/salir")
        self.assertEqual(response.status, 303)
        self.assertEqual(self.request("GET", "/panel")[0].status, 303)
        self.login()
        self.assertTrue(self.access.revoke(self.access.device(self.cookies[phone.DEVICE_COOKIE])))
        response, _ = self.request("GET", "/panel")
        self.assertEqual(response.status, 303)
        self.assertEqual(self.wizard.calls, [])

    def test_internal_error_is_plain(self):
        self.login()
        self.wizard.handle = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("secret detail"))
        response, text = self.request("GET", "/panel")
        self.assertEqual(response.status, 500)
        self.assertNotIn("secret detail", text)
        self.assertIn("RuntimeError", self.log.getvalue())

    def test_logs_hold_no_secrets(self):
        code, csrf = self.login()
        self.request("GET", "/panel?codigo=leak")
        log = self.log.getvalue()
        self.assertIn("/emparejar", log)
        for secret in [code, PIN, csrf, "leak", *self.cookies.values()]:
            self.assertNotIn(secret, log)


class ControllerTests(StateCase):
    def make(self, address=lambda: "127.0.0.1", port=0):
        self.lock = threading.Lock()
        controller = phone.PhoneController(FakeWizard(self.lock), self.access, self.lock, lambda: None,
                                           port=port, address=address, log_stream=io.StringIO())
        self.addCleanup(controller.stop)
        return controller

    def test_refuses_when_inactive(self):
        controller = self.make()
        self.assertIsInstance(controller.start(), str)
        self.assertFalse(controller.status()["escuchando"])

    def test_refuses_without_private_address(self):
        self.access.enable(PIN)
        controller = self.make(address=lambda: None)
        message = controller.start()
        self.assertIsInstance(message, str)
        self.assertEqual(controller.status()["problema"], message)

    def test_start_stop_and_pairing_url(self):
        self.access.enable(PIN)
        controller = self.make()
        self.assertIsNone(controller.start())
        self.assertIsNone(controller.start())  # no-op when running
        status = controller.status()
        self.assertTrue(status["activo"])
        self.assertTrue(status["escuchando"])
        port = int(status["direccion"].rsplit(":", 1)[1])
        self.assertEqual(status["direccion"], f"http://127.0.0.1:{port}")
        self.assertEqual(controller.pairing_url("abc"), f"http://127.0.0.1:{port}/emparejar?codigo=abc")
        controller.stop()
        self.assertFalse(controller.status()["escuchando"])

    def test_busy_port(self):
        self.access.enable(PIN)
        first = self.make()
        first.start()
        port = int(first.status()["direccion"].rsplit(":", 1)[1])
        second = self.make(port=port)
        message = second.start()
        self.assertIsInstance(message, str)
        self.assertIn(str(port), message)
        self.assertEqual(phone.PORT, 47321)


if __name__ == "__main__":
    unittest.main()
