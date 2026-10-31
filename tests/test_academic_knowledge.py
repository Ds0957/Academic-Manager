"""Etapa 2: índice reconstruible, rutas permitidas, búsqueda y evidencia."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from canvas_sync.common import SyncError
from academic_knowledge import index as k


class KnowledgeTests(unittest.TestCase):
    def test_path_traversal_and_symlink_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp)
            with self.assertRaises(SyncError):
                k.safe_path(base,"..","outside")
            with self.assertRaises(SyncError):
                k.safe_path(base,"../../etc")
            with self.assertRaises(SyncError):
                k.safe_path(base,"C:/Windows")
            with self.assertRaises(SyncError):
                k._drive_source("2026-2\\x.md",base)
            (base/"doc.txt").write_text("Contenido",encoding="utf-8")
            self.assertEqual(k.safe_path(base,"doc.txt"),base/"doc.txt")

    def test_chunks_and_axes(self):
        self.assertEqual(k.eje_of("ACTIVIDAD EVALUATIVA - EJE 4"),4)
        self.assertIsNone(k.eje_of("Semana 2"))
        self.assertEqual(k.eje_of("Eje12"),None)
        parts=list(k.chunk_text("A"*1601,limit=1500))
        self.assertEqual(len(parts),2)
        self.assertEqual("".join(parts),"A"*1601)

    def test_pdf_hash_mismatch_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp)
            pdf=base/"prueba.pdf"
            pdf.write_bytes("PDF sintético".encode("utf-8"))
            text=base/"contenido.txt"
            text.write_text("Fuente comprobable",encoding="utf-8")
            documents={}
            k.add(documents,"fuente","MAT-X","guia","Guía",
                  path=text,pdf=pdf,pdf_sha=k.hash_file(pdf))
            self.assertEqual(documents["fuente"]["estado"],"texto")
            pdf.write_bytes(b"tampered")
            with self.assertRaises(SyncError):
                k.add({},"fuente","MAT-X","guia","Guía",pdf=pdf,
                      pdf_sha=documents["fuente"]["sha256_pdf"])

    def test_build_search_context_and_idempotence_without_real_excel(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/"runtime"
            root.mkdir()
            drive=Path(tmp)/"drive"
            sub=drive/"2027-1"/"Materia A"/"Clases"/"Grabacion"
            sub.mkdir(parents=True)
            source=sub/"analysis.md"
            source.write_text("# La clase\n\nLa investigación técnica utiliza aprendizaje colaborativo.",encoding="utf-8")
            registry={"courses":[{"subject_id":"MAT-X","course_id":990001,
                                  "semester":"2027-1","blocks":[1]}]}
            classes={"01 - Materias":[{"materia_id":"MAT-X","nombre":"Materia A",
                                      "semestre":"2027-1"}],
                     "02 - Actividades":[{"actividad_id":"ACT-X-1","materia_id":"MAT-X",
                                         "titulo":"Actividad evaluativa - Eje 1",
                                         "organizacion":"Eje 1","estado_entrega":"pendiente"}],
                     "03 - Clases":[{"clase_id":"CLS-X-1","materia_id":"MAT-X",
                                     "titulo":"Primera clase",
                                     "analisis":"2027-1/Materia A/Clases/Grabacion/analysis.md"}],
                     "04 - Recursos":[]}
            with (patch.object(k,"load_registry",return_value=registry),
                  patch.object(k,"excel_catalog",return_value=classes),
                  patch.object(k,"workbook_version",return_value="sha-ficticio"),
                  patch.object(k,"study_sources",return_value=None)):
                first=k.build(root,drive_root=drive)
                self.assertTrue(first["changed"])
                self.assertEqual(first["con_texto"],1)
                self.assertEqual(k.overview(root,"MAT-X")["MAT-X"]["actividades"],1)
                results=k.search(root,"investigacion colaborativo",subject="MAT-X")
                self.assertTrue(results)
                self.assertEqual(results[0]["tipo"],"analisis")
                self.assertIn("investigación",source.read_text(encoding="utf-8"))
                detail=k.activity_context(root,"ACT-X-1")
                self.assertEqual(len(detail["analisis_de_clases_de_la_materia"]),1)
                self.assertFalse(k.build(root,drive_root=drive)["changed"])
                source.write_text(source.read_text(encoding="utf-8")+"\n\nNuevo tema de clase.",encoding="utf-8")
                self.assertTrue(k.build(root,drive_root=drive)["changed"])
                self.assertTrue(k.search(root,"Nuevo tema",subject="MAT-X"))
                with self.assertRaises(SyncError):
                    k.search(root,"investigación",subject="MAT-UNKNOWN")

    def material_build(self, tmp, metadata_changes=None, subject_name="Materia A", subject_folder="Materia A",
                       metadata_text=None):
        root=Path(tmp)/"runtime"
        root.mkdir(exist_ok=True)
        drive=Path(tmp)/"drive"
        folder=drive/"2027-1"/subject_folder/"Material"/"Entropía explicada"
        folder.mkdir(parents=True,exist_ok=True)
        (folder/"transcript.md").write_text("# Transcripción\n\nLa entropía mide el desorden termodinámico.",encoding="utf-8")
        (folder/"analysis.md").write_text("Pendiente",encoding="utf-8")
        metadata={"schema_version":1,"job_id":"a"*32,"content_type":"Material","subject_id":"MAT-X",
                  "source":{"kind":"youtube","original_name":"Entropía explicada",
                            "location":"https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
                  "analysis_status":"pending"}
        metadata.update(metadata_changes or {})
        (folder/"metadata.json").write_text(metadata_text or json.dumps(metadata),encoding="utf-8")
        (drive/"2027-1"/subject_folder/"Material"/"notas sueltas.txt").write_text("x",encoding="utf-8")
        registry={"courses":[{"subject_id":"MAT-X","course_id":990001,"semester":"2027-1","blocks":[1]}]}
        catalog={"01 - Materias":[{"materia_id":"MAT-X","nombre":subject_name,"semestre":"2027-1"}],
                 "02 - Actividades":[],"03 - Clases":[],"04 - Recursos":[]}
        with (patch.object(k,"load_registry",return_value=registry),
              patch.object(k,"excel_catalog",return_value=catalog),
              patch.object(k,"workbook_version",return_value="sha-ficticio"),
              patch.object(k,"study_sources",return_value=None)):
            return root,folder,k.build(root,drive_root=drive)

    def test_published_material_is_indexed_and_found_by_search_and_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root,folder,result=self.material_build(tmp)
            self.assertEqual(result["con_texto"],1)
            results=k.search(root,"entropia desorden",subject="MAT-X")
            self.assertEqual([(r["fuente_id"],r["tipo"],r["titulo"]) for r in results],
                             [("material:"+"a"*32,"material","Entropía explicada")])
            self.assertEqual(results[0]["origen"],"https://www.youtube.com/watch?v=dQw4w9WgXcQ")
            self.assertEqual(results[0]["ruta_fuente"],str(folder/"transcript.md"))
            from academic_access import core
            with (patch.object(k,"DRIVE_ROOT",Path(tmp)/"drive"),
                  patch.object(core,"workbook_version",return_value="sha-ficticio")):
                found=core.search(root,"entropía",subject="MAT-X")
                self.assertEqual(found["resultados"][0]["fuente_id"],"material:"+"a"*32)
                text=core.source(root,"material:"+"a"*32,subject="MAT-X")
            self.assertIn("desorden termodinámico",text["fragmento"])

    def test_material_of_another_subject_or_type_fails_closed(self):
        for changes in ({"subject_id":"MAT-Y"},{"content_type":"Clase"}):
            with tempfile.TemporaryDirectory() as tmp:
                with self.assertRaisesRegex(SyncError,"Materia A/Material/Entropía explicada"):
                    self.material_build(tmp,changes)

    def test_corrupt_material_metadata_fails_closed_naming_the_folder(self):
        # p. ej. una sincronización de Drive interrumpida deja metadata.json truncado
        with tempfile.TemporaryDirectory() as tmp:
            for broken in ('{"job_id": "a', '["no es un objeto"]'):
                with self.subTest(broken=broken):
                    with self.assertRaisesRegex(SyncError,"Materia A/Material/Entropía explicada"):
                        self.material_build(tmp,metadata_text=broken)

    def test_material_found_when_subject_name_has_characters_capture_replaces(self):
        # Capture publica en safe_name(materia): «Física: ondas?» queda «Física_ ondas_».
        with tempfile.TemporaryDirectory() as tmp:
            _,_,result=self.material_build(tmp,subject_name="Física: ondas?",
                                           subject_folder="Física_ ondas_")
            self.assertEqual(result["con_texto"],1)

    def test_material_still_being_published_is_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            root,folder,_=self.material_build(tmp)
            (folder/".capture-owner.json").write_text("{}",encoding="utf-8")
            root,folder,result=self.material_build(tmp)
            self.assertEqual(result["con_texto"],0)

    def test_missing_source_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/"missing.txt"
            with self.assertRaises(SyncError):
                k.add({},"f","MAT-X","analisis","Clase",path=path)


if __name__=="__main__":
    unittest.main()
