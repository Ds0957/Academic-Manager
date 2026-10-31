"""Coordinator's narrow academic operations: no real Drive/Excel modifications."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from capture.capture import parser as capture_parser, run as capture_run
from capture.storage.metadata import atomic_json, read_json, sha256
from capture.storage.publisher import validate
from academic_operations.actions import OperationError, preview, complete
from academic_operations.__main__ import main


class OperationsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.runtime = self.root / "runtime"
        self.drive = self.root / "Academic Manager"
        self.drive.mkdir()
        self.control = self.runtime / "control"
        self.control.mkdir(parents=True)
        self.config = self.control / "academic-operations.json"
        atomic_json(self.config, {"schema_version": 1,
            "drive_root": str(self.drive),
            "database_state_file_id": "1SampleDatabaseStateId_Tests00001",
            "subjects": {"Materia simulada": "MAT-FAKE"}})
        self.source = self.root / "source.json"
        atomic_json(self.source, {"segments": [{"start": 0, "end": 12, "text":
                      "Transcripción completamente ficticia para test."}]})
        args = capture_parser().parse_args([
            "--runtime", str(self.runtime), "--transcript", str(self.source),
            "--semester", "2026-2", "--subject", "Materia simulada",
            "--class-date", "2026-10-01"])
        with contextlib.redirect_stdout(io.StringIO()):
            self.job = capture_run(args)
        self.job_id = self.job["job_id"]
        self.work = self.runtime / "procesamiento" / self.job_id
        self.reports = self.runtime / "procesamiento" / "analysis-review"
        self.reports.mkdir()
        self.report = self.reports / "revisado.md"
        self.report.write_text(
            "# Análisis de clase ficticia\n\n## Lo esencial\n\n"
            + ("Análisis revisado de transcripción sintética. " * 8)
            + "\n\n## Verificaciones\n\n" + ("Sin hechos reales. " * 8),
            encoding="utf-8")
        self.credential = self.control / "service-account-drive.json"
        self.credential.write_text("{}", encoding="utf-8")

    def test_preview_is_read_only_and_plan_bound_to_report(self):
        before = self.job["hashes"].copy()
        draft = preview(self.runtime, self.job_id, "revisado.md")
        self.assertEqual(draft["materia_id"], "MAT-FAKE")
        self.assertEqual(draft["analysis_status"], "pending")
        self.assertEqual(draft["steps"],
                         ["attach_analysis", "publish", "register_database", "refresh_index"])
        self.assertEqual(len(draft["plan_id"]), 64)
        self.assertEqual(read_json(self.work / "job.json")["hashes"], before)
        self.report.write_text(self.report.read_text(encoding="utf-8") + "\nextra",
                               encoding="utf-8")
        self.assertNotEqual(draft["plan_id"],
                            preview(self.runtime, self.job_id, "revisado.md")["plan_id"])

    def test_cannot_execute_without_user_approval(self):
        draft = preview(self.runtime, self.job_id, "revisado.md")
        with self.assertRaises(OperationError) as cm:
            complete(self.runtime, self.job_id, filename="revisado.md",
                     plan_id=draft["plan_id"], approved=False)
        self.assertEqual(cm.exception.code, "APPROVAL_REQUIRED")

    def test_wrong_or_stale_plan_cannot_modify_artifacts(self):
        hashes = self.job["hashes"].copy()
        with self.assertRaises(OperationError) as cm:
            complete(self.runtime, self.job_id, filename="revisado.md",
                     plan_id="0" * 64, approved=True)
        self.assertEqual(cm.exception.code, "PLAN_CHANGED")
        self.assertEqual(read_json(self.work / "job.json")["hashes"], hashes)
        self.assertFalse(any(self.drive.iterdir()))

    def test_finalizes_all_steps_and_can_resume_idempotently(self):
        draft = preview(self.runtime, self.job_id, "revisado.md")
        fake = {"class_id": "CLS-FAKE-20261001-01", "already_registered": False}
        with patch("capture.academic_manager.database.register_class", return_value=fake) as register:
            result = complete(self.runtime, self.job_id, filename="revisado.md",
                plan_id=draft["plan_id"], approved=True, index_builder=lambda *a, **kw: {})
        self.assertTrue(result["published"])
        self.assertTrue(result["database_registered"])
        self.assertTrue(result["index_refreshed"])
        self.assertEqual(register.call_count, 1)
        self.assertEqual(register.call_args.args[1]["analysis_status"], "completed_reviewed")
        job = read_json(self.work / "job.json")
        self.assertEqual(job["database_registration"]["class_id"], fake["class_id"])
        published = Path(job["publication_path"])
        validate(published, self.job_id, job["hashes"])
        self.assertEqual(read_json(published / "metadata.json")["analysis_status"], "reviewed")
        self.assertEqual(sha256(published / "transcript.json"),
                         self.job["hashes"]["transcript.json"])
        again = preview(self.runtime, self.job_id)
        self.assertEqual(again["steps"], ["refresh_index"])
        with patch("capture.academic_manager.database.register_class") as no_register:
            repeated = complete(self.runtime, self.job_id, plan_id=again["plan_id"],
                approved=True, index_builder=lambda *a, **kw: {})
        self.assertEqual(repeated["class_id"], fake["class_id"])
        no_register.assert_not_called()

    def test_publish_then_register_recover_from_interruption(self):
        draft = preview(self.runtime, self.job_id, "revisado.md")
        with patch("capture.academic_manager.database.register_class",
                   side_effect=RuntimeError("simulated network error")):
            with self.assertRaises(RuntimeError):
                complete(self.runtime, self.job_id, filename="revisado.md",
                    plan_id=draft["plan_id"], approved=True,
                    index_builder=lambda *a, **kw: {})
        stage = read_json(self.work / "job.json")
        self.assertTrue(stage["publication_path"])
        self.assertFalse(stage.get("database_registration"))
        draft2 = preview(self.runtime, self.job_id)
        self.assertEqual(draft2["steps"], ["register_database", "refresh_index"])
        with patch("capture.academic_manager.database.register_class",
                   return_value={"class_id": "CLS-FAKE-20261001-01"}) as register:
            result = complete(self.runtime, self.job_id,
                plan_id=draft2["plan_id"], approved=True,
                index_builder=lambda *a, **kw: {})
        self.assertTrue(result["database_registered"])
        self.assertEqual(register.call_count, 1)

    def test_reject_unknown_subject_and_outside_report(self):
        self.report.rename(self.root / "outside.md")
        with self.assertRaises(OperationError) as cm:
            preview(self.runtime, self.job_id, "../outside.md")
        self.assertEqual(cm.exception.code, "INVALID_ARGUMENT")
        with self.assertRaises(OperationError) as cm:
            preview(self.runtime, self.job_id, "revisado.md")
        self.assertEqual(cm.exception.code, "NOT_FOUND")
        state = read_json(self.config)
        state["subjects"] = {"Otra materia": "MAT-OTHER"}
        atomic_json(self.config, state)
        with self.assertRaises(OperationError) as cm:
            preview(self.runtime, self.job_id)
        self.assertEqual(cm.exception.code, "SUBJECT_UNMAPPED")

    def test_index_error_is_reported_as_warning_not_failed_publication(self):
        draft = preview(self.runtime, self.job_id, "revisado.md")
        with patch("capture.academic_manager.database.register_class",
                   return_value={"class_id": "CLS-FAKE-20261001-01"}):
            result = complete(self.runtime, self.job_id, filename="revisado.md",
                              plan_id=draft["plan_id"], approved=True,
                              index_builder=lambda *args, **kw: (_ for _ in ()).throw(
                                  ValueError("stale index")))
        self.assertTrue(result["database_registered"])
        self.assertFalse(result["index_refreshed"])
        self.assertIsNotNone(result["warning"])


    def test_partial_publication_marker_is_not_treated_as_published(self):
        args = capture_parser().parse_args([
            "--runtime", str(self.runtime), "--resume", self.job_id,
            "--attach-analysis", str(self.report),
            "--analysis-transcript-sha256", self.job["hashes"]["transcript.json"],
            "--analysis-reviewed"])
        with contextlib.redirect_stdout(io.StringIO()):
            capture_run(args)
        reserved = self.drive / "2026-2" / "Materia simulada" / "Clases" / "reserved"
        reserved.mkdir(parents=True)
        atomic_json(reserved / ".capture-owner.json", {"job_id": self.job_id})
        job = read_json(self.work / "job.json")
        job["publication_path"] = str(reserved)
        atomic_json(self.work / "job.json", job)
        pending = preview(self.runtime, self.job_id)
        self.assertFalse(pending["published"])
        self.assertEqual(pending["steps"], ["publish", "register_database", "refresh_index"])
        with patch("capture.academic_manager.database.register_class",
                   return_value={"class_id": "CLS-FAKE-20261001-01"}):
            done = complete(self.runtime, self.job_id,
                plan_id=pending["plan_id"], approved=True,
                index_builder=lambda *a, **kw: {})
        self.assertTrue(done["published"])
        self.assertFalse((reserved / ".capture-owner.json").exists())
        validate(reserved, self.job_id, read_json(self.work / "job.json")["hashes"])


    def test_windows_e2e_complete_with_denied_directory_rename(self):
        original_rename = Path.rename
        stage = self.work / "artifacts"

        def deny_stage_rename(source, target):
            if source == stage and target.name == ".analysis-previous":
                raise PermissionError(13, "Windows directory sharing denied")
            return original_rename(source, target)

        draft = preview(self.runtime, self.job_id, "revisado.md")
        with patch.object(Path, "rename", deny_stage_rename), \
             patch("capture.analysis.reviewed.time.sleep", return_value=None), \
             patch("capture.academic_manager.database.register_class",
                   return_value={"class_id": "CLS-FAKE-20261001-01",
                                 "already_registered": False}) as register:
            result = complete(self.runtime, self.job_id, filename="revisado.md",
                plan_id=draft["plan_id"], approved=True, index_builder=lambda *a, **kw: {})
        self.assertTrue(result["published"])
        self.assertTrue(result["database_registered"])
        self.assertTrue(result["index_refreshed"])
        register.assert_called_once()
        finished = read_json(self.work / "job.json")
        self.assertEqual(read_json(self.work / "artifacts" / "metadata.json")["analysis_status"], "reviewed")
        validate(Path(finished["publication_path"]), self.job_id, finished["hashes"])
        self.assertEqual(finished["hashes"]["transcript.json"], self.job["hashes"]["transcript.json"])

    def test_cli_json_preview_and_permission_error(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main(["preview", "--runtime", str(self.runtime),
                         "--job", self.job_id, "--analysis", "revisado.md"])
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(out.getvalue())["ok"])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main(["complete", "--runtime", str(self.runtime), "--job",
                         self.job_id, "--plan-id", "0" * 64])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(out.getvalue())["error"]["code"], "APPROVAL_REQUIRED")


    def test_preview_and_complete_argument_errors_are_unchanged(self):
        # Las acciones de chat no deben alterar el contrato de preview/complete.
        cases = ((["preview", "--runtime", str(self.runtime)],
                  "the following arguments are required: --job"),
                 (["complete", "--runtime", str(self.runtime)],
                  "the following arguments are required: --job"),
                 (["preview", "--job", self.job_id, "--texto", "x"],
                  "unrecognized arguments: --texto x"),
                 (["complete", "--job", self.job_id, "--materia", "MAT-A"],
                  "unrecognized arguments: --materia MAT-A"))
        for argv, message in cases:
            with self.subTest(argv=argv[:2]):
                err, out = io.StringIO(), io.StringIO()
                with contextlib.redirect_stderr(err), contextlib.redirect_stdout(out), \
                     self.assertRaises(SystemExit) as stop:
                    main(argv)
                self.assertEqual(stop.exception.code, 2)
                self.assertIn("usage:", err.getvalue())
                self.assertIn("{preview,complete}", err.getvalue())
                self.assertIn(message, err.getvalue())
                self.assertEqual(out.getvalue(), "")

    def test_permission_denied_in_capture_returns_stage_and_no_path(self):
        draft = preview(self.runtime, self.job_id, "revisado.md")
        secret = str(self.runtime / "secret-file-name.xlsx")
        with patch("academic_operations.actions._capture",
                   side_effect=PermissionError(13, secret)):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = main(["complete", "--runtime", str(self.runtime),
                    "--job", self.job_id, "--analysis", "revisado.md",
                    "--plan-id", draft["plan_id"], "--approve"])
        self.assertEqual(code, 2)
        result = json.loads(out.getvalue())
        self.assertFalse(result["ok"])
        error = result["error"]
        self.assertEqual(error["code"], "FILE_ACCESS_DENIED")
        self.assertEqual(error["stage"], "attach_analysis")
        self.assertTrue(error["retryable"])
        self.assertNotIn(secret, out.getvalue())
        self.assertEqual(read_json(self.work / "job.json")["hashes"], self.job["hashes"])
        self.assertFalse(any(self.drive.iterdir()))

    def test_missing_file_in_capture_reports_safe_stage(self):
        draft = preview(self.runtime, self.job_id, "revisado.md")
        with patch("academic_operations.actions._capture",
                   side_effect=FileNotFoundError(2, "private-filename")):
            with self.assertRaises(OperationError) as cm:
                complete(self.runtime, self.job_id, filename="revisado.md",
                    plan_id=draft["plan_id"], approved=True)
        self.assertEqual(cm.exception.code, "FILE_NOT_FOUND")
        self.assertEqual(cm.exception.stage, "attach_analysis")
        self.assertNotIn("private-filename", str(cm.exception))

    def test_capture_semantic_error_reports_phase_without_leak(self):
        from capture.models import CaptureError
        draft = preview(self.runtime, self.job_id, "revisado.md")
        with patch("academic_operations.actions._capture",
                   side_effect=CaptureError("private runtime details")):
            with self.assertRaises(OperationError) as cm:
                complete(self.runtime, self.job_id, filename="revisado.md",
                    plan_id=draft["plan_id"], approved=True)
        self.assertEqual(cm.exception.code, "CAPTURE_FAILED")
        self.assertEqual(cm.exception.stage, "attach_analysis")
        self.assertNotIn("private runtime details", str(cm.exception))


    # Issue #16: el nombre visible de Capture se resuelve con `01 - Materias` del semestre activo.
    def write_store(self, *subjects):
        from academic_store import store
        from tests.test_academic_store import workbook
        from tests.xlsx_fixture import write_sheets
        source = self.root / "catalog.xlsx"
        write_sheets(source, workbook(**{
            "01 - Materias": [["materia_id", "semestre", "nombre", "curso_externo_id", "bloques"],
                              *subjects],
            "05 - Semestres": [["semestre_id", "nombre", "estado", "bloque_activo", None],
                               ["2026-2", "Semestre 2 - 2026", "Activo", 2, None],
                               ["2026-1", "Semestre 1 - 2026", "Cerrado", 1, None]]}))
        (self.runtime / store.DB_NAME).unlink(missing_ok=True)
        store.migrate(source, self.runtime / store.DB_NAME)

    def without_config_subjects(self, value=None):
        state = read_json(self.config)
        if value is None:
            del state["subjects"]
        else:
            state["subjects"] = value
        atomic_json(self.config, state)

    def test_subject_resolved_from_active_semester_in_database(self):
        self.write_store(["MAT-SIM", "2026-2", "  materia SIMULADA ", "1", "2"],
                         ["MAT-SIM-OLD", "2026-1", "Materia simulada", "2", "1"],
                         ["MAT-OTRA", "2026-2", "Otra materia", "3", "2"])
        for value in (None, {}):
            with self.subTest(subjects=value):
                self.without_config_subjects(value)
                draft = preview(self.runtime, self.job_id, "revisado.md")
                self.assertEqual((draft["materia"], draft["materia_id"]), ("Materia simulada", "MAT-SIM"))
                self.assertEqual(draft["steps"],
                                 ["attach_analysis", "publish", "register_database", "refresh_index"])

    def test_configured_subject_still_wins_over_database(self):
        self.write_store(["MAT-SIM", "2026-2", "Materia simulada", "1", "2"])
        self.assertEqual(preview(self.runtime, self.job_id, "revisado.md")["materia_id"], "MAT-FAKE")

    def test_database_resolution_fails_closed(self):
        self.without_config_subjects()
        cases = {"SUBJECT_UNMAPPED": (["MAT-SIM-OLD", "2026-1", "Materia simulada", "2", "1"],),
                 "SUBJECT_AMBIGUOUS": (["MAT-SIM", "2026-2", "Materia simulada", "1", "2"],
                                       ["MAT-SIM2", "2026-2", "materia simulada", "2", "2"])}
        for code, rows in cases.items():
            with self.subTest(code=code):
                self.write_store(*rows)
                with self.assertRaises(OperationError) as cm:
                    preview(self.runtime, self.job_id, "revisado.md")
                self.assertEqual(cm.exception.code, code)
                self.assertIn("Materia simulada", str(cm.exception))

    def test_missing_database_and_no_mapping_is_unmapped(self):
        self.without_config_subjects()
        with self.assertRaises(OperationError) as cm:
            preview(self.runtime, self.job_id, "revisado.md")
        self.assertEqual(cm.exception.code, "SUBJECT_UNMAPPED")

    def test_invalid_configured_subjects_are_still_rejected(self):
        for value in (["Materia simulada"], {"Materia simulada": "no-es-id"}):
            with self.subTest(subjects=value):
                self.without_config_subjects(value)
                with self.assertRaises(OperationError) as cm:
                    preview(self.runtime, self.job_id, "revisado.md")
                self.assertEqual(cm.exception.code, "INVALID_CONFIG")

    def test_complete_registers_with_database_mapping(self):
        self.write_store(["MAT-SIM", "2026-2", "Materia simulada", "1", "2"])
        self.without_config_subjects()
        draft = preview(self.runtime, self.job_id, "revisado.md")
        with patch("capture.academic_manager.database.register_class",
                   return_value={"class_id": "CLS-SIM-20261001-01"}) as register:
            result = complete(self.runtime, self.job_id, filename="revisado.md",
                              plan_id=draft["plan_id"], approved=True, index_builder=lambda *a, **kw: {})
        self.assertTrue(result["database_registered"])
        self.assertEqual(register.call_count, 1)
        self.assertEqual(register.call_args.args[2], "MAT-SIM")
        job = read_json(self.work / "job.json")
        self.assertEqual(job["database_registration"]["class_id"], "CLS-SIM-20261001-01")



if __name__ == "__main__":
    unittest.main()
