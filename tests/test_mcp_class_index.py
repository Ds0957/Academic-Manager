"""Conector de Claude: «indice_clase» y «guardar_indice_clase» (odd/tasks/indice-clase.md, S5)."""
import unittest
from pathlib import Path
from unittest.mock import patch

from academic_mcp import tools
from academic_mcp.tools import ToolError
from academic_operations.actions import OperationError

RUNTIME = Path("/runtime-que-no-existe")
PLAN = "a" * 64
CLASS = "CLS-SER-20261029-01"
INDICE = {"resumen": "Actividad del eje 3.", "temas": []}
ARGS = {"clase": CLASS, "indice": INDICE}
REQUEST = {"clase_id": CLASS, "indice": INDICE}


class RegistryTests(unittest.TestCase):
    def test_read_tool_and_write_tool_are_declared_honestly(self):
        self.assertTrue(tools.TOOLS["indice_clase"].read_only)
        save = tools.TOOLS["guardar_indice_clase"]
        self.assertFalse(save.read_only)
        self.assertTrue(save.destructive)
        self.assertEqual(save.schema["required"], ["clase", "indice"])
        for words in ("aprobar", "cita", "leer_fuente", "reemplaza", "transcripcion", "instrucciones", "inicio"):
            self.assertIn(words, save.description.lower())

    def test_class_id_must_look_like_a_class(self):
        with self.assertRaises(ToolError):
            tools.call("indice_clase", {"clase": "../secreto"}, RUNTIME)


class SaveTests(unittest.TestCase):
    def test_without_approval_it_only_previews(self):
        preview = {"plan_id": PLAN, "already_applied": False, "operation": {"x": 1},
                   "detalle": {"temas": 2}, "requires_explicit_approval": True}
        with patch("academic_operations.chat.preview", return_value=preview) as p, \
                patch("academic_operations.chat.apply") as a:
            result = tools.call("guardar_indice_clase", ARGS, RUNTIME)
        p.assert_called_once_with(RUNTIME, "class-index", REQUEST)
        a.assert_not_called()
        self.assertEqual((result["guardado"], result["plan_id"], result["detalle"]), (False, PLAN, {"temas": 2}))
        self.assertIn("aprobar", result["siguiente_paso"])
        self.assertNotIn("operation", result)

    def test_with_approval_it_saves_with_the_preview_plan(self):
        with patch("academic_operations.chat.apply",
                   return_value={"already_applied": False, "warning": None}) as a:
            result = tools.call("guardar_indice_clase", {**ARGS, "plan_id": PLAN, "aprobar": True}, RUNTIME)
        a.assert_called_once_with(RUNTIME, "class-index", REQUEST, plan_id=PLAN, approved=True)
        self.assertEqual(result, {"guardado": True, "ya_guardado": False, "aviso": None})

    def test_approval_without_plan_does_not_write(self):
        with patch("academic_operations.chat.apply") as a, self.assertRaises(ToolError) as caught:
            tools.call("guardar_indice_clase", {**ARGS, "aprobar": True}, RUNTIME)
        a.assert_not_called()
        self.assertIn("plan_id", str(caught.exception))

    def test_plan_without_approval_does_not_write(self):
        with patch("academic_operations.chat.apply") as a, self.assertRaises(ToolError):
            tools.call("guardar_indice_clase", {**ARGS, "plan_id": PLAN}, RUNTIME)
        a.assert_not_called()

    def test_validation_message_reaches_the_person(self):
        error = OperationError("INVALID_ARGUMENT", "Tema «Examen», 00:04:12: la cita no aparece en ese fragmento.")
        with patch("academic_operations.chat.preview", side_effect=error), self.assertRaises(ToolError) as caught:
            tools.call("guardar_indice_clase", ARGS, RUNTIME)
        self.assertIn("00:04:12", str(caught.exception))

    def test_uncertain_save_points_to_the_read_tool(self):
        error = OperationError("PUBLISH_FAILED", "x")
        error.published = "incierto"
        with patch("academic_operations.chat.apply", side_effect=error), self.assertRaises(ToolError) as caught:
            tools.call("guardar_indice_clase", {**ARGS, "plan_id": PLAN, "aprobar": True}, RUNTIME)
        self.assertIn("el índice", str(caught.exception))
        self.assertIn("«indice_clase»", str(caught.exception))
        self.assertNotIn("mapa", str(caught.exception))


class ReadTests(unittest.TestCase):
    def test_saved_index_is_returned(self):
        saved = {"clase_id": CLASS, "indice": INDICE}
        with patch("academic_access.core.class_index", return_value=saved) as read:
            self.assertEqual(tools.call("indice_clase", {"clase": CLASS}, RUNTIME), saved)
        read.assert_called_once_with(RUNTIME, CLASS)

    def test_missing_index_says_how_to_create_it(self):
        with patch("academic_access.core.class_index", return_value={"clase_id": CLASS, "indice": None}):
            result = tools.call("indice_clase", {"clase": CLASS}, RUNTIME)
        self.assertIn("guardar_indice_clase", result["aviso"])


if __name__ == "__main__":
    unittest.main()
