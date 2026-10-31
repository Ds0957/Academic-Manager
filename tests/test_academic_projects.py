"""Plantillas de ChatGPT Projects: identidad canónica, límites y falta de efectos."""
import io
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from unittest.mock import patch

from academic_projects.__main__ import render, main


class ProjectInstructionsTests(unittest.TestCase):
    def setUp(self):
        self.rows = {"materias": [
            {"materia_id": "MAT-ING3", "nombre": "Inglés III",
             "semestre_id": "2026-2", "bloques": [1,2]},
            {"materia_id": "MAT-SER", "nombre": "SER (BE-IT)",
             "semestre_id": "2026-2", "bloques": [2]}
        ]}

    def test_coordinator_no_runtime_or_writes(self):
        with patch("academic_projects.__main__.subjects",
                   side_effect=AssertionError("No consultar Excel para plantilla genérica")):
            output=render("coordinador")
        self.assertIn("GitHub Issues",output)
        self.assertIn("Ds0957/Academic-Manager",output)
        self.assertIn("no existe comunicación automática",output.lower())
        self.assertIn("no crear",output.lower())
        self.assertNotIn("{{",output)

    def test_coordinator_starts_with_summary_and_saves_through_chat_writes(self):
        output=render("coordinador")
        for expected in ("academic-read.cmd summary","academic-read.cmd today","anomalies",
                         "academic-act.cmd note","track","task-status","--input",
                         "--plan-id","--approve","batch --input","STORE_BUSY","Una escritura a la vez","`avisos`","academic-read.cmd system","academic-store.cmd unlock"):
            self.assertIn(expected,output)
        # La base ya no es Excel: el Coordinador no debe proponer abrirlo ni escribirlo.
        self.assertNotIn("Excel",output)
        # Límite de instrucciones de un Project de ChatGPT.
        self.assertLess(len(output),8000)

    def test_subject_is_grounded_in_canonical_catalog(self):
        with patch("academic_projects.__main__.subjects",return_value=self.rows):
            text=render("materia",subject_id="MAT-ING3",runtime=Path("ignored"))
        self.assertIn("Inglés III",text)
        self.assertIn("MAT-ING3",text)
        self.assertIn("bloques `1 y 2`",text)
        self.assertIn("2026-2",text)
        self.assertNotIn("MAT-SER",text)
        self.assertNotIn("{{",text)

    def test_subject_instructions_cover_work_and_respect_project_limits(self):
        with patch("academic_projects.__main__.subjects", return_value=self.rows):
            result = render("materia", subject_id="MAT-SER", runtime=Path("ignored"))
        self.assertLess(len(result), 8000)
        for expected in ("actividades y entregables", "rúbrica", "pendientes reales",
                         "MAT-SER", "Coordinador", "sin comprobación",
                         "fuente_id", "actividad_id", "No supongas comunicación automática"):
            self.assertIn(expected, result)
        self.assertNotIn("MAT-ING3", result)
        self.assertNotIn("{{", result)

    def test_subject_uses_capture_material_and_does_not_post_to_github(self):
        # Comentario del Project de GBD en #17: revisó Capture por su cuenta y publicó en GitHub.
        with patch("academic_projects.__main__.subjects", return_value=self.rows):
            result = render("materia", subject_id="MAT-SER", runtime=Path("ignored"))
        for expected in ("YouTube", "Capture como material de estudio de `MAT-SER`",
                         "No revises el código de Capture", "No publiques en GitHub"):
            self.assertIn(expected, result)
        self.assertLess(len(result), 8000)

    def test_subject_applies_recorded_decisions_and_separates_title_from_filename(self):
        # Issue #13: SO2 propuso otra nomenclatura porque no consultaba las decisiones guardadas.
        with patch("academic_projects.__main__.subjects", return_value=self.rows):
            result = render("materia", subject_id="MAT-SER", runtime=Path("ignored"))
        for expected in ("notes --materia MAT-SER --tipo decision", "nota_id", "creado_utc",
                         "no pudiste consultarlas", "como propuesta, no como acordada",
                         "título académico", "nombre del archivo"):
            self.assertIn(expected, result)
        self.assertLess(len(result), 8000)

    def test_subject_starts_each_chat_with_its_subject_brief(self):
        # Issue #16: el Project de materia empieza con `subject-brief` en vez de varias consultas.
        with patch("academic_projects.__main__.subjects", return_value=self.rows):
            result = render("materia", subject_id="MAT-SER", runtime=Path("ignored"))
        self.assertIn("**Al empezar cada chat**, ejecuta `scripts\\academic-read.cmd subject-brief --materia MAT-SER`",
                      result)
        for expected in ("pendientes", "entregas", "decisiones", "resúmenes de clase"):
            self.assertIn(expected, result)
        self.assertNotIn("subject-brief --materia MAT-ING3", result)
        self.assertLess(len(result), 8000)

    def test_third_term_semesters_are_rendered_like_the_registry(self):
        rows = {"materias": [{"materia_id": "MAT-VER", "nombre": "Verano", "semestre_id": "2027-3",
                              "bloques": [1]}]}
        with patch("academic_projects.__main__.subjects", return_value=rows):
            text = render("materia", subject_id="MAT-VER", runtime=Path("ignored"))
        self.assertIn("semestre `2027-3`", text)
        for term in ("2027-4", "2027-0", "27-1"):
            rows["materias"][0]["semestre_id"] = term
            with self.subTest(term=term), self.assertRaises(ValueError), \
                 patch("academic_projects.__main__.subjects", return_value=rows):
                render("materia", subject_id="MAT-VER", runtime=Path("ignored"))

    def test_coordinator_has_no_fixed_subject_names(self):
        output = render("coordinador")
        self.assertNotIn("Inglés III", output)
        self.assertIn("Una materia transversal a ambos bloques aparece una sola vez", output)
        self.assertLess(len(output), 8000)

    def test_coordinator_saves_cross_subject_decisions_in_each_subject(self):
        output = render("coordinador")
        self.assertIn("en cada materia afectada", output)
        self.assertLess(len(output), 8000)

    def test_bad_and_unknown_id_rejected(self):
        with patch("academic_projects.__main__.subjects",return_value=self.rows):
            for value in ("MAT-INVENTADA","MAT-SER; Remove-Item C:","../MAT-SER","MAT-ser"):
                with self.subTest(value=value),self.assertRaises(ValueError):
                    render("materia",subject_id=value,runtime=Path("unused"))
            with self.assertRaises(ValueError):
                render("coordinador",subject_id="MAT-SER")

    def test_no_materia_metadata_available_fails_closed(self):
        with patch("academic_projects.__main__.subjects", return_value={"materias":[]}):
            with self.assertRaises(ValueError):
                render("materia",subject_id="MAT-ING3",runtime=Path("unused"))

    def test_cli_outputs_only_rendered_instructions(self):
        with patch("academic_projects.__main__.subjects",return_value=self.rows):
            with redirect_stdout(io.StringIO()) as output:
                code=main(["materia","--materia","MAT-SER","--runtime","C:\\Fake"])
        self.assertEqual(code,0)
        self.assertTrue(output.getvalue().startswith("# Asistente especializado"))
        self.assertIn("MAT-SER",output.getvalue())
        self.assertNotIn("{{",output.getvalue())

    def test_cli_does_not_pretend_to_create_unrecognized_project(self):
        with patch("academic_projects.__main__.subjects",return_value=self.rows), \
             redirect_stderr(io.StringIO()) as error:
            exitcode=main(["materia","--materia","MAT-NO"])
        self.assertEqual(exitcode,2)
        self.assertIn("NO_GENERADO",error.getvalue())

    def test_cli_coordinator_output_is_unchanged(self):
        with redirect_stdout(io.StringIO()) as output:
            code = main(["coordinador"])
        self.assertEqual(code, 0)
        self.assertEqual(output.getvalue(), render("coordinador"))

    def active_rows(self):
        rows = [{**row, "semestre_activo": True} for row in self.rows["materias"]]
        rows.append({"materia_id": "MAT-OLD", "nombre": "Antigua", "semestre_id": "2026-1",
                     "bloques": [1], "semestre_activo": False})
        return {"materias": rows}

    def run_all(self, *args):
        with patch("academic_projects.__main__.subjects", return_value=self.active_rows()), \
             redirect_stdout(io.StringIO()) as output, redirect_stderr(io.StringIO()) as error:
            code = main(["all", *args])
        return code, output.getvalue(), error.getvalue()

    def test_all_writes_coordinator_and_every_active_subject(self):
        # Issue #16: una sola orden genera los archivos del Coordinador y de las materias activas.
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "salida"
            code, stdout, _ = self.run_all("--out", str(out), "--runtime", "C:\\Fake")
            self.assertEqual(code, 0)
            names = ["coordinador.md", "materia-MAT-ING3.md", "materia-MAT-SER.md"]
            self.assertEqual(sorted(p.name for p in out.iterdir()), names)
            self.assertEqual(stdout.splitlines(), [str(out / name) for name in names])
            self.assertEqual((out / "coordinador.md").read_text(encoding="utf-8"), render("coordinador"))
            with patch("academic_projects.__main__.subjects", return_value=self.rows):
                expected = render("materia", subject_id="MAT-SER", runtime=Path("ignored"))
            self.assertEqual((out / "materia-MAT-SER.md").read_text(encoding="utf-8"), expected)

    def test_all_defaults_to_runtime_temporales(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, stdout, _ = self.run_all("--runtime", tmp)
            self.assertEqual(code, 0)
            folder = Path(tmp) / "temporales" / "instrucciones-projects"
            self.assertEqual(stdout.splitlines()[0], str(folder / "coordinador.md"))
            self.assertEqual(len(list(folder.iterdir())), 3)

    def test_all_fails_closed_without_writing(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "salida"
            rows = self.active_rows()
            rows["materias"][1]["semestre_id"] = "2026-9"
            with patch("academic_projects.__main__.subjects", return_value=rows), \
                 redirect_stdout(io.StringIO()) as output, redirect_stderr(io.StringIO()) as error:
                code = main(["all", "--out", str(out)])
            self.assertEqual((code, output.getvalue()), (2, ""))
            self.assertIn("NO_GENERADO", error.getvalue())
            self.assertFalse(out.exists())

    def test_out_and_materia_only_where_they_apply(self):
        with tempfile.TemporaryDirectory() as tmp:
            for argv in (["all", "--materia", "MAT-SER", "--out", tmp],
                         ["coordinador", "--out", tmp],
                         ["materia", "--materia", "MAT-SER", "--out", tmp]):
                with self.subTest(argv=argv), \
                     patch("academic_projects.__main__.subjects", return_value=self.active_rows()), \
                     redirect_stdout(io.StringIO()) as output, redirect_stderr(io.StringIO()) as error:
                    code = main(argv)
                self.assertEqual((code, output.getvalue()), (2, ""))
                self.assertIn("NO_GENERADO", error.getvalue())
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_issue_template_requires_non_sensitive_evidence(self):
        path=Path(__file__).resolve().parent.parent / ".github" / "ISSUE_TEMPLATE" / "necesidad-o-defecto.md"
        template=path.read_text(encoding="utf-8")
        for item in ("Problema comprobado","Comportamiento actual",
                     "Comportamiento esperado","Reproducción", "Impacto",
                     "Criterio de aceptación"):
            self.assertIn(item,template)
        self.assertIn("tokens",template)
        self.assertIn("no aprobada",template.lower())


if __name__=="__main__":
    unittest.main()
