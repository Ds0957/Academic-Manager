"""`academic-read system`: comprobación en vivo del sistema real, solo lectura (S3)."""
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from academic_access import system
from academic_access.__main__ import main
from academic_store import store
from canvas_sync.common import SyncError
from capture.storage.metadata import sha256
from tests.test_today import SHEETS
from tests.xlsx_fixture import write_sheets

DAY = "2027-03-10"


class SystemCheckTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.runtime = Path(self.tmp.name) / "runtime"
        (self.runtime / "control").mkdir(parents=True)
        source = Path(self.tmp.name) / "source.xlsx"
        write_sheets(source, SHEETS)
        self.db = self.runtime / store.DB_NAME
        store.migrate(source, self.db)
        self.state = {"status": "free", "lock": None, "canonical_drive_file_id": "db-id",
                      "published": {"sha256": sha256(self.db)}}
        (self.runtime / "control" / "database-work-state.json").write_text(json.dumps(self.state), encoding="utf-8")
        self.remote = json.loads(json.dumps(self.state))
        self.caps = {"canEdit": True, "canDownload": True}
        self.service_error = None
        self.task = ("Ready", "11/03/2027 8:00:00")
        (self.runtime / "control" / "canvas-sync").mkdir()
        (self.runtime / "control" / "canvas-sync" / "token-meta.json").write_text(json.dumps(
            {"schema_version": 1, "expires_at": "2027-06-01T23:30:00-05:00",
             "saved_at": "2027-03-01T00:00:00+00:00"}), encoding="utf-8")
        today = patch("academic_access.system.local_today", return_value=datetime(2027, 3, 10).date())
        today.start()
        self.addCleanup(today.stop)
        logs = self.runtime / "procesamiento" / "canvas-sync" / "logs"
        logs.mkdir(parents=True)
        (logs / f"canvas-{DAY}.log").write_text(f"[{DAY} 08:00:01] exit_code=0\r\nok\r\n", encoding="utf-8")
        (logs / "latest-status.json").write_text(json.dumps(
            {"timestamp": f"{DAY} 08:00:01", "exit_code": 0, "message": "ok"}), encoding="utf-8")

    def check(self):
        def service(runtime):
            if self.service_error:
                raise self.service_error
            return object()
        with patch("canvas_sync.workbook_blocks._drive_service", side_effect=service), \
             patch("canvas_sync.workbook_blocks._state_id", return_value="state-id"), \
             patch("capture.academic_manager.drive_workbook.metadata",
                   side_effect=lambda api, fid: {"id": fid, "capabilities": self.caps}), \
             patch("capture.academic_manager.drive_workbook.read_json_file",
                   side_effect=lambda api, fid, target: json.loads(json.dumps(self.remote))), \
             patch("academic_access.system._scheduled_task", side_effect=lambda: self.task):
            out = io.StringIO()
            with patch.dict(os.environ, {"ACADEMIC_MANAGER_DATA": str(self.runtime)}), redirect_stdout(out):
                code = main(["system"])
        body = json.loads(out.getvalue())
        self.assertEqual((code, body["ok"], body["action"]), (0, True, "system"), body)
        data = body["data"]
        return data, {c["nombre"]: c for c in data["comprobaciones"]}

    def test_everything_healthy_is_ok_and_nothing_is_written(self):
        before = (sha256(self.db), (self.runtime / "control" / "database-work-state.json").read_bytes())
        data, checks = self.check()
        self.assertEqual(data["estado"], "ok")
        self.assertEqual(list(checks), ["base_local", "drive", "canvas_sync", "token_canvas",
                                        "indice", "tarea_diaria"])
        self.assertEqual(checks["base_local"]["estado"], "ok")
        self.assertEqual(checks["drive"]["estado"], "ok")
        self.assertEqual(checks["tarea_diaria"]["estado"], "ok")
        self.assertEqual(before, (sha256(self.db),
                                  (self.runtime / "control" / "database-work-state.json").read_bytes()))

    def test_drive_identity_failure_is_an_error_but_other_checks_still_run(self):
        self.service_error = SyncError("No hay identidad técnica local válida para publicar.")
        data, checks = self.check()
        self.assertEqual((data["estado"], checks["drive"]["estado"]), ("error", "error"))
        self.assertIn("identidad", checks["drive"]["detalle"])
        self.assertEqual(checks["base_local"]["estado"], "ok")

    def test_missing_edit_permission_is_an_error(self):
        self.caps = {"canEdit": False, "canDownload": True}
        _, checks = self.check()
        self.assertEqual(checks["drive"]["estado"], "error")
        self.assertIn("edición", checks["drive"]["detalle"])

    def test_remote_lock_and_newer_version_elsewhere_are_warnings(self):
        started = (datetime.now(timezone.utc) - timedelta(minutes=45)).isoformat()
        self.remote.update({"status": "editing", "lock": {"machine": "OTRO", "started_at": started}})
        data, checks = self.check()
        self.assertEqual((data["estado"], checks["drive"]["estado"]), ("aviso", "aviso"))
        self.assertIn("45 minutos", checks["drive"]["detalle"])
        self.assertIn("unlock", checks["drive"]["detalle"])
        self.remote.update({"status": "free", "lock": None, "published": {"sha256": "f" * 64}})
        _, checks = self.check()
        self.assertEqual(checks["drive"]["estado"], "aviso")
        self.assertIn("otro equipo", checks["drive"]["detalle"])

    def test_local_base_being_edited_is_a_warning(self):
        busy = {**self.state, "status": "editing", "lock": {"machine": "ESTE"}}
        (self.runtime / "control" / "database-work-state.json").write_text(json.dumps(busy), encoding="utf-8")
        _, checks = self.check()
        self.assertEqual(checks["base_local"]["estado"], "aviso")

    def test_failing_canvas_sync_and_disabled_task_are_reported(self):
        logs = self.runtime / "procesamiento" / "canvas-sync" / "logs"
        (logs / f"canvas-{DAY}.log").write_text(
            f"[{DAY} 08:00:01] exit_code=1\r\nx\r\n[{DAY} 18:00:01] exit_code=1\r\nx\r\n", encoding="utf-8")
        self.task = ("Disabled", "N/A")
        data, checks = self.check()
        self.assertEqual(checks["canvas_sync"]["estado"], "aviso")
        self.assertIn("2 intentos seguidos", checks["canvas_sync"]["detalle"])
        self.assertEqual(checks["tarea_diaria"]["estado"], "error")
        self.assertEqual(data["estado"], "error")

    def test_scheduled_task_output_is_parsed_in_english_and_spanish(self):
        for raw, expected in (('"\\AcademicManager-CanvasDaily","24/10/2026 8:00:00\xa0a.\xa0m.","Ready"', ("Ready", "24/10/2026 8:00:00 a. m.")),
                              ('"\\AcademicManager-CanvasDaily","N/A","Deshabilitado"', ("Deshabilitado", "N/A"))):
            with self.subTest(expected[0]):
                self.assertEqual(system._parse_task(raw), expected)
        self.assertEqual(system._task_check(("Listo", "x"))["estado"], "ok")
        self.assertEqual(system._task_check(("Deshabilitado", "N/A"))["estado"], "error")
        self.assertEqual(system._task_check(None)["estado"], "error")
        self.assertIsNone(system._parse_task('"solo un campo"'))
        self.assertIsNone(system._parse_task(""))

    def test_a_failing_task_query_is_reported_without_breaking_the_check(self):
        self.task = "No se pudo consultar la tarea programada (código 1)."
        data, checks = self.check()
        self.assertEqual(checks["tarea_diaria"]["estado"], "error")
        self.assertIn("código 1", checks["tarea_diaria"]["detalle"])

    def test_capabilities_lists_system_and_it_takes_no_filters(self):
        out = io.StringIO()
        with redirect_stdout(out):
            main(["capabilities"])
        self.assertIn("system", json.loads(out.getvalue())["data"]["commands"])
        out = io.StringIO()
        with patch.dict(os.environ, {"ACADEMIC_MANAGER_DATA": str(self.runtime)}), redirect_stdout(out):
            code = main(["system", "--fecha", DAY])
        self.assertEqual((code, json.loads(out.getvalue())["error"]["code"]), (2, "INVALID_ARGUMENT"))


if __name__ == "__main__":
    unittest.main()
