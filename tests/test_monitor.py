"""Checkpoint visibility and safe atomic JSON updates."""
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from capture.capture import parser, run
from capture.models import CaptureError
from capture.storage.job import save
from capture.storage.metadata import atomic_json, read_json
from capture.storage.monitor import inspect_job, print_status, record_event


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(dir=os.environ.get("CAPTURE_TEST_ROOT"))
        self.root = Path(self.folder.name)
        self.runtime = self.root / "runtime"
        self.work = self.runtime / "procesamiento" / ("a" * 32)
        self.work.mkdir(parents=True)

    def tearDown(self):
        self.folder.cleanup()

    def test_events_survive_and_status_never_modifies_job(self):
        job = {"schema_version": 1, "job_id": "a" * 32, "status": "created"}
        save(self.work, job)
        record_event(self.work, job["job_id"], "transcribing")
        save(self.work, job, "completed")
        before = (self.work / "job.json").read_bytes()
        received, events = inspect_job(self.runtime, "latest")
        self.assertEqual(received["status"], "completed")
        self.assertEqual([e["stage"] for e in events],
                         ["created", "transcribing", "completed"])
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertIsNone(run(parser().parse_args([
                "--runtime", str(self.runtime), "--status", "latest"])))
        self.assertIn("transcribing", output.getvalue())
        self.assertIn("Estado: completed", output.getvalue())
        self.assertEqual(before, (self.work / "job.json").read_bytes())
        self.assertEqual(len((self.work / "events.jsonl").read_text().splitlines()), 3)

    def test_status_rejects_invalid_or_missing_job(self):
        with self.assertRaises(CaptureError):
            inspect_job(self.runtime, "../outside")
        with self.assertRaises(CaptureError):
            inspect_job(self.runtime, "latest")

    def test_atomic_json_retries_short_permission_denial(self):
        target = self.root / "existing.json"
        atomic_json(target, {"value": "before"})
        original_replace = os.replace
        calls = []

        def temporary_denial(src, dst):
            calls.append(dst)
            if len(calls) <= 2:
                raise PermissionError(13, "simulated Windows file in use")
            return original_replace(src, dst)

        with patch("capture.storage.metadata.os.replace", side_effect=temporary_denial), \
             patch("capture.storage.metadata.time.sleep") as sleep:
            atomic_json(target, {"value": "after"})
        self.assertEqual(len(calls), 3)
        self.assertEqual(sleep.call_count, 2)
        self.assertEqual(read_json(target), {"value": "after"})
        self.assertEqual(list(self.root.glob("*.tmp")), [])

    def test_atomic_json_keeps_original_on_persistent_denial(self):
        target = self.root / "existing.json"
        atomic_json(target, {"safe": True})
        original = target.read_bytes()
        with patch("capture.storage.metadata.os.replace",
                   side_effect=PermissionError(13, "simulated persistent denial")), \
             patch("capture.storage.metadata.time.sleep") as sleep:
            with self.assertRaises(PermissionError):
                atomic_json(target, {"safe": False})
        self.assertEqual(sleep.call_count, 4)
        self.assertEqual(target.read_bytes(), original)
        self.assertEqual(list(self.root.glob("*.tmp")), [])

    def test_partial_event_line_is_not_used_as_state(self):
        job = {"schema_version": 1, "job_id": "a" * 32, "status": "created"}
        save(self.work, job)
        with (self.work / "events.jsonl").open("a", encoding="utf-8") as stream:
            stream.write('{"at":')
        received, events = inspect_job(self.runtime, job["job_id"])
        self.assertEqual(received["status"], "created")
        self.assertEqual(len(events), 1)


if __name__ == "__main__":
    unittest.main()
