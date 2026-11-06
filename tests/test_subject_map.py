"""Mapa de la materia (odd/tasks/mapa-materia.md, S2–S4): validación, citas reales y guardado."""
import copy
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from academic_access import core
from academic_access.core import AccessError
from academic_store import records, store
from capture.storage.metadata import sha256
from tests.test_chat_writes import ChatFixture

SOURCES = {
    ("MAT-A", "lectura:9201:1:1"): "Un proceso es un programa en ejecución.\nCada proceso tiene su\n"
                                   "propio espacio de direcciones y uno o más hilos.",
    ("MAT-A", "clase:CLS-A-20271015-01:transcripcion"): "Hoy vimos planificación round robin con un "
                                                        "quantum fijo.",
}


def fake_source_text(runtime, source_id, *, subject):
    try:
        return SOURCES[(subject, source_id)]
    except KeyError:
        raise AccessError("NOT_FOUND", "Fuente no registrada para esta materia.") from None


def sample_map():
    return {
        "resumen": "Procesos, hilos y planificación.",
        "temas": [
            {"id": "SO2", "titulo": "Sistemas Operativos II"},
            {"id": "T1", "titulo": "Procesos", "padre": "SO2", "eje": 1,
             "explicacion": "Programa en ejecución con su propio espacio de direcciones.",
             "fuentes": [{"fuente_id": "lectura:9201:1:1",
                          "cita": "Un proceso es un programa en ejecución."}]},
            {"id": "T2", "titulo": "Planificación", "padre": "SO2",
             "fuentes": [{"fuente_id": "clase:CLS-A-20271015-01:transcripcion",
                          "cita": "planificación round robin con un quantum fijo"}]},
        ],
        "relaciones": [{"de": "T2", "a": "T1", "texto": "decide qué proceso usa la CPU"}],
        "diagramas": [{"titulo": "Mapa mental", "mermaid": "mindmap\n  root((SO II))\n    Procesos\n    Planificación"}],
    }


class SubjectMapFixture(ChatFixture):
    def setUp(self):
        super().setUp()
        patcher = patch("academic_operations.subject_map.source_text", side_effect=fake_source_text)
        patcher.start()
        self.addCleanup(patcher.stop)

    def write_input(self, content, name="mapa.json"):
        path = Path(self.tmp.name) / name
        path.write_text(json.dumps(content, ensure_ascii=False), encoding="utf-8")
        return str(path)

    def map_args(self, mapa, materia="MAT-A"):
        return ("map", "--input", self.write_input({"materia": materia, "mapa": mapa}))


class SubjectMapWriteTests(SubjectMapFixture):
    def test_preview_describes_the_map_and_writes_nothing(self):
        before = sha256(self.db)
        code, body = self.run_cli(*self.map_args(sample_map()))
        self.assertEqual(code, 0, body)
        data = body["data"]
        self.assertRegex(data["plan_id"], r"^[0-9a-f]{64}$")
        self.assertFalse(data["already_applied"])
        self.assertEqual(data["detalle"], {"materia_id": "MAT-A", "temas": 3, "relaciones": 1,
                                           "diagramas": 1, "reemplaza": False, "temas_anteriores": 0})
        self.assertEqual(sha256(self.db), before)
        self.assertEqual(self.uploads, 0)
        self.assertEqual(self.run_cli(*self.map_args(sample_map()))[1]["data"]["plan_id"], data["plan_id"])

    def test_approved_map_is_published_once_and_retry_is_already_applied(self):
        args = self.map_args(sample_map())
        plan_id, (code, body), build = self.approve(*args)
        self.assertEqual(code, 0, body)
        self.assertEqual((body["data"]["published"], body["data"]["already_applied"]), (True, False))
        build.assert_called_once()
        self.assertEqual(sha256(self.db), sha256(self.shared))
        [row] = records.read(self.db, "mapas")
        self.assertEqual(row["materia_id"], "MAT-A")
        saved = json.loads(row["contenido"])
        self.assertEqual(saved["version"], 1)
        self.assertEqual([t["id"] for t in saved["temas"]], ["SO2", "T1", "T2"])
        self.assertEqual(saved["temas"][0]["fuentes"], [])
        self.assertIsNone(saved["temas"][2]["eje"])
        self.assertEqual(saved["relaciones"], sample_map()["relaciones"])
        with self.drive():
            code, body = self.run_cli(*args, "--plan-id", plan_id, "--approve")
        self.assertEqual(code, 0, body)
        self.assertEqual((body["data"]["already_applied"], body["data"]["published"]), (True, False))
        self.assertEqual(self.uploads, 1)

    def test_saving_again_replaces_the_previous_map(self):
        self.approve(*self.map_args(sample_map()))
        smaller = sample_map()
        smaller["temas"] = smaller["temas"][:2]
        smaller["relaciones"] = []
        code, body = self.run_cli(*self.map_args(smaller))
        self.assertEqual(code, 0, body)
        self.assertEqual((body["data"]["detalle"]["reemplaza"], body["data"]["detalle"]["temas_anteriores"]),
                         (True, 3))
        _, (code, body), _ = self.approve(*self.map_args(smaller))
        self.assertEqual(code, 0, body)
        [row] = records.read(self.db, "mapas")
        self.assertEqual(len(json.loads(row["contenido"])["temas"]), 2)

    def test_invalid_maps_are_rejected_without_writing(self):
        def changed(edit):
            mapa = sample_map()
            edit(mapa)
            return mapa

        many = [{"id": f"X{n}", "titulo": "x"} for n in range(81)]
        cases = {
            "clave desconocida": changed(lambda m: m.update(extra=1)),
            "sin temas": changed(lambda m: m.update(temas=[])),
            "demasiados temas": changed(lambda m: m.update(temas=many)),
            "id repetido": changed(lambda m: m["temas"][2].update(id="T1")),
            "padre inexistente": changed(lambda m: m["temas"][1].update(padre="NOPE")),
            "ciclo": changed(lambda m: m["temas"][0].update(padre="T1")),
            "sin fuentes": changed(lambda m: m["temas"][1].update(fuentes=[])),
            "eje booleano": changed(lambda m: m["temas"][1].update(eje=True)),
            "relación a tema inexistente": changed(lambda m: m["relaciones"][0].update(a="NOPE")),
            "relación consigo mismo": changed(lambda m: m["relaciones"][0].update(a="T2")),
            "mermaid sin tipo": changed(lambda m: m["diagramas"][0].update(mermaid="hola")),
            "directiva mermaid": changed(lambda m: m["diagramas"][0].update(
                mermaid="%%{init: {'securityLevel': 'loose'}}%%\nflowchart TD\n A-->B")),
            "fórmula": changed(lambda m: m["temas"][1].update(titulo="=HYPERLINK(1)")),
        }
        before = sha256(self.db)
        for name, mapa in cases.items():
            with self.subTest(name):
                code, body = self.run_cli(*self.map_args(mapa))
                self.assertEqual(code, 2, body)
                self.assertEqual(body["error"]["code"], "INVALID_ARGUMENT")
        self.assertEqual(sha256(self.db), before)

    def test_citations_must_appear_in_a_source_of_the_subject(self):
        invented = sample_map()
        invented["temas"][1]["fuentes"][0]["cita"] = "Un proceso es una tarea del kernel."
        code, body = self.run_cli(*self.map_args(invented))
        self.assertEqual((code, body["error"]["code"]), (2, "INVALID_ARGUMENT"))
        self.assertIn("T1", body["error"]["message"])
        self.assertIn("no aparece", body["error"]["message"])

        foreign = sample_map()
        foreign["temas"][1]["fuentes"][0]["fuente_id"] = "lectura:9999:1:1"
        code, body = self.run_cli(*self.map_args(foreign))
        self.assertEqual((code, body["error"]["code"]), (2, "INVALID_ARGUMENT"))
        self.assertIn("lectura:9999:1:1", body["error"]["message"])

        spaced = sample_map()
        spaced["temas"][1]["fuentes"][0]["cita"] = "Cada proceso   tiene su propio espacio de direcciones"
        code, body = self.run_cli(*self.map_args(spaced))
        self.assertEqual(code, 0, body)

    def test_unknown_subject_is_not_found(self):
        code, body = self.run_cli(*self.map_args(sample_map(), materia="MAT-Z"))
        self.assertEqual((code, body["error"]["code"]), (2, "NOT_FOUND"))

    def test_map_requires_input_and_accepts_files_over_64_kb(self):
        code, body = self.run_cli("map", "--materia", "MAT-A")
        self.assertEqual((code, body["error"]["code"]), (2, "INVALID_ARGUMENT"))
        big = sample_map()
        big["temas"] += [{"id": f"E{n}", "titulo": f"Tema {n}", "padre": "SO2",
                          "explicacion": "x" * 1400,
                          "fuentes": [{"fuente_id": "lectura:9201:1:1",
                                       "cita": "Un proceso es un programa en ejecución."}]}
                         for n in range(60)]
        args = self.map_args(big)
        self.assertGreater(Path(args[2]).stat().st_size, 64 * 1024)
        code, body = self.run_cli(*args)
        self.assertEqual(code, 0, body)

    def test_note_input_keeps_its_64_kb_limit(self):
        path = self.write_input({"materia": "MAT-A", "tipo": "nota", "texto": "x" * (70 * 1024)}, "nota.json")
        code, body = self.run_cli("note", "--input", path)
        self.assertEqual((code, body["error"]["code"]), (2, "INVALID_ARGUMENT"))
        self.assertIn("64 KB", body["error"]["message"])


class SubjectMapReadTests(SubjectMapFixture):
    def read(self):
        with patch("academic_access.core.workbook_version", return_value="sha"):
            return core.subject_map(self.runtime, "MAT-A")

    def test_no_map_yet(self):
        self.assertEqual(self.read(), {"materia_id": "MAT-A", "mapa": None, "actualizado_utc": None,
                                       "workbook_sha256": "sha"})

    def test_saved_map_is_returned_parsed(self):
        self.approve(*self.map_args(sample_map()))
        result = self.read()
        self.assertEqual([t["titulo"] for t in result["mapa"]["temas"]],
                         ["Sistemas Operativos II", "Procesos", "Planificación"])
        self.assertRegex(result["actualizado_utc"], r"Z$")

    def test_unknown_subject(self):
        with patch("academic_access.core.workbook_version", return_value="sha"), \
                self.assertRaises(AccessError) as caught:
            core.subject_map(self.runtime, "MAT-Z")
        self.assertEqual(caught.exception.code, "NOT_FOUND")


class SourceTextTests(unittest.TestCase):
    def test_source_text_returns_the_whole_verified_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = Path(tmp)
            text = runtime / "fuente.txt"
            text.write_text("a" * 5000 + "fin", encoding="utf-8")
            manifest = {"data": {"fuentes": {"lectura:1:1:1": {
                "materia_id": "MAT-A", "estado": "texto", "ruta_texto": str(text),
                "sha256_texto": sha256(text), "titulo": "t", "tipo": "lectura", "eje": 1, "origen": "o"}}}}
            with patch("academic_access.core._required_index", return_value=(manifest, {})):
                self.assertTrue(core.source_text(runtime, "lectura:1:1:1", subject="MAT-A").endswith("fin"))
                with self.assertRaises(AccessError) as caught:
                    core.source_text(runtime, "lectura:1:1:1", subject="MAT-B")
                self.assertEqual(caught.exception.code, "NOT_FOUND")
                excerpt = core.source(runtime, "lectura:1:1:1", subject="MAT-A", offset=4998, length=10)
                self.assertEqual(excerpt["fragmento"], "aafin")


if __name__ == "__main__":
    unittest.main()
