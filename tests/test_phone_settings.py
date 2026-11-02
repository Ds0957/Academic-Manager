"""Configuración «Celular»: switch + PIN, QR (segno simulated), devices, phone-only navigation, wiring.

The wizard runs on the real PC server of tests.test_academic_app.ServerCase; the phone listener is
replaced by a fake controller around a real PhoneAccess in a temporary folder (no LAN sockets).
"""
import contextlib
import io
import sys
import tempfile
import types
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

from academic_app import panel, phone, qr

from tests.test_academic_panel import PanelCase

PIN = "482913"
URL_BASE = "http://192.168.1.20:47321"


class FakeController:
    def __init__(self, access):
        self.access = access
        self.problem = None
        self.listening = False
        self.starts = 0
        self.stops = 0

    def start(self):
        self.starts += 1
        self.listening = self.problem is None
        return self.problem

    def stop(self):
        self.stops += 1
        self.listening = False

    def status(self):
        return {"activo": self.access.is_active(), "escuchando": self.listening,
                "direccion": URL_BASE if self.listening else None, "problema": self.problem}

    def pairing_url(self, code):
        return f"{URL_BASE}/emparejar?codigo={code}"


def fake_segno():
    module = types.ModuleType("segno")
    module.calls = []

    class Code:
        def __init__(self, content):
            self.content = content

        def svg_inline(self, **kw):
            module.calls.append(kw)
            return '<svg class="fake-qr" data-len="%d"></svg>' % len(self.content)

    def make_qr(content, error=None):
        module.calls.append({"content": content, "error": error})
        return Code(content)

    module.make_qr = make_qr
    return module


class PhoneCase(PanelCase):
    def setUp(self):
        super().setUp()
        self.phone_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.phone_dir.cleanup)
        self.access = phone.PhoneAccess(Path(self.phone_dir.name))
        self.controller = FakeController(self.access)
        self.wizard.deps["phone"] = self.controller
        self.segno = fake_segno()
        patcher = patch.dict(sys.modules, {"segno": self.segno})
        patcher.start()
        self.addCleanup(patcher.stop)

    def activate(self):
        status, response, _ = self.post("/celular/activar", {"pin": PIN, "pin2": PIN})
        self.assertEqual(status, 303)
        self.assertEqual(response.headers["Location"], "/celular")

    def pair_device(self, name="Android", when=None):
        code = self.access.new_pairing()
        token = self.access.pair(code, name)
        return token, self.access.device(token)


class OffPageTests(PhoneCase):
    def test_off_page_explains_and_asks_for_the_pin(self):
        status, _, body = self.get("/celular")
        self.assertEqual(status, 200)
        self.assertIn("mismo Wi-Fi", body)
        self.assertIn("Academic Manager abierto", body)
        self.assertIn('name="pin"', body)
        self.assertIn('name="pin2"', body)
        self.assertIn("Activar", body)
        self.assertIn("no está cifrada", body)
        self.assertIn("redes privadas", body)
        self.assertNotIn("Mostrar código QR", body)

    def test_activate_with_valid_pin_starts_the_listener(self):
        self.activate()
        self.assertTrue(self.access.is_active())
        self.assertEqual(self.controller.starts, 1)
        body = self.get("/celular")[2]
        self.assertIn("activado", body)
        self.assertIn(URL_BASE, body)
        self.assertIn("Mostrar código QR", body)
        self.assertNotIn(PIN, body)

    def test_invalid_or_mismatched_pin_does_not_activate(self):
        for pin, pin2, text in (("12345", "12345", "entre 6 y 12 números"),
                                ("12ab56", "12ab56", "entre 6 y 12 números"),
                                (PIN, "999999", "no coinciden")):
            status, _, body = self.post("/celular/activar", {"pin": pin, "pin2": pin2})
            self.assertEqual(status, 400)
            self.assertIn(text, body)
            self.assertIn('class="card msg bad"', body)
        self.assertFalse(self.access.is_active())
        self.assertEqual(self.controller.starts, 0)

    def test_start_problem_is_shown_plainly(self):
        self.controller.problem = "El puerto 47321 está ocupado por otro programa."
        status, _, body = self.post("/celular/activar", {"pin": PIN, "pin2": PIN})
        self.assertEqual(status, 400)
        self.assertIn("El puerto 47321 está ocupado por otro programa.", body)
        self.assertIn("Volver a intentar", body)
        self.controller.problem = None
        status, response, _ = self.post("/celular/iniciar")
        self.assertEqual(status, 303)
        self.assertTrue(self.controller.listening)

    def test_every_post_requires_csrf(self):
        for path, fields in (("/celular/activar", {"pin": PIN, "pin2": PIN}), ("/celular/iniciar", {}),
                             ("/celular/qr", {}), ("/celular/quitar", {"dispositivo": "x"}),
                             ("/celular/pin", {"pin": PIN, "pin2": PIN}), ("/celular/desactivar", {})):
            status, _, _ = self.post(path, fields, csrf=False)
            self.assertEqual(status, 403, path)
        self.assertFalse(self.access.is_active())
        self.assertEqual(self.controller.starts, 0)

    def test_without_data_folder_the_feature_is_unavailable(self):
        del self.wizard.deps["phone"]
        body = self.get("/celular")[2]
        self.assertIn("carpeta de datos", body)
        self.assertNotIn('name="pin"', body)
        status, _, body = self.post("/celular/activar", {"pin": PIN, "pin2": PIN})
        self.assertEqual(status, 400)

    def test_home_links_to_the_phone_page(self):
        body = self.get("/")[2]
        self.assertIn('href="/celular"', body)
        self.assertIn("Acceso desde el celular", body)


class OnPageTests(PhoneCase):
    def setUp(self):
        super().setUp()
        self.activate()

    def test_devices_are_listed_with_local_dates(self):
        _, device_id = self.pair_device("iPhone")
        stamp = self.access.devices()[0]["creado"]
        local = datetime.fromtimestamp(stamp, panel.LOCAL_ZONE)
        body = self.get("/celular")[2]
        self.assertIn("iPhone", body)
        self.assertIn("autorizado el " + panel.day_text(local.date().isoformat()), body)
        self.assertIn("visto por última vez", body)
        self.assertIn(f'value="{device_id}"', body)
        self.assertIn("Quitar", body)

    def test_qr_page_shows_svg_and_single_use_note_without_logging_the_code(self):
        status, _, body = self.post("/celular/qr")
        self.assertEqual(status, 200)
        self.assertIn('<svg class="fake-qr"', body)
        self.assertIn("válido 5 minutos, un solo uso", body)
        content = self.segno.calls[0]["content"]
        self.assertTrue(content.startswith(URL_BASE + "/emparejar?codigo="))
        self.assertEqual(self.segno.calls[0]["error"], "m")
        code = content.split("codigo=", 1)[1]
        self.assertTrue(self.access.pair(code, "x"))  # the shown code is the real pending one
        self.assertNotIn(code, self.log.getvalue())
        self.assertIn("/celular/qr", self.log.getvalue())

    def test_missing_segno_falls_back_to_the_address_as_text(self):
        with patch.dict(sys.modules, {"segno": None}):
            status, _, body = self.post("/celular/qr")
        self.assertEqual(status, 200)
        self.assertNotIn("<svg class", body.split("</header>", 1)[1])
        self.assertIn("No se pudo dibujar el código QR", body)
        self.assertIn(URL_BASE + "/emparejar?codigo=", body)
        code = body.split("codigo=", 1)[1].split("<", 1)[0]
        self.assertTrue(self.access.pair(code, "x"))
        self.assertNotIn(code, self.log.getvalue())

    def test_revoke_removes_the_device(self):
        _, device_id = self.pair_device()
        status, response, _ = self.post("/celular/quitar", {"dispositivo": device_id})
        self.assertEqual(status, 303)
        self.assertEqual(self.access.devices(), [])
        self.assertIn("quitó", self.get("/celular")[2])

    def test_change_pin(self):
        _, device_id = self.pair_device()
        status, _, body = self.post("/celular/pin", {"pin": "123", "pin2": "123"})
        self.assertEqual(status, 400)
        self.assertIn("entre 6 y 12 números", body)
        status, _, body = self.post("/celular/pin", {"pin": "777777", "pin2": "777778"})
        self.assertEqual(status, 400)
        self.assertIn("no coinciden", body)
        self.assertTrue(self.access.login(device_id, PIN))
        status, _, _ = self.post("/celular/pin", {"pin": "777777", "pin2": "777777"})
        self.assertEqual(status, 303)
        self.assertTrue(self.access.login(device_id, "777777"))
        with self.assertRaises(phone.PhoneError):
            self.access.login(device_id, PIN)

    def test_disable_stops_the_listener(self):
        status, _, _ = self.post("/celular/desactivar")
        self.assertEqual(status, 303)
        self.assertFalse(self.access.is_active())
        self.assertEqual(self.controller.stops, 1)
        body = self.get("/celular")[2]
        self.assertIn("Activar", body)
        self.assertIn("desactivado", body)


class PhoneNavigationTests(PanelCase):
    def phone_page(self, path):
        from academic_app import updates
        release = updates.Release("9.9.0", "https://example.invalid/x.exe", "0" * 64, 1)
        self.wizard.deps["check_update"] = MagicMock(return_value=release)
        self.wizard.start_update_check().join(5)
        return self.wizard.handle("GET", path, {}, {}, "tok", phone=True).body

    def test_phone_pages_offer_only_phone_parts(self):
        for path in ("/panel", "/panel/resumen", "/panel/materias", "/panel/instrucciones"):
            body = self.phone_page(path)
            for link in ('href="/panel"', 'href="/panel/resumen"', 'href="/panel/materias"',
                         'href="/panel/instrucciones"', 'action="/salir"', ">Salir<"):
                self.assertIn(link, body, path)
            for pc_only in ('href="/"', "Configuración</a>", "/actualizar", "/panel/sincronizar",
                            "Sincronizar ahora", "Cerrar el asistente"):
                self.assertNotIn(pc_only, body, path)

    def test_phone_problem_card_has_no_pc_links(self):
        from academic_access.core import AccessError
        self.instructions.side_effect = AccessError("INDEX_STALE", "El índice académico no corresponde.")
        with contextlib.redirect_stderr(io.StringIO()):
            body = self.phone_page("/panel/instrucciones")
        self.assertIn("desactualizado", body)
        self.assertNotIn('href="/paso/materias"', body)
        self.assertNotIn("/paso/materias/indice", body)
        self.assertIn("En el PC", body)

    def test_pc_flag_is_reset_after_a_phone_request(self):
        self.phone_page("/panel")
        body = self.wizard.handle("GET", "/panel", {}, {}, "tok").body
        self.assertIn('href="/"', body)
        self.assertNotIn('action="/salir"', body)

    def test_nav_without_flag_is_unchanged(self):
        self.assertEqual(panel.nav("hoy"), panel.nav("hoy", phone=False))
        self.assertNotIn("/salir", panel.nav("hoy"))


class QrModuleTests(unittest.TestCase):
    def test_svg_uses_a_regular_qr_with_medium_correction(self):
        module = fake_segno()
        with patch.dict(sys.modules, {"segno": module}):
            out = qr.svg("http://x/emparejar?codigo=abc")
        self.assertTrue(out.startswith("<svg"))
        self.assertEqual(module.calls[0], {"content": "http://x/emparejar?codigo=abc", "error": "m"})
        self.assertEqual(module.calls[1].get("scale"), 5)

    def test_missing_segno_raises_a_plain_error(self):
        with patch.dict(sys.modules, {"segno": None}):
            with self.assertRaises(qr.QRUnavailable):
                qr.svg("http://x")


class MainWiringTests(unittest.TestCase):
    def run_main(self, runtime, active):
        from academic_app import __main__ as entry
        srv = MagicMock()
        srv.url = "http://127.0.0.1:1/?clave=x"
        wizard = MagicMock()
        wizard.deps = {}
        wizard.complete.return_value = False
        wizard.data_folder.return_value = runtime
        access = MagicMock()
        access.is_active.return_value = active
        controller = MagicMock()
        controller.access = access
        controller.start.return_value = None
        controller.status.return_value = {"direccion": URL_BASE}
        with patch.object(entry, "Wizard", return_value=wizard), \
                patch.object(entry.server, "create", return_value=srv), \
                patch.object(entry.phone, "PhoneAccess", return_value=access) as make_access, \
                patch.object(entry.phone, "PhoneController", return_value=controller) as make_controller, \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(entry.main(["--sin-navegador"]), 0)
        return srv, wizard, controller, make_access, make_controller

    def test_starts_only_when_access_is_active_and_stops_on_exit(self):
        srv, wizard, controller, make_access, make_controller = self.run_main(Path("/datos"), True)
        make_access.assert_called_once_with(Path("/datos"))
        args = make_controller.call_args[0]
        self.assertIs(args[0], wizard)
        self.assertIs(args[2], srv.action_lock)
        self.assertIs(wizard.deps["phone"], controller)
        controller.start.assert_called_once_with()
        controller.stop.assert_called_once_with()
        srv.server_close.assert_called_once_with()
        args[3]()  # phone activity refreshes the idle clock without leaking the active counter
        self.assertEqual([c.args for c in srv.touch.call_args_list], [(1,), (-1,)])

    def test_inactive_access_is_not_started(self):
        _, wizard, controller, _, _ = self.run_main(Path("/datos"), False)
        controller.start.assert_not_called()
        self.assertIs(wizard.deps["phone"], controller)

    def test_without_data_folder_there_is_no_phone_access(self):
        _, wizard, _, make_access, make_controller = self.run_main(None, True)
        make_access.assert_not_called()
        make_controller.assert_not_called()
        self.assertNotIn("phone", wizard.deps)


if __name__ == "__main__":
    unittest.main()
