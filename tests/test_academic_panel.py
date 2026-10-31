"""Panel del día a día: solo lectura, lenguaje sencillo y fallos sin detalles internos.

Los lectores (`today`, `summary`, `subject_brief`, `render_all`) se simulan con datos
sintéticos; el servidor es el real de tests.test_academic_app.ServerCase.
"""
import contextlib
import io
import sqlite3
import re
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from academic_access.core import AccessError
from academic_store import store
from canvas_sync.common import StoreBusy, SyncError

from tests.test_academic_app import RAW, ServerCase

TODAY_ITEMS = [
    {"tipo": "actividad_vencida", "prioridad": 0, "fecha": "2026-10-29", "materia_id": "MAT-GBD",
     "motivo": "Venció hace 2 días y aún no consta como entregada", "actividad_id": "ACT-1",
     "titulo": "Taller <uno>"},
    {"tipo": "actividad_proxima", "prioridad": 1, "fecha": "2026-11-01", "materia_id": "MAT-SO2",
     "motivo": "Se entrega mañana", "actividad_id": "ACT-2", "titulo": "Quiz 2"},
    {"tipo": "tarea_sin_fecha", "prioridad": 6, "fecha": None, "materia_id": None,
     "motivo": "Tarea abierta sin fecha límite", "tarea_id": "TAR-1", "titulo": "Comprar libro"},
]
TODAY = {"fecha": "2026-10-31", "dias": 14, "fechas_zona": "America/Bogota", "resumen": {},
         "items": TODAY_ITEMS, "workbook_sha256": "abc"}
HISTORY_OK = {"ultimo_ok": "2026-10-31 08:00:12", "fallos_seguidos": 0, "fallando_desde": None,
              "ultimo_intento": "2026-10-31 08:00:12", "ultimo_error": None}


def summary_data(**sistema):
    base = {"ultimo_canvas_sync": {"status": "ok", "timestamp_local": "2026-10-31 08:00:12", "exit_code": 0},
            "canvas_sync_historial": dict(HISTORY_OK),
            "indice": {"fresh": True, "generated_at_utc": None},
            "token_canvas": {"vence": "2027-01-05", "dias_restantes": 66, "aviso": None, "motivo": None},
            "publicado": {"sha256": "abc", "drive_modified_time": None, "revision": 3}}
    base.update(sistema)
    return {"avisos": [], "fecha": "2026-10-31", "fechas_zona": "America/Bogota",
            "semestre": {"semestre_id": "2026-2", "nombre": "Segundo semestre 2026", "bloque_activo": 2},
            "materias": [{"materia_id": "MAT-GBD", "nombre": "Gestión de bases <datos>",
                          "ultima_clase": "2026-10-28"},
                         {"materia_id": "MAT-SO2", "nombre": "Sistemas operativos II", "ultima_clase": None}],
            "urgente": {"items": TODAY_ITEMS[:2], "resumen": {}},
            "reciente": {"notas": [{"nota_id": "N1", "materia_id": "MAT-GBD", "tipo": "decision",
                                    "texto": "Estudiar los martes", "fecha": "2026-10-30"}],
                         "tareas": [{"tarea_id": "TAR-1", "titulo": "Comprar libro", "materia_id": None,
                                     "fecha_limite": None, "estado": "pendiente"}]},
            "sistema": base}


BRIEF = {"fecha": "2026-10-31", "fechas_zona": "America/Bogota", "dias": 60,
         "materia": {"materia_id": "MAT-GBD", "nombre": "Gestión de bases <datos>", "semestre_id": "2026-2",
                     "semestre_activo": True, "ultima_clase": "2026-10-28"},
         "pendientes": {"items": [TODAY_ITEMS[0]], "resumen": {}},
         "entregas": [{"actividad_id": "ACT-0", "estado": "calificada", "nota": 4.5, "puntos": 45,
                       "comentario": "Bien hecho", "actualizado_utc": "2026-10-20T15:00:00Z",
                       "materia_id": "MAT-GBD", "titulo": "Parcial 1"}],
         "tareas": [{"tarea_id": "TAR-2", "titulo": "Repasar SQL", "materia_id": "MAT-GBD",
                     "fecha_limite": "2026-11-03", "detalle": None, "estado": "en_progreso"}],
         "decisiones": [{"nota_id": "N1", "materia_id": "MAT-GBD", "tipo": "decision",
                         "texto": "Estudiar los martes", "clase_id": None,
                         "creado_utc": "2026-10-31T03:00:00Z"}],  # 30 de octubre en Bogotá
         "resumenes_clase": [{"nota_id": "N2", "materia_id": "MAT-GBD", "tipo": "resumen_clase",
                              "texto": "Normalización hasta 3FN", "clase_id": "CLS-1",
                              "creado_utc": "2026-10-28T23:30:00Z"}],
         "workbook_sha256": "abc"}
EMPTY_BRIEF = {**BRIEF, "pendientes": {"items": [], "resumen": {}}, "entregas": [], "tareas": [],
               "decisiones": [], "resumenes_clase": []}
INSTRUCTIONS = {"coordinador.md": "Instrucciones del <Coordinador>",
                "materia-MAT-GBD.md": "Instrucciones de GBD", "materia-MAT-SO2.md": "Instrucciones de SO2"}

FAILURES = [AccessError("SOURCE_UNAVAILABLE", "La base publicada no está disponible o no valida."),
            AccessError("INVALID_SOURCE", "La base debe tener un único semestre activo."),
            SyncError("No hay base local y estado de control para reconciliar."),
            StoreBusy("El estado local de la base no está libre.", lock={"x": 1}),
            store.StoreError("Esquema de la base no reconocido."),
            sqlite3.DatabaseError("file is not a database"),
            OSError(13, "Permission denied"), ValueError("No hay materias del semestre activo en la base."),
            KeyError("materia_id"), StopIteration()]
INTERNAL = ("SOURCE_UNAVAILABLE", "INVALID_SOURCE", "Permission denied", "materia_id",
            "no valida", "Traceback", "único semestre activo", "Esquema", "file is not")


class BriefTextTests(unittest.TestCase):
    def test_sections_dates_and_local_time(self):
        from academic_app.panel import brief_text
        text = brief_text(BRIEF)
        self.assertIn("Gestión de bases <datos> (MAT-GBD)", text)
        self.assertIn("2026-2", text)
        for section in ("Pendientes", "Entregas", "Tareas abiertas", "Decisiones", "Resúmenes de clase"):
            self.assertIn(section, text)
        self.assertIn("Taller <uno>", text)
        self.assertIn("jueves 29 de octubre de 2026", text)
        self.assertIn("Venció hace 2 días", text)
        self.assertIn("Parcial 1", text)
        self.assertIn("4.5", text)
        self.assertIn("Repasar SQL", text)
        self.assertIn("martes 3 de noviembre de 2026", text)
        # 03:00 UTC del 31 es el 30 en Bogotá; 23:30 UTC del 28 sigue siendo el 28.
        self.assertIn("viernes 30 de octubre de 2026: Estudiar los martes", text)
        self.assertIn("miércoles 28 de octubre de 2026: Normalización hasta 3FN", text)
        self.assertNotIn("2026-10-31T03", text)
        self.assertNotIn("&lt;", text)  # texto plano, no HTML

    def test_empty_sections_are_omitted(self):
        from academic_app.panel import brief_text
        text = brief_text(EMPTY_BRIEF)
        self.assertIn("MAT-GBD", text)
        for section in ("Pendientes", "Entregas", "Tareas abiertas", "Decisiones", "Resúmenes de clase"):
            self.assertNotIn(section, text)

    def test_inactive_semester_is_said(self):
        from academic_app.panel import brief_text
        text = brief_text({**EMPTY_BRIEF, "materia": {**BRIEF["materia"], "semestre_activo": False}})
        self.assertIn("no es el semestre activo", text)


class ReadTests(unittest.TestCase):
    def test_every_failure_class_becomes_a_plain_notice(self):
        from academic_app.panel import read
        for exc in FAILURES:
            with self.subTest(exc=type(exc).__name__), contextlib.redirect_stderr(io.StringIO()) as err:
                result = read(MagicMock(side_effect=exc), Path("x"))
                self.assertIsNone(result.value)
                self.assertTrue(result.problem)
                for raw in INTERNAL + RAW:
                    self.assertNotIn(raw, result.problem)
                self.assertIn(type(exc).__name__, err.getvalue())  # el registro solo nombra la clase

    def test_success_returns_the_value(self):
        from academic_app.panel import read
        result = read(lambda runtime: {"ok": runtime}, Path("x"))
        self.assertEqual(result.value, {"ok": Path("x")})
        self.assertIsNone(result.problem)

    def test_stale_index_is_flagged(self):
        from academic_app.panel import read
        with contextlib.redirect_stderr(io.StringIO()):
            result = read(MagicMock(side_effect=AccessError("INDEX_STALE", "x")), Path("x"))
        self.assertTrue(result.stale_index)

    def test_plain_warnings_from_system_state(self):
        from academic_app.panel import warnings
        self.assertEqual(warnings(summary_data()), [])
        failing = summary_data(canvas_sync_historial={**HISTORY_OK, "fallos_seguidos": 3,
                                                      "fallando_desde": "2026-10-29 08:00:00",
                                                      "ultimo_error": "Traceback raro"},
                               indice={"fresh": False, "generated_at_utc": None},
                               token_canvas={"vence": "2026-11-01", "dias_restantes": 1,
                                             "aviso": "Token Canvas vence en 1 días: prepare la renovación "
                                                      "con renew.", "motivo": None})
        text = " ".join(warnings(failing))
        self.assertIn("3 intentos", text)
        self.assertIn("1 día", text)
        self.assertNotIn("1 días", text)
        self.assertIn("índice", text)
        for raw in ("renew", "Traceback", "knowledge.cmd", "Canvas Sync"):
            self.assertNotIn(raw, text)
        expired = summary_data(token_canvas={"vence": "2026-10-01", "dias_restantes": -30,
                                             "aviso": "Token Canvas vencido: ejecute renew.", "motivo": None},
                               canvas_sync_historial={**HISTORY_OK, "ultimo_intento": "2026-10-20 08:00:00"})
        text = " ".join(warnings(expired))
        self.assertIn("venció", text)
        self.assertIn("20 de octubre", text)


class PanelCase(ServerCase):
    def setUp(self):
        super().setUp()
        self.choose_data_folder()
        self.today = MagicMock(return_value=TODAY)
        self.summary = MagicMock(return_value=summary_data())
        self.brief = MagicMock(return_value=BRIEF)
        self.instructions = MagicMock(return_value=dict(INSTRUCTIONS))
        self.version = MagicMock(return_value="abc")
        self.wizard.deps.update(panel_today=self.today, panel_summary=self.summary, panel_brief=self.brief,
                                panel_instructions=self.instructions, workbook_version=self.version)

    def complete(self, done=True):
        state = "hecho" if done else "falta"
        steps = {k: (state, "x") for k in ("datos", "canvas", "google", "drive", "materias")}
        steps.update(equipo=("falta", "x"), tarea=("falta", "x"))
        patcher = patch.object(type(self.wizard), "status", return_value=steps)
        patcher.start()
        self.addCleanup(patcher.stop)

    def assert_plain(self, body):
        for raw in RAW + INTERNAL:
            self.assertNotIn(raw, body)

    def assert_nav(self, body):
        for link in ('href="/panel"', 'href="/panel/resumen"', 'href="/panel/materias"',
                     'href="/panel/instrucciones"', 'href="/"'):
            self.assertIn(link, body)
        self.assertIn("Configuración", body)


class PanelPagesTests(PanelCase):
    def test_today_lists_items_in_order_with_names_and_plain_types(self):
        status, _, body = self.get("/panel")
        self.assertEqual(status, 200)
        self.assert_nav(body)
        self.assertIn("Taller &lt;uno&gt;", body)
        self.assertNotIn("Taller <uno>", body)
        self.assertLess(body.index("Taller"), body.index("Quiz 2"))
        self.assertLess(body.index("Quiz 2"), body.index("Comprar libro"))
        self.assertIn("Gestión de bases &lt;datos&gt;", body)
        self.assertIn("Actividad vencida", body)
        self.assertIn("domingo 1 de noviembre de 2026", body)
        self.assertNotIn("actividad_proxima", body)
        self.assert_plain(body)

    def test_today_shows_plain_warnings_not_raw_ones(self):
        self.summary.return_value = summary_data(indice={"fresh": False, "generated_at_utc": None})
        self.summary.return_value["avisos"] = ["El índice local está desactualizado: ejecutar "
                                               "scripts\\knowledge.cmd build."]
        body = self.get("/panel")[2]
        self.assertIn("índice", body)
        self.assertNotIn("knowledge.cmd", body)

    def test_empty_today(self):
        self.today.return_value = {**TODAY, "items": []}
        body = self.get("/panel")[2]
        self.assertIn("No hay nada pendiente", body)

    def test_summary_page(self):
        status, _, body = self.get("/panel/resumen")
        self.assertEqual(status, 200)
        self.assert_nav(body)
        for text in ("Segundo semestre 2026", "2026-2", "Sistemas operativos II", "Estudiar los martes",
                     "Comprar libro", "Quiz 2", "sábado 31 de octubre de 2026", "08:00"):
            self.assertIn(text, body)
        self.assertIn("2 materias", body)
        self.assert_plain(body)

    def test_one_subject_is_counted_in_singular(self):
        data = summary_data()
        data["materias"] = data["materias"][:1]
        self.summary.return_value = data
        for path in ("/panel/resumen", "/panel/materias"):
            body = self.get(path)[2]
            self.assertIn("1 materia", body)
            self.assertNotIn("1 materias", body)

    def test_today_groups_items_by_date_with_counts_and_relative_days(self):
        body = self.get("/panel")[2]
        late, tomorrow, undated = (body.index(f">{title} <span") for title in ("Atrasado", "Mañana", "Sin fecha"))
        self.assertLess(late, body.index("Taller &lt;uno&gt;"))
        self.assertLess(body.index("Taller &lt;uno&gt;"), tomorrow)
        self.assertLess(tomorrow, body.index("Quiz 2"))
        self.assertLess(body.index("Quiz 2"), undated)
        self.assertLess(undated, body.index("Comprar libro"))
        self.assertNotIn(">Hoy <span", body)  # grupos vacíos no aparecen
        self.assertIn("hace 2 días", body)
        self.assertIn(">mañana<", body)
        self.assertIn('<span class="num">1</span><span class="lbl">Atrasado</span>', body)
        self.assertIn('<span class="num">1</span><span class="lbl">Sin fecha</span>', body)

    def test_each_subject_keeps_its_color_across_pages(self):
        for path in ("/panel", "/panel/materias"):
            body = self.get(path)[2]
            self.assertIn('class="subject t0" href="/panel/materia/MAT-GBD"' if path == "/panel"
                          else 'class="card subject-card t0" href="/panel/materia/MAT-GBD"', body)
            self.assertIn("t1", body)

    def test_subjects_page_counts_pending_and_overdue(self):
        body = self.get("/panel/materias")[2]
        self.assertIn("1 vencida", body)
        self.assertIn("1 pendiente<", body)
        self.assertNotIn("1 pendientes", body)
        self.assertIn("hace 3 días (miércoles 28 de octubre de 2026)", body)

    def test_subjects_page_without_counts_when_today_fails(self):
        self.today.side_effect = AccessError("SOURCE_UNAVAILABLE", "La base publicada no está disponible.")
        status, _, body = self.get("/panel/materias")
        self.assertEqual(status, 200)
        self.assertIn('href="/panel/materia/MAT-SO2"', body)
        self.assertNotIn("pendiente", body)
        self.assert_plain(body)

    def test_configuration_shows_panel_tabs_only_when_complete(self):
        self.complete(done=False)
        self.assertNotIn('class="panel-nav"', self.get("/")[2])
        self.wizard.complete = lambda steps=None: True
        body = self.get("/")[2]
        self.assertIn('class="panel-nav"', body)
        self.assertIn('href="/" aria-current="page"', body)

    def test_subjects_page_links_each_subject(self):
        body = self.get("/panel/materias")[2]
        self.assert_nav(body)
        self.assertIn('href="/panel/materia/MAT-GBD"', body)
        self.assertIn('href="/panel/materia/MAT-SO2"', body)

    def test_subject_page_with_copy_context(self):
        status, _, body = self.get("/panel/materia/MAT-GBD")
        self.assertEqual(status, 200)
        self.assert_nav(body)
        self.brief.assert_called_once()
        self.assertEqual(self.brief.call_args.args[1], "MAT-GBD")
        self.assertIn('id="contexto-MAT-GBD"', body)
        self.assertIn("<textarea", body)
        self.assertIn("readonly", body)
        self.assertIn("Copiar contexto", body)
        self.assertIn("Taller &lt;uno&gt;", body)
        self.assertIn("Parcial 1", body)
        self.assertIn("4.5", body)
        self.assertIn("Repasar SQL", body)
        self.assert_plain(body)

    def test_unknown_subject_is_404_without_calling_the_brief(self):
        for path in ("/panel/materia/MAT-NOPE", "/panel/materia/<script>", "/panel/materia/"):
            status, _, body = self.get(path)
            self.assertEqual(status, 404)
            self.assertNotIn("<script>", body)
        self.brief.assert_not_called()

    def test_instructions_one_textarea_per_project(self):
        status, _, body = self.get("/panel/instrucciones")
        self.assertEqual(status, 200)
        self.assert_nav(body)
        self.assertEqual(body.count("<textarea"), 3)
        for ident in ("instrucciones-coordinador", "instrucciones-MAT-GBD", "instrucciones-MAT-SO2"):
            self.assertIn(f'id="{ident}"', body)
        self.assertIn("Instrucciones del &lt;Coordinador&gt;", body)
        self.assertIn("Copiar instrucciones", body)
        self.assertIn("Sistemas operativos II", body)

    def test_stale_index_shows_a_notice_and_the_configuration_link(self):
        self.instructions.side_effect = AccessError("INDEX_STALE", "El índice académico no corresponde.")
        with contextlib.redirect_stderr(io.StringIO()):
            status, _, body = self.get("/panel/instrucciones")
        self.assertEqual(status, 200)
        self.assertNotIn("<textarea", body)
        self.assertIn('href="/paso/materias"', body)
        self.assertIn('action="/paso/materias/indice"', body)
        self.assertIn("desactualizado", body)
        self.assert_plain(body)

    def test_update_card_is_visible_on_the_panel(self):
        from academic_app import updates
        release = updates.Release("9.9.0", "https://example.invalid/x.exe", "0" * 64, 1)
        self.wizard.deps["check_update"] = MagicMock(return_value=release)
        self.wizard.start_update_check().join(5)
        body = self.get("/panel")[2]
        self.assertIn("Actualizar ahora", body)
        self.assertIn('action="/actualizar"', body)


class PanelFailureTests(PanelCase):
    def test_reader_failures_never_break_the_pages(self):
        pages = ("/panel", "/panel/resumen", "/panel/materias", "/panel/materia/MAT-GBD", "/panel/instrucciones")
        for exc in FAILURES:
            for dep in (self.today, self.summary, self.brief, self.instructions):
                dep.side_effect = exc
            with self.subTest(exc=type(exc).__name__), contextlib.redirect_stderr(io.StringIO()):
                for path in pages:
                    status, _, body = self.get(path)
                    self.assertIn(status, (200, 404), path)
                    self.assertIn('href="/"', body)
                    self.assert_plain(body)
        self.assertNotIn("fallo interno", self.log.getvalue())

    def test_one_failing_subject_does_not_break_the_other_pages(self):
        self.brief.side_effect = AccessError("INVALID_SOURCE", "Fecha no reconocible en la base.")
        with contextlib.redirect_stderr(io.StringIO()):
            status, _, body = self.get("/panel/materia/MAT-GBD")
        self.assertEqual(status, 200)
        self.assertNotIn("<textarea", body)
        self.assert_plain(body)
        self.assertIn("Quiz 2", self.get("/panel")[2])
        self.assertIn("Sistemas operativos II", self.get("/panel/materias")[2])

    def test_no_data_folder_points_to_configuration(self):
        from academic_config import paths
        self.config.unlink()
        body = self.get("/panel")[2]
        self.assertIn('href="/"', body)
        self.today.assert_not_called()
        self.assert_plain(body)


class CompletenessTests(PanelCase):
    def test_complete_requires_the_five_steps_and_a_valid_database(self):
        self.complete()
        self.assertTrue(self.wizard.complete())
        self.version.side_effect = SyncError("No hay base local y estado de control para reconciliar.")
        with contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertFalse(self.wizard.complete())
        self.assertIn("SyncError", err.getvalue())
        self.assertNotIn("reconciliar", err.getvalue())

    def test_incomplete_steps_are_not_complete(self):
        self.complete(done=False)
        self.assertFalse(self.wizard.complete())
        self.version.assert_not_called()

    def test_home_links_to_the_panel_only_when_complete(self):
        self.complete()
        body = self.get("/")[2]
        self.assertIn('href="/panel"', body)

    def test_home_without_panel_link_when_incomplete(self):
        self.complete(done=False)
        status, _, body = self.get("/")
        self.assertEqual(status, 200)
        self.assertNotIn('href="/panel"', body)
        self.assertIn("Configuración de Academic Manager", body)

    def test_entry_link_lands_on_the_panel_once_when_complete(self):
        self.wizard.land_on_panel()
        status, response, _ = self.get(f"/?clave={self.server.secret}", cookie=False)
        self.assertEqual((status, response.getheader("Location")), (303, "/"))
        self.assertIn("am_sesion=", response.getheader("Set-Cookie"))
        status, response, _ = self.get("/")
        self.assertEqual((status, response.getheader("Location")), (303, "/panel"))
        status, _, body = self.get("/")  # después, «Configuración» vuelve a ser el asistente
        self.assertEqual(status, 200)
        self.assertIn("Configuración de Academic Manager", body)

    def test_entry_link_stays_on_the_wizard_when_incomplete(self):
        status, response, _ = self.get(f"/?clave={self.server.secret}", cookie=False)
        self.assertEqual((status, response.getheader("Location")), (303, "/"))
        status, _, body = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn("Configuración de Academic Manager", body)


class MainPanelTests(unittest.TestCase):
    def run_main(self, complete):
        from academic_app import __main__ as entry, updates
        from academic_app.wizard import Wizard
        fake = MagicMock()
        fake.url = "http://127.0.0.1:5000/?clave=s"
        with patch.object(entry.server, "create", return_value=fake) as create, \
                patch.object(entry.webbrowser, "open") as opened, \
                patch.object(updates, "check", return_value=None), \
                patch.object(Wizard, "complete", return_value=complete), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(entry.main([]), 0)
            wizard = create.call_args.args[0]
            wizard.update_thread.join(5)
        opened.assert_called_once_with(fake.url)
        return wizard, out.getvalue()

    def test_complete_configuration_opens_the_panel(self):
        wizard, out = self.run_main(True)
        self.assertEqual(wizard.handle("GET", "/", {}, {}, "c").location, "/panel")
        self.assertIn("panel", out)

    def test_incomplete_configuration_opens_the_wizard_as_before(self):
        wizard, out = self.run_main(False)
        with patch.object(type(wizard), "page_home", return_value="home") as home:
            self.assertEqual(wizard.handle("GET", "/", {}, {}, "c"), "home")
        home.assert_called_once()
        self.assertIn("Asistente", out)


class MalformedSystemDataTests(unittest.TestCase):
    """Fechas raras en el historial de sincronización no rompen la página (sin 500)."""

    def test_warnings_ignore_a_malformed_last_attempt(self):
        from academic_app import panel
        for last in ("ayer", "2026-13-40", "x" * 10):
            with self.subTest(last=last):
                summary = {"fecha": "2026-10-31", "sistema": {"canvas_sync_historial": {"ultimo_intento": last}}}
                self.assertEqual(panel.warnings(summary), [])

    def test_sync_text_with_an_empty_timestamp_is_plain(self):
        from academic_app.wizard import _sync_text
        text = _sync_text({"ultimo_canvas_sync": {"status": "ok", "timestamp_local": ""}})
        self.assertEqual(text, "Todavía no hay registro de la sincronización con Canvas en este PC.")


class CopyButtonTests(PanelCase):
    """S3: un único script fijo, autorizado por su hash, que solo copia el cuadro al portapapeles."""

    @staticmethod
    def script_hash():
        import base64
        import hashlib
        from academic_app import pages
        return "sha256-" + base64.b64encode(hashlib.sha256(pages.COPY_SCRIPT.encode("utf-8")).digest()).decode()

    def test_csp_allows_only_the_copy_script_by_its_hash(self):
        from academic_app import server as app_server
        _, response, _ = self.get("/panel/materia/MAT-GBD")
        csp = response.getheader("Content-Security-Policy")
        self.assertEqual(csp, app_server.CSP)
        self.assertIn(f"script-src '{self.script_hash()}'", csp)
        self.assertNotIn("script-src 'unsafe", csp)
        for kept in ("default-src 'none'", "style-src 'unsafe-inline'", "form-action 'self'",
                     "frame-ancestors 'none'", "base-uri 'none'"):
            self.assertIn(kept, csp)

    def test_every_copy_box_has_a_button_and_the_page_carries_exactly_the_fixed_script(self):
        from academic_app import pages
        for path, ids in (("/panel/materia/MAT-GBD", ["contexto-MAT-GBD"]),
                          ("/panel/instrucciones", None)):
            body = self.get(path)[2]
            boxes = re.findall(r'<textarea id="([^"]+)" class="copiar"', body)
            self.assertTrue(boxes)
            if ids:
                self.assertEqual(boxes, ids)
            for ident in boxes:
                self.assertIn(f'<button type="button" class="copiar-boton" data-copiar="{ident}" hidden>', body)
            self.assertEqual(body.count("<script"), 1)
            self.assertIn(f"<script>{pages.COPY_SCRIPT}</script>", body)
            self.assertNotRegex(body, r"\son[a-z]+=")  # sin manejadores en línea

    def test_pages_without_copy_boxes_carry_no_script(self):
        for path in ("/", "/panel", "/panel/resumen", "/panel/materias"):
            self.assertNotIn("<script", self.get(path)[2], path)

    def test_copy_script_only_touches_the_clipboard_and_its_own_box(self):
        from academic_app import pages
        script = pages.COPY_SCRIPT
        for forbidden in ("fetch", "XMLHttpRequest", "eval", "innerHTML", "location", "cookie",
                          "localStorage", "import(", "WebSocket", "sendBeacon"):
            self.assertNotIn(forbidden, script)
        self.assertIn("navigator.clipboard.writeText", script)


class VisualHelperTests(unittest.TestCase):
    def test_relative_days(self):
        from academic_app.panel import relative
        cases = {"2026-10-31": "hoy", "2026-11-01": "mañana", "2026-10-30": "ayer", "2026-11-03": "en 3 días",
                 "2026-10-29": "hace 2 días", None: None, "no es fecha": None}
        for value, expected in cases.items():
            self.assertEqual(relative(value, "2026-10-31"), expected, value)

    def test_initials_skip_connectors(self):
        from academic_app.panel import initials
        self.assertEqual(initials("Gestión de bases de datos"), "GB")
        self.assertEqual(initials("Sistemas operativos II"), "SO")
        self.assertEqual(initials(""), "?")


if __name__ == "__main__":
    unittest.main()
