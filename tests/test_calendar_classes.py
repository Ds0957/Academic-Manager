"""Horario y grabaciones desde Google Calendar (odd/tasks/clases-automaticas.md, S5)."""
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from academic_operations import auto_classes as auto
from academic_operations import calendar_classes as cal
from capture.models import CaptureError
from tests.test_auto_classes import VIDEO, FakeDrive

SUBJECTS = {"MAT-SO2": "Sistemas Operativos II", "MAT-GBD": "Gestión de Bases de Datos",
            "MAT-ST": "Sistemas Transaccionales", "MAT-ING3": "Inglés III"}
REC = "1SampleDriveFileIdForTests_000001"


def event(summary, start, end=None, attachments=None, all_day=False):
    when = {"date": start} if all_day else {"dateTime": start}
    found = {"summary": summary, "start": when, "end": {"dateTime": end or start}}
    if attachments:
        found["attachments"] = attachments
    return found


EVENTS = [
    event("Sesión Sistemas Operativos II", "2026-10-27T20:00:00-05:00", "2026-10-27T21:00:00-05:00",
          [{"fileId": REC, "title": "Sesión Sistemas Operativos II - 2026/10/27 19:55 GMT-05:00 - Recording",
            "mimeType": "video/mp4"},
           {"fileId": "1SampleDriveFileIdForTests_000002", "title": "Notas de Gemini", "mimeType":
            "application/vnd.google-apps.document"}]),
    event("4146_GESTION DE BASES DE DATOS", "2026-10-29T18:00:00-05:00", "2026-10-29T19:00:00-05:00"),
    event("NRC 4161 TIC00036 SISTEMAS TRANSACCIONALES G-61", "2026-10-30T02:00:00Z"),  # 21:00 del 29 en Bogotá
    event("English III NRC 4199", "2026-11-04T17:51:00-05:00"),
    event("Día de las brujas", "2026-10-31", all_day=True),
    event("Cumpleaños de alguien", "2026-11-01T10:00:00-05:00"),
]


class FakeCalendar:
    def __init__(self, pages):
        self.pages, self.calls = pages, []

    def events(self):
        return self

    def list(self, **kw):
        self.calls.append(kw)
        self.page = self.pages[len(self.calls) - 1]
        return self

    def execute(self):
        if isinstance(self.page, Exception):
            raise self.page
        return self.page


class Base(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.runtime = Path(folder.name)
        patcher = patch.object(cal, "_subjects", return_value=dict(SUBJECTS))
        patcher.start()
        self.addCleanup(patcher.stop)


class FetchTests(Base):
    def test_class_events_are_matched_to_their_subject_with_local_dates(self):
        api = FakeCalendar([{"items": EVENTS[:3], "nextPageToken": "p2"}, {"items": EVENTS[3:]}])
        summary = cal.fetch(self.runtime, api=api, on=date(2026, 11, 12))
        self.assertEqual(summary, {"estado": "ok", "eventos": 4, "grabaciones": 1})
        self.assertEqual(api.calls[0]["timeMin"], "2026-10-22T00:00:00-05:00")
        self.assertEqual(api.calls[0]["timeMax"], "2026-11-20T00:00:00-05:00")
        self.assertTrue(api.calls[0]["singleEvents"])
        self.assertEqual(api.calls[1]["pageToken"], "p2")
        events = cal.events(self.runtime)
        self.assertEqual([(e["materia_id"], e["fecha"], e["inicio"]) for e in events], [
            ("MAT-SO2", "2026-10-27", "20:00"), ("MAT-GBD", "2026-10-29", "18:00"),
            ("MAT-ST", "2026-10-29", "21:00"), ("MAT-ING3", "2026-11-04", "17:51")])
        self.assertEqual(events[0]["grabaciones"], [{"file_id": REC, "nombre": EVENTS[0]["attachments"][0]["title"]}])

    def test_without_login_or_permission_nothing_is_read(self):
        with patch("capture.sources.auth.load_calendar_credentials", return_value=None):
            self.assertEqual(cal.fetch(self.runtime)["estado"], "sin_sesion")
        with patch("capture.sources.auth.load_calendar_credentials",
                   side_effect=CaptureError("Su sesión de Google no tiene permiso para leer Google Calendar.")):
            result = cal.fetch(self.runtime)
        self.assertEqual(result["estado"], "sin_permiso")
        self.assertIn("Calendar", result["mensaje"])

    def test_a_calendar_failure_keeps_the_previous_schedule(self):
        cal.fetch(self.runtime, api=FakeCalendar([{"items": EVENTS}]), on=date(2026, 11, 12))
        result = cal.fetch(self.runtime, api=FakeCalendar([RuntimeError("HTTP 500")]), on=date(2026, 11, 12))
        self.assertEqual(result["estado"], "error")
        self.assertEqual(len(cal.events(self.runtime)), 4)

    def test_a_damaged_event_is_skipped_and_the_rest_is_read(self):
        damaged = [event("Sesión Sistemas Operativos II", "no es una fecha"),
                   {"summary": "Sesión Sistemas Operativos II", "start": {"dateTime": None}},
                   {"summary": "English III", "start": {"dateTime": "2026-11-04T17:51:00-05:00"}, "end": "roto",
                    "attachments": "roto"}, "roto"]
        summary = cal.fetch(self.runtime, api=FakeCalendar([{"items": damaged + EVENTS[:1]}]), on=date(2026, 11, 12))
        self.assertEqual(summary["estado"], "ok")
        self.assertEqual([(e["materia_id"], e["fecha"], e["fin"]) for e in cal.events(self.runtime)],
                         [("MAT-ING3", "2026-11-04", None), ("MAT-SO2", "2026-10-27", "21:00")])


class LastResultTests(Base):
    def test_the_last_result_is_kept_for_the_wizard(self):
        self.assertIsNone(cal.last(self.runtime))
        with patch("capture.sources.auth.load_calendar_credentials",
                   side_effect=CaptureError("Su sesión de Google no tiene permiso para leer Google Calendar.")):
            cal.fetch(self.runtime)
        self.assertEqual(cal.last(self.runtime)["estado"], "sin_permiso")
        cal.fetch(self.runtime, api=FakeCalendar([{"items": EVENTS}]), on=date(2026, 11, 12))
        self.assertEqual(cal.last(self.runtime), {"estado": "ok", "eventos": 4, "grabaciones": 1})


class RecordingsFromCalendarTests(Base):
    def test_attached_recordings_join_the_automatic_list(self):
        cal.fetch(self.runtime, api=FakeCalendar([{"items": EVENTS}]), on=date(2026, 11, 12))
        with patch.object(auto, "_courses", return_value={}), patch.object(auto, "_registered", return_value=set()):
            summary = auto.detect(self.runtime, api=FakeDrive({REC: VIDEO}))
        entry = auto.found(self.runtime)[REC]
        self.assertEqual((entry["materia_id"], entry["fecha"], entry["fuente"], entry["descargable"]),
                         ("MAT-SO2", "2026-10-27", "Google Calendar", True))
        self.assertEqual(summary["encontradas"], 1)

    def test_canvas_wins_when_calendar_has_the_same_recording(self):
        cal.fetch(self.runtime, api=FakeCalendar([{"items": EVENTS}]), on=date(2026, 11, 12))
        path = self.runtime / "procesamiento" / "canvas-sync" / "v01" / "91005" / "latest.json"
        path.parent.mkdir(parents=True)
        text = f"26 de octubre\nhttps://drive.google.com/file/d/{REC}/view"
        path.write_text(json.dumps({"captured_at_utc": "2026-11-12T13:00:00Z", "data": {"assignments": [], "sections": {
            "discussion_topics": {"status": "ok", "data": [{"id": 1, "title": "Grabaciones", "instructions":
                                                           {"text": text, "links": []}}]}}}}), encoding="utf-8")
        with patch.object(auto, "_courses", return_value={"91005": "MAT-SER"}), \
                patch.object(auto, "_registered", return_value=set()):
            summary = auto.detect(self.runtime, api=FakeDrive({REC: VIDEO}))
        entry = auto.found(self.runtime)[REC]
        self.assertEqual((entry["materia_id"], entry["fecha"], entry["fuente"]), ("MAT-SER", "2026-10-26", "Grabaciones"))
        self.assertEqual(summary["encontradas"], 1)


if __name__ == "__main__":
    unittest.main()
