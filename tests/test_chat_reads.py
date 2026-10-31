"""Lecturas de las tablas de chat (notes, tasks, tracking) por academic_access."""
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
from tests.test_academic_store import workbook
from tests.xlsx_fixture import write_sheets


class ChatReadsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.runtime = Path(self.tmp.name) / "runtime"
        (self.runtime / "control").mkdir(parents=True)
        source = Path(self.tmp.name) / "source.xlsx"
        write_sheets(source, workbook())
        self.db = self.runtime / store.DB_NAME
        store.migrate(source, self.db)
        records.apply(self.db, [
            {"tabla": "notas", "accion": "insertar", "registro": {
                "nota_id": "NOTA-1", "materia_id": "MAT-A", "tipo": "decision", "texto": "Parcial el jueves"}},
            {"tabla": "notas", "accion": "insertar", "registro": {
                "nota_id": "NOTA-2", "materia_id": "MAT-B", "tipo": "nota", "texto": "Traer calculadora"}},
            {"tabla": "notas", "accion": "insertar", "registro": {
                "nota_id": "NOTA-3", "materia_id": "MAT-A", "tipo": "nota", "texto": "Leer capítulo 2"}},
            {"tabla": "tareas", "accion": "insertar", "registro": {
                "tarea_id": "TAR-1", "titulo": "Sin fecha", "materia_id": None, "fecha_limite": None,
                "detalle": None, "estado": "pendiente"}},
            {"tabla": "tareas", "accion": "insertar", "registro": {
                "tarea_id": "TAR-2", "titulo": "Preparar parcial", "materia_id": "MAT-A",
                "fecha_limite": "2027-03-01", "detalle": None, "estado": "pendiente"}},
            {"tabla": "tareas", "accion": "insertar", "registro": {
                "tarea_id": "TAR-3", "titulo": "Hecha", "materia_id": "MAT-A",
                "fecha_limite": "2027-02-01", "detalle": None, "estado": "hecha"}},
            {"tabla": "seguimiento", "accion": "guardar", "registro": {
                "actividad_id": "ACT-C9201-A1", "estado": "calificada", "nota": 4.5, "puntos": 90,
                "comentario": None}},
            {"tabla": "seguimiento", "accion": "guardar", "registro": {
                "actividad_id": "ACT-C9202-A2", "estado": "entregada", "nota": None, "puntos": None,
                "comentario": "Subido"}},
        ])
        self.publish_state()

    def publish_state(self):
        state = {"status": "free", "lock": None, "published": {"sha256": sha256(self.db)}}
        (self.runtime / "control" / "database-work-state.json").write_text(
            json.dumps(state), encoding="utf-8")

    def read(self, *args):
        out = io.StringIO()
        with patch.dict(os.environ, {"ACADEMIC_MANAGER_DATA": str(self.runtime)}), redirect_stdout(out):
            code = main(list(args))
        return code, json.loads(out.getvalue())

    def items(self, *args):
        code, body = self.read(*args)
        self.assertEqual(code, 0, body)
        self.assertTrue(body["ok"])
        self.assertEqual(body["data"]["workbook_sha256"], sha256(self.db))
        return body["data"]["resultado"]

    def test_notes_filtered_by_subject_and_type_and_paginated(self):
        result = self.items("notes", "--materia", "MAT-A")
        self.assertEqual([n["nota_id"] for n in result["items"]], ["NOTA-1", "NOTA-3"])
        self.assertEqual(set(result["items"][0]), {"nota_id", "materia_id", "tipo", "texto",
                                                   "clase_id", "creado_utc"})
        self.assertEqual([n["nota_id"] for n in self.items("notes", "--tipo", "nota")["items"]],
                         ["NOTA-2", "NOTA-3"])
        page = self.items("notes", "--limit", "1", "--offset", "1")
        self.assertEqual((page["total"], page["devueltos"], page["mas_resultados"]), (3, 1, True))
        self.assertEqual(page["items"][0]["nota_id"], "NOTA-2")

    def test_subject_decision_lookup_used_by_specialized_projects(self):
        # La consulta exacta de la plantilla de materia: presente en MAT-A, ausente en MAT-B.
        [decision] = self.items("notes", "--materia", "MAT-A", "--tipo", "decision")["items"]
        self.assertEqual((decision["nota_id"], decision["texto"]), ("NOTA-1", "Parcial el jueves"))
        absent = self.items("notes", "--materia", "MAT-B", "--tipo", "decision")
        self.assertEqual((absent["total"], absent["items"]), (0, []))

    def test_tasks_filtered_by_state_and_ordered_by_deadline(self):
        result = self.items("tasks", "--estado", "pendiente")
        self.assertEqual([t["tarea_id"] for t in result["items"]], ["TAR-2", "TAR-1"])
        self.assertEqual([t["tarea_id"] for t in self.items("tasks", "--materia", "MAT-A")["items"]],
                         ["TAR-3", "TAR-2"])

    def test_tracking_joins_activity_subject_and_title(self):
        [item] = self.items("tracking", "--materia", "MAT-A")["items"]
        self.assertEqual({k: item[k] for k in ("actividad_id", "materia_id", "titulo", "estado",
                                               "nota", "puntos")},
                         {"actividad_id": "ACT-C9201-A1", "materia_id": "MAT-A", "titulo": "Taller 1",
                          "estado": "calificada", "nota": 4.5, "puntos": 90.0})
        self.assertEqual([i["actividad_id"] for i in self.items("tracking", "--estado", "entregada")["items"]],
                         ["ACT-C9202-A2"])

    def test_reads_need_published_store_not_index(self):
        self.assertEqual(self.items("notes")["total"], 3)
        (self.runtime / "control" / "database-work-state.json").write_text(
            json.dumps({"status": "free", "lock": None, "published": {"sha256": "0" * 64}}),
            encoding="utf-8")
        for action in ("notes", "tasks", "tracking"):
            with self.subTest(action=action):
                code, body = self.read(action)
                self.assertEqual(code, 2)
                self.assertEqual(body["error"]["code"], "SOURCE_UNAVAILABLE")

    def test_invalid_filters(self):
        cases = ((("tasks", "--estado", "cerrada"), "INVALID_ARGUMENT"),
                 (("tracking", "--estado", "hecha"), "INVALID_ARGUMENT"),
                 (("notes", "--tipo", "otro"), "INVALID_ARGUMENT"),
                 (("notes", "--materia", "MAT-Z"), "NOT_FOUND"),
                 (("tasks", "--limit", "0"), "INVALID_ARGUMENT"))
        for args, expected in cases:
            with self.subTest(args=args):
                code, body = self.read(*args)
                self.assertEqual(code, 2)
                self.assertEqual((body["action"], body["error"]["code"]), (args[0], expected))

    def test_new_filters_are_rejected_on_other_actions_as_before(self):
        for args in (("classes", "--materia", "MAT-A", "--estado", "x"),
                     ("activities", "--materia", "MAT-A", "--tipo", "x"),
                     ("notes", "--estado", "pendiente"), ("tasks", "--tipo", "nota")):
            with self.subTest(args=args):
                code, body = self.read(*args)
                self.assertEqual(code, 2)
                self.assertEqual((body["action"], body["error"]["code"]), ("unknown", "INVALID_ARGUMENT"))

    def test_store_without_record_tables_reads_empty(self):
        conn = sqlite3.connect(self.db)
        with conn:
            for table in store.RECORD_TABLES:
                conn.execute(f"DROP TABLE {table}")
        conn.close()
        self.publish_state()
        for action in ("notes", "tasks", "tracking"):
            with self.subTest(action=action):
                self.assertEqual(self.items(action)["total"], 0)


if __name__ == "__main__":
    unittest.main()
