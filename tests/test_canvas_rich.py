"""Regresión Canvas Sync enriquecido: datos sintéticos, sin operaciones sobre Canvas."""
import contextlib
from datetime import date
import io
import json
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from canvas_sync.common import SyncError
from canvas_sync.enriched import (BudgetClient, assignment_row, build_course, build_user,
                                  content_from_html, normalize, snapshot, sync_rich,
                                  provisional_kind)


class MockCanvas:
    def __init__(self, token):
        if token != "fake-secret":
            raise AssertionError("Unexpected identity")
        self.requests = 0
        self.actions = []

    def profile(self):
        self.actions.append("GET profile")
        return 123

    def courses(self):
        self.actions.append("GET courses")
        return [{"id": 82862, "name": "Materia simulada"}]

    def course(self, course_id):
        self.actions.append("GET course")
        return {"id": course_id, "name": "Materia simulada",
                "time_zone": "America/Bogota"}

    def assignments(self, course_id):
        self.actions.append("GET assignments")
        return [{"id": 391671, "name": "Evaluación de Eje 1",
                 "quiz_id": 228076, "points_possible": 20,
                 "description": "<p>Estudia <b>SQL</b> <script>EVIL</script>."
                                "<a href='/courses/82862/files/7'>Guía</a></p>",
                 "submission": {"workflow_state": "graded",
                                "score": 5.7, "submitted_at": "2026-09-26T12:05:00-05:00"},
                 "rubric": [{"id": "criterion-1", "description": "SQL", "points": 20,
                             "ratings": [{"id": "r", "points": 20, "description": "Bien"}]}]},
                {"id": 391692, "name": "Nota final [P1]", "points_possible": 100}]

    def pages(self, path, params=None):
        self.requests += 1
        self.actions.append("GET " + path)
        if "/assignment_groups" in path:
            return [{"id": 1, "name": "Ejes", "group_weight": 80,
                     "assignments": [{"id": 391671}]}]
        if "/modules/" in path and path.endswith("/items"):
            return [{"id": 200, "title": "Eje 1", "type": "Assignment",
                     "content_id": 391671}]
        if path.endswith("/modules"):
            return [{"id": 300, "name": "Master", "items_count": 1}]
        if path.endswith("/quizzes"):
            return [{"id": 228076, "title": "Quiz E1", "description": "<p>15 preguntas</p>",
                     "question_count": 15}]
        if path.endswith("/discussion_topics"):
            return [{"id": 45, "title": "Foro", "message": "<p>Comenta el tema</p>"}]
        if path.endswith("/files"):
            return [{"id": 9, "display_name": "Guía.pdf", "url": "https://signed.example/token"}]
        if path.endswith("/pages"):
            return [{"url": "aula", "title": "Aula"}]
        if path.endswith("/announcements"):
            return [{"id": 50, "title": "Novedad", "message": "<p>Lee esto</p>"}]
        if path.endswith("/enrollments"):
            return [{"course_id": 82862, "grades": {"current_score": 84.5},
                     "user_id": 999, "other_private_field": "no publicar"}]
        if path.endswith("/todo"):
            return [{"id": 1, "title": "Actividad"}]
        if path.endswith("/upcoming_events"):
            return [{"id": 2, "title": "Actividad próxima"}]
        if path.endswith("/students/submissions"):
            assert params["include[]"] == ["submission_comments", "rubric_assessment"]
            return [{"id": 7001, "assignment_id": 391671, "user_id": 999, "score": 18,
                     "submission_comments": [
                         {"id": 1, "author_id": 555, "author_name": "Profesora Ana",
                          "author": {"avatar_image_url": "https://avatar.example/a.png"},
                          "comment": "Buen trabajo, revisa la normalización.",
                          "created_at": "2026-10-01T10:00:00Z"},
                         {"id": 2, "author_id": 999, "author_name": "Estudiante Uno",
                          "comment": "Gracias", "created_at": "2026-10-01T11:00:00Z"}],
                     "rubric_assessment": {"criterion-1": {"points": 18, "rating_id": "r",
                                                           "comments": "Falta un ejemplo"}}},
                    {"id": 7002, "assignment_id": 391692, "user_id": 999,
                     "submission_comments": [], "rubric_assessment": None}]
        if path.endswith("/conversations"):
            assert params["scope"] == "unread"
            return [{"id": 1, "subject": "Asunto privado", "last_message": "Cuerpo privado",
                     "participants": [{"name": "Docente X"}], "context_code": "course_82862"},
                    {"id": 2, "subject": "Asunto privado", "context_code": None,
                     "audience_contexts": {"courses": {"82862": ["TeacherEnrollment"]}}},
                    {"id": 3, "subject": "Asunto privado", "context_code": "course_1"}]
        if path.endswith("/planner/items"):
            return [{"plannable_id": 391671, "plannable_type": "assignment",
                     "plannable": {"title": "Evaluación de Eje 1"}}]
        raise AssertionError("Unexpected path " + path)

    def get(self, url):
        self.requests += 1
        self.actions.append("GET " + url)
        if url.endswith("/front_page"):
            return {"url": "aula", "title": "Aula", "body": "<iframe src='https://example.org/embed'></iframe>"}, ""
        if url.endswith("/pages/aula"):
            return {"url": "aula", "title": "Aula", "body": "<p>Bienvenido</p>"}, ""
        raise AssertionError("Unexpected GET " + url)


class RichSyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.patcher = patch("canvas_sync.enriched.valid",
                             return_value=({"token": "fake-secret", "account_id": 123}, None))
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        self.tmp.cleanup()

    def test_html_is_never_executable(self):
        item = content_from_html('<div>Texto <b>SQL</b><script>alert("X")</script>'
                                 '<a href="https://example.org/ref">Enlace</a>'
                                 '<img src="/files/1" alt="Diagrama"></div>')
        self.assertIn("Texto SQL", item["text"])
        self.assertNotIn("alert", item["text"])
        self.assertIn("Diagrama", item["text"])
        self.assertIn("https://example.org/ref", item["links"])
        self.assertIn("/files/1", item["links"])

    def test_classification_is_provisional(self):
        self.assertEqual(provisional_kind({"name": "Nota final [P1]"}), "nota_agregada")
        self.assertEqual(provisional_kind({"name": "CLASS RECORDINGS"}), "informativo")
        self.assertEqual(provisional_kind({"name": "Actividad Complementaria eje 1"}), "complementaria")
        self.assertEqual(provisional_kind({"name": "Eje 1", "quiz_id": 8}), "cuestionario")
        self.assertFalse(assignment_row({"name": "Nota final", "id": 9})["classification_confirmed"])

    def test_rich_get_all_and_idempotency(self):
        instances = []
        def factory(token):
            inst = MockCanvas(token)
            instances.append(inst)
            return inst

        with contextlib.redirect_stdout(io.StringIO()):
            first = sync_rich(self.root, all_active=True, client_factory=factory)
            second = sync_rich(self.root, all_active=True, client_factory=factory)
        self.assertTrue(first[0]["changed"])
        self.assertFalse(second[0]["changed"])
        p = self.root / "procesamiento" / "canvas-sync" / "v01" / "82862"
        self.assertEqual(len(list((p / "snapshots").glob("*.json"))), 1)
        record = json.loads((p / "latest.json").read_text(encoding="utf-8"))
        tasks = record["data"]["assignments"]
        self.assertEqual(tasks[0]["rubric"][0]["points"], 20)
        self.assertEqual(tasks[0]["submission"]["submitted_at"], "2026-09-26T12:05:00-05:00")
        self.assertNotIn("EVIL", json.dumps(record))
        self.assertNotIn("signed.example", json.dumps(record))  # No ephemeral download URLs.
        self.assertEqual(record["data"]["sections"]["page_details"]["data"][0]["status"], "ok")
        self.assertEqual(record["data"]["sections"]["files"]["data"][0]["display_name"], "Guía.pdf")
        self.assertTrue(all(s.startswith("GET") for ins in instances for s in ins.actions))
        self.assertFalse((self.root / "database.xlsx").exists())
        self.assertFalse((self.root / "academic-manager.sqlite").exists())
        user = json.loads((self.root / "procesamiento" / "canvas-sync" / "v01" / "user" / "latest.json").read_text(encoding="utf-8"))
        self.assertNotIn("other_private_field", json.dumps(user))
        self.assertEqual(user["data"]["sections"]["enrollments"]["data"][0]["grades"]["current_score"], 84.5)

    def test_explicit_selected_courses_are_verified(self):
        with contextlib.redirect_stdout(io.StringIO()):
            result = sync_rich(self.root, course_ids=[82862],
                               client_factory=MockCanvas)
        self.assertEqual([x["course_id"] for x in result], [82862])
        with self.assertRaises(SyncError):
            sync_rich(self.root, course_ids=[990001], client_factory=MockCanvas)
        with self.assertRaises(SyncError):
            sync_rich(self.root, course_ids=[82862, 82862],
                      client_factory=MockCanvas)

    def test_optional_403_recorded_not_interpreted_empty(self):
        class Denied(MockCanvas):
            def pages(self, path, params=None):
                if path.endswith("/files"):
                    raise SyncError("Sin permisos para esta consulta (HTTP 403).")
                return super().pages(path, params)
        rich = build_course(Denied("fake-secret"), 82862)
        self.assertEqual(rich["sections"]["files"]["status"], "forbidden")
        self.assertIsNone(rich["sections"]["files"]["data"])

    def test_unexpected_failure_does_not_write_partial(self):
        class Failure(MockCanvas):
            def pages(self, path, params=None):
                if path.endswith("/modules"):
                    raise SyncError("Canvas limitó temporalmente las consultas (HTTP 429).")
                return super().pages(path, params)
        with self.assertRaises(SyncError), contextlib.redirect_stdout(io.StringIO()):
            sync_rich(self.root, all_active=True, client_factory=Failure)
        self.assertFalse((self.root / "procesamiento").exists())

    def test_pages_require_unique_slugs_not_integer_ids(self):
        with self.assertRaises(SyncError):
            normalize("pages", [{"url": "a"}, {"url": "a"}])
        result = normalize("pages", [{"url": "a", "title": "A"}])
        self.assertEqual(result[0]["url"], "a")

    def test_quiz_questions_are_not_requested(self):
        client = MockCanvas("fake-secret")
        build_course(client, 82862)
        # Only the student's own submissions list; never quiz questions or quiz submissions.
        self.assertTrue(all("/questions" not in a and "/quizzes/" not in a
                            and ("/submissions" not in a or a.endswith("/students/submissions"))
                            for a in client.actions))
        self.assertTrue(all(not a.startswith("POST") for a in client.actions))

    def test_teacher_comments_and_rubric_are_kept_without_names(self):
        client = MockCanvas("fake-secret")
        rich = build_course(client, 82862)
        section = rich["sections"]["submissions"]
        self.assertEqual(section["status"], "ok")
        self.assertIn("GET /api/v1/courses/82862/students/submissions", client.actions)
        rows = section["data"]
        self.assertEqual([r["assignment_id"] for r in rows], [391671])  # without feedback → not kept
        comments = rows[0]["comments"]
        self.assertEqual([c["text"] for c in comments], ["Buen trabajo, revisa la normalización.", "Gracias"])
        self.assertEqual([c["from_me"] for c in comments], [False, True])
        self.assertEqual(comments[0]["created_at"], "2026-10-01T10:00:00Z")
        self.assertEqual(rows[0]["rubric"], {"criterion-1": {"points": 18, "rating_id": "r",
                                                              "comments": "Falta un ejemplo"}})
        dumped = json.dumps(rich)
        for private in ("Profesora Ana", "avatar.example", "Estudiante Uno"):
            self.assertNotIn(private, dumped)
        # Feedback lives in a section: assignment rows (review signatures) are unchanged.
        self.assertEqual(rich["assignments"], normalize("assignments", client.assignments(82862)))

    def test_submissions_forbidden_is_not_a_failure(self):
        class Denied(MockCanvas):
            def pages(self, path, params=None):
                if path.endswith("/students/submissions"):
                    raise SyncError("Sin permisos para esta consulta (HTTP 403).")
                return super().pages(path, params)
        rich = build_course(Denied("fake-secret"), 82862)
        self.assertEqual(rich["sections"]["submissions"], {"status": "forbidden", "data": None})

    def test_long_comment_is_truncated(self):
        rows = normalize("submissions", [{"id": 1, "assignment_id": 2, "user_id": 3,
                                          "submission_comments": [{"author_id": 4, "comment": "x" * 9000}]}])
        self.assertEqual(len(rows[0]["comments"][0]["text"]), 4000)

    def test_unread_messages_are_only_counted_per_course(self):
        client = MockCanvas("fake-secret")
        user = build_user(client, [82862, 82863], today=date(2026, 11, 2))
        unread = user["sections"]["unread_messages"]
        self.assertEqual(unread, {"status": "ok", "data": {"by_course": {"82862": 2, "82863": 0}, "other": 1}})
        self.assertIn("GET /api/v1/conversations", client.actions)
        self.assertTrue(all(not a.startswith("GET /api/v1/conversations/") for a in client.actions))
        dumped = json.dumps(user)
        for private in ("Asunto privado", "Cuerpo privado", "Docente X"):
            self.assertNotIn(private, dumped)

    def test_unread_messages_unavailable_is_not_a_failure(self):
        class Denied(MockCanvas):
            def pages(self, path, params=None):
                if path.endswith("/conversations"):
                    raise SyncError("Canvas respondió HTTP 404.")
                return super().pages(path, params)
        user = build_user(Denied("fake-secret"), [82862], today=date(2026, 11, 2))
        self.assertEqual(user["sections"]["unread_messages"], {"status": "unavailable", "data": None})


if __name__ == "__main__":
    unittest.main()
