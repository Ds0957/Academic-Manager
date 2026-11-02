"""Auditoría de cobertura extra de Canvas (`canvas_sync audit-extra`): solo GET, solo metadatos."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from canvas_sync.common import SyncError

COURSES = [{"id": 82862, "name": "Gestión de bases de datos"}, {"id": 91004, "name": "Sistemas operativos II"}]
SECRET_TEXT = "Profe Ana: revise la normalización"  # comentario del docente: nunca se guarda
SUBJECT = "Cambio de fecha del parcial"             # asunto de un mensaje: nunca se guarda


def submissions(course_id):
    return [
        {"id": 1, "assignment_id": 10, "score": 4.5, "grade": "4.5", "workflow_state": "graded",
         "late": False, "missing": False, "excused": None, "graded_at": "2026-10-20T15:00:00Z",
         "submission_comments": [{"comment": SECRET_TEXT, "author_name": "Ana"}],
         "rubric_assessment": {"_1": {"points": 2}}},
        {"id": 2, "assignment_id": 11, "score": None, "grade": None, "workflow_state": "unsubmitted",
         "late": False, "missing": True, "excused": None, "graded_at": None,
         "submission_comments": [], "rubric_assessment": None},
        {"id": 3, "assignment_id": 12, "score": 3, "grade": "3", "workflow_state": "graded",
         "late": True, "missing": False, "excused": None, "seconds_late": 3600,
         "submission_comments": [], "rubric_assessment": None},
    ]


class FakeCanvas:
    """Responde por ruta; un valor SyncError se lanza como lo haría el cliente real."""

    def __init__(self, token, responses=None):
        assert token == "fake-secret"
        self.calls = []
        self.responses = responses or {}

    def _answer(self, path):
        for prefix, value in self.responses.items():
            if path.startswith(prefix):
                if isinstance(value, Exception):
                    raise value
                return value
        raise AssertionError(f"consulta no esperada: {path}")

    def profile(self):
        self.calls.append(("GET", "/api/v1/users/self/profile", None))
        return 123

    def courses(self):
        self.calls.append(("GET", "/api/v1/courses", None))
        return COURSES

    def pages(self, path, params=None):
        self.calls.append(("GET", path, params))
        return self._answer(path)

    def get(self, url):
        path = url.split("areandina.instructure.com", 1)[1]
        self.calls.append(("GET", path.split("?")[0], None))
        return self._answer(path.split("?")[0]), ""


def responses():
    forbidden = SyncError("Sin permisos para esta consulta (HTTP 403).")
    missing = SyncError("Canvas respondió HTTP 404.")
    return {
        "/api/v1/courses/82862/students/submissions": submissions(82862),
        "/api/v1/courses/91004/students/submissions": forbidden,
        "/api/v1/courses/82862/grading_periods": {"grading_periods": [
            {"id": 1, "title": "Bloque 1", "start_date": "2026-08-01", "end_date": "2026-09-30"},
            {"id": 2, "title": "Bloque 2", "start_date": "2026-10-01", "end_date": "2026-11-30"}]},
        "/api/v1/courses/91004/grading_periods": missing,
        "/api/v1/courses/82862/media_objects": [
            {"media_id": "m1", "title": "Clase 3", "media_type": "video",
             "media_tracks": [{"kind": "subtitles", "locale": "es", "id": 7}]},
            {"media_id": "m2", "title": "Clase 4", "media_type": "video", "media_tracks": []}],
        "/api/v1/courses/91004/media_objects": [],
        "/api/v1/courses/82862/smartsearch": forbidden,
        "/api/v1/courses/91004/smartsearch": missing,
        "/api/v1/users/self/enrollments": [
            {"course_id": 82862, "type": "StudentEnrollment",
             "grades": {"current_score": 87.5, "final_score": 40.1, "current_grade": None, "html_url": "x"}},
            {"course_id": 91004, "type": "StudentEnrollment", "grades": {"current_score": None}}],
        "/api/v1/conversations/unread_count": {"unread_count": "2"},
        "/api/v1/conversations": [
            {"id": 5, "subject": SUBJECT, "last_message": "Hola a todos", "context_name": "GBD",
             "participants": [{"id": 9, "name": "Compañero Uno"}], "workflow_state": "unread"},
            {"id": 6, "subject": "Otro", "last_message": "x", "context_name": "SO2",
             "participants": [], "workflow_state": "unread"}],
        "/api/v1/planner/items": [
            {"plannable_type": "assignment", "course_id": 82862,
             "submissions": {"with_feedback": True, "missing": False, "graded": True, "late": False}},
            {"plannable_type": "announcement", "course_id": 91004, "submissions": False},
            {"plannable_type": "discussion_topic", "course_id": 91004,
             "submissions": {"with_feedback": False, "missing": True}}],
    }


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        patcher = patch("canvas_sync.coverage.valid", return_value=({"token": "fake-secret", "account_id": 123}, None))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = None

    def factory(self, token):
        self.client = FakeCanvas(token, responses())
        return self.client

    def run_audit(self):
        from canvas_sync.coverage import audit
        return audit(self.root, client_factory=self.factory, today="2026-10-31")

    def test_counts_and_field_presence_per_course(self):
        report = self.run_audit()
        subs = report["cursos"]["82862"]["submissions"]
        self.assertEqual(subs["status"], "ok")
        self.assertEqual((subs["total"], subs["con_comentarios"], subs["comentarios"], subs["con_rubrica"]),
                         (3, 1, 1, 1))
        self.assertEqual((subs["missing"], subs["late"], subs["excused"], subs["con_graded_at"]), (1, 1, 0, 1))
        self.assertIn("seconds_late", subs["campos"])
        self.assertEqual(report["cursos"]["91004"]["submissions"]["status"], "forbidden")
        periods = report["cursos"]["82862"]["grading_periods"]
        self.assertEqual((periods["status"], periods["total"], periods["titulos"]), ("ok", 2, ["Bloque 1", "Bloque 2"]))
        self.assertEqual(report["cursos"]["91004"]["grading_periods"]["status"], "not_found")
        media = report["cursos"]["82862"]["media_objects"]
        self.assertEqual((media["total"], media["con_pistas"], media["pistas"]), (2, 1, ["subtitles:es"]))
        self.assertEqual(report["cursos"]["82862"]["smartsearch"]["status"], "forbidden")

    def test_user_level_probes(self):
        report = self.run_audit()
        user = report["usuario"]
        self.assertEqual(user["enrollments"]["total"], 2)
        self.assertEqual(user["enrollments"]["campos_nota"], ["current_score", "final_score", "html_url"])
        self.assertEqual(user["conversations"]["sin_leer"], 2)
        self.assertEqual(user["conversations"]["listado"], 2)
        self.assertIn("subject", user["conversations"]["campos"])
        planner = user["planner_new_activity"]
        self.assertEqual(planner["total"], 3)
        self.assertEqual(planner["por_tipo"], {"announcement": 1, "assignment": 1, "discussion_topic": 1})
        self.assertEqual((planner["with_feedback"], planner["missing"]), (1, 1))

    def test_only_allowed_reads_and_never_opens_a_message(self):
        self.run_audit()
        paths = [p for _, p, _ in self.client.calls]
        self.assertTrue(all(method == "GET" for method, _, _ in self.client.calls))
        self.assertFalse([p for p in paths if p.startswith("/api/v1/conversations/") and
                          p != "/api/v1/conversations/unread_count"])
        conv = next(params for _, p, params in self.client.calls if p == "/api/v1/conversations")
        self.assertEqual(conv["scope"], "unread")
        self.assertFalse([p for p in paths if "what_if" in p or "study_assist" in p or "media_tracks" in p])
        subs = next(params for _, p, params in self.client.calls if p.endswith("/students/submissions"))
        self.assertEqual(subs["include[]"], ["submission_comments", "rubric_assessment"])

    def test_report_keeps_no_texts_names_or_grades(self):
        self.run_audit()
        saved = (self.root / "procesamiento" / "canvas-sync" / "coverage" / "extra-latest.json").read_text("utf-8")
        for private in (SECRET_TEXT, "Ana", SUBJECT, "Hola a todos", "Compañero Uno", "87.5", "40.1",
                        "Clase 3", "fake-secret"):
            self.assertNotIn(private, saved)
        data = json.loads(saved)
        self.assertEqual(data["esquema"], 1)
        self.assertEqual(sorted(data["cursos"]), ["82862", "91004"])

    def test_summary_is_plain_text_without_private_data(self):
        from canvas_sync.coverage import summary
        text = summary(self.run_audit())
        self.assertIn("82862", text)
        self.assertIn("submissions: ok", text)
        self.assertIn("sin permiso", text)
        for private in (SECRET_TEXT, SUBJECT, "87.5"):
            self.assertNotIn(private, text)

    def test_unexpected_failure_is_recorded_not_raised(self):
        def factory(token):
            data = responses()
            data["/api/v1/planner/items"] = SyncError("Conexión segura con Canvas no disponible.")
            self.client = FakeCanvas(token, data)
            return self.client
        from canvas_sync.coverage import audit
        report = audit(self.root, client_factory=factory, today="2026-10-31")
        self.assertEqual(report["usuario"]["planner_new_activity"]["status"], "error")
        self.assertEqual(report["usuario"]["planner_new_activity"]["detalle"],
                         "Conexión segura con Canvas no disponible.")

    def test_account_change_stops_before_any_probe(self):
        def factory(token):
            self.client = FakeCanvas(token, responses())
            self.client.profile = lambda: 999
            return self.client
        from canvas_sync.coverage import audit
        with self.assertRaises(SyncError):
            audit(self.root, client_factory=factory, today="2026-10-31")
        self.assertFalse((self.root / "procesamiento").exists())


class CliTests(unittest.TestCase):
    def test_audit_extra_prints_the_summary(self):
        import contextlib
        import io
        from canvas_sync import __main__ as cli
        out = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp, \
                patch("canvas_sync.coverage.audit", return_value={"x": 1}) as audit, \
                patch("canvas_sync.coverage.summary", return_value="RESUMEN DE PRUEBA"), \
                contextlib.redirect_stdout(out):
            code = cli.main(["audit-extra", "--runtime", tmp])
        self.assertEqual(code, 0)
        audit.assert_called_once()
        self.assertIn("RESUMEN DE PRUEBA", out.getvalue())


if __name__ == "__main__":
    unittest.main()
