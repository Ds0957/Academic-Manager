"""Procesar una clase desde Claude (odd/tasks/capture-desde-claude.md, S1-S6)."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from academic_mcp import tools
from academic_mcp.tools import ToolError
from academic_operations import class_capture as cc
from academic_operations.actions import OperationError
from capture.models import CaptureError
from capture.storage.job import job_lock

LINK = "https://drive.google.com/file/d/1EjemploDeArchivoDeDrive0000000000/view"
FILE_ID = "1EjemploDeArchivoDeDrive0000000000"
NAME = " SISTEMAS TRANSACCIONALES - 2026/10/29 20:50 GMT-05:00 - Recording"
JOB = "a" * 32
PLAN = "b" * 64
ANALYSIS = "# Análisis\n\n" + "Contenido revisado de la clase. " * 20


class FakeApi:
    """Lo mínimo de la API de Drive que usa capture.sources.drive.inspect."""

    def __init__(self, **fields):
        self.fields = {"id": FILE_ID, "name": NAME, "mimeType": "video/mp4", "size": "1000",
                       "modifiedTime": "2026-10-30T01:00:00Z", "webViewLink": LINK,
                       "capabilities": {"canDownload": True}, **fields}

    def files(self):
        return self

    def get(self, **_):
        return self

    def execute(self):
        if isinstance(self.fields.get("error"), Exception):
            raise self.fields["error"]
        return self.fields


class Popen:
    def __init__(self, error=None):
        self.calls, self.error = [], error

    def __call__(self, argv, **kwargs):
        if self.error:
            raise self.error
        self.calls.append((argv, kwargs))


class Base(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.runtime = Path(folder.name)
        patcher = patch.object(cc, "_subject", return_value=("Sistemas Transaccionales", "2026-2"))
        self.subject = patcher.start()
        self.addCleanup(patcher.stop)

    def job(self, identity=JOB, status="completed", file_id=FILE_ID, transcript="[00:00:01] Hola clase",
            semester="2026-2", subject="Sistemas Transaccionales", day="2026-10-29"):
        work = self.runtime / "procesamiento" / identity
        (work / "artifacts").mkdir(parents=True)
        (work / "job.json").write_text(json.dumps({
            "schema_version": 1, "job_id": identity, "status": status,
            "source_metadata": {"file_id": file_id},
            "request": {"semester": semester, "subject": subject, "class_date": day}}), encoding="utf-8")
        (work / "artifacts" / "transcript.md").write_text(transcript, encoding="utf-8")
        return work

    def plan(self, api=None, day="2026-10-29", link=LINK):
        return cc.plan(self.runtime, link, "MAT-ST", day, api=api or FakeApi())

    def state(self):
        return json.loads((self.runtime / "procesamiento" / "claude-capture" / "estado.json").read_text("utf-8"))


class PlanTests(Base):
    def test_preview_shows_the_recording_and_subject_and_starts_nothing(self):
        result = self.plan()
        self.assertEqual((result["grabacion"], result["materia"], result["materia_id"], result["semestre"],
                          result["fecha"]), (NAME, "Sistemas Transaccionales", "MAT-ST", "2026-2", "2026-10-29"))
        self.assertRegex(result["plan_id"], r"^[0-9a-f]{64}$")
        self.assertIsNone(result["reanuda"])
        self.assertFalse((self.runtime / "procesamiento" / "claude-capture" / "estado.json").exists())

    def test_rejects_what_is_not_a_drive_link(self):
        for link in ("C:/clases/video.mp4", FILE_ID, "--publish", "https://evil.example/file/d/" + FILE_ID,
                     "http://drive.google.com/file/d/" + FILE_ID):
            with self.subTest(link=link), self.assertRaises(OperationError) as caught:
                self.plan(link=link)
            self.assertEqual(caught.exception.code, "INVALID_ARGUMENT")

    def test_rejects_a_date_that_contradicts_the_recording(self):
        with self.assertRaises(OperationError) as caught:
            self.plan(day="2026-10-28")
        self.assertEqual(caught.exception.code, "DATE_MISMATCH")
        self.assertIn("2026-10-29", str(caught.exception))

    def test_rejects_an_impossible_date(self):
        with self.assertRaises(OperationError) as caught:
            self.plan(day="2026-13-40")
        self.assertEqual(caught.exception.code, "INVALID_ARGUMENT")

    def test_recording_without_a_date_keeps_the_given_one(self):
        self.assertEqual(self.plan(FakeApi(name="clase.mp4"))["fecha"], "2026-10-29")

    def test_rejects_a_file_that_is_not_audio_or_video(self):
        with self.assertRaises(OperationError) as caught:
            self.plan(FakeApi(mimeType="application/pdf"))
        self.assertEqual(caught.exception.code, "NOT_MEDIA")

    def test_a_drive_refusal_is_a_plain_message(self):
        error = Exception("detalle interno")
        error.resp = type("R", (), {"status": 404})()
        with self.assertRaises(OperationError) as caught:
            self.plan(FakeApi(error=error))
        self.assertEqual(caught.exception.code, "DRIVE_DENIED")
        self.assertNotIn("--transcript", str(caught.exception))
        self.assertNotIn("detalle interno", str(caught.exception))

    def test_a_recording_already_processed_is_refused_with_its_job(self):
        self.job()
        with self.assertRaises(OperationError) as caught:
            self.plan()
        self.assertEqual(caught.exception.code, "ALREADY_PROCESSED")
        self.assertIn(JOB, str(caught.exception))

    def test_an_unfinished_job_of_the_same_recording_is_resumed(self):
        self.job(status="failed")
        self.assertEqual(self.plan()["reanuda"], JOB)

    def test_resuming_refuses_a_subject_or_date_the_job_would_not_use(self):
        self.job(status="failed", subject="Sistemas Operativos II", day="2026-10-01")
        with self.assertRaises(OperationError) as caught:
            self.plan(FakeApi(name="clase.mp4"))
        self.assertEqual(caught.exception.code, "RESUME_MISMATCH")
        self.assertIn("Sistemas Operativos II", str(caught.exception))
        self.assertIn("2026-10-01", str(caught.exception))

    def test_the_plan_changes_with_the_drive_file(self):
        self.assertNotEqual(self.plan()["plan_id"], self.plan(FakeApi(modifiedTime="2026-11-01T00:00:00Z"))["plan_id"])


class StartTests(Base):
    def start(self, plan_id=None, approved=True, popen=None):
        popen = popen or Popen()
        result = cc.start(self.runtime, LINK, "MAT-ST", "2026-10-29", plan_id=plan_id or self.plan()["plan_id"],
                          approved=approved, api=FakeApi(), popen=popen)
        return result, popen

    def test_needs_explicit_approval(self):
        popen = Popen()
        with self.assertRaises(OperationError) as caught:
            self.start(approved=False, popen=popen)
        self.assertEqual(caught.exception.code, "APPROVAL_REQUIRED")
        self.assertEqual(popen.calls, [])

    def test_refuses_a_plan_that_changed(self):
        popen = Popen()
        with self.assertRaises(OperationError) as caught:
            self.start(plan_id=PLAN, popen=popen)
        self.assertEqual(caught.exception.code, "PLAN_CHANGED")
        self.assertEqual(popen.calls, [])

    def test_launches_a_separate_process_that_takes_no_user_text(self):
        result, popen = self.start()
        self.assertEqual(result["estado"], "lanzada")
        argv, options = popen.calls[0]
        self.assertEqual(argv[1:], ["-m", "academic_operations.class_capture", "--runtime", str(self.runtime)])
        self.assertEqual(Path(options["cwd"]), Path(cc.__file__).resolve().parents[1])
        self.assertEqual(self.state()["pedido"]["fuente"], "https://drive.google.com/file/d/" + FILE_ID + "/view")

    def test_only_one_at_a_time(self):
        self.start()
        popen = Popen()
        with self.assertRaises(OperationError) as caught:
            self.start(popen=popen)
        self.assertEqual(caught.exception.code, "BUSY")
        self.assertEqual(popen.calls, [])

    def test_a_process_holding_the_lock_also_blocks(self):
        with job_lock(self.runtime / "procesamiento" / "claude-capture"):
            popen = Popen()
            with self.assertRaises(OperationError) as caught:
                self.start(popen=popen)
        self.assertEqual(caught.exception.code, "BUSY")

    def test_a_launch_failure_is_recorded_and_plain(self):
        with self.assertRaises(OperationError) as caught:
            self.start(popen=Popen(OSError("ruta privada")))
        self.assertEqual(caught.exception.code, "NOT_STARTED")
        self.assertNotIn("ruta privada", str(caught.exception))
        self.assertEqual(self.state()["estado"], "error")


class RunnerTests(Base):
    def launch(self, order):
        cc._write(self.runtime, {"estado": "lanzada", "inicio_epoch": 1, "pedido": order})

    def test_runs_capture_with_the_stored_order_and_records_the_job(self):
        self.launch({"tipo": "capturar", "fuente": LINK, "semestre": "2026-2", "materia": "Sistemas Transaccionales",
                     "fecha": "2026-10-29", "reanuda": None})
        with patch("academic_operations.actions._capture", return_value={"job_id": JOB}) as run:
            self.assertEqual(cc.run(self.runtime), 0)
        run.assert_called_once_with(self.runtime, "--source=" + LINK, "--semester=2026-2",
                                    "--subject=Sistemas Transaccionales", "--class-date=2026-10-29")
        state = self.state()
        self.assertEqual((state["estado"], state["job_id"]), ("listo", JOB))
        self.assertIn("transcripción", state["mensaje"])

    def test_resumes_an_unfinished_job(self):
        self.launch({"tipo": "capturar", "fuente": LINK, "semestre": "2026-2", "materia": "X", "fecha": "2026-10-29",
                     "reanuda": JOB})
        with patch("academic_operations.actions._capture", return_value={"job_id": JOB}) as run:
            cc.run(self.runtime)
        run.assert_called_once_with(self.runtime, "--resume", JOB)

    def test_a_capture_failure_is_recorded(self):
        self.launch({"tipo": "capturar", "fuente": LINK, "semestre": "2026-2", "materia": "X", "fecha": "2026-10-29",
                     "reanuda": None})
        with patch("academic_operations.actions._capture", side_effect=CaptureError("Falló la descarga.")):
            self.assertEqual(cc.run(self.runtime), 1)
        self.assertEqual((self.state()["estado"], self.state()["mensaje"]), ("error", "Falló la descarga."))

    def test_an_unexpected_failure_shows_no_internal_text(self):
        self.launch({"tipo": "capturar", "fuente": LINK, "semestre": "2026-2", "materia": "X", "fecha": "2026-10-29",
                     "reanuda": None})
        with patch("academic_operations.actions._capture", side_effect=RuntimeError("C:/secreto")):
            self.assertEqual(cc.run(self.runtime), 1)
        self.assertEqual(self.state()["estado"], "error")
        self.assertNotIn("secreto", self.state()["mensaje"])

    def test_waits_briefly_for_a_status_probe_holding_the_lock(self):
        self.launch({"tipo": "capturar", "fuente": LINK, "semestre": "2026-2", "materia": "X", "fecha": "2026-10-29",
                     "reanuda": None})
        real = cc.job_lock
        attempts = []

        def busy_once(folder):
            attempts.append(folder)
            if len(attempts) == 1:
                raise CaptureError("ocupado")
            return real(folder)

        with patch.object(cc, "job_lock", busy_once), patch.object(cc.time, "sleep"), \
                patch("academic_operations.actions._capture", return_value={"job_id": JOB}):
            self.assertEqual(cc.run(self.runtime), 0)
        self.assertEqual(self.state()["estado"], "listo")

    def test_does_nothing_without_a_launched_order(self):
        with patch("academic_operations.actions._capture") as run:
            self.assertEqual(cc.run(self.runtime), 1)
        run.assert_not_called()

    def test_completes_a_class_with_the_approved_plan(self):
        self.launch({"tipo": "completar", "job_id": JOB, "informe": cc.report_name(JOB), "plan_id": PLAN})
        with patch("academic_operations.actions.complete",
                   return_value={"class_id": "CLS-ST-20261029-01", "warning": None}) as done:
            self.assertEqual(cc.run(self.runtime), 0)
        done.assert_called_once_with(self.runtime, JOB, filename=cc.report_name(JOB), plan_id=PLAN, approved=True)
        self.assertEqual((self.state()["estado"], self.state()["clase_id"]), ("listo", "CLS-ST-20261029-01"))


class StatusTests(Base):
    def test_nothing_yet(self):
        self.assertEqual(cc.status(self.runtime), {"estado": "ninguna"})

    def test_a_dead_process_is_reported_as_interrupted(self):
        cc._write(self.runtime, {"estado": "en_curso", "inicio_epoch": 1, "pedido": {"tipo": "capturar"}})
        before = (self.runtime / "procesamiento" / "claude-capture" / "estado.json").read_bytes()
        self.assertEqual(cc.status(self.runtime)["estado"], "interrumpida")
        self.assertEqual((self.runtime / "procesamiento" / "claude-capture" / "estado.json").read_bytes(), before)

    def test_a_state_written_during_the_lock_probe_is_the_one_reported(self):
        cc._write(self.runtime, {"estado": "en_curso", "inicio_epoch": 1, "pedido": {"tipo": "capturar"}})

        def finished(_):
            cc._write(self.runtime, {"estado": "listo", "inicio_epoch": 1, "job_id": JOB})
            return False

        with patch.object(cc, "_locked", finished):
            self.assertEqual(cc.status(self.runtime)["job_id"], JOB)
        self.assertEqual(self.state()["estado"], "listo")

    def test_a_running_process_stays_running(self):
        cc._write(self.runtime, {"estado": "en_curso", "inicio_epoch": 1, "pedido": {"tipo": "capturar"}})
        with job_lock(self.runtime / "procesamiento" / "claude-capture"):
            self.assertEqual(cc.status(self.runtime)["estado"], "en_curso")

    def test_a_launch_just_made_is_not_interrupted_yet(self):
        cc._write(self.runtime, {"estado": "lanzada", "inicio_epoch": 1000, "pedido": {"tipo": "capturar"}})
        self.assertEqual(cc.status(self.runtime, now=lambda: 1010)["estado"], "lanzada")
        self.assertEqual(cc.status(self.runtime, now=lambda: 1000 + cc.START_GRACE + 1)["estado"], "interrumpida")

    def test_status_hides_the_internal_order(self):
        cc._write(self.runtime, {"estado": "listo", "inicio_epoch": 1, "pedido": {"plan_id": PLAN}, "mensaje": "ok"})
        self.assertNotIn("pedido", cc.status(self.runtime))


class TranscriptTests(Base):
    def test_reads_by_parts(self):
        self.job(transcript="abcdefghij")
        first = cc.transcript(self.runtime, JOB, offset=0, length=4)
        self.assertEqual((first["texto"], first["hay_mas"], first["siguiente_offset"], first["total"]),
                         ("abcd", True, 4, 10))
        last = cc.transcript(self.runtime, JOB, offset=8, length=4)
        self.assertEqual((last["texto"], last["hay_mas"]), ("ij", False))

    @unittest.skipIf(os.name == "nt", "enlaces simbólicos")
    def test_refuses_redirected_artifacts(self):
        work = self.job()
        elsewhere = self.runtime / "otra"
        (work / "artifacts").rename(elsewhere)
        (work / "artifacts").symlink_to(elsewhere)
        with self.assertRaises(OperationError):
            cc.transcript(self.runtime, JOB)

    def test_missing_job(self):
        with self.assertRaises(OperationError) as caught:
            cc.transcript(self.runtime, JOB)
        self.assertEqual(caught.exception.code, "NOT_FOUND")

    @unittest.skipIf(os.name == "nt", "enlaces simbólicos")
    def test_refuses_a_redirected_transcript(self):
        work = self.job()
        target = self.runtime / "otro.md"
        target.write_text("secreto", encoding="utf-8")
        (work / "artifacts" / "transcript.md").unlink()
        (work / "artifacts" / "transcript.md").symlink_to(target)
        with self.assertRaises(OperationError):
            cc.transcript(self.runtime, JOB)


class CompleteTests(Base):
    OVERVIEW = {"plan_id": PLAN, "materia": "Sistemas Transaccionales", "steps": ["publish"]}

    def test_preview_of_an_unknown_job_stores_nothing(self):
        with self.assertRaises(OperationError) as caught:
            cc.complete_preview(self.runtime, JOB, ANALYSIS)
        self.assertEqual(caught.exception.code, "NOT_FOUND")
        self.assertFalse((self.runtime / "procesamiento" / "analysis-review").exists())

    def test_preview_stores_the_report_and_writes_nothing_else(self):
        self.job()
        with patch("academic_operations.actions.preview", return_value=self.OVERVIEW) as preview:
            result = cc.complete_preview(self.runtime, JOB, ANALYSIS)
        preview.assert_called_once_with(self.runtime, JOB, cc.report_name(JOB))
        self.assertEqual(result, self.OVERVIEW)
        report = self.runtime / "procesamiento" / "analysis-review" / cc.report_name(JOB)
        self.assertEqual(report.read_text("utf-8"), ANALYSIS)

    def test_start_needs_approval_and_the_same_plan(self):
        self.job()
        popen = Popen()
        with patch("academic_operations.actions.preview", return_value=self.OVERVIEW):
            with self.assertRaises(OperationError) as refused:
                cc.complete_start(self.runtime, JOB, ANALYSIS, plan_id=PLAN, approved=False, popen=popen)
            with self.assertRaises(OperationError) as changed:
                cc.complete_start(self.runtime, JOB, ANALYSIS, plan_id="c" * 64, approved=True, popen=popen)
        self.assertEqual((refused.exception.code, changed.exception.code), ("APPROVAL_REQUIRED", "PLAN_CHANGED"))
        self.assertEqual(popen.calls, [])

    def test_start_launches_the_existing_completion_in_the_background(self):
        self.job()
        popen = Popen()
        with patch("academic_operations.actions.preview", return_value=self.OVERVIEW):
            result = cc.complete_start(self.runtime, JOB, ANALYSIS, plan_id=PLAN, approved=True, popen=popen)
        self.assertEqual(result["estado"], "lanzada")
        self.assertEqual(len(popen.calls), 1)
        self.assertEqual(self.state()["pedido"],
                         {"tipo": "completar", "job_id": JOB, "informe": cc.report_name(JOB), "plan_id": PLAN})


class ToolTests(Base):
    def test_declared_honestly(self):
        for name in ("capturas", "leer_transcripcion"):
            self.assertTrue(tools.TOOLS[name].read_only, name)
        for name in ("procesar_clase", "completar_clase"):
            self.assertFalse(tools.TOOLS[name].read_only, name)
            self.assertIn("aprobar", tools.TOOLS[name].description)

    def test_process_without_approval_only_previews(self):
        with patch.object(cc, "plan", return_value={"plan_id": PLAN, "grabacion": NAME}) as plan, \
                patch.object(cc, "start") as start:
            result = tools.call("procesar_clase", {"enlace": LINK, "materia": "MAT-ST", "fecha": "2026-10-29"},
                                self.runtime)
        plan.assert_called_once_with(self.runtime, LINK, "MAT-ST", "2026-10-29")
        start.assert_not_called()
        self.assertEqual((result["iniciado"], result["plan_id"]), (False, PLAN))
        self.assertIn("aprobar", result["siguiente_paso"])

    def test_process_with_approval_starts(self):
        with patch.object(cc, "start", return_value={"estado": "lanzada"}) as start:
            result = tools.call("procesar_clase", {"enlace": LINK, "materia": "MAT-ST", "fecha": "2026-10-29",
                                                   "plan_id": PLAN, "aprobar": True}, self.runtime)
        start.assert_called_once_with(self.runtime, LINK, "MAT-ST", "2026-10-29", plan_id=PLAN, approved=True)
        self.assertTrue(result["iniciado"])
        self.assertIn("capturas", result["siguiente_paso"])

    def test_half_an_approval_is_refused(self):
        with patch.object(cc, "start") as start, self.assertRaises(ToolError):
            tools.call("procesar_clase", {"enlace": LINK, "materia": "MAT-ST", "fecha": "2026-10-29",
                                          "aprobar": True}, self.runtime)
        start.assert_not_called()

    def test_operation_errors_reach_the_person_plainly(self):
        with patch.object(cc, "plan", side_effect=OperationError("BUSY", "Ya hay una clase en proceso.")):
            with self.assertRaises(ToolError) as caught:
                tools.call("procesar_clase", {"enlace": LINK, "materia": "MAT-ST", "fecha": "2026-10-29"},
                           self.runtime)
        self.assertEqual(str(caught.exception), "Ya hay una clase en proceso.")

    def test_bad_formats_never_reach_the_operation(self):
        bad = ({"enlace": "C:/video.mp4", "materia": "MAT-ST", "fecha": "2026-10-29"},
               {"enlace": LINK, "materia": "MAT-ST", "fecha": "ayer"})
        with patch.object(cc, "plan") as plan:
            for args in bad:
                with self.subTest(args=args), self.assertRaises(ToolError):
                    tools.call("procesar_clase", args, self.runtime)
        plan.assert_not_called()

    def test_jobs_tool_joins_the_process_state_and_the_jobs(self):
        self.job()
        result = tools.call("capturas", {}, self.runtime)
        self.assertEqual(result["en_curso"], {"estado": "ninguna"})
        self.assertEqual(result["trabajos"][0]["job_id"], JOB)

    def test_transcript_tool_pages(self):
        self.job(transcript="abcdef")
        result = tools.call("leer_transcripcion", {"trabajo": JOB, "offset": 2, "length": 2}, self.runtime)
        self.assertEqual(result["texto"], "cd")

    def test_complete_without_approval_only_previews(self):
        with patch.object(cc, "complete_preview", return_value={"plan_id": PLAN, "steps": []}) as preview, \
                patch.object(cc, "complete_start") as start:
            result = tools.call("completar_clase", {"trabajo": JOB, "analisis": ANALYSIS}, self.runtime)
        preview.assert_called_once_with(self.runtime, JOB, ANALYSIS)
        start.assert_not_called()
        self.assertEqual((result["iniciado"], result["plan_id"]), (False, PLAN))

    def test_a_full_class_analysis_is_accepted(self):
        long_text = "# Análisis\n\n" + "x" * 200_000
        with patch.object(cc, "complete_preview", return_value={"plan_id": PLAN}) as preview:
            tools.call("completar_clase", {"trabajo": JOB, "analisis": long_text}, self.runtime)
        preview.assert_called_once_with(self.runtime, JOB, long_text)

    def test_complete_with_approval_starts(self):
        with patch.object(cc, "complete_start", return_value={"estado": "lanzada"}) as start:
            tools.call("completar_clase", {"trabajo": JOB, "analisis": ANALYSIS, "plan_id": PLAN, "aprobar": True},
                       self.runtime)
        start.assert_called_once_with(self.runtime, JOB, ANALYSIS, plan_id=PLAN, approved=True)


class SubjectTests(unittest.TestCase):
    CATALOG = {"01 - Materias": [{"materia_id": "MAT-ST", "semestre": "2026-2", "nombre": "Sistemas Transaccionales"},
                                 {"materia_id": "MAT-OLD", "semestre": "2026-1", "nombre": "Vieja"}],
               "05 - Semestres": [{"semestre_id": "2026-2", "estado": "Activo"}]}

    def test_resolves_only_a_subject_of_the_active_semester(self):
        with patch("academic_store.store.read_catalog", return_value=self.CATALOG):
            self.assertEqual(cc._subject(Path("/r"), "MAT-ST"), ("Sistemas Transaccionales", "2026-2"))
            with self.assertRaises(OperationError) as caught:
                cc._subject(Path("/r"), "MAT-OLD")
        self.assertEqual(caught.exception.code, "SUBJECT_UNMAPPED")


if __name__ == "__main__":
    unittest.main()
