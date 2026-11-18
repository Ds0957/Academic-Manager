"""Renovar la sesión de Google desde Claude (`iniciar_sesion_google`)."""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from academic_mcp import tools
from academic_mcp.tools import ToolError
from academic_operations import google_login as gl


class Popen:
    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    def __call__(self, argv, **kwargs):
        if self.fail:
            raise OSError("no")
        self.calls.append((argv, kwargs))


class GoogleLoginTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.runtime = Path(self.tmp.name)
        self.clock = [1000.0]
        self.popen = Popen()

    def tearDown(self):
        self.tmp.cleanup()

    def client(self):
        folder = self.runtime / "control" / "drive-oauth"
        folder.mkdir(parents=True)
        (folder / "oauth-client.json").write_text("{}", encoding="utf-8")

    def start(self, **kwargs):
        return gl.start(self.runtime, popen=self.popen, now=lambda: self.clock[0], os_name="nt", **kwargs)

    def saved(self):
        return json.loads((self.runtime / gl.FOLDER / gl.STATE).read_text("utf-8"))

    def test_opens_the_login_in_a_separate_process(self):
        self.client()
        with patch("academic_setup.whisperx.console_python", return_value="python.exe"):
            result = self.start()
        self.assertEqual(result["estado"], "esperando")
        self.assertIn("navegador", result["mensaje"])
        self.assertNotIn("inicio_epoch", result)
        argv, kwargs = self.popen.calls[0]
        self.assertEqual(argv[1:], ["-m", "academic_operations.google_login", "--runtime", str(self.runtime)])
        self.assertTrue(kwargs["creationflags"] & gl.CREATE_NO_WINDOW)

    def test_a_second_call_while_waiting_does_not_open_another(self):
        self.client()
        with patch("academic_setup.whisperx.console_python", return_value="python.exe"):
            self.start()
            self.clock[0] += 10  # todavía dentro del tiempo para arrancar
            self.assertEqual(self.start()["estado"], "esperando")
        self.assertEqual(len(self.popen.calls), 1)

    def test_without_the_client_file_says_how_to_add_it_and_creates_nothing(self):
        with self.assertRaises(gl.OperationError) as caught:
            self.start()
        self.assertEqual(caught.exception.code, "NO_CLIENT")
        self.assertIn("paso de Google", str(caught.exception))
        self.assertFalse((self.runtime / "control").exists())
        self.assertEqual(self.popen.calls, [])

    def test_only_on_windows(self):
        self.client()
        with self.assertRaises(gl.OperationError) as caught:
            gl.start(self.runtime, popen=self.popen, os_name="posix")
        self.assertEqual(caught.exception.code, "NOT_WINDOWS")

    def test_a_process_that_cannot_start_is_an_error(self):
        self.client()
        self.popen = Popen(fail=True)
        with patch("academic_setup.whisperx.console_python", return_value="python.exe"):
            with self.assertRaises(gl.OperationError) as caught:
                self.start()
        self.assertEqual(caught.exception.code, "NOT_STARTED")
        self.assertEqual(self.saved()["estado"], "error")

    def test_the_process_records_success(self):
        self.client()
        seen = []
        self.assertEqual(gl.run(self.runtime, login=lambda r, c: seen.append(c)), 0)
        self.assertEqual(seen, [self.runtime / "control" / "drive-oauth" / "oauth-client.json"])
        self.assertEqual(gl.status(self.runtime)["estado"], "listo")

    def test_the_process_records_a_failure_without_its_detail(self):
        def refuse(*_):
            raise RuntimeError("invalid_grant secreto")
        self.assertEqual(gl.run(self.runtime, login=refuse), 1)
        state = gl.status(self.runtime)
        self.assertEqual(state["estado"], "error")
        self.assertNotIn("secreto", json.dumps(self.saved()))

    def test_a_login_nobody_finished_becomes_an_error_after_the_wait(self):
        self.client()
        with patch("academic_setup.whisperx.console_python", return_value="python.exe"):
            self.start()
        from capture.storage.job import job_lock
        with job_lock(self.runtime / gl.FOLDER / gl.PROCESS):  # el proceso sigue esperando en el navegador
            self.assertEqual(gl.status(self.runtime, now=lambda: self.clock[0] + 60)["estado"], "esperando")
            later = gl.status(self.runtime, now=lambda: self.clock[0] + gl.WAIT_SECONDS + 1)
        self.assertEqual(later["estado"], "error")
        self.assertEqual(later["mensaje"], gl.FAILED)

    def test_a_redirected_state_file_is_not_read(self):
        other = self.runtime / "otro.json"
        other.write_text(json.dumps({"estado": "listo", "refresh_token": "SECRETO"}), encoding="utf-8")
        (self.runtime / gl.FOLDER).mkdir(parents=True)
        (self.runtime / gl.FOLDER / gl.STATE).symlink_to(other)
        self.assertEqual(gl.status(self.runtime)["estado"], "ninguno")

    def test_only_plain_fields_reach_the_chat(self):
        (self.runtime / gl.FOLDER).mkdir(parents=True)
        (self.runtime / gl.FOLDER / gl.STATE).write_text(
            json.dumps({"estado": "listo", "mensaje": gl.DONE, "refresh_token": "SECRETO"}), encoding="utf-8")
        self.assertNotIn("SECRETO", json.dumps(gl.status(self.runtime)))

    def test_a_call_that_races_another_says_it_is_waiting(self):
        self.client()
        from capture.storage.job import job_lock
        with job_lock(self.runtime / gl.FOLDER):
            result = self.start()
        self.assertEqual(result["estado"], "esperando")
        self.assertEqual(self.popen.calls, [])

    def test_a_login_process_that_died_early_does_not_wait_the_full_time(self):
        self.client()
        with patch("academic_setup.whisperx.console_python", return_value="python.exe"):
            self.start()  # el proceso nunca llegó a correr: nadie tiene su bloqueo
            self.clock[0] += gl.START_GRACE + 1
            self.assertEqual(gl.status(self.runtime, now=lambda: self.clock[0])["estado"], "error")
            self.assertEqual(self.start()["estado"], "esperando")
        self.assertEqual(len(self.popen.calls), 2)

    def test_a_running_login_process_keeps_waiting(self):
        self.client()
        with patch("academic_setup.whisperx.console_python", return_value="python.exe"):
            self.start()
        from capture.storage.job import job_lock
        with job_lock(self.runtime / gl.FOLDER / "proceso"):  # el proceso espera en el navegador
            later = gl.status(self.runtime, now=lambda: self.clock[0] + gl.START_GRACE + 60)
        self.assertEqual(later["estado"], "esperando")

    def test_the_process_holds_its_lock_while_it_waits_for_google(self):
        self.client()
        held = []
        def login(runtime, client):
            from capture.storage.job import job_lock
            from capture.models import CaptureError
            try:
                with job_lock(self.runtime / gl.FOLDER / "proceso"):
                    held.append(False)
            except CaptureError:
                held.append(True)
        gl.run(self.runtime, login=login)
        self.assertEqual(held, [True])

    def test_the_result_is_written_before_the_process_lets_go(self):
        written = []
        original = gl._write
        def write(runtime, **state):
            written.append((state["estado"], gl._running(runtime)))
            return original(runtime, **state)
        with patch.object(gl, "_write", write):
            gl.run(self.runtime, login=lambda r, c: None)
        self.assertEqual(written, [("listo", True)])  # un «status» a la vez no puede pisarlo con «error»

    def test_a_login_that_finishes_while_status_looks_is_not_overwritten(self):
        self.client()
        with patch("academic_setup.whisperx.console_python", return_value="python.exe"):
            self.start()
        def finishes(runtime):  # el proceso escribe «listo» y suelta su bloqueo justo ahora
            gl._write(runtime, estado="listo", mensaje=gl.DONE)
            return False
        with patch.object(gl, "_running", finishes):
            result = gl.status(self.runtime, now=lambda: self.clock[0] + gl.START_GRACE + 5)
        self.assertEqual(result["estado"], "listo")
        self.assertEqual(self.saved()["estado"], "listo")

    def test_status_without_any_request(self):
        self.assertEqual(gl.status(self.runtime)["estado"], "ninguno")


class ToolTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.runtime = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_the_tool_starts_the_login(self):
        with patch.object(gl, "start", return_value={"estado": "esperando", "mensaje": gl.WAITING}) as start:
            result = tools.call("iniciar_sesion_google", {}, self.runtime)
        start.assert_called_once_with(self.runtime)
        self.assertEqual(result["estado"], "esperando")

    def test_consultar_only_reads_the_result(self):
        with patch.object(gl, "start") as start:
            result = tools.call("iniciar_sesion_google", {"consultar": True}, self.runtime)
        start.assert_not_called()
        self.assertEqual(result["estado"], "ninguno")

    def test_a_missing_client_is_a_plain_tool_error(self):
        with patch.object(gl, "os", SimpleNamespace(name="nt")):
            with self.assertRaises(ToolError) as caught:
                tools.call("iniciar_sesion_google", {}, self.runtime)
        self.assertEqual(str(caught.exception), gl.NO_CLIENT)

    def test_the_tool_writes_and_is_listed(self):
        self.assertFalse(tools.TOOLS["iniciar_sesion_google"].read_only)


if __name__ == "__main__":
    unittest.main()
