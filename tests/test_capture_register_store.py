"""Registro de clases de Capture sobre la base SQLite con Drive simulado."""
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from academic_store import store
from tests.xlsx_fixture import write_sheets
from capture.academic_manager import database as db
from capture.models import CaptureError
from capture.storage.metadata import sha256
from tests.test_academic_store import workbook

EXISTING = ["CLS-SER-20261001-01", "MAT-SER", 46296.0, None, None, "SER grabación", None,
            "realizada", None, "https://drive.example/rec-1", "procesada", "t.md", "a.md", True,
            "Google Drive", "verificada", None]


def job(recording="https://drive.example/rec-2", date="2026-10-01"):
    return {"job_id": "job-1", "publication_path": "pub/2026-10-01-ser",
            "request": {"class_date": date, "semester": "2026-2", "subject": "SER"},
            "source_metadata": {"original_name": "SER - 2026/10/01", "location": recording,
                                "kind": "drive"},
            "analysis_status": "completed_reviewed", "engine": {}, "media": {}}


class CaptureRegisterStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "runtime"
        self.root.mkdir()
        source = Path(self.tmp.name) / "source.xlsx"
        write_sheets(source, workbook(**{"03 - Clases": [list(db.CLASS_HEADERS), EXISTING]}))
        self.shared = Path(self.tmp.name) / "shared.sqlite"
        store.migrate(source, self.shared)
        self.state = {"status": "free", "lock": None, "canonical_drive_file_id": "file",
                      "published": {"sha256": sha256(self.shared)}}
        self.uploads = []

    def tearDown(self):
        self.tmp.cleanup()

    def register(self, record_job, **kw):
        def read_json(api, file_id, target):
            return json.loads(json.dumps(self.state))

        def write_json(api, file_id, obj, target):
            self.state = json.loads(json.dumps(obj))
            return {"id": file_id}

        def download(api, file_id, target):
            shutil.copyfile(self.shared, target)
            return {"id": file_id}

        def upload(api, file_id, source, mime):
            self.uploads.append(mime)
            shutil.copyfile(source, self.shared)
            return {"modifiedTime": "simulado"}

        with patch.object(db.drive_workbook, "service", return_value=object()) as self.service, \
             patch.object(db.drive_workbook, "read_json_file", side_effect=read_json), \
             patch.object(db.drive_workbook, "write_json_file", side_effect=write_json), \
             patch.object(db.drive_workbook, "download", side_effect=download), \
             patch.object(db.drive_workbook, "upload", side_effect=upload):
            return db.register_class(self.root, record_job, "MAT-SER", "state", machine="PC-TEST", **kw)

    def test_new_class_is_published_with_lock_and_redownload(self):
        result = self.register(job())
        self.assertEqual(result["class_id"], "CLS-SER-20261001-02")
        self.assertFalse(result["already_registered"])
        self.assertEqual(self.uploads, ["application/vnd.sqlite3"])
        self.assertEqual((self.state["status"], self.state["lock"]), ("free", None))
        self.assertEqual(self.state["published"]["sha256"], sha256(self.shared))
        self.assertEqual(sha256(self.root / store.DB_NAME), sha256(self.shared))
        self.assertTrue(Path(result["checkpoint"]).name.endswith("-before-class.sqlite"))
        classes = store.read_catalog(self.shared, ("03 - Clases",))["03 - Clases"]
        self.assertEqual([c["clase_id"] for c in classes],
                         ["CLS-SER-20261001-01", "CLS-SER-20261001-02"])
        added = classes[1]
        self.assertEqual((added["fecha"], added["procesada"], added["url_grabacion"]),
                         (46296.0, True, "https://drive.example/rec-2"))
        self.assertEqual(added["transcripcion"], "2026-2/SER/Clases/2026-10-01-ser/transcript.md")
        self.assertEqual(store.revision(self.shared), 2)

    def test_given_client_is_used_instead_of_adc(self):
        result = self.register(job(), api="user-api")
        self.assertFalse(result["already_registered"])
        self.service.assert_not_called()
        self.register(job())
        self.service.assert_called_once_with(self.root.resolve())

    def test_same_recording_is_not_registered_twice(self):
        before = sha256(self.shared)
        result = self.register(job(recording="https://drive.example/rec-1"))
        self.assertEqual(result, {"class_id": "CLS-SER-20261001-01", "already_registered": True,
                                  "sha256": before, "size_bytes": self.shared.stat().st_size,
                                  "drive_modified_time": None, "checkpoint": None})
        self.assertEqual(self.uploads, [])
        self.assertEqual(sha256(self.shared), before)
        self.assertEqual(self.state["status"], "free")

    def test_locked_state_refuses_without_writing(self):
        self.state = {**self.state, "status": "editing", "lock": {"machine": "OTRO"}}
        with self.assertRaisesRegex(CaptureError, "bloqueado"):
            self.register(job())
        self.assertEqual(self.uploads, [])

    def test_validate_rejects_a_store_with_other_class_columns(self):
        source = Path(self.tmp.name) / "other.xlsx"
        write_sheets(source, workbook(**{"03 - Clases": [["clase_id", "materia_id"]]}))
        other = Path(self.tmp.name) / "other.sqlite"
        store.migrate(source, other)
        with self.assertRaisesRegex(CaptureError, "contrato V2"):
            db.validate_workbook(other)
        with self.assertRaisesRegex(CaptureError, "no aparece"):
            db.validate_workbook(self.shared, "CLS-NO-EXISTE")


if __name__ == "__main__":
    unittest.main()
