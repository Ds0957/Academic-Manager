"""Capture progress tests with synthetic jobs, no GPU, Drive, or Excel writes."""
from datetime import datetime, timedelta, timezone
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch, MagicMock
import sys

from capture.storage.metadata import atomic_json
from capture.storage.monitor import job_summary, list_jobs, print_status, record_event
from capture.storage.progress import ProgressReporter
from capture.transcription.whisperx_engine import transcribe
from academic_access import core


class ProgressTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.jobid="8"*32
        self.jobdir=self.root/"procesamiento"/self.jobid
        self.jobdir.mkdir(parents=True)
        self.job={"schema_version":1,"job_id":self.jobid,"status":"audio_ready",
                  "updated_at":datetime.now(timezone.utc).isoformat(),
                  "request":{"subject":"SO2","class_date":"2026-09-29"},
                  "media":{"duration_seconds":3346.4}}
        atomic_json(self.jobdir/"job.json",self.job)
        record_event(self.jobdir,self.jobid,"transcribing")

    def tearDown(self):
        self.temp.cleanup()

    def test_callback_percentage_and_eta(self):
        timeline=[0.]
        reporter=ProgressReporter(self.jobdir,self.jobid,clock=lambda: timeline[0])
        self.assertEqual(json.loads((self.jobdir/"progress.json").read_text())["percent_phase"],0)
        timeline[0]=60
        reporter.update(25)
        progress=json.loads((self.jobdir/"progress.json").read_text())
        self.assertEqual(progress["percent_phase"],25)
        self.assertEqual(progress["estimated_remaining_seconds"],180)
        self.assertEqual(progress["estimate_basis"],"observed_vad_chunk_rate")
        timeline[0]=90
        reporter.update(50)
        self.assertEqual(json.loads((self.jobdir/"progress.json").read_text())["estimated_remaining_seconds"],90)
        summary=job_summary(self.root,self.jobid)
        self.assertEqual(summary["phase_percent"],50)
        self.assertEqual(summary["estimated_remaining_seconds"],90)
        self.assertTrue(summary["signal_recent"])

    def test_completed_does_not_reuse_stale_eta(self):
        reporter=ProgressReporter(self.jobdir,self.jobid)
        reporter.update(50,force=True)
        self.job["status"]="completed"
        atomic_json(self.jobdir/"job.json",self.job)
        record_event(self.jobdir,self.jobid,"completed")
        summary=job_summary(self.root,self.jobid)
        self.assertEqual(summary["current_phase"],"completed")
        self.assertIsNone(summary["phase_percent"])
        self.assertIsNone(summary["estimated_remaining_seconds"])
        self.assertFalse(summary["published"])
        self.assertFalse(summary["database_registered"])

    def test_non_recent_progress_does_not_claim_eta(self):
        outdated={"schema_version":1,"job_id":self.jobid,"stage":"transcribing",
                  "percent_phase":55,"estimated_remaining_seconds":120,
                  "elapsed_seconds":500,"updated_at_utc":"2020-01-01T00:00:00Z"}
        atomic_json(self.jobdir/"progress.json",outdated)
        s=job_summary(self.root,self.jobid)
        self.assertEqual(s["phase_percent"],55)
        self.assertFalse(s["signal_recent"])
        self.assertIsNone(s["estimated_remaining_seconds"])

    def test_status_all_and_coordinator_contract_are_read_only(self):
        with redirect_stdout(io.StringIO()) as stream:
            print_status(self.root,"all")
        self.assertIn("SO2",stream.getvalue())
        self.assertEqual(len(list_jobs(self.root)),1)
        response=core.capture_jobs(self.root,identity=self.jobid)
        self.assertEqual(response["trabajos"][0]["job_id"],self.jobid)
        self.assertNotIn("request",response["trabajos"][0])
        self.assertIn("capture-jobs",core.capabilities()["commands"])

    def test_whisperx_callback_forwarded_without_changing_inference(self):
        torch,whisperx=MagicMock(),MagicMock()
        torch.cuda.is_available.return_value=True
        model=MagicMock()
        model.transcribe.return_value={"segments":[{"start":0,"end":1,"text":"texto"}]}
        whisperx.load_model.return_value=model
        callback=MagicMock()
        with patch.dict(sys.modules,{"torch":torch,"whisperx":whisperx}):
            with redirect_stdout(io.StringIO()):
                output,_=transcribe("fake.wav",progress_callback=callback)
        self.assertIn("segments",output)
        self.assertIs(model.transcribe.call_args.kwargs["progress_callback"],callback)


if __name__=="__main__":
    unittest.main()
