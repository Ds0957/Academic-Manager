import contextlib
import io
import json
import os
import shutil
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock
import wave

from capture.capture import parser, run
from capture.models import CaptureError
from capture.sources.drive import file_id
from capture.storage.job import job_lock
from capture.media.audio import extract
from capture.media.probe import probe
from capture.storage.metadata import atomic_json, read_json, sha256
from capture.storage.paths import safe_name, clean_media
from capture.storage.publisher import publish, validate
from capture.transcription.formatter import normalize, markdown
from capture.transcription.whisperx_engine import transcribe


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=os.environ.get("CAPTURE_TEST_ROOT"))
        self.root = Path(self.tmp.name)
        self.runtime = self.root / "runtime"
        self.source = self.root / "original.mp4"
        self.source.write_bytes(b"original media must survive")
        self.imported = self.root / "existing.json"
        atomic_json(self.imported, {"language": "es", "segments": [
            {"start": 0, "end": 5, "text": " Texto  de prueba. "}]})

    def tearDown(self):
        self.tmp.cleanup()

    def args(self, *more):
        return parser().parse_args(["--runtime", str(self.runtime), "--semester", "2026-2",
                                    "--subject", "SER (BE-IT)", *more])

    def invoke(self, args):
        with contextlib.redirect_stdout(io.StringIO()):
            return run(args)

    def test_drive_ids_and_host_restriction(self):
        identity = "1SampleDriveFileIdForTests_000001"
        for source in (identity, "https://drive.google.com/file/d/" + identity + "/view",
                       "https://drive.google.com/open?id=" + identity):
            self.assertEqual(file_id(source), identity)
        with self.assertRaises(CaptureError):
            file_id("https://evil.example/file/d/" + identity)

    def test_windows_names(self):
        for name in ("CON.txt", "nul", "../Clase:*? ", "a" * 300, ".", " "):
            result = safe_name(name)
            self.assertLessEqual(len(result), 100)
            self.assertNotIn("/", result)
            self.assertFalse(result.endswith((".", " ")))
        self.assertEqual(safe_name("SER (BE-IT)"), "SER (BE-IT)")

    def test_normalize_and_offsets(self):
        transcript = normalize({"segments": [{"start": 1, "end": 2, "text": " hola  mundo "}]}, 300, 5)
        self.assertEqual(transcript["segments"], [{"start": 301, "end": 302, "text": "hola mundo"}])
        self.assertIn("00:05:01", markdown(transcript, "Clase"))
        for start, end in ((float("nan"), 2), (-1, 2), (3, 2), (True, 2), (0, 100)):
            with self.assertRaises(CaptureError):
                normalize({"segments": [{"start": start, "end": end, "text": "texto"}]}, duration=5)
        with self.assertRaises(CaptureError):
            normalize({"segments": []})

    def test_import_resume_and_original_preserved(self):
        original = self.imported.read_bytes()
        job = self.invoke(self.args("--transcript", str(self.imported)))
        self.assertEqual(job["status"], "completed")
        work = self.runtime / "procesamiento" / job["job_id"]
        validate(work / "artifacts", job["job_id"], job["hashes"])
        resumed = self.invoke(parser().parse_args(["--runtime", str(self.runtime), "--resume", job["job_id"]]))
        self.assertEqual(resumed["hashes"], job["hashes"])
        self.assertEqual(self.imported.read_bytes(), original)

    def test_database_registration_requires_resume(self):
        with self.assertRaises(CaptureError):
            self.invoke(self.args("--transcript", str(self.imported),
                                  "--register-database",
                                  "--subject-id", "MAT-SER",
                                  "--database-state-file-id", "state-file"))

    def test_metadata_does_not_duplicate_database_registration_state(self):
        job = self.invoke(self.args("--transcript", str(self.imported)))
        metadata = read_json(
            self.runtime / "procesamiento" / job["job_id"] / "artifacts" / "metadata.json")
        self.assertNotIn("database_status", metadata)

    def test_text_import_never_invents_timestamps(self):
        text = self.root / "transcript.md"
        text.write_text("Texto existente sin timestamps.", encoding="utf-8")
        job = self.invoke(self.args("--transcript", str(text)))
        result = read_json(self.runtime / "procesamiento" / job["job_id"] / "artifacts" / "transcript.json")
        self.assertIsNone(result["segments"][0]["start"])
        self.assertTrue(result["timestamps_unavailable"])

    def test_tampered_artifacts_block_cleanup(self):
        job = self.invoke(self.args("--transcript", str(self.imported)))
        work = self.runtime / "procesamiento" / job["job_id"]
        temp = self.runtime / "temporales" / job["job_id"]
        (temp / "audio.wav").write_bytes(b"preserve diagnostic audio")
        (work / "artifacts" / "analysis.md").write_text("changed", encoding="utf-8")
        with self.assertRaises(CaptureError):
            self.invoke(parser().parse_args(["--runtime", str(self.runtime), "--resume", job["job_id"]]))
        self.assertTrue((temp / "audio.wav").exists())

    def test_pipeline_failure_resume_and_scoped_cleanup(self):
        def fake_extract(source, target, *args):
            with wave.open(str(target), "wb") as out:
                out.setnchannels(1)
                out.setsampwidth(2)
                out.setframerate(16000)
                out.writeframes(b"\0\0" * 16000)
            return 1
        with patch("capture.capture.probe", return_value={"duration_seconds": 600}), \
                patch("capture.capture.extract", side_effect=fake_extract), \
                patch("capture.capture.transcribe", side_effect=CaptureError("simulated failure")):
            with self.assertRaises(CaptureError):
                self.invoke(self.args("--source", str(self.source), "--duration", "300"))
        work = next((self.runtime / "procesamiento").iterdir())
        job = read_json(work / "job.json")
        temp = self.runtime / "temporales" / job["job_id"]
        self.assertTrue((temp / "source.media").exists())
        self.assertTrue((temp / "audio.wav").exists())
        self.assertEqual(job["last_error"]["failed_after"], "audio_ready")
        (temp / "unrelated.txt").write_text("keep")
        with patch("capture.capture.transcribe", return_value=(
                {"language": "es", "segments": [{"start": 0, "end": 1, "text": "prueba"}]},
                {"engine": "fake"})), patch("capture.capture.probe", return_value={"duration_seconds": 600}):
            job = self.invoke(parser().parse_args(["--runtime", str(self.runtime), "--resume", job["job_id"]]))
        self.assertEqual(job["status"], "completed")
        self.assertFalse((temp / "source.media").exists())
        self.assertFalse((temp / "audio.wav").exists())
        self.assertTrue((temp / "unrelated.txt").exists())
        self.assertEqual(self.source.read_bytes(), b"original media must survive")

    def test_publication_collision_and_revalidation(self):
        drive = self.root / "drive"
        drive.mkdir()
        args = self.args("--transcript", str(self.imported), "--publish", "--drive-root", str(drive))
        first = self.invoke(args)
        second = self.invoke(args)
        self.assertNotEqual(first["publication_path"], second["publication_path"])
        for job in (first, second):
            self.assertEqual(set(p.name for p in Path(job["publication_path"]).iterdir()),
                             {"metadata.json", "transcript.md", "transcript.json", "analysis.md"})
            validate(Path(job["publication_path"]), job["job_id"], job["hashes"])

    def test_fragment_publication_blocked(self):
        with self.assertRaises(CaptureError):
            self.invoke(self.args("--source", str(self.source), "--duration", "300",
                                  "--publish", "--drive-root", str(self.root / "drive")))

    def test_partial_publication_recovers(self):
        job = self.invoke(self.args("--transcript", str(self.imported)))
        stage = self.runtime / "procesamiento" / job["job_id"] / "artifacts"
        drive = self.root / "drive"
        drive.mkdir()
        with patch("capture.storage.publisher.shutil.copyfileobj", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                publish(stage, drive, "2026-2", "SER", "Clase", job, lambda: None)
        destination = Path(job["publication_path"])
        self.assertTrue((destination / ".capture-owner.json").exists())
        publish(stage, drive, "2026-2", "SER", "Clase", job, lambda: None)
        validate(destination, job["job_id"], job["hashes"])
        self.assertFalse((destination / ".capture-owner.json").exists())

    def test_cleanup_rejects_symlink(self):
        identity = "a" * 32
        temp = self.runtime / "temporales" / identity
        temp.mkdir(parents=True)
        try:
            (temp / "source.media").symlink_to(self.source)
        except OSError:
            self.skipTest("El PC no permite crear symlinks sin privilegios.")
        with self.assertRaises(CaptureError):
            clean_media(temp, self.runtime, identity)
        self.assertTrue(self.source.exists())

    def test_cuda_oom_fallback(self):
        torch, whisperx = MagicMock(), MagicMock()
        torch.cuda.is_available.return_value = True
        model = MagicMock()
        model.transcribe.return_value = {"segments": [{"start": 0, "end": 1, "text": "texto"}]}
        whisperx.load_model.side_effect = [RuntimeError("CUDA out of memory"), model]
        with patch.dict(sys.modules, {"torch": torch, "whisperx": whisperx}), \
                contextlib.redirect_stdout(io.StringIO()):
            _, engine = transcribe("unused.wav")
        self.assertEqual(engine["batch_size"], 2)
        self.assertEqual(engine["model"], "large-v3")
        self.assertEqual(len(engine["fallback_attempts"]), 1)

    def test_default_model_is_large_v3_and_model_option_reaches_whisperx(self):
        self.assertEqual(self.args("--source", str(self.source)).model, None)
        torch, whisperx = MagicMock(), MagicMock()
        torch.cuda.is_available.return_value = False
        model = MagicMock()
        model.transcribe.return_value = {"segments": [{"start": 0, "end": 1, "text": "texto"}]}
        whisperx.load_model.return_value = model
        with patch.dict(sys.modules, {"torch": torch, "whisperx": whisperx}), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            _, engine = transcribe("unused.wav")
            self.assertEqual(whisperx.load_model.call_args.args[0], "large-v3")
            self.assertEqual(engine["model"], "large-v3")
            _, engine = transcribe("unused.wav", device="cpu", model="small")
        self.assertEqual(whisperx.load_model.call_args.args[:2], ("small", "cpu"))
        self.assertEqual(engine["model"], "small")
        self.assertIn("WhisperX small: cpu", out.getvalue())

    def test_invalid_model_rejected_and_device_choices_unchanged(self):
        for bad in (["--model", "tiny"], ["--device", "gpu"]):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                self.args("--source", str(self.source), *bad)
        self.assertEqual(self.args("--source", str(self.source)).device, "auto")

    def fake_extract(self, source, target, *args):
        with wave.open(str(target), "wb") as out:
            out.setnchannels(1)
            out.setsampwidth(2)
            out.setframerate(16000)
            out.writeframes(b"\0\0" * 16000)
        return 1

    def test_model_option_recorded_in_request_and_engine(self):
        def fake_transcribe(audio, language, device, cache, progress_callback=None, model="large-v3"):
            return ({"language": "es", "segments": [{"start": 0, "end": 1, "text": "x"}]},
                    {"engine": "whisperx", "model": model, "device": "cpu"})
        with patch("capture.capture.probe", return_value={"duration_seconds": 600}), \
                patch("capture.capture.extract", side_effect=self.fake_extract), \
                patch("capture.capture.transcribe", side_effect=fake_transcribe) as call:
            job = self.invoke(self.args("--source", str(self.source), "--duration", "300",
                                        "--device", "cpu", "--model", "small"))
        self.assertEqual(job["request"]["model"], "small")
        self.assertEqual(job["request"]["device"], "cpu")
        self.assertEqual(job["engine"]["model"], "small")
        self.assertEqual(call.call_args.kwargs["model"], "small")
        self.assertEqual(call.call_args.args[2], "cpu")
        with patch("capture.capture.probe", return_value={"duration_seconds": 600}), \
                patch("capture.capture.extract", side_effect=self.fake_extract), \
                patch("capture.capture.transcribe", side_effect=fake_transcribe):
            job = self.invoke(self.args("--source", str(self.source), "--duration", "300"))
        self.assertEqual(job["request"]["model"], "large-v3")
        self.assertEqual(job["engine"]["model"], "large-v3")

    def test_old_job_without_model_resumes_with_large_v3(self):
        with patch("capture.capture.probe", return_value={"duration_seconds": 600}), \
                patch("capture.capture.extract", side_effect=self.fake_extract), \
                patch("capture.capture.transcribe", side_effect=CaptureError("simulated failure")):
            with self.assertRaises(CaptureError):
                self.invoke(self.args("--source", str(self.source), "--duration", "300"))
        work = next((self.runtime / "procesamiento").iterdir())
        job = read_json(work / "job.json")
        job["request"].pop("model", None)  # job creado antes de --model
        atomic_json(work / "job.json", job)
        with patch("capture.capture.transcribe", return_value=(
                {"language": "es", "segments": [{"start": 0, "end": 1, "text": "prueba"}]},
                {"engine": "fake"})) as call, patch("capture.capture.probe", return_value={"duration_seconds": 600}):
            job = self.invoke(parser().parse_args(["--runtime", str(self.runtime), "--resume", job["job_id"]]))
        self.assertEqual(job["status"], "completed")
        self.assertEqual(call.call_args.kwargs["model"], "large-v3")
        self.assertNotIn("model", job["request"])

    def test_model_only_applies_when_creating_a_source_job(self):
        for more in (["--resume", "a" * 32], ["--status", "latest"],
                     ["--transcript", str(self.imported), "--semester", "2026-2", "--subject", "SER"]):
            args = parser().parse_args(["--runtime", str(self.runtime), "--model", "small", *more])
            with self.assertRaisesRegex(CaptureError, "--model"):
                self.invoke(args)

    def test_cli_failure_has_no_traceback(self):
        result = subprocess.run([sys.executable, "-m", "capture", "--runtime", str(self.runtime),
                                 "--transcript", str(self.root / "missing.json"),
                                 "--semester", "2026-2", "--subject", "SER"],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertNotIn("Traceback", result.stderr)



    def test_job_lock_and_invalid_resume(self):
        directory = self.root / "locked"
        with job_lock(directory):
            with self.assertRaises(CaptureError):
                with job_lock(directory):
                    self.fail("Concurrent job lock accepted")
        with job_lock(directory):
            pass
        with self.assertRaises(CaptureError):
            self.invoke(parser().parse_args(["--runtime", str(self.runtime), "--resume", "../other"]))

    def test_cleanup_rejects_another_directory(self):
        with self.assertRaises(CaptureError):
            clean_media(self.root, self.runtime, "a" * 32)
        self.assertTrue(self.source.exists())

    def test_real_ffmpeg_fragment_when_available(self):
        if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
            self.skipTest("FFmpeg/FFprobe no instalados en este PC.")
        source, target = self.root / "original.wav", self.root / "prepared.wav"
        with wave.open(str(source), "wb") as out:
            out.setnchannels(2)
            out.setsampwidth(2)
            out.setframerate(44100)
            out.writeframes(b"\0\0\0\0" * 44100)
        original_hash = sha256(source)
        self.assertAlmostEqual(probe(source)["duration_seconds"], 1, places=2)
        self.assertAlmostEqual(extract(source, target, .2, .5), .5, places=2)
        with wave.open(str(target), "rb") as out:
            self.assertEqual((out.getnchannels(), out.getframerate(), out.getsampwidth()), (1, 16000, 2))
        self.assertEqual(sha256(source), original_hash)

    def test_cuda_to_cpu_and_unexpected_error_not_retried(self):
        torch, whisperx = MagicMock(), MagicMock()
        torch.cuda.is_available.return_value = True
        model = MagicMock()
        model.transcribe.return_value = {"segments": [{"start": 0, "end": 1, "text": "texto"}]}
        whisperx.load_model.side_effect = [RuntimeError("CUDA out of memory")] * 4 + [model]
        with patch.dict(sys.modules, {"torch": torch, "whisperx": whisperx}), \
                contextlib.redirect_stdout(io.StringIO()):
            _, engine = transcribe("unused.wav")
        self.assertEqual(engine["device"], "cpu")
        self.assertEqual(engine["compute_type"], "int8")
        whisperx.load_model.reset_mock()
        whisperx.load_model.side_effect = ValueError("invalid model")
        with patch.dict(sys.modules, {"torch": torch, "whisperx": whisperx}), \
                contextlib.redirect_stdout(io.StringIO()), self.assertRaises(CaptureError):
            transcribe("unused.wav")
        self.assertEqual(whisperx.load_model.call_count, 1)



    @unittest.skipUnless(os.name == "nt", "Junctions solo aplican a Windows.")
    def test_cleanup_rejects_windows_junction(self):
        identity = "b" * 32
        temp = self.runtime / "temporales" / identity
        temp.parent.mkdir(parents=True)
        other = self.root / "other-job"
        other.mkdir()
        evidence = other / "source.media"
        evidence.write_bytes(b"must survive junction cleanup")
        result = subprocess.run(["cmd", "/c", "mklink", "/J", str(temp), str(other)],
                                capture_output=True)
        if result.returncode:
            self.skipTest("El PC no permite crear junctions.")
        with self.assertRaises(CaptureError):
            clean_media(temp, self.runtime, identity)
        self.assertEqual(evidence.read_bytes(), b"must survive junction cleanup")


class RegisterWithOwnLoginTests(unittest.TestCase):
    """--register-database (vía academic_operations.complete) con el inicio de sesión del asistente."""

    def setUp(self):
        from tests.test_academic_operations import OperationsTests
        OperationsTests.setUp(self)
        self.credential.unlink()  # sin cuenta de servicio, como una persona nueva
        from capture.sources import auth
        self.write_token = auth.write_token_path(self.runtime)
        self.write_token.write_text("{}", encoding="utf-8")
        self.fake = {"class_id": "CLS-FAKE-20261001-01", "already_registered": False}
        # El cargador real necesita las librerías de Google; aquí devuelve una credencial válida.
        loader = patch("capture.sources.auth.load_write_credentials",
                       side_effect=lambda runtime: "creds" if self.write_token.is_file() else None)
        self.loader = loader.start()
        self.addCleanup(loader.stop)

    def complete(self):
        from academic_operations.actions import complete, preview
        draft = preview(self.runtime, self.job_id, "revisado.md")
        return complete(self.runtime, self.job_id, filename="revisado.md", plan_id=draft["plan_id"],
                        approved=True, index_builder=lambda *a, **kw: {})

    def test_write_login_registers_without_touching_adc(self):
        seen = []
        def register(*args, **kwargs):
            seen.append(os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"))
            return self.fake
        with patch.dict(os.environ, {}, clear=False), \
                patch("capture.academic_manager.drive_workbook.user_service", return_value="user-api") as user, \
                patch("capture.academic_manager.database.register_class", side_effect=register) as reg:
            os.environ.pop("GOOGLE_APPLICATION_CREDENTIALS", None)
            result = self.complete()
        self.assertTrue(result["database_registered"])
        user.assert_called_once_with(self.runtime.resolve())
        self.assertEqual(reg.call_args.kwargs, {"api": "user-api"})
        self.assertEqual(seen, [None])

    def test_service_account_path_is_unchanged(self):
        """Regresión: con la cuenta de servicio, ADC apunta a ella durante el registro y luego se restaura."""
        self.write_token.unlink()
        self.credential.write_text("{}", encoding="utf-8")
        seen = []
        def register(*args, **kwargs):
            seen.append((os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"), kwargs))
            return self.fake
        with patch.dict(os.environ, {"GOOGLE_APPLICATION_CREDENTIALS": "previa"}), \
                patch("capture.academic_manager.drive_workbook.user_service") as user, \
                patch("capture.academic_manager.database.register_class", side_effect=register):
            self.complete()
            self.assertEqual(os.environ["GOOGLE_APPLICATION_CREDENTIALS"], "previa")
        self.assertEqual(seen, [(str(self.credential.resolve()), {})])
        user.assert_not_called()

    def test_service_account_wins_over_write_login(self):
        self.credential.write_text("{}", encoding="utf-8")
        with patch("capture.academic_manager.drive_workbook.user_service") as user, \
                patch("capture.academic_manager.database.register_class", return_value=self.fake) as reg:
            self.complete()
        user.assert_not_called()
        self.assertEqual(reg.call_args.kwargs, {})

    def test_without_any_credential_the_error_is_unchanged(self):
        from academic_operations.actions import OperationError
        self.write_token.unlink()
        with self.assertRaises(OperationError) as cm:
            self.complete()
        self.assertEqual(cm.exception.code, "CREDENTIAL_UNAVAILABLE")
        self.assertEqual(str(cm.exception), "No existe la credencial local autorizada de publicación.")

    def test_write_login_without_drive_permission_is_explained_before_capture(self):
        from academic_operations.actions import OperationError
        from capture.sources import auth
        self.loader.side_effect = CaptureError(auth.MISSING_WRITE)
        with patch("academic_operations.actions._capture", wraps=__import__(
                "academic_operations.actions", fromlist=["_capture"])._capture) as capture, \
                patch("capture.academic_manager.database.register_class") as reg:
            with self.assertRaises(OperationError) as cm:
                self.complete()
        self.assertEqual(cm.exception.code, "CREDENTIAL_UNAVAILABLE")
        self.assertIn("no tiene permiso para guardar en Drive", str(cm.exception))
        self.assertFalse(any("--register-database" in c.args for c in capture.call_args_list))
        reg.assert_not_called()

    def test_dangling_service_account_link_is_refused_like_capture(self):
        from academic_operations.actions import OperationError
        self.credential.symlink_to(self.root / "no-existe.json")
        with patch("capture.academic_manager.database.register_class") as reg, \
                self.assertRaises(OperationError) as cm:
            self.complete()
        self.assertEqual(cm.exception.code, "CREDENTIAL_UNAVAILABLE")
        self.assertEqual(str(cm.exception), "No existe la credencial local autorizada de publicación.")
        reg.assert_not_called()
        self.loader.assert_not_called()

    def test_cli_without_any_credential_keeps_its_message(self):
        self.write_token.unlink()
        args = parser().parse_args(["--runtime", str(self.runtime), "--resume", self.job_id,
                                    "--register-database", "--subject-id", "MAT-FAKE",
                                    "--database-state-file-id", "1SampleDatabaseStateId_Tests00001"])
        job = read_json(self.work / "job.json")
        job["publication_path"] = str(self.root / "pub")
        atomic_json(self.work / "job.json", job)
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(CaptureError) as cm:
            run(args)
        self.assertEqual(str(cm.exception), "No existe la credencial local del Drive personal.")

    def test_explicit_workbook_credentials_never_fall_back(self):
        args = parser().parse_args(["--runtime", str(self.runtime), "--resume", self.job_id,
                                    "--register-database", "--subject-id", "MAT-FAKE",
                                    "--database-state-file-id", "1SampleDatabaseStateId_Tests00001",
                                    "--workbook-credentials", str(self.root / "missing.json")])
        job = read_json(self.work / "job.json")
        job["publication_path"] = str(self.root / "pub")
        atomic_json(self.work / "job.json", job)
        with patch("capture.academic_manager.drive_workbook.user_service") as user, \
                contextlib.redirect_stdout(io.StringIO()), self.assertRaises(CaptureError) as cm:
            run(args)
        self.assertEqual(str(cm.exception), "No existe la credencial local del Drive personal.")
        user.assert_not_called()


if __name__ == "__main__":
    unittest.main()