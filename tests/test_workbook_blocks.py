"""V2.1 bloque: edición aislada de la base SQLite con datos sintéticos."""
import tempfile
import unittest
from pathlib import Path

from academic_store import store
from academic_store.sheetview import open_book
from tests.xlsx_fixture import write_sheets
from canvas_sync.common import SyncError
from canvas_sync.workbook_blocks import plan, mutate, validate_store
from canvas_sync.workbook_enrollment import SUBJECT_HEADERS, TERM_HEADERS
from tests.test_academic_store import workbook


def blocks_workbook(path):
    subject = ["MAT-20271-C990001", "2027-1", "Materia sintética"] + [None] * 8 + ["Canvas"]
    write_sheets(path, workbook(**{
        "01 - Materias": [list(SUBJECT_HEADERS), subject],
        "05 - Semestres": [list(TERM_HEADERS),
                           ["2027-1", "Semestre 1 de 2027", None, None, "Activo"]],
    }))


class WorkbookBlocksTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        source = Path(self.tmp.name) / "source.xlsx"
        blocks_workbook(source)
        self.path = Path(self.tmp.name) / store.DB_NAME
        store.migrate(source, self.path)
        self.registry = {"schema_version": 2, "active_semester": "2027-1",
                         "active_block": 1, "courses": [
                             {"subject_id": "MAT-20271-C990001", "semester": "2027-1",
                              "course_id": 990001, "blocks": [1, 2]}]}

    def tearDown(self):
        self.tmp.cleanup()

    def test_apply_and_repeat_without_duplication(self):
        validate_store(self.path)
        changes = plan(self.path, self.registry)
        self.assertEqual(sorted((x[0], x[2], x[4]) for x in changes), [
            ("01 - Materias", 13, "990001"), ("01 - Materias", 16, "1,2"),
            ("05 - Semestres", 7, 1)])
        mutate(self.path, changes)
        validate_store(self.path)
        self.assertEqual(plan(self.path, self.registry), [])
        self.assertEqual(self.registry["courses"][0]["blocks"], [1, 2])
        app, book = open_book(self.path)
        try:
            self.assertEqual(str(book.Worksheets.Item("01 - Materias").Cells.Item(2, 16).Value), "1,2")
            self.assertEqual(book.Worksheets.Item("05 - Semestres").Cells.Item(2, 7).Value, 1)
            self.assertEqual(str(book.Worksheets.Item("01 - Materias").Cells.Item(2, 13).Value), "990001")
        finally:
            book.Close(False)
            app.Quit()

    def test_external_course_conflict_rejected(self):
        store.apply_cells(self.path, [("01 - Materias", 2, 13, None, "990002")])
        with self.assertRaises(SyncError):
            plan(self.path, self.registry)

    def test_missing_subject_rejected(self):
        self.registry["courses"][0]["subject_id"] = "MAT-NOT-IN-EXCEL"
        with self.assertRaises(SyncError):
            plan(self.path, self.registry)

    def test_stale_preview_is_rejected_by_the_writer(self):
        changes = plan(self.path, self.registry)
        store.apply_cells(self.path, [("01 - Materias", 2, 16, None, "2")])
        with self.assertRaisesRegex(SyncError, "cambió desde la previsualización"):
            mutate(self.path, changes)


if __name__ == "__main__":
    unittest.main()
