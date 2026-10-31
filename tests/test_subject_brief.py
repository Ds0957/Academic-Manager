"""`academic_access subject-brief`: resumen de una sola materia para su Project (issue #16, S1)."""
import io
import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from academic_access.__main__ import main
from academic_store import records, store
from capture.storage.metadata import sha256
from tests.test_today import SHEETS, TODAY, task
from tests.xlsx_fixture import write_sheets


def note(nid, subject, kind, text):
    return {"tabla": "notas", "accion": "insertar", "registro": {
        "nota_id": nid, "materia_id": subject, "tipo": kind, "texto": text}}


STAMPS = {"DEC-1": "2027-03-01T15:00:00Z", "DEC-2": "2027-03-08T15:00:00Z",
          "DEC-OTRA": "2027-03-09T15:00:00Z", "NOTA-C": "2027-03-09T16:00:00Z",
          "DEC-OLD": "2026-11-01T15:00:00Z",
          **{f"RES-{n}": f"2027-03-0{n}T12:00:00Z" for n in range(1, 8)}}


class SubjectBriefTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.runtime = Path(self.tmp.name) / "runtime"
        (self.runtime / "control").mkdir(parents=True)
        source = Path(self.tmp.name) / "source.xlsx"
        write_sheets(source, SHEETS)
        self.db = self.runtime / store.DB_NAME
        store.migrate(source, self.db)
        records.apply(self.db, [
            note("DEC-1", "MAT-C", "decision", "Entregas en PDF."),
            note("DEC-2", "MAT-C", "decision", "y" * 250),
            note("DEC-OTRA", "MAT-A", "decision", "Otra materia."),
            note("NOTA-C", "MAT-C", "nota", "No es decisión."),
            note("DEC-OLD", "MAT-OLD", "decision", "Semestre cerrado."),
            *[note(f"RES-{n}", "MAT-C", "resumen_clase", f"Clase {n}") for n in range(1, 8)],
            task("TAR-C1", "pendiente", "2027-03-20", subject="MAT-C"),
            task("TAR-C2", "en_progreso", None, subject="MAT-C"),
            task("TAR-C3", "hecha", "2027-03-01", subject="MAT-C"),
            task("TAR-A", "pendiente", "2027-03-12"),
            task("TAR-LIBRE", "pendiente", "2027-03-12", subject=None),
            {"tabla": "seguimiento", "accion": "guardar", "registro": {
                "actividad_id": "ACT-SEG", "estado": "entregada", "nota": 4.5}},
            {"tabla": "seguimiento", "accion": "guardar", "registro": {
                "actividad_id": "ACT-SEGPEND", "estado": "en_progreso"}},
            {"tabla": "seguimiento", "accion": "guardar", "registro": {
                "actividad_id": "ACT-P1", "estado": "en_progreso"}},
        ])
        conn = sqlite3.connect(self.db)
        with conn:
            for nid, stamp in STAMPS.items():
                conn.execute("UPDATE notas SET creado_utc = ? WHERE nota_id = ?", (stamp, nid))
        conn.close()
        self.publish_state()

    def publish_state(self, digest=None):
        state = {"status": "free", "lock": None, "published": {"sha256": digest or sha256(self.db)}}
        (self.runtime / "control" / "database-work-state.json").write_text(
            json.dumps(state), encoding="utf-8")

    def read(self, *args):
        out = io.StringIO()
        with patch.dict(os.environ, {"ACADEMIC_MANAGER_DATA": str(self.runtime)}), redirect_stdout(out):
            code = main(list(args))
        return code, json.loads(out.getvalue())

    def brief(self, subject, *args):
        code, body = self.read("subject-brief", "--materia", subject, "--fecha", TODAY, *args)
        self.assertEqual(code, 0, body)
        self.assertEqual((body["ok"], body["schema_version"], body["action"]),
                         (True, 1, "subject-brief"))
        return body["data"]

    def failure(self, *args):
        code, body = self.read(*args)
        self.assertEqual(code, 2)
        self.assertFalse(body["ok"])
        return body

    def test_subject_identity_and_last_class(self):
        data = self.brief("MAT-A")
        self.assertEqual((data["fecha"], data["fechas_zona"]), (TODAY, "America/Bogota"))
        self.assertEqual(data["materia"], {"materia_id": "MAT-A", "nombre": "A",
                                           "semestre_id": "2027-1", "semestre_activo": True,
                                           "ultima_clase": "2027-03-09"})
        self.assertEqual(data["workbook_sha256"], sha256(self.db))

    def test_pending_items_are_the_subject_items_of_today(self):
        data = self.brief("MAT-A")
        code, body = self.read("today", "--fecha", TODAY, "--dias", "60")
        expected = [i for i in body["data"]["items"] if i["materia_id"] == "MAT-A"]
        self.assertEqual(data["pendientes"]["items"], expected)
        self.assertEqual(data["dias"], 60)
        self.assertIn("ACT-LEJOS", [i.get("actividad_id") for i in expected])
        self.assertNotIn("TAR-LIBRE", [i.get("tarea_id") for i in data["pendientes"]["items"]])
        self.assertEqual(data["pendientes"]["resumen"]["actividad_vencida"], 2)
        self.assertEqual(sum(data["pendientes"]["resumen"].values()), len(expected))

    def test_delivery_states_tasks_decisions_and_recent_class_summaries(self):
        data = self.brief("MAT-C")
        self.assertEqual(data["entregas"], [
            {"actividad_id": "ACT-SEG", "estado": "entregada", "nota": 4.5, "puntos": None,
             "comentario": None, "actualizado_utc": data["entregas"][0]["actualizado_utc"],
             "materia_id": "MAT-C", "titulo": "Título ACT-SEG"},
            {"actividad_id": "ACT-SEGPEND", "estado": "en_progreso", "nota": None, "puntos": None,
             "comentario": None, "actualizado_utc": data["entregas"][1]["actualizado_utc"],
             "materia_id": "MAT-C", "titulo": "Título ACT-SEGPEND"}])
        self.assertEqual([t["tarea_id"] for t in data["tareas"]], ["TAR-C1", "TAR-C2"])
        self.assertEqual(set(data["tareas"][0]), {"tarea_id", "titulo", "materia_id", "fecha_limite",
                                                  "detalle", "estado", "creado_utc", "actualizado_utc"})
        self.assertEqual([n["nota_id"] for n in data["decisiones"]], ["DEC-2", "DEC-1"])
        self.assertEqual(data["decisiones"][0]["texto"], "y" * 250)
        self.assertEqual(data["decisiones"][0]["creado_utc"], "2027-03-08T15:00:00Z")
        self.assertEqual([n["nota_id"] for n in data["resumenes_clase"]],
                         ["RES-7", "RES-6", "RES-5", "RES-4", "RES-3"])
        self.assertEqual(data["resumenes_clase"][0]["texto"], "Clase 7")

    def test_decisions_match_the_notes_command(self):
        data = self.brief("MAT-C")
        code, body = self.read("notes", "--materia", "MAT-C", "--tipo", "decision")
        self.assertEqual(data["decisiones"], body["data"]["resultado"]["items"])

    def test_subject_with_nothing_saved_returns_empty_lists(self):
        data = self.brief("MAT-E")
        for key in ("entregas", "tareas", "decisiones", "resumenes_clase"):
            self.assertEqual(data[key], [], key)
        self.assertEqual([i["tipo"] for i in data["pendientes"]["items"]], ["materia_descuidada"])

    def test_closed_semester_subject_has_no_pending_but_keeps_decisions(self):
        data = self.brief("MAT-OLD")
        self.assertEqual((data["materia"]["semestre_id"], data["materia"]["semestre_activo"]),
                         ("2026-2", False))
        self.assertEqual(data["pendientes"]["items"], [])
        self.assertEqual([n["nota_id"] for n in data["decisiones"]], ["DEC-OLD"])

    def test_unknown_subject_is_not_found(self):
        body = self.failure("subject-brief", "--materia", "MAT-NOPE")
        self.assertEqual((body["action"], body["error"]["code"]), ("subject-brief", "NOT_FOUND"))

    def test_materia_is_required_and_other_filters_rejected(self):
        body = self.failure("subject-brief")
        self.assertEqual((body["action"], body["error"]["code"]), ("subject-brief", "INVALID_ARGUMENT"))
        for args in (("--tipo", "decision"), ("--estado", "pendiente"), ("--dias", "7"),
                     ("--fecha", "2027-02-30"), ("--limit", "1"), ("--offset", "1"),
                     ("--query", "x"), ("--actividad", "ACT-1"), ("--fuente", "F"),
                     ("--job", "latest"), ("--length", "10")):
            with self.subTest(args=args):
                body = self.failure("subject-brief", "--materia", "MAT-A", *args)
                self.assertEqual(body["error"]["code"], "INVALID_ARGUMENT")

    def test_gate_fails_when_store_differs_from_published_hash(self):
        self.publish_state("0" * 64)
        body = self.failure("subject-brief", "--materia", "MAT-A")
        self.assertEqual((body["action"], body["error"]["code"]), ("subject-brief", "SOURCE_UNAVAILABLE"))

    def test_capabilities_lists_subject_brief(self):
        code, body = self.read("capabilities")
        self.assertEqual(code, 0)
        self.assertIn("subject-brief", body["data"]["commands"])

    def test_fecha_is_still_rejected_on_reads_without_it(self):
        body = self.failure("notes", "--fecha", TODAY)
        self.assertEqual((body["action"], body["error"]["code"]), ("unknown", "INVALID_ARGUMENT"))

    def test_deterministic(self):
        self.assertEqual(self.brief("MAT-C"), self.brief("MAT-C"))


if __name__ == "__main__":
    unittest.main()
