"""Regression tests for safe manual analysis attachment, without real academic data."""
import contextlib
import io
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from capture.capture import parser, run
from capture.models import CaptureError
from capture.storage.metadata import atomic_json, read_json, sha256
from capture.storage.publisher import validate


class ReviewedAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.runtime = self.root / "runtime"
        self.transcript = self.root / "source.json"
        atomic_json(self.transcript, {"segments": [{"start": 0, "end": 30, "text":
            "Clase ficticia de ejemplo, sin datos académicos reales."}]})
        self.report = self.root / "revisado.md"
        self.report.write_text("# Análisis académico\n\n"
            "## Puntos esenciales\n\n" +
            "Se explica el concepto central con un ejemplo sintético. " * 5 +
            "\n\n## Aplicación y seguimiento\n\n" +
            "Esta explicación fue revisada por una persona. " * 5,
            encoding="utf-8")
        self.job = self.call("--transcript", str(self.transcript),
                             "--semester", "2026-2", "--subject", "Materia ficticia")
        self.work = self.runtime / "procesamiento" / self.job["job_id"]
        self.stage = self.work / "artifacts"
        self.original_hashes = dict(self.job["hashes"])

    def call(self, *options):
        args = parser().parse_args(["--runtime", str(self.runtime), *options])
        with contextlib.redirect_stdout(io.StringIO()):
            return run(args)

    def attach(self, *extra):
        return self.call("--resume", self.job["job_id"], "--attach-analysis",
            str(self.report), "--analysis-transcript-sha256",
            self.original_hashes["transcript.json"], "--analysis-reviewed", *extra)

    def test_attach_idempotent_and_consistent(self):
        before = sha256(self.stage / "transcript.json")
        changed = self.attach()
        self.assertEqual(changed["status"], "completed")
        self.assertEqual(sha256(self.stage / "transcript.json"), before)
        self.assertNotEqual(changed["hashes"], self.original_hashes)
        metadata = read_json(self.stage / "metadata.json")
        self.assertEqual(metadata["analysis_status"], "reviewed")
        self.assertEqual(metadata["analysis_source"]["transcript_sha256"], before)
        validate(self.stage, self.job["job_id"], changed["hashes"])
        again = self.attach()
        self.assertEqual(changed["hashes"], again["hashes"])
        self.assertFalse((self.work / "analysis-transaction.json").exists())

    def test_wrong_transcript_hash_does_not_mutate_job(self):
        with self.assertRaises(CaptureError):
            self.call("--resume", self.job["job_id"], "--attach-analysis",
                      str(self.report), "--analysis-reviewed",
                      "--analysis-transcript-sha256", "0" * 64)
        self.assertEqual(read_json(self.work / "job.json")["hashes"], self.original_hashes)
        validate(self.stage, self.job["job_id"], self.original_hashes)

    def test_requires_explicit_review(self):
        with self.assertRaises(CaptureError):
            self.call("--resume", self.job["job_id"], "--attach-analysis",
                      str(self.report), "--analysis-transcript-sha256",
                      self.original_hashes["transcript.json"])
        validate(self.stage, self.job["job_id"], self.original_hashes)

    def test_rejects_pending_or_trivial_analysis(self):
        self.report.write_text("# Análisis\n\nEstado: pendiente de análisis." + " repetido" * 50,
                               encoding="utf-8")
        with self.assertRaises(CaptureError):
            self.attach()
        validate(self.stage, self.job["job_id"], self.original_hashes)

    def test_rejects_published_job(self):
        drive = self.root / "drive"
        drive.mkdir()
        self.call("--resume", self.job["job_id"], "--publish", "--drive-root", str(drive))
        with self.assertRaises(CaptureError):
            self.attach()
        self.assertEqual(read_json(self.work / "job.json")["hashes"], self.original_hashes)

    def test_rejects_attach_with_publication_in_same_call(self):
        drive = self.root / "drive"
        drive.mkdir()
        with self.assertRaises(CaptureError):
            self.attach("--publish", "--drive-root", str(drive))
        self.assertFalse((self.work / "analysis-transaction.json").exists())

    def test_revise_again_before_publication(self):
        self.attach()
        old_hash = sha256(self.stage / "analysis.md")
        self.report.write_text(self.report.read_text(encoding="utf-8") +
                               "\n\n## Corrección\n\nNuevo ejemplo revisado y confirmado.",
                               encoding="utf-8")
        changed = self.attach()
        self.assertNotEqual(sha256(self.stage / "analysis.md"), old_hash)
        validate(self.stage, self.job["job_id"], changed["hashes"])

    def test_tampering_reviewed_provenance_is_rejected(self):
        self.attach()
        metadata = read_json(self.stage / "metadata.json")
        metadata["analysis_source"]["transcript_sha256"] = "0" * 64
        atomic_json(self.stage / "metadata.json", metadata)
        with self.assertRaises(CaptureError):
            validate(self.stage, self.job["job_id"])

    def test_recover_rollback_missing_stage(self):
        old = self.original_hashes
        stage, backup = self.stage, self.work / ".analysis-previous"
        candidate = self.work / ".analysis-next"
        shutil.copytree(stage, candidate)
        data = read_json(candidate / "metadata.json")
        data["analysis_status"] = "reviewed"
        data["analysis_source"] = {
            "job_id": self.job["job_id"], "approval": "human_confirmed",
            "transcript_sha256": old["transcript.json"],
            "analysis_sha256": sha256(candidate / "analysis.md")}
        atomic_json(candidate / "metadata.json", data)
        new = validate(candidate, self.job["job_id"])
        atomic_json(self.work / "analysis-transaction.json", {
            "job_id": self.job["job_id"], "old_hashes": old, "new_hashes": new})
        stage.rename(backup)
        recovered = self.call("--resume", self.job["job_id"])
        self.assertEqual(recovered["hashes"], old)
        validate(stage, self.job["job_id"], old)
        self.assertFalse(backup.exists())
        self.assertFalse(candidate.exists())

    def test_recover_rollforward_new_stage(self):
        old_dir = self.root / "old"
        shutil.copytree(self.stage, old_dir)
        new_job = self.attach()
        new_hashes = dict(new_job["hashes"])
        shutil.copytree(old_dir, self.work / ".analysis-previous")
        job = read_json(self.work / "job.json")
        job["hashes"] = self.original_hashes
        atomic_json(self.work / "job.json", job)
        atomic_json(self.work / "analysis-transaction.json", {
            "job_id": self.job["job_id"], "old_hashes": self.original_hashes,
            "new_hashes": new_hashes})
        resumed = self.call("--resume", self.job["job_id"])
        self.assertEqual(resumed["hashes"], new_hashes)
        validate(self.stage, self.job["job_id"], new_hashes)
        self.assertFalse((self.work / ".analysis-previous").exists())

    def test_failure_in_swap_restores_old_artifacts(self):
        original = Path.rename

        def fail_candidate(path, target):
            if path.name == ".analysis-next":
                raise OSError("simulated failure")
            return original(path, target)

        with patch.object(Path, "rename", fail_candidate):
            with self.assertRaises(OSError):
                self.attach()
        validate(self.stage, self.job["job_id"], self.original_hashes)
        self.assertEqual(read_json(self.work / "job.json")["hashes"], self.original_hashes)
        self.assertFalse((self.work / "analysis-transaction.json").exists())


    def test_publish_and_register_after_review(self):
        self.attach()
        review_job = read_json(self.work / "job.json")
        drive = self.root / "drive"
        drive.mkdir()
        published = self.call("--resume", self.job["job_id"], "--publish",
                              "--drive-root", str(drive))
        destination = Path(published["publication_path"])
        self.assertTrue(destination.is_dir())
        validate(destination, self.job["job_id"], review_job["hashes"])
        self.assertEqual(read_json(destination / "metadata.json")["analysis_status"], "reviewed")
        credentials = self.runtime / "control" / "test-adc.json"
        credentials.parent.mkdir(parents=True)
        credentials.write_text("{}", encoding="utf-8")
        with patch("capture.academic_manager.database.register_class",
                   side_effect=[{"class_id": "CLS-FAKE-01", "already_registered": False},
                                {"class_id": "CLS-FAKE-01", "already_registered": True}]) as mocked:
            first = self.call("--resume", self.job["job_id"],
                "--register-database", "--subject-id", "MAT-FAKE",
                "--database-state-file-id", "synthetic-state",
                "--workbook-credentials", str(credentials))
            second = self.call("--resume", self.job["job_id"],
                "--register-database", "--subject-id", "MAT-FAKE",
                "--database-state-file-id", "synthetic-state",
                "--workbook-credentials", str(credentials))
        self.assertEqual(mocked.call_count, 2)
        self.assertEqual(mocked.call_args.args[1]["analysis_status"], "completed_reviewed")
        self.assertEqual(first["database_registration"]["class_id"], "CLS-FAKE-01")
        self.assertTrue(second["database_registration"]["already_registered"])
        self.assertEqual(read_json(destination / "metadata.json")["analysis_status"], "reviewed")

    def test_review_state_exposed_in_monitor(self):
        from capture.storage.monitor import job_summary
        self.assertEqual(job_summary(self.runtime, self.job["job_id"])["analysis_status"], "pending")
        self.attach()
        self.assertEqual(job_summary(self.runtime, self.job["job_id"])["analysis_status"], "reviewed")


    def test_windows_transient_rename_denials_recover_and_commit(self):
        original = Path.rename
        attempts = {"artifacts": 0, ".analysis-next": 0}

        def intermittent(path, target):
            if path.name in attempts:
                attempts[path.name] += 1
                if attempts[path.name] <= 2:
                    raise PermissionError(13, "simulated temporary Windows sharing denial")
            return original(path, target)

        with patch.object(Path, "rename", intermittent), \
             patch("capture.analysis.reviewed.time.sleep", return_value=None):
            completed = self.attach()
        self.assertEqual(attempts, {"artifacts": 3, ".analysis-next": 3})
        validate(self.stage, self.job["job_id"], completed["hashes"])
        self.assertEqual(read_json(self.stage / "metadata.json")["analysis_status"], "reviewed")
        self.assertFalse((self.work / "analysis-transaction.json").exists())

    def test_windows_persistent_candidate_denial_restores_previous(self):
        original = Path.rename

        def denied_candidate(path, target):
            if path.name == ".analysis-next":
                raise PermissionError(13, "simulated persistent Windows sharing denial")
            return original(path, target)

        with patch.object(Path, "rename", denied_candidate), \
             patch("capture.analysis.reviewed.time.sleep", return_value=None):
            with self.assertRaises(PermissionError):
                self.attach()
        current = read_json(self.work / "job.json")
        self.assertEqual(current["hashes"], self.original_hashes)
        validate(self.stage, self.job["job_id"], self.original_hashes)
        self.assertFalse((self.work / ".analysis-next").exists())
        self.assertFalse((self.work / ".analysis-previous").exists())
        self.assertFalse((self.work / "analysis-transaction.json").exists())



    def test_windows_denies_directory_rename_fallback_updates_only_two_files(self):
        import os
        original_rename = Path.rename
        original_replace = os.replace
        targets = []

        def deny_original_directory(source, target):
            if source == self.stage and target.name == ".analysis-previous":
                raise PermissionError(13, "Windows access denied")
            return original_rename(source, target)

        def trace_replace(source, target):
            targets.append(Path(target).name)
            return original_replace(source, target)

        before_transcript = sha256(self.stage / "transcript.json")
        before_markdown = sha256(self.stage / "transcript.md")
        with patch.object(Path, "rename", deny_original_directory), \
             patch("capture.analysis.reviewed.time.sleep", return_value=None), \
             patch("capture.analysis.reviewed.os.replace", trace_replace):
            result = self.attach()
        validate(self.stage, self.job["job_id"], result["hashes"])
        self.assertEqual(sha256(self.stage / "transcript.json"), before_transcript)
        self.assertEqual(sha256(self.stage / "transcript.md"), before_markdown)
        self.assertIn("analysis.md", targets)
        self.assertIn("metadata.json", targets)
        self.assertNotIn("transcript.json", targets)
        self.assertNotIn("transcript.md", targets)
        self.assertEqual(read_json(self.stage / "metadata.json")["analysis_status"], "reviewed")
        for name in (".analysis-next", ".analysis-previous", "analysis-transaction.json"):
            self.assertFalse((self.work / name).exists())

    def test_windows_fallback_denied_before_file_swap_preserves_old(self):
        import os
        original_rename = Path.rename
        original_replace = os.replace

        def deny_directory(source, target):
            if source == self.stage and target.name == ".analysis-previous":
                raise PermissionError(13, "Windows access denied")
            return original_rename(source, target)

        def deny_analysis_replace(source, target):
            if Path(target) == self.stage / "analysis.md":
                raise PermissionError(13, "File access denied")
            return original_replace(source, target)

        with patch.object(Path, "rename", deny_directory), \
             patch("capture.analysis.reviewed.time.sleep", return_value=None), \
             patch("capture.analysis.reviewed.os.replace", deny_analysis_replace):
            with self.assertRaises(PermissionError) as denial:
                self.attach()
        self.assertEqual(denial.exception.substage, "replace_analysis")
        validate(self.stage, self.job["job_id"], self.original_hashes)
        self.assertEqual(read_json(self.work / "job.json")["hashes"], self.original_hashes)
        for name in (".analysis-next", ".analysis-previous", "analysis-transaction.json"):
            self.assertFalse((self.work / name).exists())

    def test_windows_fallback_mid_file_swap_rolls_back(self):
        import os
        original_rename = Path.rename
        original_replace = os.replace
        rejected = {"done": False}

        def deny_directory(source, target):
            if source == self.stage and target.name == ".analysis-previous":
                raise PermissionError(13, "Windows access denied")
            return original_rename(source, target)

        def deny_metadata_once(source, target):
            if Path(target) == self.stage / "metadata.json" and not rejected["done"]:
                rejected["done"] = True
                raise PermissionError(13, "Metadata access denied")
            return original_replace(source, target)

        with patch.object(Path, "rename", deny_directory), \
             patch("capture.analysis.reviewed.time.sleep", return_value=None), \
             patch("capture.analysis.reviewed.os.replace", deny_metadata_once):
            # The replacement helper retries the transient failure and completes.
            result = self.attach()
        self.assertTrue(rejected["done"])
        validate(self.stage, self.job["job_id"], result["hashes"])


    def test_windows_fallback_persistent_metadata_denial_rolls_back_mixed_stage(self):
        import os
        original_rename = Path.rename
        original_replace = os.replace

        def deny_directory(source, target):
            if source == self.stage and target.name == ".analysis-previous":
                raise PermissionError(13, "Windows directory is in use")
            return original_rename(source, target)

        def deny_metadata(source, target):
            if Path(target) == self.stage / "metadata.json":
                raise PermissionError(13, "Metadata file is locked")
            return original_replace(source, target)

        with patch.object(Path, "rename", deny_directory), \
             patch("capture.analysis.reviewed.time.sleep", return_value=None), \
             patch("capture.analysis.reviewed.os.replace", deny_metadata):
            with self.assertRaises(PermissionError):
                self.attach()
        validate(self.stage, self.job["job_id"], self.original_hashes)
        self.assertEqual(read_json(self.work / "job.json")["hashes"], self.original_hashes)
        for name in (".analysis-next", ".analysis-previous", "analysis-transaction.json"):
            self.assertFalse((self.work / name).exists())

    def test_windows_crash_between_two_file_replacements_recovers_original(self):
        import os
        original_rename = Path.rename
        original_replace = os.replace

        def deny_directory(source, target):
            if source == self.stage and target.name == ".analysis-previous":
                raise PermissionError(13, "Windows access denied")
            return original_rename(source, target)

        def terminate_at_metadata(source, target):
            if Path(target) == self.stage / "metadata.json":
                raise SystemExit("simulated process termination")
            return original_replace(source, target)

        with patch.object(Path, "rename", deny_directory), \
             patch("capture.analysis.reviewed.time.sleep", return_value=None), \
             patch("capture.analysis.reviewed.os.replace", terminate_at_metadata):
            with self.assertRaises(SystemExit):
                self.attach()
        self.assertTrue((self.work / "analysis-transaction.json").exists())
        self.assertTrue((self.work / ".analysis-previous").exists())
        recovered = self.call("--resume", self.job["job_id"])
        self.assertEqual(recovered["hashes"], self.original_hashes)
        validate(self.stage, self.job["job_id"], self.original_hashes)
        self.assertFalse((self.work / "analysis-transaction.json").exists())
        self.assertFalse((self.work / ".analysis-next").exists())
        self.assertFalse((self.work / ".analysis-previous").exists())



if __name__ == "__main__":
    unittest.main()
