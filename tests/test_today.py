"""`academic_access today`: lista determinista de qué hacer primero (S5a)."""
import io
import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from academic_access.__main__ import main
from academic_store import records, store
from capture.storage.metadata import sha256
from tests.test_academic_store import workbook
from tests.xlsx_fixture import write_sheets

TODAY = "2027-03-10"


def serial(text):
    """Serial de Excel en hora local de Bogotá ('YYYY-MM-DD HH:MM')."""
    local = datetime.strptime(text, "%Y-%m-%d %H:%M")
    return (local - datetime(1899, 12, 30)).total_seconds() / 86400.0


def activity(aid, subject, due, *, close=None, delivery=None, grade=None):
    return [aid, subject, "Título " + aid, serial(due) if due else None,
            serial(close) if close else None, delivery, grade]


SHEETS = workbook(**{
    "01 - Materias": [["materia_id", "semestre", "nombre", "curso_externo_id", "bloques"],
                      ["MAT-A", "2027-1", "A", "1", "1,2"],
                      ["MAT-B", "2027-1", "B", "2", "2"],
                      ["MAT-C", "2027-1", "C", "3", "2"],
                      ["MAT-D", "2027-1", "D", "4", "2"],
                      ["MAT-E", "2027-1", "E", "5", "2"],
                      ["MAT-OLD", "2026-2", "Antigua", "6", "1"]],
    "02 - Actividades": [
        ["actividad_id", "materia_id", "titulo", "fecha_entrega", "fecha_cierre",
         "estado_entrega", "estado_calificacion"],
        activity("ACT-VENC", "MAT-A", "2027-03-08 23:59"),
        activity("ACT-VENC-CERR", "MAT-A", "2027-03-05 23:59", close="2027-03-09 23:59"),
        activity("ACT-VENC-ABIERTA", "MAT-A", "2027-03-09 23:59", close="2027-03-10 08:00"),
        activity("ACT-P1B", "MAT-A", "2027-03-10 10:00"),
        activity("ACT-P1", "MAT-A", "2027-03-12 23:59"),
        activity("ACT-P2", "MAT-A", "2027-03-17 23:59"),
        activity("ACT-P3", "MAT-A", "2027-03-24 23:59"),
        activity("ACT-LEJOS", "MAT-A", "2027-03-25 00:30"),
        activity("ACT-SEG", "MAT-C", "2027-03-11 23:59"),
        activity("ACT-SEGCAL", "MAT-C", "2027-03-08 23:59"),
        activity("ACT-SEGPEND", "MAT-C", "2027-03-12 08:00", delivery="pendiente"),
        activity("ACT-CANV-E", "MAT-A", "2027-03-11 23:59", delivery="entregada"),
        activity("ACT-CANV-C", "MAT-A", "2027-03-08 23:59", grade="calificada"),
        activity("ACT-NOFECHA", "MAT-A", None)],
    "03 - Clases": [["clase_id", "materia_id", "fecha", "procesada", "titulo"],
                    ["CLS-A-1", "MAT-A", serial("2027-03-09 18:00"), False, "Clase 1"],
                    ["CLS-A-2", "MAT-A", serial("2027-02-10 18:00"), False, "Clase 2"],
                    ["CLS-A-3", "MAT-A", serial("2027-03-05 18:00"), True, "Clase 3"],
                    ["CLS-A-4", "MAT-A", serial("2027-02-17 18:00"), False, "Clase 4"]],
    "05 - Semestres": [["semestre_id", "nombre", "estado", "bloque_activo", None],
                       ["2027-1", "Semestre 1 - 2027", "Activo", 2, None],
                       ["2026-2", "Semestre 2 - 2026", "Cerrado", 1, None]],
})


def task(tid, state, deadline, subject="MAT-A"):
    return {"tabla": "tareas", "accion": "insertar", "registro": {
        "tarea_id": tid, "titulo": "Tarea " + tid, "materia_id": subject,
        "fecha_limite": deadline, "detalle": None, "estado": state}}


class TodayTests(unittest.TestCase):
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
            task("TAR-VENC", "pendiente", "2027-03-01"),
            task("TAR-P1", "en_progreso", "2027-03-11", subject=None),
            task("TAR-P3", "pendiente", "2027-03-20"),
            task("TAR-HECHA", "hecha", "2027-03-09"),
            task("TAR-LEJOS", "pendiente", "2027-04-30"),
            task("TAR-SINF", "pendiente", None),
            task("TAR-SINF-DESC", "descartada", None),
            {"tabla": "seguimiento", "accion": "guardar", "registro": {
                "actividad_id": "ACT-SEG", "estado": "entregada"}},
            {"tabla": "seguimiento", "accion": "guardar", "registro": {
                "actividad_id": "ACT-SEGCAL", "estado": "calificada"}},
            {"tabla": "seguimiento", "accion": "guardar", "registro": {
                "actividad_id": "ACT-SEGPEND", "estado": "en_progreso"}},
            {"tabla": "notas", "accion": "insertar", "registro": {
                "nota_id": "NOTA-B", "materia_id": "MAT-B", "tipo": "nota", "texto": "x"}},
            {"tabla": "notas", "accion": "insertar", "registro": {
                "nota_id": "NOTA-D", "materia_id": "MAT-D", "tipo": "nota", "texto": "y"}},
        ])
        conn = sqlite3.connect(self.db)
        with conn:
            conn.execute("UPDATE notas SET creado_utc = '2027-03-01T15:00:00Z' WHERE nota_id = 'NOTA-B'")
            # 03:00 UTC del 24 = 22:00 del 23 en Bogotá: fuera de la ventana de 14 días.
            conn.execute("UPDATE notas SET creado_utc = '2027-02-24T03:00:00Z' WHERE nota_id = 'NOTA-D'")
            conn.execute("UPDATE seguimiento SET actualizado_utc = '2027-01-10T12:00:00Z'")
            conn.execute("UPDATE seguimiento SET actualizado_utc = '2027-03-02T12:00:00Z' "
                         "WHERE actividad_id = 'ACT-SEGPEND'")
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

    def today(self, *args):
        code, body = self.read("today", "--fecha", TODAY, *args)
        self.assertEqual(code, 0, body)
        self.assertEqual((body["ok"], body["action"]), (True, "today"))
        return body["data"]

    def ids(self, data, kind=None, priority=None):
        return [self.key(i) for i in data["items"]
                if (kind is None or i["tipo"] == kind)
                and (priority is None or i["prioridad"] == priority)]

    @staticmethod
    def key(item):
        return item.get("actividad_id") or item.get("tarea_id") or item.get("clase_id") or item["materia_id"]

    def failure(self, *args):
        code, body = self.read(*args)
        self.assertEqual(code, 2)
        self.assertFalse(body["ok"])
        return body

    def test_overdue_activity_is_priority_zero_unless_closed(self):
        data = self.today()
        self.assertEqual(self.ids(data, "actividad_vencida", 0), ["ACT-VENC", "ACT-VENC-ABIERTA"])
        self.assertNotIn("ACT-VENC-CERR", self.ids(data))
        item = next(i for i in data["items"] if i.get("actividad_id") == "ACT-VENC")
        self.assertEqual(item, {"tipo": "actividad_vencida", "prioridad": 0,
                                "motivo": item["motivo"], "fecha": "2027-03-08",
                                "materia_id": "MAT-A", "actividad_id": "ACT-VENC",
                                "titulo": "Título ACT-VENC"})
        self.assertIsInstance(item["motivo"], str)
        self.assertTrue(item["motivo"])

    def test_upcoming_activity_windows_two_seven_and_n_days(self):
        data = self.today()
        self.assertEqual(self.ids(data, "actividad_proxima", 1), ["ACT-P1B", "ACT-P1", "ACT-SEGPEND"])
        self.assertEqual(self.ids(data, "actividad_proxima", 2), ["ACT-P2"])
        self.assertEqual(self.ids(data, "actividad_proxima", 3), ["ACT-P3"])
        self.assertNotIn("ACT-LEJOS", self.ids(data))
        self.assertNotIn("ACT-NOFECHA", self.ids(data))

    def test_done_by_tracking_or_canvas_values_is_excluded(self):
        found = self.ids(self.today())
        for done in ("ACT-SEG", "ACT-SEGCAL", "ACT-CANV-E", "ACT-CANV-C"):
            self.assertNotIn(done, found)
        self.assertIn("ACT-SEGPEND", found)

    def test_personal_tasks_overdue_and_windows(self):
        data = self.today()
        self.assertEqual(self.ids(data, "tarea", 0), ["TAR-VENC"])
        self.assertEqual(self.ids(data, "tarea", 1), ["TAR-P1"])
        self.assertEqual(self.ids(data, "tarea", 3), ["TAR-P3"])
        for excluded in ("TAR-HECHA", "TAR-LEJOS", "TAR-SINF-DESC"):
            self.assertNotIn(excluded, self.ids(data))
        item = next(i for i in data["items"] if i.get("tarea_id") == "TAR-P1")
        self.assertEqual({k: item[k] for k in ("fecha", "materia_id", "titulo")},
                         {"fecha": "2027-03-11", "materia_id": None, "titulo": "Tarea TAR-P1"})

    def test_unprocessed_classes_of_the_last_21_days(self):
        data = self.today()
        self.assertEqual(self.ids(data, "clase_sin_procesar", 4), ["CLS-A-4", "CLS-A-1"])
        item = next(i for i in data["items"] if i.get("clase_id") == "CLS-A-1")
        self.assertEqual((item["fecha"], item["materia_id"]), ("2027-03-09", "MAT-A"))

    def test_neglected_active_subjects(self):
        data = self.today()
        self.assertEqual(self.ids(data, "materia_descuidada", 5), ["MAT-D", "MAT-E"])
        [d, e] = [i for i in data["items"] if i["tipo"] == "materia_descuidada"]
        self.assertEqual((d["fecha"], e["fecha"]), ("2027-02-23", None))

    def test_only_the_active_semester_counts(self):
        activities = store.column_map(self.db, "02 - Actividades")
        classes = store.column_map(self.db, "03 - Clases")
        store.apply_cells(self.db, [
            ("02 - Actividades", 40, activities["actividad_id"], None, "ACT-OLD"),
            ("02 - Actividades", 40, activities["materia_id"], None, "MAT-OLD"),
            ("02 - Actividades", 40, activities["fecha_entrega"], None, serial("2026-12-01 23:59")),
            ("03 - Clases", 40, classes["clase_id"], None, "CLS-OLD-1"),
            ("03 - Clases", 40, classes["materia_id"], None, "MAT-OLD"),
            ("03 - Clases", 40, classes["fecha"], None, serial("2027-03-08 18:00")),
            ("03 - Clases", 40, classes["procesada"], None, False)])
        records.apply(self.db, [task("TAR-OLD", "pendiente", "2027-03-01", subject="MAT-OLD"),
                                task("TAR-SINMAT", "pendiente", "2027-03-01", subject=None)])
        self.publish_state()
        found = self.ids(self.today())
        for old in ("ACT-OLD", "CLS-OLD-1", "TAR-OLD", "MAT-OLD"):
            self.assertNotIn(old, found)
        self.assertIn("TAR-SINMAT", found)
        self.assertIn("TAR-P1", found)

    def test_tasks_without_deadline(self):
        self.assertEqual(self.ids(self.today(), "tarea_sin_fecha", 6), ["TAR-SINF"])

    def test_global_order_priority_then_date_then_id_and_summary(self):
        data = self.today()
        self.assertEqual(self.ids(data), [
            "TAR-VENC", "ACT-VENC", "ACT-VENC-ABIERTA",
            "ACT-P1B", "TAR-P1", "ACT-P1", "ACT-SEGPEND",
            "ACT-P2",
            "TAR-P3", "ACT-P3",
            "CLS-A-4", "CLS-A-1",
            "MAT-D", "MAT-E",
            "TAR-SINF"])
        self.assertEqual([i["prioridad"] for i in data["items"]],
                         [0, 0, 0, 1, 1, 1, 1, 2, 3, 3, 4, 4, 5, 5, 6])
        self.assertEqual(data["fecha"], TODAY)
        self.assertEqual(data["dias"], 14)
        self.assertEqual(data["resumen"], {"actividad_vencida": 2, "actividad_proxima": 5,
                                           "tarea": 3, "clase_sin_procesar": 2,
                                           "materia_descuidada": 2, "tarea_sin_fecha": 1})
        self.assertEqual(data["workbook_sha256"], sha256(self.db))
        self.assertEqual(self.today(), data)

    def test_dias_limits_the_horizon(self):
        data = self.today("--dias", "7")
        self.assertNotIn("ACT-P3", self.ids(data))
        self.assertNotIn("TAR-P3", self.ids(data))
        self.assertIn("ACT-P2", self.ids(data))
        self.assertNotIn("ACT-P2", self.ids(self.today("--dias", "2")))
        self.assertEqual(self.today("--dias", "60")["dias"], 60)

    def test_default_date_is_today_in_bogota(self):
        code, body = self.read("today")
        self.assertEqual(code, 0, body)
        self.assertEqual(body["data"]["fecha"],
                         datetime.now(ZoneInfo("America/Bogota")).date().isoformat())

    def test_invalid_dias_and_fecha(self):
        for args in (("--dias", "0"), ("--dias", "61"), ("--dias", "x"),
                     ("--fecha", "2027-02-30"), ("--fecha", "10/03/2027"), ("--fecha", "")):
            with self.subTest(args=args):
                body = self.failure("today", *args)
                self.assertEqual(body["error"]["code"], "INVALID_ARGUMENT")

    def test_needs_published_store_not_index(self):
        self.publish_state("0" * 64)
        body = self.failure("today", "--fecha", TODAY)
        self.assertEqual((body["action"], body["error"]["code"]), ("today", "SOURCE_UNAVAILABLE"))

    def test_new_options_are_rejected_on_other_actions(self):
        for args in (("notes", "--dias", "3"), ("tasks", "--fecha", TODAY),
                     ("classes", "--materia", "MAT-A", "--dias", "3")):
            with self.subTest(args=args):
                body = self.failure(*args)
                self.assertEqual((body["action"], body["error"]["code"]), ("unknown", "INVALID_ARGUMENT"))

    def test_capabilities_lists_today_and_keeps_existing_actions(self):
        code, body = self.read("capabilities")
        self.assertEqual(code, 0)
        commands = body["data"]["commands"]
        self.assertIn("today", commands)
        self.assertLessEqual({"health", "subjects", "subject", "activities", "activity", "classes",
                              "search", "source", "capture-jobs", "notes", "tasks", "tracking"},
                             set(commands))


if __name__ == "__main__":
    unittest.main()
