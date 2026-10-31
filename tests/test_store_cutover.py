"""Corte supervisado xlsx → SQLite con Drive simulado: nada se sobrescribe ni se pierde."""
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from academic_store import store
from academic_store.cutover import cutover
from tests.xlsx_fixture import write_sheets
from canvas_sync.common import SyncError
from capture.storage.metadata import sha256
from tests.test_academic_store import workbook


class FakeDrive:
    def __init__(self, root, xlsx):
        self.root = root
        self.files = {"xlsx-id": xlsx.read_bytes()}
        self.state = {"schema_version": 1, "status": "free", "lock": None,
                      "canonical_drive_file_id": "xlsx-id",
                      "published": {"sha256": sha256(xlsx)}}
        self.created = []
        self.fail_on_create = False
        self.folder = []  # archivos con el nombre de la base ya presentes en la carpeta

    def download(self, api, file_id, target):
        Path(target).parent.mkdir(parents=True, exist_ok=True)
        Path(target).write_bytes(self.files[file_id])
        return {"id": file_id}

    def upload(self, api, file_id, source, mime):
        self.files[file_id] = Path(source).read_bytes()
        return {"modifiedTime": "simulado"}

    def create(self, api, folder_id, name, source, mime):
        if self.fail_on_create:
            from capture.models import CaptureError
            raise CaptureError("Drive no disponible")
        file_id = f"new-{len(self.created) + 1}"
        self.created.append((folder_id, name, mime))
        self.files[file_id] = Path(source).read_bytes()
        return {"id": file_id, "modifiedTime": "simulado"}

    def find_in_folder(self, api, folder_id, name):
        return [{"id": file_id, "name": name} for file_id in self.folder]

    def metadata(self, api, file_id):
        return {"id": file_id, "parents": ["folder"]}

    def read_json(self, api, file_id, target):
        return json.loads(json.dumps(self.state))

    def write_json(self, api, file_id, data, target):
        self.state = json.loads(json.dumps(data))
        return {"id": file_id}


class CutoverTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "runtime"
        (self.root / "control" / "canvas-sync").mkdir(parents=True)
        self.xlsx = self.root / "database.xlsx"
        write_sheets(self.xlsx, workbook())
        self.original = self.xlsx.read_bytes()
        self.drive = FakeDrive(self.root, self.xlsx)
        (self.root / "control" / "database-work-state.json").write_text(
            json.dumps(self.drive.state), encoding="utf-8")
        (self.root / "control" / "canvas-sync" / "course-registry.json").write_text(
            json.dumps({"schema_version": 2, "workbook_sha256": sha256(self.xlsx)}), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def run_cutover(self, **kw):
        d = self.drive
        targets = {"download": d.download, "upload": d.upload, "create": d.create,
                   "metadata": d.metadata, "find_in_folder": d.find_in_folder, "read_json_file": d.read_json, "write_json_file": d.write_json}
        patches = [patch(f"capture.academic_manager.drive_workbook.{name}", side_effect=fn)
                   for name, fn in targets.items()]
        patches.append(patch("academic_store.cutover._state_id", return_value="state-id"))
        for p in patches:
            p.start()
        try:
            return cutover(self.root, api=object(), machine="PC-TEST", **kw)
        finally:
            for p in reversed(patches):
                p.stop()

    def test_dry_run_changes_nothing(self):
        report = self.run_cutover()
        self.assertEqual(report["mode"], "dry_run")
        self.assertEqual(report["rows"]["02 - Actividades"], 2)
        self.assertFalse((self.root / store.DB_NAME).exists())
        self.assertEqual(self.drive.created, [])
        self.assertEqual(self.drive.state["status"], "free")
        self.assertEqual(self.drive.state["canonical_drive_file_id"], "xlsx-id")
        self.assertEqual(report["store_file"], "create")

    def test_apply_reuses_a_store_file_created_by_the_owner(self):
        # La cuenta de servicio no tiene cuota propia: si el dueño ya creó el archivo, se sube encima.
        self.drive.files["placeholder"] = b""
        self.drive.folder = ["placeholder"]
        self.assertEqual(self.run_cutover()["store_file"], "reuse")
        report = self.run_cutover(apply=True)
        self.assertEqual(report["mode"], "cut_over")
        self.assertEqual(self.drive.created, [])
        db = self.root / store.DB_NAME
        self.assertEqual(self.drive.state["canonical_drive_file_id"], "placeholder")
        self.assertEqual(self.drive.state["legacy_xlsx_drive_file_id"], "xlsx-id")
        self.assertEqual(self.drive.files["placeholder"], db.read_bytes())
        self.assertEqual(self.drive.state["published"]["sha256"], sha256(db))
        self.assertEqual(self.drive.files["xlsx-id"], self.original)

    def test_refuses_several_store_files_before_locking(self):
        self.drive.folder = ["a", "b"]
        with self.assertRaisesRegex(SyncError, "varios archivos"):
            self.run_cutover(apply=True)
        self.assertEqual((self.drive.state["status"], self.drive.state["lock"]), ("free", None))
        self.assertFalse((self.root / store.DB_NAME).exists())

    def test_apply_publishes_store_as_new_file_and_keeps_legacy_xlsx(self):
        report = self.run_cutover(apply=True)
        self.assertEqual(report["mode"], "cut_over")
        self.assertEqual(self.drive.created, [
            ("folder", "Academic Manager - Database.sqlite", "application/vnd.sqlite3")])
        self.assertEqual(self.drive.files["xlsx-id"], self.original)
        db = self.root / store.DB_NAME
        state = self.drive.state
        self.assertEqual((state["status"], state["lock"]), ("free", None))
        self.assertEqual(state["canonical_drive_file_id"], "new-1")
        self.assertNotIn("view_drive_file_id", state)
        self.assertEqual(state["legacy_xlsx_drive_file_id"], "xlsx-id")
        self.assertEqual(state["published"]["sha256"], sha256(db))
        self.assertEqual(self.drive.files["new-1"], db.read_bytes())
        local = json.loads((self.root / "control" / "database-work-state.json").read_text(encoding="utf-8"))
        self.assertEqual(local, state)
        registry = json.loads((self.root / "control" / "canvas-sync" / "course-registry.json")
                              .read_text(encoding="utf-8"))
        self.assertEqual(registry["workbook_sha256"], sha256(db))
        self.assertTrue(store.verify(self.root / "temporales" / Path(report["legacy_checkpoint"]).name, db)["ok"])
        self.assertEqual(self.xlsx.read_bytes(), self.original)  # el xlsx local queda como histórico
        from canvas_sync.routine import workbook_version
        self.assertEqual(workbook_version(self.root), sha256(db))

    def test_refuses_when_already_cut_over_or_local_copy_is_stale(self):
        self.run_cutover(apply=True)
        with self.assertRaisesRegex(SyncError, "ya existe"):
            self.run_cutover(apply=True)
        (self.root / store.DB_NAME).unlink()
        self.drive.state = {"status": "free", "lock": None, "canonical_drive_file_id": "xlsx-id",
                            "published": {"sha256": "otro"}}
        (self.root / "control" / "database-work-state.json").write_text(
            json.dumps(self.drive.state), encoding="utf-8")
        with self.assertRaisesRegex(SyncError, "no coincide con la versión publicada"):
            self.run_cutover()

    def test_refuses_when_shared_state_is_locked(self):
        self.drive.state = {**self.drive.state, "status": "editing", "lock": {"machine": "OTRO"}}
        with self.assertRaisesRegex(SyncError, "bloquead"):
            self.run_cutover(apply=True)
        self.assertFalse((self.root / store.DB_NAME).exists())

    def test_failure_after_lock_keeps_the_lock_and_installs_nothing(self):
        self.drive.fail_on_create = True
        with self.assertRaisesRegex(SyncError, "Drive no disponible"):
            self.run_cutover(apply=True)
        self.assertEqual(self.drive.state["status"], "editing")
        self.assertEqual(self.drive.state["canonical_drive_file_id"], "xlsx-id")
        self.assertFalse((self.root / store.DB_NAME).exists())
        self.assertEqual(self.xlsx.read_bytes(), self.original)


    def test_local_install_failure_after_release_is_resumed_by_a_rerun(self):
        with patch("academic_store.cutover._install", side_effect=OSError("disco lleno")):
            with self.assertRaisesRegex(SyncError, "Repita el corte"):
                self.run_cutover(apply=True)
        self.assertEqual(self.drive.state["status"], "free")
        self.assertEqual(self.drive.state["canonical_drive_file_id"], "new-1")
        self.assertFalse((self.root / store.DB_NAME).exists())
        report = self.run_cutover(apply=True)
        self.assertEqual(report["mode"], "resumed")
        db = self.root / store.DB_NAME
        self.assertEqual(sha256(db), self.drive.state["published"]["sha256"])
        local = json.loads((self.root / "control" / "database-work-state.json").read_text(encoding="utf-8"))
        self.assertEqual(local, self.drive.state)
        self.assertEqual(len(self.drive.created), 1)
        self.assertEqual([p.name for p in self.root.iterdir() if ".tmp" in p.name], [])

    def test_cli_rejects_options_that_cutover_would_ignore(self):
        import io
        from contextlib import redirect_stderr
        from academic_store.__main__ import main
        for argv in (["cutover", "--db", "x.sqlite"], ["cutover", "--xlsx", "x.xlsx"], ["info", "--apply"]):
            err = io.StringIO()
            with redirect_stderr(err), self.assertRaises(SystemExit):
                main(argv)
            self.assertIn("no se admite", err.getvalue())


if __name__ == "__main__":
    unittest.main()
