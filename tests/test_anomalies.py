"""`academic_access anomalies`: inconsistencias de datos, solo lectura (S7b)."""
import hashlib
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from academic_access.__main__ import main
from academic_store import store
from capture.storage.metadata import sha256
from tests.test_academic_store import workbook
from tests.xlsx_fixture import write_sheets

SHEETS = workbook(**{
    "01 - Materias": [["materia_id", "semestre", "nombre", "lms", "curso_externo_id", "bloques"],
                      ["MAT-SER", "2027-1", "SER", "Canvas", "91005", "1,2"],
                      ["MAT-ST", "2027-1", "Sistemas Transaccionales", "Canvas", "82892.0", "2"],
                      ["MAT-ING", "2027-1", "Inglés", "Canvas", "90001", "2"],
                      ["MAT-OLD", "2026-2", "Antigua", "Canvas", "70000", "1"]],
    "02 - Actividades": [["actividad_id", "materia_id", "titulo", "fecha_entrega"],
                         ["ACT-SER-1", "MAT-SER", "Tarea 2", 46350.5],
                         ["ACT-SER-2", "MAT-SER", "Foro sin fecha", None],
                         ["ACT-ST-1", "MAT-ST", "Proyecto", ""],
                         ["ACT-OLD-1", "MAT-OLD", "Antigua sin fecha", None]],
    "05 - Semestres": [["semestre_id", "nombre", "estado", "bloque_activo", None],
                       ["2027-1", "Semestre 1 - 2027", "Activo", 2, None],
                       ["2026-2", "Semestre 2 - 2026", "Cerrado", 1, None]],
})


class AnomaliesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.runtime = Path(self.tmp.name) / "runtime"
        (self.runtime / "control").mkdir(parents=True)
        source = Path(self.tmp.name) / "source.xlsx"
        write_sheets(source, SHEETS)
        self.db = self.runtime / store.DB_NAME
        store.migrate(source, self.db)
        self.publish_state()

    def publish_state(self, digest=None):
        state = {"status": "free", "lock": None, "published": {"sha256": digest or sha256(self.db)}}
        (self.runtime / "control" / "database-work-state.json").write_text(
            json.dumps(state), encoding="utf-8")

    def pdf(self, course, file_id, name, content, text=None):
        """Misma forma que escribe canvas_sync.resources.save_pdf."""
        digest = hashlib.sha256(content).hexdigest()
        base = self.runtime / "procesamiento" / "canvas-sync" / "pdfs" / str(course) / str(file_id)
        base.mkdir(parents=True, exist_ok=True)
        (base / (digest + ".pdf")).write_bytes(content)
        meta = {"schema_version": 1, "source": "canvas_api_file_metadata_and_signed_read",
                "course_id": course, "file_id": file_id, "display_name": name,
                "content_type": "application/pdf", "source_size": len(content),
                "download_bytes": len(content), "sha256": digest,
                "last_checked_utc": "2027-03-01T00:00:00+00:00",
                "extraction_status": "text" if text else "no_text_layer", "pages": 1,
                "pages_read": 1, "text_truncated": False, "text_chars": len(text or ""),
                "relative_document": digest + ".pdf"}
        if text is not None:
            (base / (digest + ".txt")).write_bytes((text + "\n").encode("utf-8"))
            meta["relative_text"] = digest + ".txt"
        (base / "latest.json").write_text(json.dumps(meta), encoding="utf-8")

    def evidence(self):
        self.pdf(91005, 22615456, "Tarea2.pdf", b"%PDF-1 nino", "El Divino Niño")
        self.pdf(91005, 22615502, "Tarea2.pdf", b"%PDF-1 reto", "Planificando el Reto")
        self.pdf(91005, 22615600, "Guia.pdf", b"%PDF-1 guia", "Guía normal")
        self.pdf(82892, 22388616, "Tarea_E3.pdf", b"%PDF-1 e3",
                 "Negocios internacionales - Eje 1\nLorem Ipsum dolor sit amet")
        self.pdf(82892, 22388626, "Tarea_E1.pdf", b"%PDF-1 e1", "Este es un TEXTO DE PRUEBA.")
        self.pdf(82892, 22388632, "Proyecto_E4.pdf", b"%PDF-1 e4", "Consigna real del proyecto")
        # Mismo nombre y mismo contenido: no es una anomalía.
        self.pdf(82892, 22388700, "Copia.pdf", b"%PDF-1 same", "igual")
        self.pdf(82892, 22388701, "Copia.pdf", b"%PDF-1 same", "igual")
        # Semestre cerrado: se excluye aunque tenga anomalías.
        self.pdf(70000, 1, "Tarea2.pdf", b"%PDF-1 a", "lorem ipsum")
        self.pdf(70000, 2, "Tarea2.pdf", b"%PDF-1 b", "otra")

    def read(self, *args):
        out = io.StringIO()
        with patch.dict(os.environ, {"ACADEMIC_MANAGER_DATA": str(self.runtime)}), redirect_stdout(out):
            code = main(list(args))
        return code, out.getvalue()

    def anomalies(self, *args):
        code, raw = self.read("anomalies", *args)
        body = json.loads(raw)
        self.assertEqual(code, 0, body)
        self.assertEqual((body["ok"], body["action"]), (True, "anomalies"))
        return body["data"]

    def failure(self, *args):
        code, raw = self.read(*args)
        body = json.loads(raw)
        self.assertEqual(code, 2)
        self.assertFalse(body["ok"])
        return body

    @staticmethod
    def of(data, kind):
        return [(i["materia_id"], i["ids"]) for i in data["items"] if i["tipo"] == kind]

    def test_duplicate_file_name_with_different_hash(self):
        self.evidence()
        data = self.anomalies()
        self.assertEqual(self.of(data, "archivo_duplicado"),
                         [("MAT-SER", ["22615456", "22615502"])])
        item = next(i for i in data["items"] if i["tipo"] == "archivo_duplicado")
        self.assertEqual(set(item), {"tipo", "materia_id", "ids", "detalle"})
        self.assertIn("Tarea2.pdf", item["detalle"])

    def test_placeholder_text_in_extracted_resource(self):
        self.evidence()
        data = self.anomalies()
        self.assertEqual(self.of(data, "texto_de_prueba"),
                         [("MAT-ST", ["22388616"]), ("MAT-ST", ["22388626"])])
        details = [i["detalle"] for i in data["items"] if i["tipo"] == "texto_de_prueba"]
        self.assertIn("Tarea_E3.pdf", details[0])
        self.assertIn("lorem ipsum", details[0])
        self.assertIn("texto de prueba", details[1])

    def test_activity_without_due_date(self):
        data = self.anomalies()
        self.assertEqual(self.of(data, "actividad_sin_fecha"),
                         [("MAT-SER", ["ACT-SER-2"]), ("MAT-ST", ["ACT-ST-1"])])
        item = next(i for i in data["items"] if i["tipo"] == "actividad_sin_fecha")
        self.assertIn("Foro sin fecha", item["detalle"])

    def test_order_and_summary_are_deterministic(self):
        self.evidence()
        data = self.anomalies()
        self.assertEqual([i["tipo"] for i in data["items"]],
                         ["archivo_duplicado", "texto_de_prueba", "texto_de_prueba",
                          "actividad_sin_fecha", "actividad_sin_fecha"])
        self.assertEqual(data["resumen"], {"archivo_duplicado": 1, "texto_de_prueba": 2,
                                           "actividad_sin_fecha": 2})
        self.assertEqual(data["fuentes"], {"recursos_pdf": "disponible", "actividades": "disponible"})
        self.assertEqual(data["workbook_sha256"], sha256(self.db))
        self.assertEqual(self.read("anomalies"), self.read("anomalies"))

    def test_materia_filter(self):
        self.evidence()
        data = self.anomalies("--materia", "MAT-ST")
        self.assertEqual(data["materia_id"], "MAT-ST")
        self.assertEqual({i["materia_id"] for i in data["items"]}, {"MAT-ST"})
        self.assertEqual([i["tipo"] for i in data["items"]],
                         ["texto_de_prueba", "texto_de_prueba", "actividad_sin_fecha"])
        body = self.failure("anomalies", "--materia", "MAT-NOPE")
        self.assertEqual(body["error"]["code"], "NOT_FOUND")

    def test_inactive_semester_is_excluded(self):
        self.evidence()
        data = self.anomalies()
        self.assertNotIn("MAT-OLD", {i["materia_id"] for i in data["items"]})
        self.assertEqual(self.anomalies("--materia", "MAT-OLD")["items"], [])

    def test_missing_evidence_gives_no_items_and_marks_source_unavailable(self):
        data = self.anomalies()
        self.assertEqual(self.of(data, "archivo_duplicado"), [])
        self.assertEqual(self.of(data, "texto_de_prueba"), [])
        self.assertEqual(data["fuentes"]["recursos_pdf"], "no_disponible")
        self.assertEqual(data["fuentes"]["actividades"], "disponible")

    def test_invalid_evidence_entries_are_skipped(self):
        self.evidence()
        bad = self.runtime / "procesamiento" / "canvas-sync" / "pdfs" / "91005" / "999"
        bad.mkdir()
        (bad / "latest.json").write_text("{no json", encoding="utf-8")
        other = self.runtime / "procesamiento" / "canvas-sync" / "pdfs" / "91005" / "998"
        other.mkdir()
        (other / "latest.json").write_text(json.dumps({
            "source": "canvas_api_file_metadata_and_signed_read", "course_id": 1, "file_id": 998,
            "display_name": "Tarea2.pdf", "sha256": "f" * 64}), encoding="utf-8")
        for name, content in (("997", "[]"), ("996", "null"), ("995", '"texto"')):
            folder = self.runtime / "procesamiento" / "canvas-sync" / "pdfs" / "91005" / name
            folder.mkdir()
            (folder / "latest.json").write_text(content, encoding="utf-8")
        data = self.anomalies()
        self.assertEqual(self.of(data, "archivo_duplicado"),
                         [("MAT-SER", ["22615456", "22615502"])])

    def test_gate_failure(self):
        self.publish_state("0" * 64)
        body = self.failure("anomalies")
        self.assertEqual((body["action"], body["error"]["code"]), ("anomalies", "SOURCE_UNAVAILABLE"))

    def test_output_has_no_local_paths(self):
        self.evidence()
        code, raw = self.read("anomalies")
        self.assertEqual(code, 0)
        self.assertNotIn(self.tmp.name, raw)
        self.assertNotIn(json.dumps(self.tmp.name)[1:-1], raw)
        self.assertNotIn("procesamiento", raw)

    def test_store_and_evidence_bytes_unchanged(self):
        self.evidence()

        def snapshot():
            return {str(p.relative_to(self.runtime)): p.read_bytes()
                    for p in sorted(self.runtime.rglob("*")) if p.is_file()}

        before = snapshot()
        self.anomalies()
        self.anomalies("--materia", "MAT-SER")
        self.assertEqual(snapshot(), before)

    def test_capabilities_lists_anomalies(self):
        code, raw = self.read("capabilities")
        self.assertEqual(code, 0)
        commands = json.loads(raw)["data"]["commands"]
        self.assertIn("anomalies", commands)
        self.assertLessEqual({"today", "summary", "notes", "tasks", "tracking"}, set(commands))

    def test_options_of_other_actions_stay_rejected(self):
        for args in (("anomalies", "--fecha", "2027-03-10"), ("anomalies", "--dias", "3"),
                     ("anomalies", "--estado", "pendiente"), ("anomalies", "--tipo", "nota")):
            with self.subTest(args=args):
                body = self.failure(*args)
                self.assertEqual(body["error"]["code"], "INVALID_ARGUMENT")
        code, raw = self.read("tasks", "--materia", "MAT-SER")
        self.assertEqual(code, 0, raw)


if __name__ == "__main__":
    unittest.main()
