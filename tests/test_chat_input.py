"""Entrada JSON (`--input`) para las escrituras desde el chat (T5b, S4b)."""
import json
import tempfile
import unittest
from pathlib import Path

from academic_store import records, store
from capture.storage.metadata import sha256
from tests.test_chat_writes import ChatFixture

TRICKY = 'Vale 30 % ^ & "comillas" «guillemets» — ñandú, acción, pingüino.'


class ChatInputTests(ChatFixture):
    def setUp(self):
        super().setUp()
        inputs = tempfile.TemporaryDirectory()
        self.addCleanup(inputs.cleanup)
        self.inputs = Path(inputs.name)
        self.count = 0

    def input_file(self, content):
        self.count += 1
        path = self.inputs / f"peticion-{self.count}.json"
        if isinstance(content, bytes):
            path.write_bytes(content)
        elif isinstance(content, str):
            path.write_text(content, encoding="utf-8")
        else:
            path.write_text(json.dumps(content, ensure_ascii=False), encoding="utf-8")
        return str(path)

    def assert_invalid(self, args, *, leaked=()):
        before = sha256(self.db)
        with self.drive():
            code, body = self.run_cli(*args)
        self.assertEqual(code, 2, body)
        self.assertFalse(body["ok"])
        self.assertEqual(body["action"], args[0])
        self.assertEqual(body["error"]["code"], "INVALID_ARGUMENT")
        text = json.dumps(body, ensure_ascii=False)
        for secret in (str(self.inputs), str(self.runtime), *leaked):
            self.assertNotIn(secret, text)
        self.assertEqual(sha256(self.db), before)
        self.assertEqual(self.uploads, 0)

    def test_each_action_via_input_has_the_same_plan_id_as_the_flags(self):
        _, (code, _), _ = self.approve("task", "--titulo", "Base para task-status")
        self.assertEqual(code, 0)
        [task] = records.read(self.db, "tareas")
        cases = (
            ("track", {"actividad": "ACT-C9201-A1", "estado": "calificada", "nota": 4.5,
                       "puntos": 90, "comentario": TRICKY},
             ("--actividad", "ACT-C9201-A1", "--estado", "calificada", "--nota", "4.5",
              "--puntos", "90", "--comentario", TRICKY)),
            ("note", {"materia": "MAT-A", "tipo": "decision", "texto": TRICKY},
             ("--materia", "MAT-A", "--tipo", "decision", "--texto", TRICKY)),
            ("task", {"titulo": "Estudiar & repasar", "materia": "MAT-A",
                      "fecha_limite": "2027-03-01", "detalle": TRICKY},
             ("--titulo", "Estudiar & repasar", "--materia", "MAT-A", "--fecha-limite",
              "2027-03-01", "--detalle", TRICKY)),
            ("task-status", {"tarea": task["tarea_id"], "estado": "en_progreso"},
             ("--tarea", task["tarea_id"], "--estado", "en_progreso")),
            ("class", {"materia": "MAT-A", "fecha": "2027-10-15", "titulo": "Repaso ^ 2",
                       "resumen": TRICKY},
             ("--materia", "MAT-A", "--fecha", "2027-10-15", "--titulo", "Repaso ^ 2",
              "--resumen", TRICKY)),
        )
        for action, content, flags in cases:
            with self.subTest(action=action):
                code, by_flags = self.run_cli(action, *flags)
                self.assertEqual(code, 0, by_flags)
                code, by_input = self.run_cli(action, "--input", self.input_file(content))
                self.assertEqual(code, 0, by_input)
                self.assertEqual(by_input, by_flags)

    def test_approve_with_input_publishes_and_text_round_trips_exactly(self):
        path = self.input_file({"materia": "MAT-A", "tipo": "aclaracion", "texto": TRICKY})
        plan_id, (code, body), build = self.approve("note", "--input", path)
        self.assertEqual(code, 0, body)
        self.assertEqual((body["data"]["plan_id"], body["data"]["published"]), (plan_id, True))
        build.assert_called_once()
        self.assertEqual(self.uploads, 1)
        self.assertEqual(sha256(self.db), sha256(self.shared))
        [row] = records.read(self.db, "notas")
        self.assertEqual((row["materia_id"], row["tipo"], row["texto"]), ("MAT-A", "aclaracion", TRICKY))
        flags = ("note", "--materia", "MAT-A", "--tipo", "aclaracion", "--texto", TRICKY)
        self.assertEqual(self.run_cli(*flags)[1]["data"]["plan_id"], plan_id)
        self.assertTrue(self.run_cli(*flags)[1]["data"]["already_applied"])

    def test_class_summary_via_input_is_stored_verbatim(self):
        path = self.input_file({"materia": "MAT-A", "fecha": "2027-10-15",
                                "titulo": 'Clase "práctica" & taller', "resumen": TRICKY})
        _, (code, body), _ = self.approve("class", "--input", path)
        self.assertEqual(code, 0, body)
        new = store.read_catalog(self.db, ("03 - Clases",))["03 - Clases"][1]
        self.assertEqual(new["titulo"], 'Clase "práctica" & taller')
        [summary] = records.read(self.db, "notas")
        self.assertEqual(summary["texto"], TRICKY)

    def test_input_mixed_with_content_flags_is_rejected(self):
        path = self.input_file({"materia": "MAT-A", "tipo": "nota", "texto": "x"})
        self.assert_invalid(("note", "--input", path, "--texto", "y"))
        self.assert_invalid(("note", "--input", path, "--materia", "MAT-A"))
        self.assertEqual(records.read(self.db, "notas"), [])

    def test_bad_input_files_are_invalid_argument_without_local_paths(self):
        missing = str(self.inputs / "no-existe.json")
        cases = {
            "clave desconocida": {"materia": "MAT-A", "tipo": "nota", "texto": "x", "autor": "yo"},
            "clave con nombre interno": {"materia_id": "MAT-A", "tipo": "nota", "texto": "x"},
            "lista": ["materia", "MAT-A"],
            "texto": "\"hola\"",
            "null": "null",
            "json roto": '{"materia": "MAT-A",',
            "utf-8 inválido": b'{"materia": "MAT-A", "tipo": "nota", "texto": "\xff"}',
            "NaN": '{"materia": "MAT-A", "tipo": "nota", "texto": "x", "nota": NaN}',
            "mayor de 64 KB": b'{"materia": "MAT-A", "tipo": "nota", "texto": "x"' + b" " * (64 * 1024) + b"}",
            "texto no string": {"materia": "MAT-A", "tipo": "nota", "texto": 5},
        }
        for name, content in cases.items():
            with self.subTest(name):
                self.assert_invalid(("note", "--input", self.input_file(content)))
        with self.subTest("archivo inexistente"):
            self.assert_invalid(("note", "--input", missing), leaked=("no-existe",))
        with self.subTest("directorio"):
            self.assert_invalid(("note", "--input", str(self.inputs)))
        self.assertEqual(records.read(self.db, "notas"), [])

    def test_field_not_valid_for_the_action_is_rejected(self):
        self.assert_invalid(("track", "--input", self.input_file(
            {"actividad": "ACT-C9201-A1", "estado": "entregada", "texto": "x"})))

    def test_input_with_bom_and_exactly_64_kb_is_accepted(self):
        content = json.dumps({"materia": "MAT-A", "tipo": "nota", "texto": "x"}).encode("utf-8")
        code, body = self.run_cli("note", "--input", self.input_file(b"\xef\xbb\xbf" + content))
        self.assertEqual(code, 0, body)
        padded = content[:-1] + b" " * (64 * 1024 - len(content)) + b"}"
        self.assertEqual(len(padded), 64 * 1024)
        self.assertEqual(self.run_cli("note", "--input", self.input_file(padded))[0], 0)

    def test_flags_without_input_keep_working(self):
        code, body = self.run_cli("note", "--materia", "MAT-A", "--tipo", "nota", "--texto", "x")
        self.assertEqual(code, 0, body)
        self.assertRegex(body["data"]["plan_id"], r"^[0-9a-f]{64}$")


if __name__ == "__main__":
    unittest.main()
