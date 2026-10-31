"""Escrituras desde el chat (T5): vista previa, aprobación y publicación con Drive simulado."""
import contextlib
import io
import json
import re
import shutil
import sqlite3
import tempfile
import unittest

from canvas_sync.common import SyncError
from pathlib import Path
from unittest.mock import patch

from academic_operations.__main__ import main
from academic_store import records, store
from capture.academic_manager.database import CLASS_HEADERS
from capture.storage.metadata import sha256
from tests.test_academic_store import workbook
from tests.xlsx_fixture import write_sheets

ISO_UTC = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")


def class_row(**values):
    return [values.get(h) for h in CLASS_HEADERS]


class ChatFixture(unittest.TestCase):
    """Base local y Drive simulados para las escrituras desde el chat."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        source = base / "source.xlsx"
        write_sheets(source, workbook(**{"03 - Clases": [
            list(CLASS_HEADERS),
            class_row(clase_id="CLS-A-20271015-01", materia_id="MAT-A", fecha=46675.0,
                      titulo="Clase grabada", estado_sesion="realizada", procesada=True,
                      fuente="capture")]}))
        built = base / "built.sqlite"
        store.migrate(source, built)
        self.runtime = base / "runtime"
        (self.runtime / "control" / "canvas-sync").mkdir(parents=True)
        self.db = self.runtime / store.DB_NAME
        shutil.copyfile(built, self.db)
        self.shared = base / "shared.sqlite"
        shutil.copyfile(built, self.shared)
        self._install_state()
        self.uploads = 0

    def _install_state(self):
        digest = sha256(self.db)
        state = {"status": "free", "lock": None, "canonical_drive_file_id": "file",
                 "published": {"sha256": digest}}
        (self.runtime / "control" / "database-work-state.json").write_text(
            json.dumps(state), encoding="utf-8")
        self.shared_state = json.loads(json.dumps(state))
        registry = {"schema_version": 2, "active_semester": "2027-1", "active_block": 2,
                    "workbook_sha256": digest, "courses": [
                        {"subject_id": "MAT-A", "semester": "2027-1", "course_id": 9201,
                         "blocks": [1, 2], "provenance": "workbook"}]}
        (self.runtime / "control" / "canvas-sync" / "course-registry.json").write_text(
            json.dumps(registry), encoding="utf-8")

    @contextlib.contextmanager
    def drive(self, index=None):
        def read_json(api, id, target):
            return json.loads(json.dumps(self.shared_state))

        def write_json(api, id, obj, target):
            self.shared_state.clear()
            self.shared_state.update(json.loads(json.dumps(obj)))
            return {"id": id}

        def download(api, id, target):
            shutil.copyfile(self.shared, target)
            return {"id": id}

        def upload(api, id, source, mime):
            self.uploads += 1
            shutil.copyfile(source, self.shared)
            return {"modifiedTime": "simulado"}

        with patch("canvas_sync.workbook_blocks._drive_service", return_value=object()), \
             patch("canvas_sync.workbook_blocks._state_id", return_value="state"), \
             patch("canvas_sync.workbook_blocks.drive.read_json_file", side_effect=read_json), \
             patch("canvas_sync.workbook_blocks.drive.write_json_file", side_effect=write_json), \
             patch("canvas_sync.workbook_blocks.drive.download", side_effect=download), \
             patch("canvas_sync.workbook_blocks.drive.upload", side_effect=upload), \
             patch("academic_knowledge.index.build", side_effect=index) as build:
            yield build

    def run_cli(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main([*args, "--runtime", str(self.runtime)])
        return code, json.loads(out.getvalue())

    def approve(self, *args):
        code, body = self.run_cli(*args)
        self.assertEqual(code, 0, body)
        plan_id = body["data"]["plan_id"]
        with self.drive() as build:
            result = self.run_cli(*args, "--plan-id", plan_id, "--approve")
        return plan_id, result, build


class ChatWritesTests(ChatFixture):
    def test_preview_returns_plan_id_and_does_not_write(self):
        before = sha256(self.db)
        with self.drive():
            code, body = self.run_cli("note", "--materia", "MAT-A", "--tipo", "decision",
                                      "--texto", "El parcial se mueve al jueves.")
        self.assertEqual(code, 0)
        self.assertTrue(body["ok"])
        self.assertEqual((body["schema_version"], body["action"]), (1, "note"))
        data = body["data"]
        self.assertRegex(data["plan_id"], r"^[0-9a-f]{64}$")
        self.assertTrue(data["requires_explicit_approval"])
        self.assertFalse(data["already_applied"])
        nota = data["operation"]["notas"][0]
        self.assertRegex(nota["nota_id"], r"^NOTA-[0-9a-f]{16}$")
        self.assertEqual((nota["materia_id"], nota["tipo"], nota["texto"]),
                         ("MAT-A", "decision", "El parcial se mueve al jueves."))
        self.assertEqual(sha256(self.db), before)
        self.assertEqual(self.uploads, 0)
        again = self.run_cli("note", "--materia", "MAT-A", "--tipo", "decision",
                             "--texto", "El parcial se mueve al jueves.")[1]
        self.assertEqual(again["data"]["plan_id"], data["plan_id"])

    def test_note_approved_is_published_with_revision_bump_and_index(self):
        revision = store.revision(self.db)
        plan_id, (code, body), build = self.approve(
            "note", "--materia", "MAT-A", "--tipo", "aclaracion", "--texto", "Vale el 30 %.")
        self.assertEqual(code, 0, body)
        data = body["data"]
        self.assertEqual((data["plan_id"], data["published"], data["already_applied"]),
                         (plan_id, True, False))
        self.assertTrue(data["index_refreshed"])
        self.assertIsNone(data["warning"])
        build.assert_called_once()
        self.assertEqual(self.uploads, 1)
        self.assertEqual(sha256(self.db), sha256(self.shared))
        self.assertEqual(self.shared_state["published"]["sha256"], sha256(self.shared))
        self.assertEqual((self.shared_state["status"], self.shared_state["lock"]), ("free", None))
        self.assertEqual(store.revision(self.db), revision + 1)
        [row] = records.read(self.db, "notas")
        self.assertEqual({k: row[k] for k in ("materia_id", "tipo", "texto", "clase_id")},
                         {"materia_id": "MAT-A", "tipo": "aclaracion", "texto": "Vale el 30 %.",
                          "clase_id": None})
        self.assertRegex(row["creado_utc"], ISO_UTC)

    def test_retry_with_same_plan_after_success_is_already_applied(self):
        args = ("task", "--titulo", "Estudiar tema 4", "--materia", "MAT-B",
                "--fecha-limite", "2027-03-01")
        plan_id, (code, _), _ = self.approve(*args)
        self.assertEqual(code, 0)
        revision = store.revision(self.db)
        with self.drive():
            code, body = self.run_cli(*args, "--plan-id", plan_id, "--approve")
        self.assertEqual(code, 0, body)
        self.assertEqual((body["data"]["already_applied"], body["data"]["published"]),
                         (True, False))
        self.assertEqual(self.uploads, 1)
        self.assertEqual(store.revision(self.db), revision)
        self.assertEqual(len(records.read(self.db, "tareas")), 1)
        self.assertTrue(self.run_cli(*args)[1]["data"]["already_applied"])

    def test_wrong_plan_id_or_missing_approval_never_writes(self):
        args = ("note", "--materia", "MAT-A", "--tipo", "nota", "--texto", "Recordar.")
        before = sha256(self.db)
        cases = (("--plan-id", "0" * 64, "--approve", "PLAN_CHANGED"),
                 ("--plan-id", "0" * 64, "APPROVAL_REQUIRED"),
                 ("--approve", "INVALID_ARGUMENT"),
                 ("--plan-id", "abc", "--approve", "INVALID_ARGUMENT"))
        for *extra, expected in cases:
            with self.subTest(extra=extra), self.drive():
                code, body = self.run_cli(*args, *extra)
                self.assertEqual(code, 2)
                self.assertFalse(body["ok"])
                self.assertEqual(body["error"]["code"], expected)
        self.assertEqual(sha256(self.db), before)
        self.assertEqual(self.uploads, 0)
        self.assertEqual(records.read(self.db, "notas"), [])

    def test_track_upserts_one_row_and_keeps_activities_untouched(self):
        activities = store.read_catalog(self.db, ("02 - Actividades",))
        self.assertEqual(self.approve("track", "--actividad", "ACT-C9201-A1", "--estado",
                                      "entregada", "--comentario", "Subido a Canvas")[1][0], 0)
        _, (code, body), _ = self.approve("track", "--actividad", "ACT-C9201-A1",
                                          "--estado", "calificada", "--nota", "4.5",
                                          "--puntos", "90")
        self.assertEqual(code, 0, body)
        [row] = records.read(self.db, "seguimiento")
        self.assertEqual({k: row[k] for k in ("actividad_id", "estado", "nota", "puntos", "comentario")},
                         {"actividad_id": "ACT-C9201-A1", "estado": "calificada", "nota": 4.5,
                          "puntos": 90.0, "comentario": "Subido a Canvas"})
        self.assertRegex(row["actualizado_utc"], ISO_UTC)
        self.assertEqual(store.read_catalog(self.db, ("02 - Actividades",)), activities)

    def test_task_then_task_status(self):
        _, (code, body), _ = self.approve("task", "--titulo", "Preparar parcial",
                                          "--detalle", "Capítulos 1 a 3")
        self.assertEqual(code, 0, body)
        [task] = records.read(self.db, "tareas")
        self.assertRegex(task["tarea_id"], r"^TAR-[0-9a-f]{16}$")
        self.assertEqual((task["titulo"], task["materia_id"], task["fecha_limite"], task["detalle"],
                          task["estado"]),
                         ("Preparar parcial", None, None, "Capítulos 1 a 3", "pendiente"))
        _, (code, body), _ = self.approve("task-status", "--tarea", task["tarea_id"],
                                          "--estado", "hecha")
        self.assertEqual(code, 0, body)
        [done] = records.read(self.db, "tareas")
        self.assertEqual(done["estado"], "hecha")
        self.assertRegex(done["actualizado_utc"], ISO_UTC)
        self.assertEqual(done["creado_utc"], task["creado_utc"])

    def test_class_adds_v2_row_and_linked_summary_once(self):
        args = ("class", "--materia", "MAT-A", "--fecha", "2027-10-15", "--titulo",
                "Repaso sin grabación", "--resumen", "Vimos árboles B.")
        plan_id, (code, body), _ = self.approve(*args)
        self.assertEqual(code, 0, body)
        classes = store.read_catalog(self.db, ("03 - Clases",))["03 - Clases"]
        self.assertEqual(len(classes), 2)
        new = classes[1]
        self.assertEqual(new["clase_id"], "CLS-A-20271015-02")
        self.assertEqual((new["materia_id"], new["fecha"], new["titulo"], new["estado_sesion"],
                          new["fuente"], new["procesada"]),
                         ("MAT-A", 46675.0, "Repaso sin grabación", "realizada", "chat", False))
        [summary] = records.read(self.db, "notas")
        self.assertEqual((summary["tipo"], summary["clase_id"], summary["texto"], summary["materia_id"]),
                         ("resumen_clase", "CLS-A-20271015-02", "Vimos árboles B.", "MAT-A"))
        with self.drive():
            code, body = self.run_cli(*args, "--plan-id", plan_id, "--approve")
        self.assertTrue(body["data"]["already_applied"])
        self.assertEqual(len(store.read_catalog(self.db, ("03 - Clases",))["03 - Clases"]), 2)
        self.assertEqual(len(records.read(self.db, "notas")), 1)

    def test_published_base_changed_since_preview_is_plan_changed_without_lock(self):
        args = ("class", "--materia", "MAT-A", "--fecha", "2027-10-15", "--titulo", "Otra")
        plan_id = self.run_cli(*args)[1]["data"]["plan_id"]
        columns = store.column_map(self.shared, "03 - Clases")
        store.apply_cells(self.shared, [("03 - Clases", 3, columns["clase_id"], None,
                                         "CLS-A-20271015-02"),
                                        ("03 - Clases", 3, columns["materia_id"], None, "MAT-A")])
        self.shared_state["published"]["sha256"] = sha256(self.shared)
        registry = self.runtime / "control" / "canvas-sync" / "course-registry.json"
        data = json.loads(registry.read_text(encoding="utf-8"))
        data.pop("workbook_sha256")
        registry.write_text(json.dumps(data), encoding="utf-8")
        with self.drive():
            code, body = self.run_cli(*args, "--plan-id", plan_id, "--approve")
        self.assertEqual(code, 2)
        self.assertEqual(body["error"]["code"], "PLAN_CHANGED")
        self.assertEqual((self.shared_state["status"], self.shared_state["lock"]), ("free", None))
        self.assertEqual(self.uploads, 0)

    def test_invalid_requests_are_rejected_without_writing(self):
        before = sha256(self.db)
        cases = (
            (("track", "--actividad", "ACT-C9201-A1", "--estado", "terminada"), "INVALID_ARGUMENT"),
            (("track", "--actividad", "ACT-NOPE", "--estado", "entregada"), "NOT_FOUND"),
            (("track", "--actividad", "ACT-C9201-A1", "--estado", "entregada", "--nota", "nan"),
             "INVALID_ARGUMENT"),
            (("track", "--actividad", "ACT-C9201-A1", "--estado", "entregada", "--texto", "x"),
             "INVALID_ARGUMENT"),
            (("note", "--materia", "MAT-Z", "--tipo", "nota", "--texto", "x"), "NOT_FOUND"),
            (("note", "--materia", "MAT-A", "--tipo", "otro", "--texto", "x"), "INVALID_ARGUMENT"),
            (("note", "--materia", "MAT-A", "--tipo", "nota", "--texto", "=HYPERLINK(1)"),
             "INVALID_ARGUMENT"),
            (("note", "--materia", "MAT-A", "--tipo", "nota", "--texto", "x" * 4001),
             "INVALID_ARGUMENT"),
            (("note", "--materia", "MAT-A", "--tipo", "nota"), "INVALID_ARGUMENT"),
            (("task", "--titulo", "Leer", "--fecha-limite", "2027-02-30"), "INVALID_ARGUMENT"),
            (("task", "--titulo", "Leer", "--fecha-limite", "1/3/2027"), "INVALID_ARGUMENT"),
            (("task-status", "--tarea", "TAR-0000000000000000", "--estado", "hecha"), "NOT_FOUND"),
            (("task-status", "--tarea", "TAR-1", "--estado", "cerrada"), "INVALID_ARGUMENT"),
            (("class", "--materia", "MAT-A", "--fecha", "2027-10-15"), "INVALID_ARGUMENT"),
            (("class", "--materia", "MAT-A", "--fecha", "2027-10-15", "--titulo", "x",
              "--job", "abc"), "INVALID_ARGUMENT"),
            (("note", "--materia", "MAT-A", "--tipo", "nota", "--texto", "x", "--bogus"),
             "INVALID_ARGUMENT"),
        )
        for args, expected in cases:
            with self.subTest(args=args[:3]):
                code, body = self.run_cli(*args)
                self.assertEqual(code, 2)
                self.assertFalse(body["ok"])
                self.assertEqual(body["action"], args[0])
                self.assertEqual(body["error"]["code"], expected)
        self.assertEqual(sha256(self.db), before)

    def test_index_failure_is_a_warning_not_a_failed_write(self):
        code, body = self.run_cli("note", "--materia", "MAT-A", "--tipo", "nota", "--texto", "x")
        with self.drive(index=RuntimeError("sin índice")):
            code, body = self.run_cli("note", "--materia", "MAT-A", "--tipo", "nota", "--texto", "x",
                                      "--plan-id", body["data"]["plan_id"], "--approve")
        self.assertEqual(code, 0, body)
        self.assertTrue(body["data"]["published"])
        self.assertFalse(body["data"]["index_refreshed"])
        self.assertTrue(body["data"]["warning"])
        self.assertEqual(len(records.read(self.db, "notas")), 1)

    def test_older_store_without_record_tables_gets_them_on_first_write(self):
        for path in (self.db, self.shared):
            conn = sqlite3.connect(path)
            with conn:
                for table in store.RECORD_TABLES:
                    conn.execute(f"DROP TABLE {table}")
            conn.close()
        self._install_state()
        self.shared_state["published"]["sha256"] = sha256(self.shared)
        store.validate(self.db)
        self.assertEqual(records.read(self.db, "notas"), [])
        _, (code, body), _ = self.approve("note", "--materia", "MAT-A", "--tipo", "nota",
                                          "--texto", "Primera nota")
        self.assertEqual(code, 0, body)
        self.assertEqual(len(records.read(self.db, "notas")), 1)

    def test_migrate_creates_record_tables(self):
        with store._reading(self.db) as conn:
            tables = {t for (t,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue(set(store.RECORD_TABLES) <= tables)


    def test_errors_sent_to_the_chat_do_not_reveal_local_paths(self):
        (self.runtime / "control" / "database-work-state.json").write_text(
            json.dumps({"status": "free", "lock": None, "published": {"sha256": "otro"}}), encoding="utf-8")
        code, body = self.run_cli("note", "--materia", "MAT-A", "--tipo", "nota", "--texto", "x")
        self.assertEqual((code, body["error"]["code"]), (2, "STORE_UNAVAILABLE"))
        self.assertNotIn(str(self.runtime), json.dumps(body))
        from academic_operations.chat import _public_message
        self.assertEqual(_public_message(SyncError(r"No existe C:\AcademicManagerData\x.sqlite ni /home/u/a.db")),
                         "No existe <ruta> ni <ruta>")
        self.assertEqual(_public_message(SyncError("Entrega 5/10 y/o Eje 2")), "Entrega 5/10 y/o Eje 2")

    def test_index_rebuild_failure_reports_its_cause_without_paths(self):
        args = ("note", "--materia", "MAT-A", "--tipo", "nota", "--texto", "índice")
        plan_id = self.run_cli(*args)[1]["data"]["plan_id"]
        with self.drive(index=OSError(f"sin acceso a {self.runtime}/indice")):
            code, body = self.run_cli(*args, "--plan-id", plan_id, "--approve")
        self.assertEqual(code, 0, body)
        data = body["data"]
        self.assertTrue(data["published"])
        self.assertFalse(data["index_refreshed"])
        self.assertIn("sin acceso a <ruta>", data["warning"])
        self.assertNotIn(str(self.runtime), data["warning"])


if __name__ == "__main__":
    unittest.main()
