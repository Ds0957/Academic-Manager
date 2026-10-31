"""Material de estudio en Capture y continuidad del flujo de clases."""
import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from capture.capture import main, parser, run
from capture.models import CaptureError
from capture.storage.metadata import atomic_json, read_json
from capture.storage.publisher import validate
from tests.test_capture_youtube import FakeYoutube, URL, VIDEO, fake_extract, info
from capture.sources import youtube


class MaterialTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.runtime = self.root / "runtime"
        self.drive = self.root / "drive"
        self.drive.mkdir()
        self.media = self.root / "downloads" / "video apoyo.mp4"
        self.media.parent.mkdir()
        self.media.write_bytes(b"local media must survive")
        self.imported = self.root / "existing.json"
        atomic_json(self.imported, {"language": "es", "segments": [
            {"start": 0, "end": 5, "text": "Texto de prueba."}]})

    def tearDown(self):
        self.tmp.cleanup()

    def args(self, *more):
        return parser().parse_args(["--runtime", str(self.runtime), "--semester", "2026-2",
                                    "--subject", "Física", *more])

    def invoke(self, args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            job = run(args)
        return job, out.getvalue()

    def whisperx(self):
        raw = {"language": "es", "segments": [{"start": 0, "end": 1, "text": "termodinámica básica"}]}
        return (patch("capture.capture.probe", return_value={"duration_seconds": 600}),
                patch("capture.capture.extract", side_effect=fake_extract),
                patch("capture.capture.transcribe", return_value=(raw, {"engine": "fake"})))

    def test_local_file_as_material_published_in_material_folder(self):
        probe, extract, transcribe = self.whisperx()
        with probe, extract, transcribe:
            job, output = self.invoke(self.args("--source", str(self.media), "--tipo", "material",
                                                "--subject-id", "MAT-FIS", "--publish",
                                                "--drive-root", str(self.drive)))
        self.assertEqual(job["status"], "completed")
        self.assertEqual(job["request"]["tipo"], "material")
        self.assertEqual(job["request"]["subject_id"], "MAT-FIS")
        self.assertIsNone(job["request"]["class_date"])
        self.assertNotIn("database_registration", job)
        target = Path(job["publication_path"])
        self.assertEqual(target.parent, self.drive / "2026-2" / "Física" / "Material")
        self.assertFalse((self.drive / "2026-2" / "Física" / "Clases").exists())
        validate(target, job["job_id"], job["hashes"])
        metadata = read_json(target / "metadata.json")
        self.assertEqual(metadata["content_type"], "Material")
        self.assertEqual(metadata["subject_id"], "MAT-FIS")
        self.assertIsNone(metadata["class_date"])
        self.assertNotIn("CLS-", output + str(metadata))
        self.assertEqual(self.media.read_bytes(), b"local media must survive")

    def test_youtube_as_material(self):
        fake = FakeYoutube(info())
        with patch.object(youtube, "_client", fake):
            job, _ = self.invoke(self.args("--source", URL, "--tipo", "material", "--subject-id", "MAT-FIS",
                                           "--publish", "--drive-root", str(self.drive)))
        metadata = read_json(Path(job["publication_path"]) / "metadata.json")
        self.assertEqual((metadata["content_type"], metadata["source"]["video_id"]), ("Material", VIDEO))
        self.assertEqual(Path(job["publication_path"]).parent.name, "Material")

    def test_material_requires_subject_id_and_rejects_class_date(self):
        for more, message in ((("--tipo", "material"), "--subject-id"),
                              (("--tipo", "material", "--subject-id", "fisica"), "--subject-id"),
                              (("--tipo", "material", "--subject-id", "MAT-FIS", "--class-date", "2026-10-01"),
                               "fecha de clase")):
            with self.assertRaisesRegex(CaptureError, message):
                self.invoke(self.args("--source", str(self.media), *more))
        self.assertFalse((self.runtime / "procesamiento").exists() and
                         any(p.is_dir() for p in (self.runtime / "procesamiento").iterdir()
                             if len(p.name) == 32))

    def test_material_is_never_registered_as_class(self):
        job, _ = self.invoke(self.args("--transcript", str(self.imported), "--tipo", "material",
                                       "--subject-id", "MAT-FIS", "--publish", "--drive-root", str(self.drive)))
        err = io.StringIO()
        with patch("capture.academic_manager.database.register_class",
                   side_effect=AssertionError("no registration")), \
                contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            code = main(["--runtime", str(self.runtime), "--resume", job["job_id"], "--register-database",
                         "--subject-id", "MAT-FIS", "--database-state-file-id", "state"])
        self.assertEqual(code, 1)
        self.assertIn("no se registra como clase", err.getvalue())

    def test_class_flow_unchanged_for_local_files(self):
        probe, extract, transcribe = self.whisperx()
        with probe, extract, transcribe:
            job, _ = self.invoke(self.args("--source", str(self.media), "--class-date", "2026-10-01",
                                           "--publish", "--drive-root", str(self.drive)))
        self.assertNotIn("tipo", job["request"])
        self.assertNotIn("subject_id", job["request"])
        self.assertEqual(job["request"]["source"], str(self.media.resolve()))
        target = Path(job["publication_path"])
        self.assertEqual(target.parent, self.drive / "2026-2" / "Física" / "Clases")
        metadata = read_json(target / "metadata.json")
        self.assertEqual((metadata["content_type"], metadata["class_date"]), ("Clase", "2026-10-01"))
        self.assertNotIn("subject_id", metadata)
        self.assertEqual(metadata["source"]["kind"], "local")

    def test_drive_url_and_id_still_use_drive(self):
        identity = "1SampleDriveFileIdForTests_000001"
        for source in (identity, "https://drive.google.com/file/d/" + identity + "/view"):
            seen = []
            with patch("capture.capture.drive.service", return_value="api"), \
                    patch("capture.capture.drive.inspect",
                          side_effect=lambda src, api: seen.append(src) or (_ for _ in ()).throw(
                              CaptureError("drive simulado"))), \
                    patch.object(youtube, "_client", side_effect=AssertionError("no YouTube")):
                with self.assertRaisesRegex(CaptureError, "drive simulado"):
                    self.invoke(self.args("--source", source))
            self.assertEqual(seen, [source])
            job = read_json(sorted((self.runtime / "procesamiento").glob("*/job.json"),
                                   key=lambda p: p.stat().st_mtime_ns)[-1])
            self.assertEqual(job["request"]["source"], source)


if __name__ == "__main__":
    unittest.main()
