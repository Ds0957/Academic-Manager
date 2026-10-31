"""Escritura transaccional por celdas y vista compatible con los planificadores V2."""
import sqlite3
import tempfile
import unittest
from pathlib import Path

from academic_store import store
from academic_store.sheetview import open_book
from tests.xlsx_fixture import write_sheets
from tests.test_academic_store import workbook


class StoreWriteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = self.root / store.DB_NAME
        source = self.root / "source.xlsx"
        write_sheets(source, workbook(**{
            "04 - Recursos": [["recurso_id", None, "titulo", "procesado"],
                              ["REC-1", None, "Guía", False]]}))
        store.migrate(source, self.db)

    def tearDown(self):
        self.tmp.cleanup()

    def rows(self, sheet):
        return store.read_catalog(self.db, (sheet,))[sheet]

    def test_view_reproduces_value2_grid_with_gaps_and_excel_columns(self):
        app, book = open_book(self.db)
        try:
            activities = book.Worksheets.Item("02 - Actividades")
            used = activities.UsedRange.Value2
            self.assertEqual(used[0], ("actividad_id", "materia_id", "titulo", "fecha_entrega",
                                       "puntos_posibles"))
            self.assertEqual(used[1], ("ACT-C9201-A1", "MAT-A", "Taller 1", 46350.208321759259, 100.0))
            self.assertEqual(used[2], (None, None, None, None, None))  # fila vacía de la plantilla
            self.assertEqual(used[3][0], "ACT-C9202-A2")
            self.assertEqual(activities.Cells.Item(4, 3).Value, "Foro & debate")
            self.assertIsNone(activities.Cells.Item(50, 1).Value)
            resources = book.Worksheets.Item("04 - Recursos")
            self.assertEqual(resources.UsedRange.Value2, (("recurso_id", None, "titulo", "procesado"),
                                                          ("REC-1", None, "Guía", False)))
            self.assertEqual(resources.Cells.Item(1, 4).Value, "procesado")
        finally:
            book.Close(False)
            app.Quit()

    def test_apply_updates_inserts_and_bumps_revision_in_one_transaction(self):
        store.apply_cells(self.db, [
            ("01 - Materias", 3, 5, "2", "1,2"),
            ("02 - Actividades", 3, 1, None, "ACT-C9201-A3"),
            ("02 - Actividades", 3, 2, None, "MAT-A"),
            ("02 - Actividades", 3, 5, None, 50),
            ("04 - Recursos", 2, 4, False, True),
            ("05 - Semestres", 2, 4, 2, 1),
            ("02 - Actividades", 1, 5, "puntos_posibles", "puntos_posibles"),
        ])
        self.assertEqual(self.rows("01 - Materias")[1]["bloques"], "1,2")
        self.assertEqual([r["actividad_id"] for r in self.rows("02 - Actividades")],
                         ["ACT-C9201-A1", "ACT-C9201-A3", "ACT-C9202-A2"])
        self.assertEqual(self.rows("02 - Actividades")[1]["puntos_posibles"], 50)
        self.assertIs(self.rows("04 - Recursos")[0]["procesado"], True)
        self.assertEqual(self.rows("05 - Semestres")[0]["bloque_activo"], 1)
        self.assertEqual(store.revision(self.db), 2)

    def test_stale_before_value_rejects_the_whole_batch(self):
        with self.assertRaisesRegex(store.StoreError, "cambió desde la previsualización"):
            store.apply_cells(self.db, [
                ("01 - Materias", 2, 5, "1,2", "2"),
                ("01 - Materias", 3, 5, "1", "1,2"),
            ])
        self.assertEqual([r["bloques"] for r in self.rows("01 - Materias")], ["1,2", "2"])
        self.assertEqual(store.revision(self.db), 1)

    def test_invalid_writes_are_rejected_without_changes(self):
        cases = {
            "encabezados": [("01 - Materias", 1, 5, "bloques", "otro")],
            "columna desconocida": [("01 - Materias", 2, 9, None, "x")],
            "booleana": [("04 - Recursos", 2, 4, False, "sí")],
            "sin identificador": [("02 - Actividades", 9, 2, None, "MAT-A")],
            "duplicado": [("02 - Actividades", 9, 1, None, "ACT-C9201-A1")],
            "fórmula": [("01 - Materias", 2, 3, "Asignatura A", "=HYPERLINK(1)")],
        }
        before = (self.db.read_bytes(), store.revision(self.db))
        for message, changes in cases.items():
            with self.subTest(message):
                with self.assertRaisesRegex(store.StoreError, message):
                    store.apply_cells(self.db, changes)
                self.assertEqual((self.db.read_bytes(), store.revision(self.db)), before)

    def test_validate_accepts_store_and_rejects_other_files(self):
        self.assertEqual(store.validate(self.db)["schema_version"], 1)
        other = self.root / "other.sqlite"
        with sqlite3.connect(other) as conn:
            conn.execute("CREATE TABLE x (a)")
        conn.close()
        with self.assertRaisesRegex(store.StoreError, "esquema"):
            store.validate(other)


if __name__ == "__main__":
    unittest.main()
