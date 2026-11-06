"""Conector de Claude: herramientas «mapa» y «guardar_mapa» (odd/tasks/mapa-materia.md, S5)."""
import unittest
from pathlib import Path
from unittest.mock import patch

from academic_mcp import tools
from academic_operations.actions import OperationError

RUNTIME = Path("/runtime-que-no-existe")
PLAN = "a" * 64
MAPA = {"temas": [{"id": "SO2", "titulo": "Sistemas Operativos II"}]}


class MapToolRegistryTests(unittest.TestCase):
    def test_read_tool_and_write_tool_are_declared_honestly(self):
        self.assertTrue(tools.TOOLS["mapa"].read_only)
        save = tools.TOOLS["guardar_mapa"]
        self.assertFalse(save.read_only)
        self.assertTrue(save.destructive)
        self.assertEqual(save.schema["required"], ["materia", "mapa"])
        props = save.schema["properties"]
        self.assertEqual(props["mapa"]["type"], "object")
        self.assertEqual(props["aprobar"]["type"], "boolean")
        self.assertEqual(props["plan_id"]["pattern"], "^[0-9a-f]{64}$")
        for words in ("aprobar", "cita", "leer_fuente", "mermaid", "reemplaza"):
            self.assertIn(words, save.description.lower())
        self.assertEqual({name for name, t in tools.TOOLS.items() if t.destructive},
                         {"guardar_mapa", "guardar_indice_clase"})


class SaveMapToolTests(unittest.TestCase):
    def test_without_approval_it_only_previews(self):
        preview = {"plan_id": PLAN, "already_applied": False, "operation": {"x": 1},
                   "detalle": {"temas": 1}, "requires_explicit_approval": True}
        with patch("academic_operations.chat.preview", return_value=preview) as p, \
                patch("academic_operations.chat.apply") as a:
            result = tools.call("guardar_mapa", {"materia": "MAT-SO2", "mapa": MAPA}, RUNTIME)
        p.assert_called_once_with(RUNTIME, "map", {"materia_id": "MAT-SO2", "mapa": MAPA})
        a.assert_not_called()
        self.assertEqual(result["plan_id"], PLAN)
        self.assertEqual(result["detalle"], {"temas": 1})
        self.assertFalse(result["guardado"])
        self.assertFalse(result["ya_guardado"])
        self.assertIn("aprobar", result["siguiente_paso"])
        self.assertNotIn("operation", result)

    def test_with_plan_and_approval_it_saves(self):
        applied = {"plan_id": PLAN, "published": True, "already_applied": False, "warning": None,
                   "operation": {}, "sha256": "x", "index_refreshed": True}
        with patch("academic_operations.chat.apply", return_value=applied) as a:
            result = tools.call("guardar_mapa", {"materia": "MAT-SO2", "mapa": MAPA, "plan_id": PLAN,
                                                 "aprobar": True}, RUNTIME)
        a.assert_called_once_with(RUNTIME, "map", {"materia_id": "MAT-SO2", "mapa": MAPA},
                                  plan_id=PLAN, approved=True)
        self.assertEqual((result["guardado"], result["ya_guardado"]), (True, False))

    def test_approval_rules_and_bad_arguments_never_reach_the_store(self):
        cases = [{"materia": "MAT-SO2", "mapa": MAPA, "aprobar": True},
                 {"materia": "MAT-SO2", "mapa": MAPA, "plan_id": PLAN},
                 {"materia": "MAT-SO2", "mapa": MAPA, "plan_id": PLAN, "aprobar": False},
                 {"materia": "MAT-SO2", "mapa": "no es objeto"},
                 {"materia": "MAT-SO2", "mapa": MAPA, "plan_id": "corto", "aprobar": True},
                 {"materia": "MAT-SO2", "mapa": MAPA, "plan_id": PLAN, "aprobar": "sí"},
                 {"materia": "SO2", "mapa": MAPA}, {"materia": "MAT-SO2"}]
        for arguments in cases:
            with self.subTest(arguments=arguments), patch("academic_operations.chat.preview") as p, \
                    patch("academic_operations.chat.apply") as a:
                with self.assertRaises(tools.ToolError) as caught:
                    tools.call("guardar_mapa", arguments, RUNTIME)
                p.assert_not_called()
                a.assert_not_called()
                self.assertTrue(str(caught.exception).strip())

    def test_operation_errors_become_plain_messages(self):
        def fail(code, message, **extra):
            exc = OperationError(code, message)
            for k, v in extra.items():
                setattr(exc, k, v)
            return exc

        cases = [
            (fail("INVALID_ARGUMENT", "Tema T1: la cita no aparece en la fuente lectura:1."), "Tema T1"),
            (fail("NOT_FOUND", "materia_id no existe en la base."), "materia_id no existe"),
            (fail("PLAN_CHANGED", "La petición o la base cambió."), "vista previa nueva"),
            (fail("PUBLISH_FAILED", "Error en C:\\AcademicManagerData\\x.sqlite"), "no se guardó"),
            (fail("PUBLISH_FAILED", "lock", published="incierto"), "«mapa»"),
            (fail("STORE_BUSY", "Otra escritura está publicando la base; no se escribió nada."),
             "Otra escritura"),
        ]
        for exc, expected in cases:
            with self.subTest(exc.code), patch("academic_operations.chat.preview", side_effect=exc):
                with self.assertRaises(tools.ToolError) as caught:
                    tools.call("guardar_mapa", {"materia": "MAT-SO2", "mapa": MAPA}, RUNTIME)
                self.assertIn(expected, str(caught.exception))
                self.assertNotIn("AcademicManagerData", str(caught.exception))


class ReadMapToolTests(unittest.TestCase):
    def test_returns_the_saved_map(self):
        saved = {"materia_id": "MAT-SO2", "mapa": MAPA, "actualizado_utc": "2026-11-02T10:00:00Z",
                 "workbook_sha256": "x"}
        with patch("academic_access.core.subject_map", return_value=saved) as r:
            result = tools.call("mapa", {"materia": "MAT-SO2"}, RUNTIME)
        r.assert_called_once_with(RUNTIME, "MAT-SO2")
        self.assertEqual(result["mapa"], MAPA)

    def test_without_a_map_it_says_how_to_create_one(self):
        empty = {"materia_id": "MAT-SO2", "mapa": None, "actualizado_utc": None, "workbook_sha256": "x"}
        with patch("academic_access.core.subject_map", return_value=empty):
            result = tools.call("mapa", {"materia": "MAT-SO2"}, RUNTIME)
        self.assertIsNone(result["mapa"])
        self.assertIn("guardar_mapa", result["aviso"])


if __name__ == "__main__":
    unittest.main()
