"""Recuperación supervisada de un lock retenido con Drive simulado: solo se libera lo seguro."""
import json
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from academic_store import records, store
from academic_store.unlock import unlock
from canvas_sync.common import SyncError
from capture.storage.job import job_lock
from capture.storage.metadata import sha256
from tests.test_academic_store import workbook
from tests.test_store_cutover import FakeDrive
from tests.xlsx_fixture import write_sheets


def ago(minutes):
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()


class UnlockTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "runtime"
        (self.root / "control" / "canvas-sync").mkdir(parents=True)
        xlsx = Path(self.tmp.name) / "source.xlsx"
        write_sheets(xlsx, workbook())
        self.db = self.root / store.DB_NAME
        store.migrate(xlsx, self.db)
        self.drive = FakeDrive(self.root, xlsx)
        self.drive.files = {"db-id": self.db.read_bytes()}
        digest = sha256(self.db)
        self.drive.state = {"schema_version": 1, "status": "editing",
                            "lock": {"machine": "PC-TEST", "started_at": ago(30), "base_sha256": digest},
                            "canonical_drive_file_id": "db-id", "published": {"sha256": digest}}
        self.write_local(self.drive.state)
        self.registry = self.root / "control" / "canvas-sync" / "course-registry.json"
        self.registry.write_text(json.dumps({"schema_version": 2, "workbook_sha256": digest}), encoding="utf-8")

    def write_local(self, state):
        (self.root / "control" / "database-work-state.json").write_text(json.dumps(state), encoding="utf-8")

    def local(self):
        return json.loads((self.root / "control" / "database-work-state.json").read_text(encoding="utf-8"))

    def run_unlock(self, **kw):
        d = self.drive
        targets = {"download": d.download, "upload": d.upload, "read_json_file": d.read_json,
                   "write_json_file": d.write_json}
        patches = [patch(f"capture.academic_manager.drive_workbook.{name}", side_effect=fn)
                   for name, fn in targets.items()]
        patches.append(patch("academic_store.unlock._state_id", return_value="state-id"))
        for p in patches:
            p.start()
        try:
            return unlock(self.root, api=object(), machine="PC-TEST", **kw)
        finally:
            for p in reversed(patches):
                p.stop()

    def test_dry_run_describes_the_release_and_changes_nothing(self):
        before = json.loads(json.dumps(self.drive.state))
        report = self.run_unlock()
        self.assertEqual((report["mode"], report["accion"], report["base_local"]),
                         ("dry_run", "liberar_lock", "coincide"))
        self.assertGreaterEqual(report["lock"]["minutos"], 30)
        self.assertEqual(self.drive.state, before)
        self.assertEqual(self.local()["status"], "editing")

    def test_apply_releases_a_stale_own_lock_and_keeps_the_published_version(self):
        published = self.drive.state["published"]
        report = self.run_unlock(apply=True)
        self.assertEqual(report["mode"], "released")
        self.assertEqual((self.drive.state["status"], self.drive.state["lock"]), ("free", None))
        self.assertEqual(self.drive.state["published"], published)
        self.assertEqual(self.local(), self.drive.state)
        from canvas_sync.routine import workbook_version
        self.assertEqual(workbook_version(self.root), published["sha256"])

    def test_a_local_copy_that_differs_is_replaced_by_the_published_one(self):
        published = self.drive.files["db-id"]
        records.apply(self.db, [{"tabla": "notas", "accion": "insertar", "registro": {
            "nota_id": "NOTA-local", "materia_id": "MAT-A", "tipo": "nota", "texto": "solo local"}}])
        self.assertNotEqual(sha256(self.db), self.drive.state["published"]["sha256"])
        report = self.run_unlock()
        self.assertEqual(report["base_local"], "se_reemplazara")
        self.assertIn("se sustituirá", report["detalle"])
        self.assertIn("copia", report["detalle"])
        local_only = self.db.read_bytes()
        result = self.run_unlock(apply=True)
        self.assertEqual(self.db.read_bytes(), published)
        backup = self.root / "temporales" / result["copia_local"]
        self.assertEqual(backup.read_bytes(), local_only)  # lo no publicado no se pierde
        registry = json.loads(self.registry.read_text(encoding="utf-8"))
        self.assertEqual(registry["workbook_sha256"], self.drive.state["published"]["sha256"])

    def test_refuses_unsafe_locks_without_touching_anything(self):
        cases = {
            "otro equipo": ({"machine": "OTRO-PC"}, "otro equipo"),
            "reciente": ({"started_at": ago(2)}, "menos de 10 minutos"),
            "otra versión": ({"base_sha256": "0" * 64}, "no corresponde"),
            "reciente sin zona": ({"started_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat()},
                                  "menos de 10 minutos"),
        }
        original = json.loads(json.dumps(self.drive.state))
        for name, (change, message) in cases.items():
            with self.subTest(name):
                self.drive.state = json.loads(json.dumps(original))
                self.drive.state["lock"].update(change)
                before = json.loads(json.dumps(self.drive.state))
                with self.assertRaisesRegex(SyncError, message):
                    self.run_unlock(apply=True)
                self.assertEqual(self.drive.state, before)
                self.assertEqual(self.local()["status"], "editing")

    def test_refuses_when_drive_changed_under_the_lock(self):
        self.drive.files["db-id"] = b"otra cosa"
        with self.assertRaisesRegex(SyncError, "cambió"):
            self.run_unlock(apply=True)
        self.assertEqual(self.drive.state["status"], "editing")

    def test_refuses_while_a_chat_write_holds_its_turn(self):
        from academic_operations import chat
        ready, done = threading.Event(), threading.Event()

        def holder():
            with job_lock(chat._turn_dir(self.root.resolve())):
                ready.set()
                done.wait(5)
        thread = threading.Thread(target=holder)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(done.set)
        ready.wait()
        with self.assertRaisesRegex(SyncError, "escritura del chat"):
            self.run_unlock(apply=True)
        self.assertEqual(self.drive.state["status"], "editing")

    def test_repairs_a_local_state_left_editing_when_drive_is_free(self):
        self.drive.state.update({"status": "free", "lock": None})
        self.assertEqual(self.run_unlock()["accion"], "reparar_estado_local")
        self.assertEqual(self.local()["status"], "editing")
        self.assertEqual(self.run_unlock(apply=True)["mode"], "local_repaired")
        self.assertEqual(self.local(), self.drive.state)

    def test_a_failed_local_install_after_release_is_finished_by_a_rerun(self):
        records.apply(self.db, [{"tabla": "notas", "accion": "insertar", "registro": {
            "nota_id": "NOTA-local", "materia_id": "MAT-A", "tipo": "nota", "texto": "solo local"}}])
        with patch("academic_store.unlock._install", side_effect=OSError("disco lleno")):
            with self.assertRaises(OSError):
                self.run_unlock(apply=True)
        self.assertEqual(self.drive.state["status"], "free")
        self.assertEqual(self.local()["status"], "editing")
        report = self.run_unlock()
        self.assertEqual((report["accion"], report["base_local"]), ("instalar_publicada", "se_reemplazara"))
        result = self.run_unlock(apply=True)
        self.assertEqual(result["mode"], "local_installed")
        self.assertEqual(sha256(self.db), self.drive.state["published"]["sha256"])
        self.assertEqual(self.local(), self.drive.state)
        self.assertTrue((self.root / "temporales" / result["copia_local"]).is_file())

    def test_an_outdated_but_free_local_copy_is_not_unlock_business(self):
        self.drive.state.update({"status": "free", "lock": None})
        self.write_local(self.drive.state)
        records.apply(self.db, [{"tabla": "notas", "accion": "insertar", "registro": {
            "nota_id": "NOTA-local", "materia_id": "MAT-A", "tipo": "nota", "texto": "x"}}])
        with self.assertRaisesRegex(SyncError, "actualice el equipo"):
            self.run_unlock(apply=True)

    def test_nothing_to_do_when_both_states_are_free(self):
        self.drive.state.update({"status": "free", "lock": None})
        self.write_local(self.drive.state)
        self.assertEqual(self.run_unlock(apply=True)["mode"], "nothing_to_do")

    def test_cli_accepts_apply_only_for_cutover_and_unlock(self):
        import io
        from contextlib import redirect_stderr
        from academic_store.__main__ import main
        for argv in (["unlock", "--db", "x.sqlite"], ["unlock", "--xlsx", "x.xlsx"], ["verify", "--apply"]):
            err = io.StringIO()
            with redirect_stderr(err), self.assertRaises(SystemExit):
                main(argv)
            self.assertIn("no se admite", err.getvalue())


if __name__ == "__main__":
    unittest.main()
