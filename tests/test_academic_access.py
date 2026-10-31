"""Contrato JSON para Coordinador/materias: pruebas aisladas y no destructivas."""
import hashlib
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from academic_access import core
from academic_access.__main__ import main


class AccessContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.data = {
            "materias": {
                "MAT-A": {"nombre": "Asignatura A", "semestre": "2027-1", "canvas_id": 9201},
                "MAT-B": {"nombre": "Asignatura B", "semestre": "2027-1", "canvas_id": 9202}
            },
            "actividades": [{"actividad_id": "ACT-1", "materia_id": "MAT-A"},
                            {"actividad_id": "ACT-2", "materia_id": "MAT-B"}],
            "fuentes": {}, "relaciones": []
        }
        self.manifest = {"sha256": "123abc", "generado_utc": "2027-01-01T12:00:00Z",
                         "data": {**self.data, "workbook_sha256": "456def"}}
        self.fresh = {"index_sha256": "123abc", "workbook_sha256": "456def",
                      "index_workbook_sha256": "456def", "fresh": True,
                      "generated_at_utc": "2027-01-01T12:00:00Z"}
        self.registry = {"active_semester": "2027-1", "active_block": 2, "courses": [
            {"subject_id": "MAT-A", "course_id": 9201, "blocks": [1, 2]},
            {"subject_id": "MAT-B", "course_id": 9202, "blocks": [2]}
        ]}

    def tearDown(self):
        self.tmp.cleanup()

    def test_capabilities_are_read_only_and_versioned(self):
        cap = core.capabilities()
        self.assertEqual(cap["schema_version"], 1)
        self.assertEqual(cap["access"], "read_only")
        self.assertEqual(set(cap["commands"]), set(
            ("health","subjects","subject","activities","activity","classes","search","source",
             "capture-jobs","notes","tasks","tracking","today","summary","subject-brief",
             "anomalies","system")))
        self.assertNotIn("write", cap["commands"])

    def test_subjects_single_cross_block_not_duplicated(self):
        rows={"01 - Materias": [
                  {"materia_id":"MAT-A","semestre":"2027-1","bloques":"1,2"},
                  {"materia_id":"MAT-B","semestre":"2027-1","bloques":"2"}],
              "05 - Semestres":[{"semestre_id":"2027-1","estado":"Activo",
                                  "bloque_activo":2.0}]}
        with patch.object(core, "_required_index", return_value=(self.manifest, self.fresh)), \
             patch.object(core, "_read_excel", return_value=rows):
            data = core.subjects(self.root)
        self.assertEqual(len(data["materias"]), 2)
        self.assertEqual(data["active_block"], 2)
        self.assertEqual(data["materias"][0]["bloques"], [1,2])
        self.assertTrue(all(m["pertenece_bloque_activo"] for m in data["materias"]))

    def test_activities_are_scoped_null_safe_and_paginated(self):
        catalog = {"02 - Actividades": [
            {"actividad_id": "ACT-1", "materia_id": "MAT-A",
             "titulo": "Evaluación", "fecha_entrega": None,
             "fecha_envio_real": 46300.5, "puntos_obtenidos": 0.0,
             "tipo": "complementaria", "estado_entrega": "pendiente"},
            {"actividad_id": "ACT-2", "materia_id": "MAT-B",
             "titulo": "Trabajo B", "fecha_entrega": 46400.0},
            {"actividad_id": "ACT-3", "materia_id": "MAT-A",
             "titulo": "Trabajo A", "fecha_entrega": 46200.0}
        ]}
        with patch.object(core, "_required_index", return_value=(self.manifest, self.fresh)), \
             patch.object(core, "_read_excel", return_value=catalog):
            data = core.activities(self.root, subject="MAT-A", limit=1, offset=0)
            second = core.activities(self.root, subject="MAT-A", limit=1, offset=1)
            self.assertEqual(data["resultado"]["total"],2)
            self.assertTrue(data["resultado"]["mas_resultados"])
            self.assertIsNone(data["resultado"]["items"][0]["fecha_entrega"])
            self.assertEqual(data["resultado"]["items"][0]["puntos_obtenidos"],0.0)
            self.assertEqual(data["obligatoriedad_complementarias"],"no_verificada")
            self.assertEqual(second["resultado"]["items"][0]["actividad_id"],"ACT-3")
            with self.assertRaises(core.AccessError):
                core.activities(self.root,subject="MAT-B",limit=101)
            with self.assertRaises(core.AccessError):
                core.activities(self.root,subject="MAT-NO")

    def test_source_text_bounded_and_hash_checked(self):
        path = self.root / "document.md"
        path.write_text("Contenido académico verificable de clase.", encoding="utf-8")
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        self.manifest["data"]["fuentes"]["fuente:uno"] = {
            "materia_id": "MAT-A", "titulo": "Clase",
            "tipo": "analisis", "eje": None, "estado": "texto",
            "ruta_texto": str(path), "sha256_texto": sha, "origen": "Excel/clase"
        }
        with patch.object(core, "_required_index", return_value=(self.manifest, self.fresh)):
            first = core.source(self.root,"fuente:uno", offset=0, length=9)
            self.assertEqual(first["fragmento"],"Contenido")
            self.assertTrue(first["hay_mas"])
            with self.assertRaises(core.AccessError):
                core.source(self.root,"fuente:uno",length=7000)
            with self.assertRaises(core.AccessError) as cross:
                core.source(self.root,"fuente:uno",subject="MAT-B")
            self.assertEqual(cross.exception.code,"NOT_FOUND")
            path.write_text("Alterado",encoding="utf-8")
            with self.assertRaises(core.AccessError) as ctx:
                core.source(self.root,"fuente:uno")
            self.assertEqual(ctx.exception.code,"SOURCE_CHANGED")

    def test_no_text_does_not_guess_content(self):
        self.manifest["data"]["fuentes"]["recurso:sinfichero"] = {
            "materia_id": "MAT-B", "titulo": "Recurso",
            "estado": "no_descargado", "ruta_texto": None
        }
        with patch.object(core, "_required_index", return_value=(self.manifest, self.fresh)):
            with self.assertRaises(core.AccessError) as ctx:
                core.source(self.root,"recurso:sinfichero")
            self.assertEqual(ctx.exception.code,"NO_TEXT")

    def test_stale_index_reports_status_but_denies_queries(self):
        with patch.object(core.knowledge, "load", return_value=(self.manifest, None)), \
             patch.object(core, "workbook_version", return_value="NEW"):
            record, meta = core._required_index(self.root,allow_stale=True)
            self.assertFalse(meta["fresh"])
            with self.assertRaises(core.AccessError) as ctx:
                core._required_index(self.root)
            self.assertEqual(ctx.exception.code,"INDEX_STALE")

    def test_health_reports_last_run_without_echoing_private_log(self):
        self.assertEqual(core._sync_last_run(self.root)["status"],"unknown")
        path=self.root/"procesamiento"/"canvas-sync"/"logs"/"latest-status.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"timestamp":"2027-01-04 08:00:00",
                                   "exit_code":2,"message":"TOKEN_SUPER_SECRET"}),encoding="utf-8")
        info=core._sync_last_run(self.root)
        self.assertEqual(info["status"],"error")
        self.assertEqual(info["exit_code"],2)
        self.assertNotIn("TOKEN_SUPER_SECRET",str(info))

    def test_invalid_cli_arguments_are_json_too(self):
        with redirect_stdout(io.StringIO()) as out:
            code = main(["invalid_operation"])
        body = json.loads(out.getvalue())
        self.assertEqual(code,2)
        self.assertEqual(body["error"]["code"],"INVALID_ARGUMENT")

    def test_cli_valid_error_is_structured_json(self):
        with redirect_stdout(io.StringIO()) as output, \
             patch("academic_access.__main__.dispatch",
                   side_effect=core.AccessError("NO_TEXT","Recurso sin texto")):
            exitcode=main(["source","--fuente","foo"])
        self.assertEqual(exitcode,2)
        body=json.loads(output.getvalue())
        self.assertFalse(body["ok"])
        self.assertEqual(body["error"]["code"],"NO_TEXT")
        self.assertEqual(body["schema_version"],1)


if __name__ == "__main__":
    unittest.main()
