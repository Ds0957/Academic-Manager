"""«Sincronizar ahora»: lanza la rutina diaria en segundo plano y sigue su resultado."""
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from tests.test_academic_panel import RAW, PanelCase


class Clock:
    def __init__(self, start=1_800_000_000.0):
        self.now = start

    def __call__(self):
        return self.now


def latest(runtime, *, code, message="", mtime):
    path = Path(runtime) / "procesamiento" / "canvas-sync" / "logs" / "latest-status.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\ufeff" + json.dumps({"timestamp": "2026-10-31 15:00:00", "exit_code": code,
                                            "message": message}), encoding="utf-8")
    os.utime(path, (mtime, mtime))


class ModuleTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.runtime = Path(tmp.name)
        self.clock = Clock()
        self.popen = MagicMock()

    def start(self, **kw):
        from academic_app import manual_sync
        return manual_sync.start(self.runtime, repo=Path("C:/App/app"), python=Path("C:/App/python/python.exe"),
                                 popen=self.popen, now=self.clock, os_name=kw.pop("os_name", "nt"), **kw)

    def status(self):
        from academic_app import manual_sync
        return manual_sync.status(self.runtime, now=self.clock)

    def test_only_on_windows(self):
        from academic_app.manual_sync import SyncUnavailable
        with self.assertRaises(SyncUnavailable) as caught:
            self.start(os_name="posix")
        self.assertIn("Windows", str(caught.exception))
        self.popen.assert_not_called()
        self.assertEqual(self.status()["estado"], "ninguna")

    def test_start_runs_the_daily_script_detached_and_only_once(self):
        state = self.start()
        self.assertEqual(state["estado"], "sincronizando")
        args, options = self.popen.call_args
        cmd = args[0]
        self.assertEqual(cmd[0], "powershell.exe")
        self.assertIn("-File", cmd)
        self.assertTrue(cmd[cmd.index("-File") + 1].replace("\\", "/").endswith("scripts/canvas-daily.ps1"))
        for flag, value in (("-Repo", "app"), ("-Python", "python.exe"), ("-Runtime", self.runtime.name)):
            self.assertTrue(cmd[cmd.index(flag) + 1].replace("\\", "/").endswith(value), flag)
        self.assertTrue(options["creationflags"] & 0x08000000)  # sin ventana
        self.clock.now += 30
        self.assertEqual(self.start()["estado"], "sincronizando")
        self.assertEqual(self.popen.call_count, 1)

    def test_result_comes_from_a_status_written_after_the_start(self):
        latest(self.runtime, code=0, mtime=self.clock.now - 3600)  # la corrida de la mañana
        self.start()
        self.clock.now += 20
        self.assertEqual(self.status()["estado"], "sincronizando")
        latest(self.runtime, code=0, mtime=self.clock.now)
        state = self.status()
        self.assertEqual(state["estado"], "listo")
        self.assertIn("Canvas está al día", state["mensaje"])

    def test_failures_are_plain_and_never_show_the_log(self):
        cases = [("Canvas Sync: Token vencido. Ejecute renew.", "token de Canvas venció"),
                 ("Canvas Sync: El estado local de la base no está libre (lock).", "otra actualización"),
                 ("Traceback C:\\Users\\diego\\x.py KeyError", "terminó con un error")]
        for message, text in cases:
            self.start()
            self.clock.now += 10
            latest(self.runtime, code=1, message=message, mtime=self.clock.now)
            state = self.status()
            self.assertEqual(state["estado"], "error")
            self.assertIn(text, state["mensaje"])
            self.assertNotIn("Ejecute renew", state["mensaje"])
            self.assertNotIn("Users", state["mensaje"])
            self.clock.now += 10

    def test_a_run_that_never_reports_ends_as_interrupted_and_can_restart(self):
        self.start()
        self.clock.now += 46 * 60
        self.assertEqual(self.status()["estado"], "interrumpida")
        self.start()
        self.assertEqual(self.popen.call_count, 2)

    def test_launch_failure_is_recorded(self):
        from academic_app.manual_sync import SyncUnavailable
        self.popen.side_effect = OSError("no powershell")
        with self.assertRaises(SyncUnavailable):
            self.start()
        self.assertEqual(self.status()["estado"], "error")
        self.assertNotIn("no powershell", self.status()["mensaje"])


class PagesTests(PanelCase):
    def setUp(self):
        super().setUp()
        self.complete()
        self.state = {"estado": "ninguna"}
        self.sync_start = MagicMock(side_effect=lambda runtime: self.state.update(estado="sincronizando") or self.state)
        self.sync_status = MagicMock(side_effect=lambda runtime: dict(self.state))
        self.wizard.deps.update(sync_start=self.sync_start, sync_status=self.sync_status)

    def test_today_and_summary_offer_the_button(self):
        for path in ("/panel", "/panel/resumen"):
            body = self.get(path)[2]
            self.assertIn('action="/panel/sincronizar"', body)
            self.assertIn("Sincronizar ahora", body)
            self.assertIn("sábado 31 de octubre de 2026 a las 08:00", body)

    def test_button_starts_and_the_page_follows_progress(self):
        status, response, _ = self.post("/panel/sincronizar", {})
        self.assertEqual(status, 303)
        self.assertEqual(response.getheader("Location"), "/panel/sincronizar")
        self.sync_start.assert_called_once()
        body = self.get("/panel/sincronizar")[2]
        self.assertIn("Sincronizando con Canvas", body)
        self.assertIn('<meta http-equiv="refresh" content="3">', body)
        self.assertNotIn('action="/panel/sincronizar"', self.get("/panel")[2])  # en curso: no otro botón
        self.assertIn('href="/panel/sincronizar"', self.get("/panel")[2])
        self.state.update(estado="listo", mensaje="Listo: Canvas está al día.")
        body = self.get("/panel/sincronizar")[2]
        self.assertIn("Listo: Canvas está al día.", body)
        self.assertNotIn("http-equiv", body)
        self.assertIn('href="/panel"', body)

    def test_error_and_unavailable_are_plain(self):
        from academic_app.manual_sync import SyncUnavailable
        self.sync_start.side_effect = SyncUnavailable("Solo se puede sincronizar desde Windows.")
        status, _, body = self.post("/panel/sincronizar", {})
        self.assertEqual(status, 400)
        self.assertIn("Solo se puede sincronizar desde Windows.", body)
        self.state.update(estado="error", mensaje="La sincronización terminó con un error.")
        body = self.get("/panel/sincronizar")[2]
        self.assertIn("La sincronización terminó con un error.", body)
        for raw in RAW:
            self.assertNotIn(raw, body)

    def test_requires_csrf(self):
        status, _, _ = self.post("/panel/sincronizar", {}, csrf=False)
        self.assertNotEqual(status, 303)
        self.sync_start.assert_not_called()


if __name__ == "__main__":
    unittest.main()
