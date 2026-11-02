"""Independent verifier probes for the phone access (T3). Real Wizard + PC server + phone listener."""
import contextlib
import hashlib
import http.client
import io
import json
import re
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.parse import urlencode

from academic_app import phone
from tests.test_academic_register import PLAN, TRACK, RegisterCase
from tests.test_phone_settings import fake_segno

PIN = "482913"
UA = "Mozilla/5.0 (Linux; Android 14; Pixel 8)"


class Clock:
    def __init__(self):
        self.now = 1_800_000_000.0

    def __call__(self):
        return self.now


class Both(RegisterCase):
    def setUp(self):
        super().setUp()
        self.phone_tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.phone_tmp.cleanup)
        self.clock = Clock()
        self.access = phone.PhoneAccess(Path(self.phone_tmp.name), clock=self.clock)
        self.access.enable(PIN)
        self.plog = io.StringIO()
        self.activity = []
        self.psrv = phone.PhoneServer(("127.0.0.1", 0), self.wizard, self.access, self.server.action_lock,
                                      lambda: self.activity.append(1), log_stream=self.plog)
        self.psrv.client_allowed = lambda ip: True
        self.pport = self.psrv.server_address[1]
        t = threading.Thread(target=self.psrv.serve_forever, daemon=True)
        t.start()
        self.addCleanup(t.join, 5)
        self.addCleanup(self.psrv.server_close)
        self.addCleanup(self.psrv.shutdown)
        self.jar = {}
        self.ptexts = []
        segno = patch.dict(sys.modules, {"segno": fake_segno()})
        segno.start()
        self.addCleanup(segno.stop)

    def preq(self, method, path, form=None, headers=None, origin=True, jar=None, raw_body=None):
        jar = self.jar if jar is None else jar
        conn = http.client.HTTPConnection("127.0.0.1", self.pport, timeout=10)
        sent = {"Host": f"127.0.0.1:{self.pport}", "User-Agent": UA}
        if method == "POST" and origin:
            sent["Origin"] = f"http://127.0.0.1:{self.pport}"
        if jar:
            sent["Cookie"] = "; ".join(f"{k}={v}" for k, v in jar.items())
        body = raw_body
        if form is not None:
            body = urlencode(form).encode()
            sent["Content-Type"] = "application/x-www-form-urlencoded"
        sent.update(headers or {})
        sent = {k: v for k, v in sent.items() if v is not None}
        conn.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
        for k, v in sent.items():
            conn.putheader(k, v)
        if body is not None and "Content-Length" not in sent and "Transfer-Encoding" not in sent:
            conn.putheader("Content-Length", str(len(body)))
        conn.endheaders(body)
        r = conn.getresponse()
        text = r.read().decode("utf-8", "replace")
        for h in r.headers.get_all("Set-Cookie") or []:
            n, _, rest = h.partition("=")
            jar[n] = rest.split(";")[0]
        conn.close()
        self.ptexts.append(text)
        return r, text

    def pair(self, jar=None):
        code = self.access.new_pairing()
        r, _ = self.preq("GET", f"/emparejar?codigo={code}", jar=jar)
        self.assertEqual(r.status, 303)
        return code

    def login(self, jar=None):
        code = self.pair(jar)
        r, _ = self.preq("POST", "/entrar", {"pin": PIN}, jar=jar)
        self.assertEqual(r.status, 303, "login")
        j = self.jar if jar is None else jar
        return code, self.access.session(j[phone.SESSION_COOKIE])[1]

    def state_hash(self):
        p = Path(self.phone_tmp.name) / "control" / "celular" / "acceso.json"
        return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None

    def assert_panel_denied(self, jar=None):
        r, _ = self.preq("GET", "/panel", jar=jar)
        self.assertEqual((r.status, r.headers.get("Location")), (303, "/entrar"))


class AuthProbes(Both):
    def test_no_cookie_forged_revoked_expired_disable_changepin(self):
        self.assert_panel_denied()
        r, t = self.preq("GET", "/entrar")
        self.assertEqual(r.status, 403)
        self.assertNotIn('name="pin"', t)  # unpaired never sees the PIN form
        self.jar.update({phone.DEVICE_COOKIE: "x" * 43, phone.SESSION_COOKIE: "y" * 43})
        self.assert_panel_denied()
        self.jar.clear()
        self.login()
        self.assertEqual(self.preq("GET", "/panel")[0].status, 200)
        # session of another device does not work with this device cookie
        other = {}
        self.login(other)
        mixed = {phone.DEVICE_COOKIE: self.jar[phone.DEVICE_COOKIE], phone.SESSION_COOKIE: other[phone.SESSION_COOKIE]}
        self.assert_panel_denied(mixed)
        # expired session
        self.clock.now += phone.SESSION_SECONDS + 1
        self.assert_panel_denied()
        self.clock.now -= phone.SESSION_SECONDS + 1
        self.preq("POST", "/entrar", {"pin": PIN})
        self.assertEqual(self.preq("GET", "/panel")[0].status, 200)
        self.access.change_pin("777777")
        self.assert_panel_denied()
        self.preq("POST", "/entrar", {"pin": "777777"})
        self.assertEqual(self.preq("GET", "/panel")[0].status, 200)
        self.access.disable()
        r, _ = self.preq("GET", "/panel")
        self.assertEqual(r.status, 403)
        self.access.enable(PIN)
        self.assert_panel_denied()
        self.preq("POST", "/entrar", {"pin": PIN})
        dev = self.access.device(self.jar[phone.DEVICE_COOKIE])
        self.access.revoke(dev)
        self.assert_panel_denied()
        r, _ = self.preq("GET", "/entrar")
        self.assertEqual(r.status, 403)

    def test_pairing_code_reuse_expiry_wrong_inactive(self):
        code = self.pair()
        h = self.state_hash()
        r, _ = self.preq("GET", f"/emparejar?codigo={code}", jar={})
        self.assertEqual(r.status, 403)
        r, _ = self.preq("GET", "/emparejar?codigo=wrong", jar={})
        self.assertEqual(r.status, 403)
        r, _ = self.preq("GET", "/emparejar", jar={})
        self.assertEqual(r.status, 403)
        self.assertEqual(self.state_hash(), h)
        code = self.access.new_pairing()
        self.preq("GET", "/emparejar?codigo=wrong", jar={})  # wrong guess keeps real code
        self.clock.now += phone.PAIRING_SECONDS + 1
        r, t = self.preq("GET", f"/emparejar?codigo={code}", jar={})
        self.assertEqual(r.status, 403)
        self.assertIn("venció", t)
        self.assertEqual(self.state_hash(), h)
        code = self.access.new_pairing()
        old = self.access.new_pairing()  # only one pending: first one dead
        r, _ = self.preq("GET", f"/emparejar?codigo={code}", jar={})
        self.assertEqual(r.status, 403)
        self.access.disable()
        h = self.state_hash()
        with self.assertRaises(phone.PhoneError):
            self.access.new_pairing()
        r, _ = self.preq("GET", f"/emparejar?codigo={old}", jar={})
        self.assertEqual(r.status, 403)
        self.assertEqual(self.state_hash(), h)

    def test_pin_lockout_global_and_reset(self):
        a, b = {}, {}
        self.pair(a)
        self.pair(b)
        h = self.state_hash()
        for _ in range(4):
            self.assertEqual(self.preq("POST", "/entrar", {"pin": "135791"}, jar=a)[0].status, 403)
        r, _ = self.preq("POST", "/entrar", {"pin": PIN}, jar=a)  # success resets counter
        self.assertEqual(r.status, 303)
        for _ in range(4):
            self.preq("POST", "/entrar", {"pin": "135791"}, jar=b)
        r, _ = self.preq("POST", "/entrar", {"pin": PIN}, jar=b)
        self.assertEqual(r.status, 303, "counter not reset after success")
        # unknown device wrong pin does not count
        for _ in range(10):
            self.assertEqual(self.preq("POST", "/entrar", {"pin": "135791"},
                                       jar={phone.DEVICE_COOKIE: "z" * 43})[0].status, 403)
        r, _ = self.preq("POST", "/entrar", {"pin": PIN}, jar=b)
        self.assertEqual(r.status, 303)
        for _ in range(5):
            self.preq("POST", "/entrar", {"pin": "135791"}, jar=a)
        r, t = self.preq("POST", "/entrar", {"pin": PIN}, jar=b)
        self.assertEqual(r.status, 403)
        self.assertIn("15 minutos", t)
        self.clock.now += 899
        self.assertEqual(self.preq("POST", "/entrar", {"pin": PIN}, jar=a)[0].status, 403)
        self.clock.now += 2
        self.assertEqual(self.preq("POST", "/entrar", {"pin": PIN}, jar=a)[0].status, 303)
        self.assertEqual(self.state_hash(), h)


class RequestProbes(Both):
    def test_header_checks(self):
        self.login()
        base = f"http://127.0.0.1:{self.pport}"
        for hdr in ({"Host": "evil:47321"}, {"Host": f"localhost:{self.pport}"}, {"Origin": "http://evil"},
                    {"Sec-Fetch-Site": "cross-site"}, {"Sec-Fetch-Site": "same-site"}):
            self.assertEqual(self.preq("GET", "/panel", headers=hdr)[0].status, 403, hdr)
        r, _ = self.preq("POST", "/panel/registrar", {**TRACK, "csrf": "x"}, origin=False)
        self.assertEqual(r.status, 403)
        r, _ = self.preq("POST", "/entrar", {"pin": PIN}, origin=False)
        self.assertEqual(r.status, 403)
        r, _ = self.preq("POST", "/salir", {}, origin=False)
        self.assertEqual(r.status, 403)
        r, _ = self.preq("POST", "/panel/registrar", raw_body=b"a" * (70 * 1024),
                         headers={"Content-Type": "application/x-www-form-urlencoded", "Origin": base})
        self.assertEqual(r.status, 413)
        r, _ = self.preq("POST", "/panel/registrar", raw_body=b"0\r\n\r\n",
                         headers={"Transfer-Encoding": "chunked", "Content-Type": "application/x-www-form-urlencoded"})
        self.assertEqual(r.status, 411)

    def test_paths_and_methods(self):
        _, csrf = self.login()
        self.wizard.handle = MagicMock(wraps=self.wizard.handle)
        for path in ("/", "/cerrar", "/celular", "/celular/qr", "/paso/datos", "/paso/canvas", "/actualizar",
                     "/panel/sincronizar", "/panelx", "/panel/registrarX", "//celular", "/panel/", "/Panel",
                     "/panel/materias/", "/%70anel", "/panel/resumen/../../celular"):
            r, t = self.preq("GET", path)
            self.assertEqual(r.status, 403, path)
            self.assertIn("No disponible desde el celular", t, path)
        for path in ("/cerrar", "/celular/activar", "/celular/desactivar", "/celular/pin", "/celular/qr",
                     "/celular/quitar", "/celular/iniciar", "/actualizar", "/panel/sincronizar", "/paso/datos",
                     "/paso/tarea", "/paso/materias/indice", "/panel/registrar/", "/panel/publicando/x"):
            r, t = self.preq("POST", path, {"csrf": csrf})
            self.assertEqual(r.status, 403, path)
            self.assertIn("No disponible desde el celular", t, path)
        self.wizard.handle.assert_not_called()
        # prefix traversal reaches wizard only as an unknown subject / job id
        r, t = self.preq("GET", "/panel/materia/../../celular")
        self.assertNotIn("/celular/activar", t)
        self.assertNotIn("PIN", t)
        r, t = self.preq("GET", "/panel/materia/%2e%2e%2f%2e%2e%2fcelular")
        self.assertNotIn("/celular/activar", t)
        for path, _ in self.wizard.handle.call_args_list:
            pass
        for call in self.wizard.handle.call_args_list:
            self.assertTrue(call.args[1].startswith(("/panel/materia/", "/panel/publicando/")))
            self.assertTrue(call.kwargs.get("phone"))
        # query strings on allowed paths
        self.assertEqual(self.preq("GET", "/panel?x=1&csrf=a")[0].status, 200)
        for m in ("HEAD", "PUT", "OPTIONS", "DELETE"):
            r, _ = self.preq(m, "/panel")
            self.assertEqual(r.status, 501, m)

    def test_post_needs_session_csrf_and_runs_under_action_lock(self):
        _, csrf = self.login()
        h = self.state_hash()
        r, _ = self.preq("POST", "/panel/registrar", TRACK)
        self.assertEqual(r.status, 403)
        r, _ = self.preq("POST", "/panel/registrar", {**TRACK, "csrf": self.server.csrf})  # PC csrf
        self.assertEqual(r.status, 403)
        self.preview.assert_not_called()
        self.assertEqual(self.state_hash(), h)
        seen = []
        orig = self.preview.side_effect

        def preview(*a, **kw):
            seen.append(self.server.action_lock.locked())
            return {"plan_id": PLAN, "already_applied": False, "requires_explicit_approval": True}
        self.preview.side_effect = preview
        r, t = self.preq("POST", "/panel/registrar", {**TRACK, "csrf": csrf})
        self.assertEqual(r.status, 200)
        self.assertEqual(seen, [True])
        self.assertIn("Aprobar y publicar", t)
        self.assertIn(f'value="{csrf}"', t)
        self.assertNotIn(self.server.csrf, t)
        r, t = self.preq("POST", "/panel/registrar/aprobar", {**TRACK, "plan_id": PLAN, "csrf": csrf})
        self.assertEqual(r.status, 303)
        loc = r.headers["Location"]
        for _ in range(100):
            r, t = self.preq("GET", loc)
            if "Publicando…" not in t:
                break
            time.sleep(0.05)
        self.assertIn("Listo", t)
        self.assert_phone_clean(t)

    def assert_phone_clean(self, body):
        for bad in ('href="/"', 'action="/"', "/paso/", "/celular", "/actualizar", "/panel/sincronizar",
                    "Sincronizar ahora", "Cerrar el asistente", "Configuración</a>"):
            self.assertNotIn(bad, body)


class RenderProbes(Both):
    def test_phone_pages_and_register_errors_clean(self):
        from academic_app import updates
        release = updates.Release("9.9.0", "https://example.invalid/x.exe", "0" * 64, 1)
        self.wizard.deps["check_update"] = MagicMock(return_value=release)
        self.wizard.start_update_check().join(5)
        _, csrf = self.login()
        for path in ("/panel", "/panel/resumen", "/panel/materias", "/panel/instrucciones",
                     "/panel/materia/MAT-GBD", "/panel/materia/NOPE", "/panel/publicando/nope"):
            r, t = self.preq("GET", path)
            self.assertIn(r.status, (200, 404), path)
            RequestProbes.assert_phone_clean(self, t)
            self.assertIn('action="/salir"', t, path)
        r, t = self.preq("POST", "/panel/registrar", {"accion": "bogus", "csrf": csrf})
        RequestProbes.assert_phone_clean(self, t)
        r, t = self.preq("POST", "/panel/registrar", {**TRACK, "volver": "/celular", "csrf": csrf})
        self.assertNotIn('href="/celular"', t)

    def test_raw_exception_never_shown(self):
        self.login()
        self.today.side_effect = RuntimeError("SECRET-RAW /home/x Traceback")
        self.summary.side_effect = RuntimeError("SECRET-RAW")
        with contextlib.redirect_stderr(io.StringIO()):
            r, t = self.preq("GET", "/panel")
        self.assertNotIn("SECRET-RAW", t)
        with patch.object(self.wizard, "handle", side_effect=RuntimeError("SECRET-RAW")):
            r, t = self.preq("GET", "/panel")
        self.assertEqual(r.status, 500)
        self.assertNotIn("SECRET-RAW", t)
        self.assertNotIn("SECRET-RAW", self.plog.getvalue())


class LeakProbes(Both):
    def test_logs_and_state_file_have_no_secrets(self):
        code, csrf = self.login()
        self.preq("POST", "/entrar", {"pin": "135791"})
        self.preq("GET", f"/emparejar?codigo={code}", jar={})
        self.preq("POST", "/salir", {})
        # PC side: QR page
        self.post("/celular/qr") if "phone" in self.wizard.deps else None
        log = self.plog.getvalue() + self.log.getvalue()
        raw_state = (Path(self.phone_tmp.name) / "control/celular/acceso.json").read_text()
        secrets = [code, PIN, "135791", csrf] + list(self.jar.values())
        for s in secrets:
            if s:
                self.assertNotIn(s, log)
                self.assertNotIn(s, raw_state)
        self.assertIn("/emparejar", self.plog.getvalue())


class ConcurrencyProbes(Both):
    def test_phone_flag_does_not_leak_to_pc_request(self):
        self.login()
        gate, entered = threading.Event(), threading.Event()
        real = self.summary.side_effect

        def slow(*a, **kw):
            if threading.current_thread().name.startswith("phone-probe"):
                entered.set()
                gate.wait(5)
            from tests.test_academic_panel import summary_data
            return summary_data()
        self.summary.side_effect = slow
        out = {}
        th = threading.Thread(target=lambda: out.setdefault("p", self.preq("GET", "/panel/resumen")),
                              name="phone-probe")
        # handler thread name differs; mark by patching: use a dep that blocks only once
        calls = {"n": 0}

        def once(*a, **kw):
            calls["n"] += 1
            if calls["n"] == 1:
                entered.set()
                gate.wait(5)
            from tests.test_academic_panel import summary_data
            return summary_data()
        self.summary.side_effect = once
        th.start()
        self.assertTrue(entered.wait(5))
        status, _, body = self.get("/panel/resumen")  # PC while phone request in flight
        gate.set()
        th.join(5)
        self.assertIn('href="/"', body)
        self.assertNotIn('action="/salir"', body)
        self.assertIn('action="/salir"', out["p"][1])
        self.assertNotIn('href="/"', out["p"][1])

    def test_concurrent_logins(self):
        jars = [{} for _ in range(6)]
        for j in jars:
            self.pair(j)
        res = []
        ths = [threading.Thread(target=lambda j=j: res.append(self.preq("POST", "/entrar", {"pin": PIN}, jar=j)[0].status))
               for j in jars]
        [t.start() for t in ths]
        [t.join(10) for t in ths]
        self.assertEqual(res, [303] * 6)
        for j in jars:
            self.assertEqual(self.preq("GET", "/panel", jar=j)[0].status, 200)
        self.assertEqual(len({j[phone.SESSION_COOKIE] for j in jars}), 6)


class ControllerProbes(unittest.TestCase):
    def test_busy_port_and_no_address(self):
        import socket
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        access = phone.PhoneAccess(tmp.name)
        access.enable(PIN)
        busy = socket.socket()
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        self.addCleanup(busy.close)
        c = phone.PhoneController(object(), access, threading.Lock(), lambda: None,
                                  port=busy.getsockname()[1], address=lambda: "127.0.0.1")
        msg = c.start()
        self.assertIn("ocupado", msg)
        self.assertFalse(c.status()["escuchando"])
        c2 = phone.PhoneController(object(), access, threading.Lock(), lambda: None, address=lambda: None)
        self.assertIn("red privada", c2.start())
        access.disable()
        c3 = phone.PhoneController(object(), access, threading.Lock(), lambda: None, port=0,
                                   address=lambda: "127.0.0.1")
        self.assertIn("apagado", c3.start())

    def test_main_idle_counter_restored(self):
        from academic_app import server as app_server
        srv = app_server.create(MagicMock(), idle_seconds=60, log_stream=io.StringIO())
        self.addCleanup(srv.server_close)
        from academic_app import __main__ as m
        wiz = MagicMock()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        wiz.data_folder.return_value = Path(tmp.name)
        wiz.deps = {}
        c = m._phone_access(wiz, srv)
        self.assertFalse(c.status()["escuchando"])  # off by default: not started
        before = srv._last
        time.sleep(0.01)
        c.on_activity()
        self.assertEqual(srv._active, 0)
        self.assertGreater(srv._last, before)



class IdleTimerTests(Both):
    def test_rejected_requests_do_not_keep_the_pc_app_alive(self):
        self.activity.clear()
        self.assertEqual(self.preq("GET", "/panel", headers={"Host": "evil:47321"})[0].status, 403)
        self.psrv.client_allowed = lambda ip: False
        self.assertEqual(self.preq("GET", "/panel")[0].status, 403)
        self.assertEqual(self.activity, [])
        self.psrv.client_allowed = lambda ip: True
        self.preq("GET", "/entrar")
        self.assertEqual(self.activity, [1])


if __name__ == "__main__":
    unittest.main()
