"""Lectores del estado canónico desde SQLite: sin Excel COM y con la misma forma de datos."""
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from academic_knowledge import index
from academic_store import store
from tests.xlsx_fixture import write_sheets
from canvas_sync import reconcile, semester
from canvas_sync.common import SyncError
from canvas_sync.routine import workbook_version
from tests.test_academic_store import workbook

ACTIVITY_HEADERS = ["actividad_id", "materia_id", "titulo", "lms", "objeto_externo_id",
                    "puntos_posibles", "puntos_obtenidos"]


def runtime_with_store(root, **overrides):
    sheets = workbook(**{
        "01 - Materias": [["materia_id", "semestre", "nombre", "lms", "curso_externo_id", "bloques"],
                          ["MAT-A", "2027-1", "Asignatura A", "Canvas", "9201", "1,2"],
                          ["MAT-B", "2027-1", "Asignatura B", "Canvas", 9202.0, "2"],
                          ["MAT-C", "2027-1", "Sin LMS", None, None, None]],
        "02 - Actividades": [ACTIVITY_HEADERS,
                             ["ACT-C9201-A1", "MAT-A", "Taller 1", "Canvas", "1", 100, None]],
        "05 - Semestres": [["semestre_id", "nombre", "estado", "bloque_activo"],
                           ["2026-2", "Semestre 2 - 2026", "Inactivo", None],
                           ["2027-1", "Semestre 1 - 2027", "Activo", 2]],
        **overrides})
    (root / "control").mkdir(exist_ok=True)
    source = root / "temporales" / "source.xlsx"
    source.parent.mkdir(exist_ok=True)
    write_sheets(source, sheets)
    db = root / store.DB_NAME
    store.migrate(source, db)
    digest = hashlib.sha256(db.read_bytes()).hexdigest()
    (root / "control" / "database-work-state.json").write_text(
        json.dumps({"status": "free", "lock": None, "published": {"sha256": digest}}), encoding="utf-8")
    return db, digest


class StoreReaderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_version_is_the_published_hash_of_the_sqlite_store(self):
        _, digest = runtime_with_store(self.root)
        self.assertEqual(workbook_version(self.root), digest)
        state = self.root / "control" / "database-work-state.json"
        state.write_text(json.dumps({"status": "free", "lock": None, "published": {"sha256": "x"}}),
                         encoding="utf-8")
        with self.assertRaisesRegex(SyncError, "difiere del hash publicado"):
            workbook_version(self.root)
        state.write_text(json.dumps({"status": "locked", "lock": {"by": "otro"},
                                     "published": {"sha256": digest}}), encoding="utf-8")
        with self.assertRaisesRegex(SyncError, "no está libre"):
            workbook_version(self.root)

    def test_version_requires_the_store_even_if_a_legacy_workbook_exists(self):
        (self.root / "control").mkdir()
        (self.root / "database.xlsx").write_bytes(b"legacy")
        digest = hashlib.sha256(b"legacy").hexdigest()
        (self.root / "control" / "database-work-state.json").write_text(
            json.dumps({"status": "free", "lock": None, "published": {"sha256": digest}}), encoding="utf-8")
        with self.assertRaisesRegex(SyncError, "No hay base local"):
            workbook_version(self.root)

    def test_catalog_comes_from_sqlite_with_value2_shapes(self):
        runtime_with_store(self.root)
        catalog = index.excel_catalog(self.root)
        self.assertEqual(list(catalog), ["01 - Materias", "02 - Actividades", "03 - Clases",
                                         "04 - Recursos", "05 - Semestres"])
        self.assertEqual(catalog["02 - Actividades"], [
            {"actividad_id": "ACT-C9201-A1", "materia_id": "MAT-A", "titulo": "Taller 1",
             "lms": "Canvas", "objeto_externo_id": "1", "puntos_posibles": 100.0,
             "puntos_obtenidos": None}])
        self.assertEqual(catalog["05 - Semestres"][1]["bloque_activo"], 2.0)

    def test_courses_come_from_sqlite(self):
        runtime_with_store(self.root)
        active, mapped, block = semester.workbook_courses(self.root)
        self.assertEqual((active, block), ("2027-1", 2))
        self.assertEqual(mapped, [
            {"course_id": 9201, "subject_id": "MAT-A", "semester": "2027-1",
             "provenance": "workbook", "blocks": [1, 2]},
            {"course_id": 9202, "subject_id": "MAT-B", "semester": "2027-1",
             "provenance": "workbook", "blocks": [2]}])

    def test_courses_keep_rejecting_two_active_semesters(self):
        runtime_with_store(self.root, **{"05 - Semestres": [
            ["semestre_id", "estado", "bloque_activo"], ["2027-1", "Activo", 1], ["2027-2", "Activo", 2]]})
        with self.assertRaisesRegex(SyncError, "único semestre activo"):
            semester.workbook_courses(self.root)

    def test_downloaded_sqlite_copies_are_read_without_the_runtime_gate(self):
        db, _ = runtime_with_store(self.root)
        copy = self.root / "procesamiento" / "refresh.sqlite"
        copy.parent.mkdir()
        copy.write_bytes(db.read_bytes())
        (self.root / "control" / "database-work-state.json").unlink()
        active, mapped, block = semester.workbook_courses(self.root, workbook_path=copy)
        self.assertEqual((active, block, len(mapped)), ("2027-1", 2, 2))

    def test_downloaded_xlsx_copies_are_rejected(self):
        runtime_with_store(self.root)
        copy = self.root / "temporales" / "source.xlsx"
        with self.assertRaisesRegex(SyncError, "Base SQLite ilegible"):
            semester.workbook_courses(self.root, workbook_path=copy)
        with self.assertRaisesRegex(SyncError, "Base SQLite ilegible"):
            reconcile.read_v2_activities(copy)

    def test_corrupt_store_is_a_handled_error(self):
        db, _ = runtime_with_store(self.root)
        db.write_bytes(b"not a sqlite database at all" * 10)
        digest = hashlib.sha256(db.read_bytes()).hexdigest()
        (self.root / "control" / "database-work-state.json").write_text(
            json.dumps({"status": "free", "lock": None, "published": {"sha256": digest}}), encoding="utf-8")
        for read in (lambda: index.excel_catalog(self.root),
                     lambda: semester.workbook_courses(self.root),
                     lambda: reconcile.read_v2_activities(db)):
            with self.assertRaisesRegex(SyncError, "Base SQLite ilegible"):
                read()
        with self.assertRaisesRegex(store.StoreError, "Base SQLite ilegible"):
            store.info(db)

    def test_reconcile_reads_activities_from_sqlite(self):
        db, _ = runtime_with_store(self.root)
        rows = reconcile.read_v2_activities(db)
        self.assertEqual(rows[0]["actividad_id"], "ACT-C9201-A1")
        self.assertEqual(rows[0]["puntos_posibles"], 100.0)

    def test_reconcile_rejects_activities_without_v2_columns(self):
        db, _ = runtime_with_store(self.root, **{"02 - Actividades": [["actividad_id", "materia_id"],
                                                                       ["ACT-1", "MAT-A"]]})
        with self.assertRaisesRegex(SyncError, "no coinciden con V2"):
            reconcile.read_v2_activities(db)

    def test_preview_defaults_to_the_store(self):
        db, _ = runtime_with_store(self.root)
        seen = {}
        original = reconcile.read_v2_activities
        reconcile.read_v2_activities = lambda path: seen.setdefault("path", Path(path)) and []
        try:
            reconcile.preview(self.root, course_ids=(), mappings={})
        finally:
            reconcile.read_v2_activities = original
        self.assertEqual(seen["path"], db)


if __name__ == "__main__":
    unittest.main()
