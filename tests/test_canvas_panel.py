"""Datos de Canvas en el panel: lector local (academic_access.canvas) y páginas que los muestran."""
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

from academic_access.canvas import canvas_extras
from canvas_sync.common import SyncError
from canvas_sync.sync import sha
from tests.test_academic_panel import PanelCase

REGISTRY = {"schema_version": 2, "active_semester": "2026-2", "active_block": 2,
            "courses": [{"course_id": 82862, "subject_id": "MAT-GBD", "semester": "2026-2", "blocks": [2]},
                        {"course_id": 1, "subject_id": "MAT-OLD", "semester": "2026-1", "blocks": [1]}]}
COURSE = {"schema_version": 2, "source": "canvas_rest_get_only", "course": {"id": 82862},
          "assignments": [
              {"id": 10, "name": "Taller SQL", "points_possible": 20,
               "rubric": [{"id": "c1", "description": "Consultas", "points": 20,
                           "ratings": [{"id": "r1", "description": "Excelente", "points": 20}]}],
               "submission": {"score": 18, "graded_at": "2026-10-31T02:00:00Z"}},  # 30 de octubre en Bogotá
              {"id": 11, "name": "Quiz viejo", "points_possible": 5, "rubric": [],
               "submission": {"score": 5, "graded_at": "2026-10-01T15:00:00Z"}},
              {"id": 12, "name": "Sin nota", "points_possible": 10, "rubric": [], "submission": None}],
          "sections": {"submissions": {"status": "ok", "data": [
              {"assignment_id": 10,
               "comments": [{"text": "Muy bien", "created_at": "2026-10-31T03:00:00Z", "from_me": False},
                            {"text": "Gracias", "created_at": "2026-10-31T04:00:00Z", "from_me": True}],
               "rubric": {"c1": {"points": 18, "rating_id": "r1", "comments": "Falta índice"}}},
              {"assignment_id": 999, "comments": [{"text": "Huérfano", "from_me": False}], "rubric": {}}]}}}
USER = {"schema_version": 2, "source": "canvas_rest_get_only", "sections": {
    "enrollments": {"status": "ok", "data": [{"course_id": 82862, "grades": {"current_score": 84.5}},
                                             {"course_id": 1, "grades": {"current_score": 10}}]},
    "unread_messages": {"status": "ok", "data": {"by_course": {"82862": 2}, "other": 1}}}}


def write_snapshot(root, slot, data, captured="2026-11-02T13:00:00+00:00", digest=None):
    folder = root / "procesamiento" / "canvas-sync" / "v01" / str(slot)
    folder.mkdir(parents=True, exist_ok=True)
    record = {"captured_at_utc": captured, "sha256": digest or sha(data), "data": data}
    (folder / "latest.json").write_text(json.dumps(record), encoding="utf-8")


class CanvasExtrasTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def fill(self):
        control = self.root / "control" / "canvas-sync"
        control.mkdir(parents=True)
        (control / "course-registry.json").write_text(json.dumps(REGISTRY), encoding="utf-8")
        write_snapshot(self.root, 82862, COURSE)
        write_snapshot(self.root, "user", USER)

    def test_without_sync_everything_is_empty(self):
        result = canvas_extras(self.root, on="2026-11-02")
        self.assertEqual(result, {"fecha": date(2026, 11, 2), "actualizado": None, "materias": {},
                                  "notas_nuevas": []})

    def test_grade_unread_feedback_and_new_grades_per_active_subject(self):
        self.fill()
        result = canvas_extras(self.root, on="2026-11-02")
        self.assertEqual(result["actualizado"], date(2026, 11, 2))
        self.assertEqual(list(result["materias"]), ["MAT-GBD"])  # MAT-OLD es de otro semestre
        subject = result["materias"]["MAT-GBD"]
        self.assertEqual(subject["nota"], 84.5)
        self.assertEqual(subject["sin_leer"], 2)
        self.assertEqual(subject["retroalimentacion"], [{
            "materia_id": "MAT-GBD", "titulo": "Taller SQL", "nota": 18, "de": 20,
            "comentarios": [{"texto": "Muy bien", "fecha": date(2026, 10, 30)}],  # sin la respuesta propia
            "rubrica": [{"criterio": "Consultas", "puntos": 18, "de": 20, "nivel": "Excelente",
                         "comentario": "Falta índice"}],
            "fecha": date(2026, 10, 30)}])
        self.assertEqual(result["notas_nuevas"], [{"materia_id": "MAT-GBD", "titulo": "Taller SQL", "nota": 18,
                                                   "de": 20, "fecha": date(2026, 10, 30)}])

    def test_new_grades_window_is_seven_days(self):
        self.fill()
        self.assertEqual(canvas_extras(self.root, on="2026-11-05")["notas_nuevas"][0]["titulo"], "Taller SQL")
        self.assertEqual(canvas_extras(self.root, on="2026-11-06")["notas_nuevas"], [])

    def test_missing_sections_give_no_values(self):
        self.fill()
        write_snapshot(self.root, "user", {**USER, "sections": {
            "unread_messages": {"status": "unavailable", "data": None}}})
        subject = canvas_extras(self.root, on="2026-11-02")["materias"]["MAT-GBD"]
        self.assertIsNone(subject["nota"])
        self.assertIsNone(subject["sin_leer"])

    def test_tampered_snapshot_is_rejected(self):
        self.fill()
        write_snapshot(self.root, 82862, COURSE, digest="0" * 64)
        with self.assertRaises(SyncError):
            canvas_extras(self.root, on="2026-11-02")
        write_snapshot(self.root, 82862, COURSE)
        write_snapshot(self.root, "user", USER, digest="0" * 64)
        with self.assertRaises(SyncError):
            canvas_extras(self.root, on="2026-11-02")


EXTRAS = {"fecha": date(2026, 10, 31), "actualizado": date(2026, 10, 31),
          "materias": {"MAT-GBD": {
              "nota": 84.5, "sin_leer": 2,
              "retroalimentacion": [{"materia_id": "MAT-GBD", "titulo": "Taller <SQL>", "nota": 18, "de": 20,
                                     "comentarios": [{"texto": "Muy <b>bien</b>", "fecha": date(2026, 10, 30)}],
                                     "rubrica": [{"criterio": "Consultas", "puntos": 18, "de": 20,
                                                  "nivel": "Excelente", "comentario": "Falta índice"}],
                                     "fecha": date(2026, 10, 30)}]},
              "MAT-SO2": {"nota": None, "sin_leer": 0, "retroalimentacion": []}},
          "notas_nuevas": [{"materia_id": "MAT-GBD", "titulo": "Taller <SQL>", "nota": 18, "de": 20,
                            "fecha": date(2026, 10, 30)}]}


class CanvasPanelPagesTests(PanelCase):
    def setUp(self):
        super().setUp()
        self.canvas = MagicMock(return_value=EXTRAS)
        self.wizard.deps.update(panel_canvas=self.canvas)

    def test_today_shows_new_grades(self):
        body = self.get("/panel")[2]
        self.assertIn("Notas nuevas en Canvas", body)
        self.assertIn("Taller &lt;SQL&gt;", body)
        self.assertNotIn("Taller <SQL>", body)
        self.assertIn("18 de 20", body)
        self.assertIn("viernes 30 de octubre", body)
        self.assertIn('href="/panel/materia/MAT-GBD"', body)
        self.assert_plain(body)

    def test_today_without_new_grades_has_no_block(self):
        self.canvas.return_value = {**EXTRAS, "notas_nuevas": []}
        self.assertNotIn("Notas nuevas en Canvas", self.get("/panel")[2])

    def test_subjects_page_shows_grade_and_unread_count(self):
        body = self.get("/panel/materias")[2]
        self.assertIn("Nota 84.5 %", body)
        self.assertIn("2 mensajes sin leer", body)
        self.assertNotIn("0 mensajes sin leer", body)
        self.assert_plain(body)

    def test_summary_page_shows_grade(self):
        body = self.get("/panel/resumen")[2]
        self.assertIn("Nota 84.5 %", body)
        self.assert_plain(body)

    def test_subject_page_shows_teacher_feedback_unread_and_copy_context(self):
        body = self.get("/panel/materia/MAT-GBD")[2]
        self.assertIn("Comentarios del profesor", body)
        self.assertIn("Muy &lt;b&gt;bien&lt;/b&gt;", body)
        self.assertNotIn("Muy <b>bien</b>", body)
        for text in ("Consultas", "Excelente", "Falta índice", "18 de 20", "84.5 %"):
            self.assertIn(text, body)
        self.assertIn("2 mensajes sin leer", body)
        self.assertIn('href="https://areandina.instructure.com/conversations"', body)
        context = body[body.index('id="contexto-MAT-GBD"'):]
        self.assertIn("Nota actual en Canvas: 84.5 %", context)
        self.assertIn("Comentarios del profesor", context)
        self.assert_plain(body)

    def test_pages_work_when_canvas_data_fails(self):
        self.canvas.side_effect = SyncError("Snapshot Canvas no supera comprobación SHA-256.")
        for path in ("/panel", "/panel/resumen", "/panel/materias", "/panel/materia/MAT-GBD"):
            status, _, body = self.get(path)
            self.assertEqual(status, 200)
            self.assertNotIn("SHA-256", body)
            self.assertNotIn("Comentarios del profesor", body)
            self.assert_plain(body)


if __name__ == "__main__":
    unittest.main()
