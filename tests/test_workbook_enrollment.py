"""Matrícula canónica: pruebas sintéticas sobre la base SQLite, sin tocar Drive real."""
import json
import shutil
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from academic_store import store
from academic_store.sheetview import open_book
from tests.xlsx_fixture import write_sheets
from canvas_sync.common import SyncError
from canvas_sync.workbook_enrollment import (plan_enrollment, publish_enrollment,
                                             SUBJECT_HEADERS, TERM_HEADERS)
from capture.storage.metadata import sha256
from canvas_sync.workbook_blocks import mutate, validate_store
from tests.test_academic_store import workbook


class EnrollmentWorkbookTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        source = Path(self.tmp.name) / "source.xlsx"
        old = {1:"MAT-ING3", 2:"2026-2", 3:"English III", 4:"ENG3",
               6:"Activa", 12:"Canvas", 13:"91003", 16:"1,2"}
        write_sheets(source, workbook(**{
            "01 - Materias": [list(SUBJECT_HEADERS), [old.get(c) for c in range(1, 17)]],
            "05 - Semestres": [list(TERM_HEADERS),
                               ["2026-2", "Semestre 2 - 2026", None, None, "Activo", None, 2]],
        }))
        self.path = Path(self.tmp.name) / store.DB_NAME
        store.migrate(source, self.path)

        self.existing = {"subject_id":"MAT-ING3", "semester":"2026-2",
                         "course_id":91003, "blocks":[1,2],
                         "provenance":"workbook"}
        self.new = {"subject_id":"MAT-20271-C990001","semester":"2027-1",
                    "course_id":990001, "blocks":[1,2],
                    "canvas_name":"Sistemas Distribuidos",
                    "canvas_code":"SD-2027",
                    "provenance":"aprobado_local_pendiente_excel"}
        self.registry={"schema_version":2,"active_semester":"2027-1",
                       "active_block":1,"courses":[self.existing,self.new]}
        self.catalog=[{"course_id":990001,"name":"Sistemas Distribuidos",
                       "course_code":"SD-2027"}]

    def tearDown(self):
        self.tmp.cleanup()

    def test_base_with_semesters_but_none_active_is_still_refused(self):
        """Regresión: solo una base sin ningún semestre puede recibir el primero."""
        store.apply_cells(self.path, [("05 - Semestres", 2, 5, "Activo", "Inactivo")])
        with self.assertRaisesRegex(SyncError, "exactamente un semestre Activo"):
            plan_enrollment(self.path, self.registry, canvas_courses=self.catalog)

    def test_new_semester_and_cross_block_subject(self):
        validate_store(self.path)
        diff=plan_enrollment(self.path,self.registry,self.catalog)
        self.assertGreater(len(diff),10)
        self.assertIn(("05 - Semestres",2,5,"Activo","Inactivo"),diff)
        self.assertTrue(any(x[0]=="01 - Materias" and x[2]==16 and x[4]=="1,2"
                            for x in diff))
        mutate(self.path,diff)
        validate_store(self.path)
        self.assertEqual(plan_enrollment(self.path,self.registry,self.catalog),[])
        app,book=open_book(self.path)
        try:
            sub=book.Worksheets.Item("01 - Materias")
            semesters=book.Worksheets.Item("05 - Semestres")
            self.assertEqual(sub.Cells.Item(2,1).Value,"MAT-ING3")
            self.assertEqual(sub.Cells.Item(3,1).Value,"MAT-20271-C990001")
            self.assertEqual(sub.Cells.Item(3,3).Value,"Sistemas Distribuidos")
            self.assertEqual(str(sub.Cells.Item(3,13).Value),"990001")
            self.assertEqual(sub.Cells.Item(3,16).Value,"1,2")
            self.assertEqual(semesters.Cells.Item(2,5).Value,"Inactivo")
            self.assertEqual(semesters.Cells.Item(3,5).Value,"Activo")
            self.assertEqual(semesters.Cells.Item(3,7).Value,1)
            self.assertIsNone(semesters.Cells.Item(3,3).Value)
        finally:
            book.Close(False)
            app.Quit()

    def test_same_semester_new_block2_course_no_semester_change(self):
        self.new["semester"]="2026-2"
        self.new["subject_id"]="MAT-20262-C990001"
        self.new["blocks"]=[2]
        self.registry["active_semester"]="2026-2"
        self.registry["active_block"]=2
        diff=plan_enrollment(self.path,self.registry,self.catalog)
        self.assertFalse(any(x[0]=="05 - Semestres" for x in diff))
        mutate(self.path,diff)
        self.assertEqual(plan_enrollment(self.path,self.registry,self.catalog),[])

    def test_no_unapproved_or_unknown_canvas_name(self):
        for kind in ("missing", "renamed", "unapproved", "formula"):
            with self.subTest(kind=kind):
                fresh = {**self.new}
                reg = {**self.registry, "courses":[self.existing,fresh]}
                catalog = self.catalog
                if kind == "missing":
                    catalog=[]
                elif kind=="renamed":
                    catalog=[{"course_id":990001,"name":"Nombre cambiado",
                              "course_code":"SD-2027"}]
                elif kind=="unapproved":
                    fresh["provenance"]="workbook"
                else:
                    fresh["canvas_name"]="=HYPERLINK(1,2)"
                    catalog=[{"course_id":990001,"name":"=HYPERLINK(1,2)",
                              "course_code":"SD-2027"}]
                with self.assertRaises(SyncError):
                    plan_enrollment(self.path,reg,catalog)

    def test_collision_course_id_rejected(self):
        fake={**self.new,"course_id":91003}
        reg={**self.registry,"courses":[self.existing,fake]}
        with self.assertRaises(SyncError):
            plan_enrollment(self.path,reg,self.catalog)

    def test_registry_without_pending_idempotent(self):
        reg={"schema_version":2,"active_semester":"2026-2",
             "active_block":2,"courses":[self.existing]}
        self.assertEqual(plan_enrollment(self.path,reg),[])

    def test_published_with_shared_lock_and_redownload(self):
        root = Path(self.tmp.name) / "runtime"
        (root / "control" / "canvas-sync").mkdir(parents=True)
        (root / "procesamiento").mkdir(parents=True)
        (root / "temporales").mkdir(parents=True)
        shutil.copyfile(self.path, root / store.DB_NAME)
        digest = sha256(root / store.DB_NAME)
        state = {"status": "free", "lock": None, "canonical_drive_file_id": "file",
                 "published": {"sha256": digest}}
        (root / "control" / "database-work-state.json").write_text(json.dumps(state),encoding="utf-8")
        registry = {**self.registry, "workbook_sha256": digest}
        (root / "control" / "canvas-sync" / "course-registry.json").write_text(
            json.dumps(registry), encoding="utf-8")
        shared_book = Path(self.tmp.name) / "shared.sqlite"
        shutil.copyfile(self.path, shared_book)
        shared_state = json.loads(json.dumps(state))
        def read_json(api, id, target):
            return json.loads(json.dumps(shared_state))
        def write_json(api, id, obj, target):
            shared_state.clear()
            shared_state.update(json.loads(json.dumps(obj)))
            return {"id": id}
        def download(api, id, target):
            shutil.copyfile(shared_book, target)
            return {"id": id}
        def upload(api, id, source, mime):
            shutil.copyfile(source, shared_book)
            return {"modifiedTime": "simulado"}
        with patch("canvas_sync.workbook_blocks._state_id", return_value="state"), \
             patch("canvas_sync.workbook_blocks.drive.read_json_file", side_effect=read_json), \
             patch("canvas_sync.workbook_blocks.drive.write_json_file", side_effect=write_json), \
             patch("canvas_sync.workbook_blocks.drive.download", side_effect=download), \
             patch("canvas_sync.workbook_blocks.drive.upload", side_effect=upload):
            outcome = publish_enrollment(root, registry, dry_run=False,
                                         api=object(), catalog=self.catalog)
        self.assertEqual(outcome["mode"], "published")
        self.assertEqual(shared_state["status"], "free")
        self.assertIsNone(shared_state["lock"])
        self.assertEqual(shared_state["published"]["sha256"],sha256(shared_book))
        self.assertEqual(sha256(root/store.DB_NAME),sha256(shared_book))
        self.assertTrue(Path(outcome["checkpoint"]).exists())
        stored = json.loads((root/"control"/"canvas-sync"/"course-registry.json").read_text(encoding="utf-8"))
        self.assertTrue(all(r["provenance"] == "workbook" for r in stored["courses"]))
        self.assertEqual(plan_enrollment(root/store.DB_NAME,stored),[])


if __name__=="__main__":
    unittest.main()
