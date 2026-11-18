"""Clases automáticas: encontrar grabaciones, ponerlas en cola y transcribirlas solas
(odd/tasks/clases-automaticas.md, S2–S3)."""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from capture.storage.job import job_lock
from academic_operations import auto_classes as auto
from academic_operations import class_capture as cc
from academic_operations.actions import OperationError
from canvas_sync.common import SyncError
from tests.test_class_capture import Popen

IDS = {n: f"1SampleDriveFileIdForTests_0000{n:02d}" for n in range(1, 8)}
LINK = "https://drive.google.com/file/d/{}/view"


def forum(*pairs):
    text = "\n".join(f"{day}\n{LINK.format(IDS[n])}?usp=sharing" for day, n in pairs)
    return {"captured_at_utc": "2026-11-12T13:00:00Z",
            "data": {"assignments": [], "sections": {"discussion_topics": {"status": "ok", "data": [
                {"id": 1, "title": "Grabaciones", "instructions": {"text": text, "links": []}}]}}}}


class FakeDrive:
    """files().get(...).execute() de la API de Drive, con un archivo por ID."""

    def __init__(self, files):
        self.files_by_id, self.calls = files, []

    def files(self):
        return self

    def get(self, fileId, **_):
        self.calls.append(fileId)
        self.current = fileId
        return self

    def execute(self):
        found = self.files_by_id[self.current]
        if isinstance(found, Exception):
            raise found
        return {"id": self.current, **found}


VIDEO = {"name": "Clase - 2026/10/01 - Recording", "mimeType": "video/mp4", "capabilities": {"canDownload": True}}


class Base(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.runtime = Path(folder.name)
        for target, value in (("_courses", {"91005": "MAT-SER"}), ("_registered", set())):
            patcher = patch.object(auto, target, return_value=value)
            setattr(self, target.strip("_"), patcher.start())
            self.addCleanup(patcher.stop)

    def snapshot(self, course, data):
        path = self.runtime / "procesamiento" / "canvas-sync" / "v01" / course / "latest.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")

    def found(self):
        return auto.found(self.runtime)


class DetectTests(Base):
    def test_each_recording_is_checked_once_in_drive_and_kept(self):
        self.snapshot("91005", forum(("1 de octubre", 1), ("8 de octubre", 2), ("15 de octubre", 3),
                                     ("22 de octubre", 4), ("29 de octubre", 5)))
        drive = FakeDrive({IDS[1]: VIDEO, IDS[2]: {**VIDEO, "capabilities": {"canDownload": False}},
                           IDS[3]: {**VIDEO, "mimeType": "application/pdf"},
                           IDS[4]: {**VIDEO, "mimeType": "application/vnd.google-apps.document"},
                           IDS[5]: RuntimeError("HTTP 500")})
        summary = auto.detect(self.runtime, api=drive)
        found = self.found()
        self.assertEqual((found[IDS[1]]["materia_id"], found[IDS[1]]["fecha"], found[IDS[1]]["descargable"]),
                         ("MAT-SER", "2026-10-01", True))
        self.assertEqual(found[IDS[1]]["nombre"], "Clase - 2026/10/01 - Recording")
        self.assertFalse(found[IDS[2]]["descargable"])
        self.assertTrue(found[IDS[3]]["ignorada"] and found[IDS[4]]["ignorada"])
        self.assertNotIn("verificada", found[IDS[5]])
        self.assertEqual(summary, {"encontradas": 5, "nuevas": 4, "sin_verificar": 1})
        auto.detect(self.runtime, api=drive)  # la siguiente vez solo pregunta por la que falló
        self.assertEqual(drive.calls.count(IDS[1]), 1)
        self.assertEqual(drive.calls.count(IDS[5]), 2)

    def test_drive_is_asked_without_holding_the_list(self):
        self.snapshot("91005", forum(("1 de octubre", 1)))
        test = self

        class Drive(FakeDrive):
            def execute(self):
                with patch.object(auto, "LOCK_TRIES", 1), auto._locked(test.runtime):  # libre para Capture
                    pass
                return super().execute()
        self.assertEqual(auto.detect(self.runtime, api=Drive({IDS[1]: VIDEO}))["nuevas"], 1)

    def test_what_a_launch_wrote_during_the_search_is_kept(self):
        self.snapshot("91005", forum(("1 de octubre", 1), ("8 de octubre", 2)))
        auto.detect(self.runtime, api=FakeDrive({IDS[1]: VIDEO, IDS[2]: RuntimeError("HTTP 500")}))
        test = self

        class Drive(FakeDrive):
            def execute(self):  # mientras se consulta Drive, la cadena de Capture lanza la primera
                records = auto.found(test.runtime)
                records[IDS[1]]["intentos"] = 1
                auto._save(test.runtime, records)
                return super().execute()
        auto.detect(self.runtime, api=Drive({IDS[2]: VIDEO}))
        self.assertEqual(self.found()[IDS[1]]["intentos"], 1)
        self.assertTrue(self.found()[IDS[2]]["descargable"])

    def test_without_drive_login_recordings_wait_unverified(self):
        self.snapshot("91005", forum(("1 de octubre", 1)))
        with patch("capture.sources.drive.service", side_effect=Exception("sin login")):
            summary = auto.detect(self.runtime)
        self.assertEqual(summary["sin_verificar"], 1)
        self.assertIn("Drive", self.found()[IDS[1]]["problema"])

    def test_an_error_inside_the_lock_is_not_taken_for_a_busy_lock(self):
        with patch.object(auto.time, "sleep") as wait, self.assertRaisesRegex(auto.CaptureError, "fallo propio"):
            with auto._locked(self.runtime):
                raise auto.CaptureError("fallo propio")
        wait.assert_not_called()
        with auto._locked(self.runtime):  # el bloqueo quedó libre
            pass

    def test_a_recording_that_could_not_be_downloaded_is_checked_again_a_day_later(self):
        from datetime import datetime, timedelta, timezone
        self.snapshot("91005", forum(("1 de octubre", 1)))
        drive = FakeDrive({IDS[1]: {**VIDEO, "capabilities": {"canDownload": False}}})
        auto.detect(self.runtime, api=drive)
        drive.files_by_id[IDS[1]] = VIDEO  # el profesor habilitó la descarga
        auto.detect(self.runtime, api=drive, now=datetime.now(timezone.utc) + timedelta(hours=2))
        self.assertFalse(self.found()[IDS[1]]["descargable"])  # todavía no toca
        auto.detect(self.runtime, api=drive, now=datetime.now(timezone.utc) + timedelta(days=2))
        self.assertTrue(self.found()[IDS[1]]["descargable"])
        self.assertEqual(drive.calls.count(IDS[1]), 2)

    def test_courses_without_snapshot_are_skipped(self):
        self.courses.return_value = {"99999": "MAT-X"}
        self.assertEqual(auto.detect(self.runtime, api=FakeDrive({}))["encontradas"], 0)


class LaunchTests(Base):
    def setUp(self):
        super().setUp()
        self.snapshot("91005", forum(("1 de octubre", 1), ("8 de octubre", 2), ("15 de octubre", 3)))
        auto.detect(self.runtime, api=FakeDrive({IDS[1]: VIDEO, IDS[2]: VIDEO, IDS[3]: VIDEO}))
        patcher = patch.object(cc, "_subject", return_value=("SER (BE-IT)", "2026-2"))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.plans = []

    def fake_plan(self, runtime, link, subject, day, api=None):
        self.plans.append((link, day))
        return {"fuente": link, "semestre": "2026-2", "materia": "SER (BE-IT)", "fecha": day, "reanuda": None,
                "plan_id": "b" * 64}

    def launch(self, **plan):
        popen = Popen()
        with patch.object(cc, "plan", side_effect=plan.get("side_effect", self.fake_plan)):
            started = auto.launch_next(self.runtime, popen=popen, now=lambda: 1000.0)
        return started, popen

    def test_starts_the_oldest_recording_as_an_automatic_capture(self):
        started, popen = self.launch()
        self.assertEqual(started["fecha"], "2026-10-01")
        self.assertEqual(len(popen.calls), 1)
        order = cc._read(self.runtime)["pedido"]
        self.assertEqual((order["tipo"], order["fuente"], order["automatica"]), ("capturar", LINK.format(IDS[1]), True))
        self.assertEqual(self.found()[IDS[1]]["intentos"], 1)

    def test_a_start_refused_as_busy_does_not_spend_an_attempt(self):
        with patch.object(cc, "start", side_effect=OperationError("BUSY", "Ya hay una clase en proceso.")):
            started, _ = self.launch()
        self.assertIsNone(started)
        self.assertNotIn("intentos", self.found()[IDS[1]])
        self.assertNotIn("problema", self.found()[IDS[1]])

    def test_nothing_starts_while_capture_is_busy(self):
        cc._write(self.runtime, {"estado": "en_curso", "inicio_epoch": 999.0, "pedido": {}})
        with patch.object(cc, "_locked", return_value=True):
            started, popen = self.launch()
        self.assertIsNone(started)
        self.assertEqual(popen.calls, [])

    def test_skips_what_is_done_registered_or_failed_twice(self):
        found = auto.found(self.runtime)
        found[IDS[1]]["intentos"] = 2
        auto._save(self.runtime, found)
        self.registered.return_value = {("MAT-SER", "2026-10-08")}
        started, _ = self.launch()
        self.assertEqual(started["fecha"], "2026-10-15")

    def test_a_refused_plan_is_recorded_and_the_next_one_starts(self):
        def plan(runtime, link, subject, day, api=None):
            if day == "2026-10-01":
                raise OperationError("DATE_MISMATCH", "La grabación dice 2026-09-30, no 2026-10-01.")
            return self.fake_plan(runtime, link, subject, day)
        started, _ = self.launch(side_effect=plan)
        self.assertEqual(started["fecha"], "2026-10-08")
        self.assertIn("2026-09-30", self.found()[IDS[1]]["problema"])

    def refuse_first(self, code, text):
        def plan(runtime, link, subject, day, api=None):
            if link == LINK.format(IDS[1]):
                self.plans.append((link, day))
                raise OperationError(code, text)
            return self.fake_plan(runtime, link, subject, day)
        return plan

    def launch_idle(self, **plan):
        """Como `launch`, sin dejar una captura en curso: así la siguiente llamada vuelve a mirar la lista."""
        with patch.object(cc, "start"):
            return self.launch(**plan)[0]

    def test_an_unexpected_error_keeps_what_was_already_recorded(self):
        def plan(runtime, link, subject, day, api=None):
            if day == "2026-10-01":
                raise OperationError("NOT_MEDIA", "Ese enlace no es una grabación de audio o video.")
            raise OSError("disco")
        with self.assertRaises(OSError):
            self.launch(side_effect=plan)
        entry = self.found()[IDS[1]]
        self.assertTrue(entry["rechazada"])
        self.assertIn("audio o video", entry["problema"])

    def test_drive_is_asked_without_holding_the_list(self):
        free = []
        def plan(runtime, link, subject, day, api=None):
            with job_lock(self.runtime / auto.FOLDER / "lista"):  # la rutina diaria podría guardar ya
                free.append(day)
            return self.fake_plan(runtime, link, subject, day)
        started, _ = self.launch(side_effect=plan)
        self.assertEqual(set(free), {"2026-10-01"})  # la vista previa y el arranque (`cc.start` vuelve a planear)
        self.assertEqual(started["fecha"], "2026-10-01")

    def test_what_detect_saved_meanwhile_is_kept(self):
        def plan(runtime, link, subject, day, api=None):
            records = auto.found(self.runtime)
            records[IDS[3]]["ignorada"] = True  # otro proceso guarda la lista mientras Drive responde
            auto._save(self.runtime, records)
            return self.fake_plan(runtime, link, subject, day)
        self.launch(side_effect=plan)
        found = self.found()
        self.assertTrue(found[IDS[3]]["ignorada"])
        self.assertEqual(found[IDS[1]]["intentos"], 1)

    def test_a_recording_refused_for_good_is_not_planned_again(self):
        plan = self.refuse_first("DATE_MISMATCH", "La grabación dice 2026-09-30, no 2026-10-01.")
        self.launch_idle(side_effect=plan)
        self.assertEqual(self.launch_idle(side_effect=plan)["file_id"], IDS[2])  # la lista se volvió a mirar
        self.assertEqual([p for p in self.plans if p[0] == LINK.format(IDS[1])], [(LINK.format(IDS[1]), "2026-10-01")])
        entry = self.found()[IDS[1]]
        self.assertTrue(entry["rechazada"])
        self.assertIn("2026-09-30", entry["problema"])  # el motivo sigue a la vista en el panel

    def test_a_refused_recording_is_tried_again_when_its_date_changes(self):
        plan = self.refuse_first("DATE_MISMATCH", "La grabación dice 2026-09-30, no 2026-10-01.")
        self.launch_idle(side_effect=plan)
        self.snapshot("91005", forum(("30 de septiembre", 1), ("8 de octubre", 2), ("15 de octubre", 3)))
        auto.detect(self.runtime, api=FakeDrive({}))
        self.assertNotIn("rechazada", self.found()[IDS[1]])
        self.assertNotIn("problema", self.found()[IDS[1]])
        started = self.launch_idle()
        self.assertEqual((started["file_id"], started["fecha"]), (IDS[1], "2026-09-30"))

    def test_a_passing_refusal_is_tried_again(self):
        plan = self.refuse_first("DRIVE_UNAVAILABLE", "No se pudo consultar Google Drive.")
        self.launch_idle(side_effect=plan)
        self.launch_idle(side_effect=plan)
        self.assertEqual(len([p for p in self.plans if p[0] == LINK.format(IDS[1])]), 2)
        self.assertNotIn("rechazada", self.found()[IDS[1]])

    def test_recordings_that_cannot_be_downloaded_never_start(self):
        found = auto.found(self.runtime)
        for entry in found.values():
            entry["descargable"] = False
        auto._save(self.runtime, found)
        self.assertIsNone(self.launch()[0])


class ChainTests(Base):
    def test_an_automatic_capture_starts_the_next_one_when_it_ends(self):
        order = {"tipo": "capturar", "fuente": LINK.format(IDS[1]), "semestre": "2026-2", "materia": "SER",
                 "fecha": "2026-10-01", "reanuda": None, "automatica": True}
        cc._write(self.runtime, {"estado": "lanzada", "inicio_epoch": 1, "pedido": order})
        with patch("academic_operations.actions._capture", return_value={"job_id": "a" * 32}), \
                patch.object(auto, "launch_next") as following:
            self.assertEqual(cc.run(self.runtime), 0)
        following.assert_called_once_with(self.runtime)

    def test_a_capture_asked_from_claude_does_not_chain(self):
        order = {"tipo": "capturar", "fuente": LINK.format(IDS[1]), "semestre": "2026-2", "materia": "SER",
                 "fecha": "2026-10-01", "reanuda": None}
        cc._write(self.runtime, {"estado": "lanzada", "inicio_epoch": 1, "pedido": order})
        with patch("academic_operations.actions._capture", return_value={"job_id": "a" * 32}), \
                patch.object(auto, "launch_next") as following:
            cc.run(self.runtime)
        following.assert_not_called()


class CommandTests(Base):
    def test_daily_step_reports_plainly_and_never_fails_the_routine(self):
        self.courses.side_effect = SyncError("Seleccione el bloque activo antes de sincronizar.")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = auto.main(["--runtime", str(self.runtime)])
        self.assertEqual(code, 0)
        self.assertIn("No se buscaron grabaciones", out.getvalue())

    def test_daily_step_detects_and_starts(self):
        self.snapshot("91005", forum(("1 de octubre", 1)))
        out = io.StringIO()
        with patch.object(auto, "detect", return_value={"encontradas": 1, "nuevas": 1, "sin_verificar": 0}), \
                patch.object(auto, "launch_next", return_value={"fecha": "2026-10-01", "materia_id": "MAT-SER"}), \
                contextlib.redirect_stdout(out):
            self.assertEqual(auto.main(["--runtime", str(self.runtime)]), 0)
        self.assertIn("1 grabaciones encontradas", out.getvalue())
        self.assertIn("MAT-SER del 2026-10-01", out.getvalue())

    def run_step(self, **calendar):
        out = io.StringIO()
        with patch("academic_operations.calendar_classes.fetch", **calendar), \
                patch.object(auto, "detect", return_value={"encontradas": 2, "nuevas": 0, "sin_verificar": 0}), \
                patch.object(auto, "launch_next", return_value=None) as following, \
                contextlib.redirect_stdout(out):
            code = auto.main(["--runtime", str(self.runtime)])
        following.assert_called_once()
        return code, out.getvalue()

    def test_daily_step_reports_the_calendar_schedule(self):
        code, text = self.run_step(return_value={"estado": "ok", "eventos": 7})
        self.assertEqual(code, 0)
        self.assertIn("2 grabaciones encontradas", text)
        self.assertIn("Google Calendar: 7 clases en el horario.", text)

    def test_daily_step_shows_the_calendar_notice(self):
        code, text = self.run_step(return_value={"estado": "sin_permiso", "mensaje": "Falta el permiso de Calendar."})
        self.assertEqual(code, 0)
        self.assertIn("Falta el permiso de Calendar.", text)

    def test_a_calendar_failure_does_not_stop_the_search_for_recordings(self):
        code, text = self.run_step(side_effect=RuntimeError("sin red"))
        self.assertEqual(code, 0)
        self.assertIn("2 grabaciones encontradas", text)
        self.assertIn("Google Calendar falló (RuntimeError).", text)
        self.assertNotIn("sin red", text)



class DailyScriptTest(unittest.TestCase):
    def test_the_daily_routine_runs_the_step_last_without_changing_its_result(self):
        script = (Path(__file__).resolve().parents[1] / "scripts" / "canvas-daily.ps1").read_text(encoding="utf-8")
        step = script.index("-m academic_operations.auto_classes --runtime $runtime")
        self.assertLess(script.index("-m academic_knowledge build"), step)
        self.assertNotIn("$code", script[step:script.index("} catch {")])


if __name__ == "__main__":
    unittest.main()
