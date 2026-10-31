"""Almacén SQLite: migración verificable desde XLSX, sin Excel COM ni datos reales."""
import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from academic_store import store
from academic_store.__main__ import main
from academic_store.xlsx import read_sheets
from tests.xlsx_fixture import write_sheets


def workbook(**overrides):
    sheets = {
        "00 - Dashboard": [["Academic Manager — Database V2 mínima"],
                           ["hojas_nucleo", "Materias · Actividades"]],
        "01 - Materias": [["materia_id", "semestre", "nombre", "curso_externo_id", "bloques"],
                          ["MAT-A", "2027-1", "Asignatura A", "9201", "1,2"],
                          ["MAT-B", "2027-1", "Asignatura «B»", "9202", "2"]],
        "02 - Actividades": [["actividad_id", "materia_id", "titulo", "fecha_entrega", "puntos_posibles"],
                             ["ACT-C9201-A1", "MAT-A", "Taller 1", 46350.208321759259, 100],
                             [None, None, None, None, None],
                             ["ACT-C9202-A2", "MAT-B", "Foro & debate", None, 12.5]],
        "03 - Clases": [["clase_id", "materia_id", "fecha", "procesada"],
                        ["CLS-A-20270201-01", "MAT-A", 46419.208333333336, True],
                        ["CLS-A-20270208-01", "MAT-A", 46426.208333333336, False]],
        "04 - Recursos": [["recurso_id", "materia_id", "titulo"]],
        "05 - Semestres": [["semestre_id", "nombre", "estado", "bloque_activo", None],
                           ["2027-1", "Semestre 1 - 2027", "Activo", 2, None]],
        "06 - Entregables": [["entregable_id", "actividad_id", "duracion_min"],
                             ["ENT-1", "ACT-C9201-A1", 1]],
    }
    sheets.update(overrides)
    return sheets


class StoreMigrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.xlsx = self.root / "database.xlsx"
        self.db = self.root / store.DB_NAME

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, **overrides):
        write_sheets(self.xlsx, workbook(**overrides))

    def test_xlsx_reader_returns_value2_shapes(self):
        self.write()
        grid = read_sheets(self.xlsx)["02 - Actividades"]
        self.assertEqual(grid[1], ["ACT-C9201-A1", "MAT-A", "Taller 1", 46350.208321759259, 100.0])
        self.assertIsInstance(grid[1][4], float)
        self.assertEqual(grid[3][3], None)

    def test_migrate_preserves_rows_ids_values_and_order(self):
        self.write()
        report = store.migrate(self.xlsx, self.db)
        self.assertTrue(report["ok"])
        self.assertEqual({k: v["rows"] for k, v in report["sheets"].items()},
                         {"00 - Dashboard": 2, "01 - Materias": 2, "02 - Actividades": 2,
                          "03 - Clases": 2, "04 - Recursos": 0, "05 - Semestres": 1,
                          "06 - Entregables": 1})
        catalog = store.read_catalog(self.db)
        self.assertEqual(list(catalog), list(store.DATA_SHEETS))
        self.assertEqual(catalog["02 - Actividades"], [
            {"actividad_id": "ACT-C9201-A1", "materia_id": "MAT-A", "titulo": "Taller 1",
             "fecha_entrega": 46350.208321759259, "puntos_posibles": 100.0},
            {"actividad_id": "ACT-C9202-A2", "materia_id": "MAT-B", "titulo": "Foro & debate",
             "fecha_entrega": None, "puntos_posibles": 12.5}])
        self.assertEqual([r["materia_id"] for r in catalog["01 - Materias"]], ["MAT-A", "MAT-B"])
        self.assertEqual(catalog["01 - Materias"][1]["nombre"], "Asignatura «B»")
        self.assertEqual(catalog["05 - Semestres"], [
            {"semestre_id": "2027-1", "nombre": "Semestre 1 - 2027", "estado": "Activo",
             "bloque_activo": 2.0}])
        self.assertEqual([r["procesada"] for r in catalog["03 - Clases"]], [True, False])
        self.assertIs(catalog["03 - Clases"][0]["procesada"], True)
        with sqlite3.connect(self.db) as conn:
            declared = conn.execute("SELECT tipo FROM _columnas WHERE tabla = 'recursos'").fetchall()
        conn.close()
        self.assertEqual(declared, [(None,), (None,), (None,)])
        dashboard = store.read_catalog(self.db, ["00 - Dashboard"])["00 - Dashboard"]
        self.assertEqual(dashboard[1], {"col_1": "hojas_nucleo", "col_2": "Materias · Actividades"})
        meta = store.info(self.db)
        self.assertEqual((meta["schema_version"], meta["revision"]), (1, 1))
        self.assertEqual(len(meta["source_sha256"]), 64)
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ["academic-manager.sqlite", "database.xlsx"])

    def test_existing_database_is_never_overwritten(self):
        self.write()
        self.db.write_bytes(b"previous")
        with self.assertRaisesRegex(store.StoreError, "no se sobrescribe"):
            store.migrate(self.xlsx, self.db)
        self.assertEqual(self.db.read_bytes(), b"previous")

    def test_ambiguous_workbooks_fail_closed_without_creating_database(self):
        cases = {
            "duplicado": {"01 - Materias": [["materia_id", "nombre"], ["MAT-A", "A"], ["MAT-A", "B"]]},
            "sin identificador": {"03 - Clases": [["clase_id", "materia_id"], [None, "MAT-A"]]},
            "sin encabezado": {"04 - Recursos": [["recurso_id", None], ["REC-1", "suelto"]]},
            "duplicados": {"04 - Recursos": [["recurso_id", "titulo", "titulo"]]},
            "mezcla booleanos": {"04 - Recursos": [["recurso_id", "procesado"], ["REC-1", True],
                                                   ["REC-2", "no"]]},
            "procesada": {"03 - Clases": [["clase_id", "procesada"], ["CLS-1", "sí"]]},
        }
        for message, override in cases.items():
            with self.subTest(message):
                self.write(**override)
                with self.assertRaisesRegex(store.StoreError, message):
                    store.migrate(self.xlsx, self.db)
                self.assertEqual(sorted(p.name for p in self.root.iterdir()), ["database.xlsx"])
        sheets = workbook()
        del sheets["06 - Entregables"]
        write_sheets(self.xlsx, sheets)
        with self.assertRaisesRegex(store.StoreError, "Faltan hojas"):
            store.migrate(self.xlsx, self.db)
        self.assertFalse(self.db.exists())

    def test_verify_detects_divergence(self):
        self.write()
        store.migrate(self.xlsx, self.db)
        with sqlite3.connect(self.db) as conn:
            conn.execute("UPDATE actividades SET puntos_posibles = '100' WHERE actividad_id = 'ACT-C9201-A1'")
        conn.close()
        report = store.verify(self.xlsx, self.db)
        self.assertFalse(report["ok"])
        self.assertFalse(report["sheets"]["02 - Actividades"]["ok"])
        self.assertIn("puntos_posibles", report["sheets"]["02 - Actividades"]["differences"][0])
        self.assertTrue(report["sheets"]["01 - Materias"]["ok"])

    def test_cli_migrate_and_info(self):
        self.write()
        out = io.StringIO()
        with redirect_stdout(out):
            code = main(["migrate", "--xlsx", str(self.xlsx), "--db", str(self.db)])
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(out.getvalue())["ok"])
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(main(["info", "--db", str(self.db)]), 0)
        self.assertEqual(json.loads(out.getvalue())["rows"]["01 - Materias"], 2)
        err = io.StringIO()
        with redirect_stderr(err):
            self.assertEqual(main(["migrate", "--xlsx", str(self.xlsx), "--db", str(self.db)]), 1)
        self.assertIn("no se sobrescribe", err.getvalue())


if __name__ == "__main__":
    unittest.main()
