"""Registrar desde el panel: vista previa, aprobación y publicación en segundo plano.

`chat.preview` y `chat.apply` se simulan; el servidor es el real de tests.test_academic_app.
"""
import contextlib
import html
import io
import re
import threading
import time
import unittest
from unittest.mock import MagicMock

from academic_operations.actions import OperationError

from tests.test_academic_panel import INTERNAL, RAW, PanelCase

PLAN = "a" * 64
OTHER_PLAN = "b" * 64
TRACK = {"accion": "track", "actividad_id": "ACT-1", "estado": "entregada", "nota": "4,5",
         "volver": "/panel/materia/MAT-GBD", "etiqueta": "Taller <uno>"}
TASK = {"accion": "task", "titulo": "Repasar SQL", "materia_id": "MAT-GBD", "fecha_limite": "2026-11-03",
        "volver": "/panel/materia/MAT-GBD"}


class RegisterCase(PanelCase):
    def setUp(self):
        super().setUp()
        self.complete()
        self.preview = MagicMock(return_value={"plan_id": PLAN, "already_applied": False,
                                               "requires_explicit_approval": True})
        self.release = threading.Event()
        self.release.set()
        self.outcome = {"published": True, "already_applied": False, "index_refreshed": True}
        self.apply = MagicMock(side_effect=self.fake_apply)
        self.wizard.deps.update(write_preview=self.preview, write_apply=self.apply)

    def fake_apply(self, runtime, action, request, *, plan_id=None, approved=False):
        self.release.wait(5)
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return dict(self.outcome, plan_id=plan_id)

    def approve(self, fields, plan=PLAN):
        return self.post("/panel/registrar/aprobar", {**fields, "plan_id": plan})

    def wait_done(self, location):
        for _ in range(100):
            body = self.get(location)[2]
            if "Publicando…" not in body:
                return body
            time.sleep(0.05)
        self.fail("la publicación no terminó")

    def assert_plain(self, body):
        super().assert_plain(body)
        for raw in ("OperationError", "STORE_BUSY", "PLAN_CHANGED", "INVALID_ARGUMENT", "NOT_FOUND"):
            self.assertNotIn(raw, body)


class PreviewTests(RegisterCase):
    def test_track_preview_shows_what_will_be_saved_and_the_approval_form(self):
        status, _, body = self.post("/panel/registrar", TRACK)
        self.assertEqual(status, 200)
        self.preview.assert_called_once()
        _, action, request = self.preview.call_args.args
        self.assertEqual(action, "track")
        self.assertEqual(request, {"actividad_id": "ACT-1", "estado": "entregada", "nota": 4.5})
        for text in ("Revisar antes de publicar", "Taller &lt;uno&gt;", "entregada", "4.5", "sin cambios",
                     "Aprobar y publicar", f'name="plan_id" value="{PLAN}"', 'action="/panel/registrar/aprobar"',
                     'href="/panel/materia/MAT-GBD">Cancelar'):
            self.assertIn(text, body)
        self.assertNotIn("Taller <uno>", body)
        self.apply.assert_not_called()
        self.assert_plain(body)

    def test_task_and_note_previews_use_plain_labels(self):
        body = self.post("/panel/registrar", TASK)[2]
        for text in ("Tarea nueva", "Repasar SQL", "Gestión de bases &lt;datos&gt;", "martes 3 de noviembre de 2026"):
            self.assertIn(text, body)
        body = self.post("/panel/registrar", {"accion": "note", "materia_id": "MAT-GBD", "tipo": "decision",
                                              "texto": "Estudiar los martes"})[2]
        self.assertIn("Decisión", body)
        self.assertIn("Estudiar los martes", body)
        body = self.post("/panel/registrar", {"accion": "task-status", "tarea_id": "TAR-1", "estado": "hecha",
                                              "etiqueta": "Comprar libro"})[2]
        self.assertIn("Comprar libro", body)
        self.assertIn("hecha", body)

    def test_form_errors_are_plain_and_never_reach_the_contract(self):
        cases = [({"accion": "task", "titulo": ""}, "Escriba el título de la tarea."),
                 ({**TRACK, "nota": "cuatro"}, "La nota debe ser un número"),
                 ({**TRACK, "nota": "-1"}, "La nota debe ser un número de cero o más."),
                 ({**TRACK, "estado": "perdida"}, "Elija el estado de la entrega."),
                 ({**TASK, "fecha_limite": "3/11/2026"}, "La fecha límite no es válida"),
                 ({"accion": "note", "materia_id": "MAT-GBD", "tipo": "decision"}, "Escriba el texto"),
                 ({"accion": "borrar"}, "No se reconoce qué quiere registrar"),
                 ({**TRACK, "actividad_id": "ACT 1; x"}, "No se reconoce qué quiere registrar"),
                 ({**TASK, "titulo": "x" * 201}, "demasiado largo")]
        for fields, text in cases:
            status, _, body = self.post("/panel/registrar", fields)
            self.assertEqual(status, 400, fields)
            self.assertIn(text, body)
            self.assert_plain(body)
        self.preview.assert_not_called()

    def test_contract_errors_become_plain_messages(self):
        cases = [(OperationError("INVALID_ARGUMENT", "fecha_limite debe ser una fecha válida YYYY-MM-DD."),
                  "Algún dato del formulario no es válido"),
                 (OperationError("NOT_FOUND", "actividad_id no existe en la base."), "ya no está en su base"),
                 (OperationError("STORE_BUSY", "La base está ocupada.", retryable=True), "está ocupada"),
                 (RuntimeError("C:\\Users\\x\\secreto falló"), "No se pudo completar el registro")]
        err = io.StringIO()
        for exc, text in cases:
            self.preview.side_effect = exc
            with contextlib.redirect_stderr(err):
                status, _, body = self.post("/panel/registrar", TRACK)
            self.assertEqual(status, 400)
            self.assertIn(text, body)
            self.assertNotIn(str(exc), body)
            self.assertNotIn("YYYY-MM-DD", body)
            self.assertIn('href="/panel/materia/MAT-GBD"', body)
        self.assertIn("(RuntimeError)", err.getvalue())  # el registro solo nombra el tipo
        self.assertIn("(OperationError, NOT_FOUND)", err.getvalue())
        self.assertNotIn("secreto", err.getvalue())

    def test_already_applied_offers_no_publish_button(self):
        self.preview.return_value = {"plan_id": PLAN, "already_applied": True}
        body = self.post("/panel/registrar", TRACK)[2]
        self.assertIn("Ya estaba registrado", body)
        self.assertNotIn("Aprobar y publicar", body)

    def test_cancel_link_only_goes_to_panel_pages(self):
        for bad in ("https://example.com", "//example.com", "/paso/datos", "/panel/../paso", "javascript:x"):
            body = self.post("/panel/registrar", {**TRACK, "volver": bad})[2]
            self.assertIn('href="/panel">Cancelar', body, bad)

    def test_requires_csrf(self):
        status, _, _ = self.post("/panel/registrar", TRACK, csrf=False)
        self.assertNotEqual(status, 200)
        self.preview.assert_not_called()


class PublishTests(RegisterCase):
    def test_approval_publishes_in_background_and_reports_done(self):
        self.release.clear()
        status, response, _ = self.approve(TRACK)
        self.assertEqual(status, 303)
        location = response.getheader("Location")
        self.assertRegex(location, r"^/panel/publicando/[A-Za-z0-9_-]+$")
        body = self.get(location)[2]
        self.assertIn("Publicando…", body)
        self.assertIn('<meta http-equiv="refresh" content="2">', body)
        self.assertIn("Taller &lt;uno&gt;", body)
        self.release.set()
        body = self.wait_done(location)
        self.assertIn("Listo: quedó guardado y publicado en Drive.", body)
        self.assertNotIn('http-equiv="refresh"', body)
        self.assertIn('href="/panel/materia/MAT-GBD"', body)
        self.apply.assert_called_once()
        call = self.apply.call_args
        self.assertEqual(call.args[1:], ("track", {"actividad_id": "ACT-1", "estado": "entregada", "nota": 4.5}))
        self.assertEqual(call.kwargs, {"plan_id": PLAN, "approved": True})
        self.assertNotIn("<script", body)

    def test_double_approval_publishes_once(self):
        self.release.clear()
        first = self.approve(TRACK)[1].getheader("Location")
        second = self.approve(TRACK)[1].getheader("Location")
        self.assertEqual(first, second)
        self.release.set()
        self.wait_done(first)
        self.assertEqual(self.apply.call_count, 1)

    def test_same_plan_after_it_finished_is_applied_again(self):
        # El plan_id depende solo del contenido: «hecha» → «pendiente» → «hecha» repite el primero.
        first = self.approve(TRACK)[1].getheader("Location")
        self.wait_done(first)
        again = self.approve(TRACK)[1].getheader("Location")
        self.assertNotEqual(first, again)
        self.assertIn("Listo", self.wait_done(again))
        self.assertEqual(self.apply.call_count, 2)

    def test_a_publication_that_cannot_start_does_not_block_the_next(self):
        from unittest.mock import patch
        broken = MagicMock()
        broken.Thread.return_value.start.side_effect = RuntimeError("sin hilos")
        with patch("academic_app.registrar.threading", broken):  # solo el hilo de la publicación
            location = self.approve(TRACK)[1].getheader("Location")
        body = self.get(location)[2]
        self.assertIn("No se pudo publicar", body)
        self.assertEqual(self.approve(TASK, plan=OTHER_PLAN)[0], 303)

    def test_an_unexpected_base_exception_ends_the_publication(self):
        self.outcome = KeyboardInterrupt()
        location = self.approve(TRACK)[1].getheader("Location")
        self.assertIn("No se pudo publicar", self.wait_done(location))
        self.outcome = {"published": True, "already_applied": False, "index_refreshed": True}
        self.assertEqual(self.approve(TASK, plan=OTHER_PLAN)[0], 303)

    def test_one_publication_at_a_time(self):
        self.release.clear()
        first = self.approve(TRACK)[1].getheader("Location")
        status, _, body = self.approve(TASK, plan=OTHER_PLAN)
        self.assertEqual(status, 400)
        self.assertIn("Ya hay otra publicación en curso", body)
        self.release.set()
        self.wait_done(first)
        self.assertEqual(self.approve(TASK, plan=OTHER_PLAN)[0], 303)

    def test_publication_failures_are_plain(self):
        uncertain = OperationError("PUBLISH_FAILED", "falló C:\\ruta\\secreta")
        uncertain.published = "incierto"
        cases = [(OperationError("PLAN_CHANGED", "La petición o la base cambió."), "Sus datos cambiaron"),
                 (OperationError("STORE_BUSY", "ocupada", retryable=True), "está ocupada"),
                 (uncertain, "No se sabe si el cambio llegó a publicarse"),
                 (ValueError("boom"), "No se pudo completar el registro")]
        err = io.StringIO()
        for n, (exc, text) in enumerate(cases):
            self.outcome = exc
            with contextlib.redirect_stderr(err):
                location = self.approve(TRACK, plan=f"{n}" * 64)[1].getheader("Location")
                body = self.wait_done(location)
            self.assertIn("No se pudo publicar", body)
            self.assertIn(text, body)
            self.assertNotIn("secreta", body)
            self.assert_plain(body)
        self.assertIn("(ValueError)", err.getvalue())
        self.assertNotIn("secreta", err.getvalue())

    def test_failed_plan_can_be_retried(self):
        self.outcome = OperationError("STORE_BUSY", "ocupada", retryable=True)
        first = self.approve(TRACK)[1].getheader("Location")
        self.wait_done(first)
        self.outcome = {"published": True, "already_applied": False, "index_refreshed": True}
        second = self.approve(TRACK)[1].getheader("Location")
        self.assertNotEqual(first, second)
        self.assertIn("Listo", self.wait_done(second))

    def test_index_warning_and_already_applied_outcomes(self):
        self.outcome = {"published": True, "already_applied": False, "index_refreshed": False}
        body = self.wait_done(self.approve(TRACK)[1].getheader("Location"))
        self.assertIn("Listo", body)
        self.assertIn("El índice de búsqueda de este PC no se pudo actualizar", body)
        self.outcome = {"published": False, "already_applied": True, "index_refreshed": True}
        body = self.wait_done(self.approve(TASK, plan=OTHER_PLAN)[1].getheader("Location"))
        self.assertIn("Ya estaba registrado", body)

    def test_bad_plan_and_unknown_job(self):
        status, _, body = self.approve(TRACK, plan="no-es-un-plan")
        self.assertEqual(status, 400)
        self.assertIn("Sus datos cambiaron", body)
        self.apply.assert_not_called()
        status, _, body = self.get("/panel/publicando/no-existe")
        self.assertEqual(status, 404)
        self.assertIn("ya no está en la lista", body)

    def test_form_is_validated_again_on_approval(self):
        status, _, body = self.approve({**TRACK, "estado": "perdida"})
        self.assertEqual(status, 400)
        self.assertIn("Elija el estado de la entrega.", body)
        self.apply.assert_not_called()


def forms_of(body):
    """Cada <form> de la página como {campo: valor}, tal como lo enviaría el navegador sin tocar nada."""
    result = []
    for match in re.finditer(r"<form [^>]*>(.*?)</form>", body, re.S):
        inner, fields = match.group(1), {}
        for name, value in re.findall(r'<input[^>]* name="([^"]+)" value="([^"]*)"', inner):
            fields[name] = html.unescape(value)
        for name, value in re.findall(r'<textarea[^>]* name="([^"]+)"[^>]*>(.*?)</textarea>', inner, re.S):
            fields[name] = html.unescape(value)
        for name, options in re.findall(r'<select[^>]* name="([^"]+)">(.*?)</select>', inner, re.S):
            chosen = re.search(r'<option value="([^"]*)" selected>', options) or re.search(
                r'<option value="([^"]*)"', options)
            fields[name] = html.unescape(chosen.group(1))
        button = re.findall(r"<button[^>]*>([^<]*)</button>", inner)
        result.append((button[-1] if button else "", fields))
    return result


class ButtonsTests(RegisterCase):
    def find(self, body, button, **match):
        for label, fields in forms_of(body):
            if label == button and all(fields.get(k) == v for k, v in match.items()):
                return fields
        self.fail(f"no hay formulario «{button}» con {match}")

    def test_today_cards_have_quick_actions_for_activities_and_tasks_only(self):
        body = self.get("/panel")[2]
        found = [(label, f.get("accion"), f.get("actividad_id") or f.get("tarea_id"))
                 for label, f in forms_of(body) if f.get("accion")]
        self.assertIn(("Marcar como entregada", "track", "ACT-1"), found)
        self.assertIn(("Marcar como entregada", "track", "ACT-2"), found)
        self.assertIn(("Marcar como hecha", "task-status", "TAR-1"), found)
        self.assertTrue(all(f["volver"] == "/panel" for _, f in forms_of(body) if f.get("accion")))
        self.assertEqual(len([x for x in found if x[0] == "Marcar como entregada"]), 2)
        self.assertNotIn("<script", body.replace(f"<script>{_copy_script()}</script>", ""))

    def test_quick_delivery_round_trip_from_today(self):
        fields = self.find(self.get("/panel")[2], "Marcar como entregada", actividad_id="ACT-1")
        body = self.post("/panel/registrar", fields)[2]
        self.assertEqual(self.preview.call_args.args[1:], ("track", {"actividad_id": "ACT-1",
                                                                     "estado": "entregada"}))
        self.assertIn("Taller &lt;uno&gt;", body)
        self.assertIn('href="/panel">Cancelar', body)

    def test_subject_page_forms_round_trip(self):
        body = self.get("/panel/materia/MAT-GBD")[2]
        grade = self.find(body, "Revisar", accion="track", actividad_id="ACT-0")
        self.assertEqual((grade["estado"], grade["nota"], grade["puntos"], grade["comentario"]),
                         ("calificada", "4.5", "45", "Bien hecho"))  # valores actuales precargados
        self.post("/panel/registrar", {**grade, "nota": "4,8"})
        self.assertEqual(self.preview.call_args.args[1:], ("track", {
            "actividad_id": "ACT-0", "estado": "calificada", "nota": 4.8, "puntos": 45.0,
            "comentario": "Bien hecho"}))
        task = self.find(body, "Revisar", accion="task", materia_id="MAT-GBD")
        self.post("/panel/registrar", {**task, "titulo": "Leer capítulo 3", "fecha_limite": "2026-11-05"})
        self.assertEqual(self.preview.call_args.args[1:], ("task", {
            "titulo": "Leer capítulo 3", "materia_id": "MAT-GBD", "fecha_limite": "2026-11-05"}))
        note = self.find(body, "Revisar", accion="note", materia_id="MAT-GBD")
        self.assertEqual(note["tipo"], "decision")
        self.post("/panel/registrar", {**note, "texto": "Repasar los jueves"})
        self.assertEqual(self.preview.call_args.args[1:], ("note", {
            "materia_id": "MAT-GBD", "tipo": "decision", "texto": "Repasar los jueves"}))
        done = self.find(body, "Marcar como hecha", tarea_id="TAR-2")
        self.post("/panel/registrar", done)
        self.assertEqual(self.preview.call_args.args[1:], ("task-status", {"tarea_id": "TAR-2", "estado": "hecha"}))
        other = self.find(body, "Revisar", accion="task-status", tarea_id="TAR-2")
        self.assertNotIn('<option value="en_progreso"', body.split('id="tarea-abierta-TAR-2"')[1].split("</select>")[0])
        self.assertIn(other["estado"], ("pendiente", "hecha", "descartada"))
        self.assertTrue(all(f["volver"] == "/panel/materia/MAT-GBD" for _, f in forms_of(body) if f.get("accion")))

    def test_odd_titles_are_escaped_and_survive_the_round_trip(self):
        odd = 'Taller "final" <b>&</b>\nparte 2'
        self.today.return_value = {**self.today.return_value,
                                   "items": [{**self.today.return_value["items"][0], "titulo": odd}]}
        body = self.get("/panel")[2]
        self.assertNotIn("<b>&</b>", body)
        fields = self.find(body, "Marcar como entregada", actividad_id="ACT-1")
        self.assertEqual(fields["etiqueta"], odd)
        preview = self.post("/panel/registrar", fields)[2]
        approve = self.find(preview, "Aprobar y publicar")
        self.assertEqual(approve["etiqueta"], odd)
        self.assertNotIn("<b>&</b>", preview)


def _copy_script():
    from academic_app.pages import COPY_SCRIPT
    return COPY_SCRIPT


if __name__ == "__main__":
    unittest.main()
