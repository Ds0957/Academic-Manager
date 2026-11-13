"""Clases desde la transcripción de Drive (odd/tasks/transcripcion-ingles.md, S3–S4)."""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from academic_mcp import tools
from academic_mcp.tools import ToolError
from academic_operations import class_capture as cc
from academic_operations.actions import OperationError
from capture.capture import parser, run
from capture.models import CaptureError
from capture.storage.metadata import read_json
from tests.test_class_capture import Popen
from tests.test_transcript_vtt import ROLLING

FILE_ID = "1SampleDriveFileIdForTests_000001"
OTHER_ID = "1SampleDriveFileIdForTests_000002"
LINK = f"https://drive.google.com/file/d/{FILE_ID}/view"
NAME = f"English III NRC 4199 - 2026_11_04 17_51 GMT-05_00 - Recording [{FILE_ID}].vtt"
OTHER = f"English III NRC 4199 - 2026_11_11 17_58 GMT-05_00 - Recording [{OTHER_ID}].vtt"


class Folders(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.runtime, self.downloads = self.root / "runtime", self.root / "Descargas"
        self.runtime.mkdir()
        self.downloads.mkdir()
        patcher = patch.object(cc, "_subject", return_value=("Inglés III", "2026-2"))
        patcher.start()
        self.addCleanup(patcher.stop)

    def download(self, name=NAME, text=ROLLING):
        path = self.downloads / name
        path.write_text(text, encoding="utf-8")
        return path

    def done_job(self, file_id=FILE_ID, status="completed", identity="c" * 32):
        work = self.runtime / "procesamiento" / identity
        work.mkdir(parents=True)
        (work / "job.json").write_text(json.dumps({"job_id": identity, "status": status, "created_at": "2026-11-12",
                                                   "source_metadata": {"file_id": file_id}}), encoding="utf-8")

    def plan(self, *names, subject="MAT-ING"):
        return cc.import_plan(self.runtime, list(names or [NAME]), subject, folder=self.downloads)


class CaptureRecordingUrlTests(Folders):
    def capture(self, *more):
        args = parser().parse_args(["--runtime", str(self.runtime), "--semester", "2026-2", "--subject", "Inglés III",
                                    "--class-date", "2026-11-04", *more])
        with contextlib.redirect_stdout(io.StringIO()):
            return run(args)

    def test_imported_vtt_keeps_the_drive_recording_as_its_provenance(self):
        job = self.capture("--transcript", str(self.download()), "--recording-name", "English III - 2026/11/04",
                           "--recording-url", f"https://drive.google.com/file/d/{FILE_ID}/view?usp=sharing")
        source = job["source_metadata"]
        self.assertEqual((source["kind"], source["location"], source["file_id"], source["original_name"]),
                         ("existing_transcript", LINK, FILE_ID, "English III - 2026/11/04"))
        self.assertEqual(job["status"], "completed")
        transcript = read_json(self.runtime / "procesamiento" / job["job_id"] / "artifacts" / "transcript.json")
        self.assertEqual(transcript["segments"][0]["start"], 21.439)
        self.assertFalse(transcript["timestamps_unavailable"])

    def test_recording_url_must_be_drive_and_only_with_an_imported_transcript(self):
        with self.assertRaises(CaptureError):
            self.capture("--transcript", str(self.download()), "--recording-url", "https://evil.example/file/d/x")
        with self.assertRaises(CaptureError):
            self.capture("--source", LINK, "--recording-url", LINK)

    def test_an_import_without_recording_url_keeps_its_local_provenance(self):
        path = self.download()
        source = self.capture("--transcript", str(path))["source_metadata"]
        self.assertEqual((source["kind"], source["location"]), ("existing_transcript", str(path.resolve())))
        self.assertNotIn("file_id", source)


class DownloadedTests(Folders):
    def test_lists_only_vtt_files_with_their_date_and_recording(self):
        self.download()
        self.download(OTHER)
        (self.downloads / "notas.txt").write_text("x", encoding="utf-8")
        (self.downloads / "carpeta.vtt").mkdir()
        self.done_job(OTHER_ID)
        found = cc.downloaded(self.runtime, folder=self.downloads)
        self.assertEqual([(f["archivo"], f["fecha"], f["ya_procesada"]) for f in found["archivos"]],
                         [(OTHER, "2026-11-11", "c" * 32), (NAME, "2026-11-04", None)])
        self.assertEqual(found["archivos"][1]["enlace"], LINK)

    def test_an_old_download_without_the_recording_code_is_listed_with_a_hint(self):
        self.download("English III - 2026_11_04 - Recording.vtt")
        item = cc.downloaded(self.runtime, folder=self.downloads)["archivos"][0]
        self.assertIsNone(item["enlace"])
        self.assertIn("botón", item["aviso"])


class ImportPlanTests(Folders):
    def test_preview_lists_each_class_and_writes_nothing(self):
        self.download()
        plan = self.plan()
        self.assertEqual((plan["materia"], plan["semestre"]), ("Inglés III", "2026-2"))
        [item] = plan["clases"]
        self.assertEqual((item["archivo"], item["fecha"], item["enlace"], item["fragmentos"]),
                         (NAME, "2026-11-04", LINK, 2))
        self.assertEqual(item["grabacion"], "English III NRC 4199 - 2026_11_04 17_51 GMT-05_00 - Recording")
        self.assertRegex(plan["plan_id"], r"^[0-9a-f]{64}$")
        self.assertEqual(self.plan()["plan_id"], plan["plan_id"])
        self.assertFalse((self.runtime / "procesamiento").exists())

    def test_several_classes_in_one_preview(self):
        self.download()
        self.download(OTHER)
        self.assertEqual([c["fecha"] for c in self.plan(NAME, OTHER)["clases"]], ["2026-11-04", "2026-11-11"])

    def assertRefused(self, code, *names):
        with self.assertRaises(OperationError) as caught:
            self.plan(*names)
        self.assertEqual(caught.exception.code, code)
        return str(caught.exception)

    def test_refuses_names_that_are_paths_or_not_vtt(self):
        for name in ("../" + NAME, "C:\\Users\\x\\" + NAME, "clase.mp4"):
            self.assertRefused("INVALID_ARGUMENT", name)

    def test_refuses_a_file_that_is_not_in_downloads(self):
        self.assertRefused("NOT_FOUND", NAME)

    def test_refuses_a_download_without_the_recording_code(self):
        self.download("English III - 2026_11_04 - Recording.vtt")
        self.assertIn("botón", self.assertRefused("NO_RECORDING_LINK", "English III - 2026_11_04 - Recording.vtt"))

    def test_refuses_a_download_without_a_date(self):
        name = f"English III - Recording [{FILE_ID}].vtt"
        self.download(name)
        self.assertRefused("NO_DATE", name)

    def test_refuses_a_file_that_is_not_a_transcript(self):
        self.download(text="WEBVTT\n\n")
        self.assertRefused("INVALID_TRANSCRIPT", NAME)

    def test_refuses_a_recording_already_processed_by_video_or_transcript(self):
        self.download()
        self.done_job()
        self.assertIn("c" * 32, self.assertRefused("ALREADY_PROCESSED", NAME))

    def test_refuses_repeated_or_too_many_files(self):
        self.download()
        self.assertRefused("INVALID_ARGUMENT", NAME, NAME)
        with self.assertRaises(OperationError):
            cc.import_plan(self.runtime, [], "MAT-ING", folder=self.downloads)


class ImportStartTests(Folders):
    def start(self, plan_id, approved=True, names=(NAME,)):
        popen = Popen()
        result = cc.import_start(self.runtime, list(names), "MAT-ING", plan_id=plan_id, approved=approved,
                                 folder=self.downloads, popen=popen, now=lambda: 1000.0)
        return result, popen

    def test_needs_explicit_approval_and_the_same_plan(self):
        self.download()
        plan_id = self.plan()["plan_id"]
        with self.assertRaises(OperationError) as caught:
            self.start(plan_id, approved=False)
        self.assertEqual(caught.exception.code, "APPROVAL_REQUIRED")
        with self.assertRaises(OperationError) as caught:
            self.start("0" * 64)
        self.assertEqual(caught.exception.code, "PLAN_CHANGED")
        self.assertFalse((self.runtime / "procesamiento").exists())

    def test_keeps_a_copy_of_each_file_and_launches_one_process(self):
        source = self.download()
        result, popen = self.start(self.plan()["plan_id"])
        self.assertEqual(result["estado"], "lanzada")
        self.assertEqual(len(popen.calls), 1)
        order = cc._read(self.runtime)["pedido"]
        self.assertEqual((order["tipo"], order["semestre"], order["materia"]), ("importar", "2026-2", "Inglés III"))
        [item] = order["clases"]
        copy = Path(item["ruta"])
        self.assertEqual(copy.parent, self.runtime / "procesamiento" / "transcripciones-importadas")
        self.assertEqual(copy.read_bytes(), source.read_bytes())
        self.assertEqual((item["fecha"], item["enlace"]), ("2026-11-04", LINK))
        source.unlink()  # borrar la descarga ya no afecta lo aprobado
        self.assertTrue(copy.is_file())

    def test_a_file_changed_after_the_preview_is_refused_without_reading_it_whole(self):
        source = self.download()
        plan_id = self.plan()["plan_id"]
        source.write_bytes(b"x" * (cc.VTT_MAX_BYTES + 10))
        with patch.object(cc, "import_plan", return_value={"plan_id": plan_id, "semestre": "2026-2", "materia": "I",
                                                           "clases": [{"archivo": NAME, "sha256": "0" * 64}]}):
            with self.assertRaises(OperationError) as caught:
                self.start(plan_id)
        self.assertEqual(caught.exception.code, "PLAN_CHANGED")
        self.assertFalse((self.runtime / "procesamiento" / "transcripciones-importadas" / ("0" * 64 + ".vtt")).exists())


class ImportRunnerTests(Folders):
    def launch(self, *items):
        order = {"tipo": "importar", "semestre": "2026-2", "materia": "Inglés III", "clases": [
            {"archivo": f"{n}.vtt", "ruta": f"C:/copias/{n}.vtt", "fecha": f"2026-11-0{n}",
             "grabacion": f"Clase {n}", "enlace": f"https://drive.google.com/file/d/{FILE_ID[:-1]}{n}/view"}
            for n in items]}
        cc._write(self.runtime, {"estado": "lanzada", "inicio_epoch": 1, "pedido": order})

    def test_imports_each_class_with_its_recording(self):
        self.launch(1, 2)
        with patch("academic_operations.actions._capture",
                   side_effect=[{"job_id": "1" * 32}, {"job_id": "2" * 32}]) as capture:
            self.assertEqual(cc.run(self.runtime), 0)
        self.assertEqual(capture.call_args_list[0].args, (
            self.runtime, "--transcript=C:/copias/1.vtt", "--semester=2026-2", "--subject=Inglés III",
            "--class-date=2026-11-01", "--recording-name=Clase 1",
            f"--recording-url=https://drive.google.com/file/d/{FILE_ID[:-1]}1/view"))
        state = cc._read(self.runtime)
        self.assertEqual(state["estado"], "listo")
        self.assertEqual([t["job_id"] for t in state["trabajos"]], ["1" * 32, "2" * 32])
        self.assertIn("2 de 2", state["mensaje"])
        self.assertIn("completar_clase", state["mensaje"])

    def test_one_failure_does_not_stop_the_others(self):
        self.launch(1, 2)
        with patch("academic_operations.actions._capture",
                   side_effect=[CaptureError("La transcripción importada cambió."), {"job_id": "2" * 32}]):
            self.assertEqual(cc.run(self.runtime), 0)
        state = cc._read(self.runtime)
        self.assertEqual(state["trabajos"][0], {"archivo": "1.vtt", "error": "La transcripción importada cambió."})
        self.assertIn("1 de 2", state["mensaje"])
        self.assertIn("1.vtt", state["mensaje"])

    def test_all_failing_is_an_error(self):
        self.launch(1)
        with patch("academic_operations.actions._capture", side_effect=CaptureError("Falló.")):
            self.assertEqual(cc.run(self.runtime), 1)
        self.assertEqual(cc._read(self.runtime)["estado"], "error")


class ToolTests(Folders):
    def setUp(self):
        super().setUp()
        patcher = patch.object(cc, "downloads_folder", return_value=self.downloads)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_both_tools_are_registered_and_only_the_import_writes(self):
        self.assertTrue(tools.TOOLS["transcripciones_descargadas"].read_only)
        self.assertFalse(tools.TOOLS["importar_transcripciones"].read_only)

    def test_lists_downloads(self):
        self.download()
        listed = tools.call("transcripciones_descargadas", {}, self.runtime)
        self.assertEqual(listed["archivos"][0]["archivo"], NAME)

    def test_preview_then_approved_start(self):
        self.download()
        preview = tools.call("importar_transcripciones", {"archivos": [NAME], "materia": "MAT-ING"}, self.runtime)
        self.assertFalse(preview["iniciado"])
        with patch.object(cc, "_launch", return_value={"estado": "lanzada", "inicio": "x"}) as launch:
            started = tools.call("importar_transcripciones", {"archivos": [NAME], "materia": "MAT-ING",
                                                               "plan_id": preview["plan_id"], "aprobar": True},
                                 self.runtime)
        self.assertTrue(started["iniciado"])
        self.assertEqual(launch.call_args.args[1]["tipo"], "importar")

    def test_list_arguments_are_checked(self):
        for bad in ("solo-texto", [], [1], ["x" * 300], [NAME] * 16):
            with self.assertRaises(ToolError):
                tools.call("importar_transcripciones", {"archivos": bad, "materia": "MAT-ING"}, self.runtime)

    def test_other_argument_types_keep_their_checks(self):
        with self.assertRaises(ToolError) as caught:
            tools.call("capturas", {"limite": "5"}, self.runtime)
        self.assertIn("número entero", str(caught.exception))



class RegistrationTests(unittest.TestCase):
    def record(self, **source):
        from capture.academic_manager.database import build_class_record
        job = {"job_id": "d" * 32, "publication_path": "G:/x/2026-2/Inglés III/Clases/Clase",
               "analysis_status": "completed_reviewed", "engine": {"engine": "import", "model": None},
               "request": {"semester": "2026-2", "subject": "Inglés III", "class_date": "2026-11-04"},
               "source_metadata": {"original_name": "English III", **source}}
        return build_class_record(job, "MAT-ING")

    def test_an_imported_transcript_of_a_drive_recording_is_registered_with_its_link(self):
        record = self.record(kind="existing_transcript", location=LINK, file_id=FILE_ID)
        self.assertEqual((record["url_grabacion"], record["fuente"]), (LINK, "Google Drive"))

    def test_other_sources_keep_their_label(self):
        self.assertEqual(self.record(kind="drive", location=LINK)["fuente"], "Google Drive")
        self.assertEqual(self.record(kind="local", location="C:/clase.mp4")["fuente"], "archivo local")
        self.assertEqual(self.record(kind="existing_transcript", location="C:/t.json")["fuente"], "archivo local")


if __name__ == "__main__":
    unittest.main()
