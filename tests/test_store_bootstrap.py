"""Base nueva del asistente con un Drive simulado: nada que existía se sobrescribe ni se borra.

Cada punto de fallo del protocolo deja un estado que `status` reconoce y que `create` retoma.
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from academic_store import bootstrap, store
from academic_store.bootstrap import BootstrapError
from academic_store.cutover import STORE_FILE_NAME
from canvas_sync import semester, workbook_enrollment
from canvas_sync.common import SyncError
from capture.models import CaptureError
from capture.storage.metadata import sha256

# Ruta absoluta en este sistema, con la forma de Google Drive para escritorio (G:\Mi unidad\...).
DRIVE_ROOT = str(Path(tempfile.gettempdir()).resolve() / "G" / "Mi unidad" / "Universidad" / "Academic Manager")
FOLDER = "application/vnd.google-apps.folder"


class FakeDrive:
    """Árbol de Drive en memoria con las funciones de drive_workbook y fallos inyectables."""

    def __init__(self):
        self.files = {"f-uni": {"name": "Universidad", "parent": "root", "mime": FOLDER, "data": b""},
                      "f-am": {"name": "Academic Manager", "parent": "f-uni", "mime": FOLDER, "data": b""}}
        self.fail = {}  # nombre de operación → excepción (una vez)
        self.after = {}  # nombre de operación → excepción tras hacerla (una vez)
        self.calls = []
        self.count = 0

    def _maybe(self, table, name):
        if name in table:
            raise table.pop(name)

    def find_in_folder(self, api, folder_id, name, mime_type=None):
        self.calls.append(("list", folder_id, name))
        return [{"id": i, "name": f["name"]} for i, f in self.files.items()
                if f["parent"] == folder_id and f["name"] == name and (mime_type in (None, f["mime"]))]

    def create(self, api, folder_id, name, source, mime):
        key = "create_state" if name == bootstrap.STATE_NAME else "create_store"
        self.calls.append(("create", name))
        self._maybe(self.fail, key)
        self.count += 1
        file_id = f"fake-drive-id-{self.count:06d}"
        self.files[file_id] = {"name": name, "parent": folder_id, "mime": mime,
                               "data": Path(source).read_bytes()}
        self._maybe(self.after, key)
        return {"id": file_id, "modifiedTime": "simulado"}

    def upload(self, api, file_id, source, mime):
        self.calls.append(("upload", file_id))
        self.files[file_id]["data"] = Path(source).read_bytes()
        return {"id": file_id, "modifiedTime": "simulado"}

    def download(self, api, file_id, target):
        self.calls.append(("download", file_id))
        self._maybe(self.fail, "download")
        Path(target).parent.mkdir(parents=True, exist_ok=True)
        data = self.files[file_id]["data"]
        if self.fail.pop("corrupt", None):
            data = data + b"x"
        Path(target).write_bytes(data)
        return {"id": file_id, "modifiedTime": "simulado", "parents": [self.files[file_id]["parent"]]}

    def read_json_file(self, api, file_id, target):
        self.download(api, file_id, target)
        state = json.loads(self.files[file_id]["data"])
        if self.fail.pop("state_readback", None):
            state["status"] = "editing"
        return state

    def write_json_file(self, api, file_id, data, target):
        self.calls.append(("write_json", file_id))
        self.files[file_id]["data"] = json.dumps(data).encode()
        return {"id": file_id}

    def metadata(self, api, file_id):
        return {"id": file_id, "parents": [self.files[file_id]["parent"]],
                "capabilities": {"canEdit": True, "canDownload": True}}

    def require_editable(self, api, file_id):
        return self.metadata(api, file_id)

    def state_id(self, api, workbook_id):
        parent = self.files[workbook_id]["parent"]
        found = [i for i, f in self.files.items() if f["parent"] == parent and f["name"] == bootstrap.STATE_NAME]
        if len(found) != 1:
            raise SyncError("No existe un estado de workbook único en la carpeta canónica.")
        return found[0]

    def names(self):
        return sorted(f["name"] for f in self.files.values() if f["mime"] != FOLDER)

    def patches(self):
        names = ("find_in_folder", "create", "upload", "download", "read_json_file", "write_json_file",
                 "metadata", "require_editable")
        found = [patch(f"capture.academic_manager.drive_workbook.{n}", side_effect=getattr(self, n))
                 for n in names]
        found.append(patch("canvas_sync.workbook_blocks._state_id", side_effect=self.state_id))
        return found


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "datos"
        (self.root / "control" / "drive-oauth").mkdir(parents=True)
        self.drive = FakeDrive()
        for p in self.drive.patches():
            p.start()
            self.addCleanup(p.stop)

    def create(self, drive_root=DRIVE_ROOT):
        return bootstrap.create(self.root, drive_root, api=object())

    def assert_installed(self):
        db, state_path = self.root / store.DB_NAME, self.root / "control" / "database-work-state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual((state["status"], state["lock"]), ("free", None))
        self.assertEqual(state["published"]["sha256"], sha256(db))
        self.assertEqual(self.drive.files[state["canonical_drive_file_id"]]["data"], db.read_bytes())
        self.assertEqual(self.drive.names(), [STORE_FILE_NAME, bootstrap.STATE_NAME])
        self.assertEqual(bootstrap.status(self.root), "existente")
        from academic_operations.actions import _config
        operations = _config(self.root.resolve())
        self.assertEqual(operations["database_state_file_id"], self.state_file_id())
        self.assertEqual((operations["drive_root"], operations["subjects"]), (DRIVE_ROOT, {}))
        raw = json.loads((self.root / "control" / "academic-operations.json").read_text(encoding="utf-8"))
        self.assertNotIn("subjects", raw)
        return state

    def state_file_id(self):
        return next(i for i, f in self.drive.files.items() if f["name"] == bootstrap.STATE_NAME)


class CreateTests(Case):
    def test_empty_folder_gets_an_empty_published_base(self):
        self.assertEqual(bootstrap.status(self.root), "nueva")
        result = self.create()
        self.assertEqual(result["mode"], "created")
        state = self.assert_installed()
        self.assertEqual(state["resource"], STORE_FILE_NAME)
        remote = json.loads(self.drive.files[result["state_drive_file_id"]]["data"])
        self.assertEqual(remote, state)
        self.assertEqual(self.drive.files[result["store_drive_file_id"]]["parent"], "f-am")
        db = self.root / store.DB_NAME
        store.validate(db)
        self.assertEqual(set(store.info(db)["rows"].values()), {0, 1})  # solo el título del tablero
        self.assertEqual(store.headers(db, "03 - Clases")[0], "clase_id")
        self.assertEqual(store.headers(db, "01 - Materias")[-1], "bloques")
        self.assertEqual(store.headers(db, "05 - Semestres")[-1], "bloque_activo")
        self.assertFalse((self.root / "control" / "canvas-sync" / "course-registry.json").exists())
        with self.assertRaisesRegex(BootstrapError, "ya se creó"):
            self.create()

    def test_existing_local_data_is_never_touched(self):
        for name in bootstrap.LOCAL_FILES:
            with self.subTest(name=name):
                path = self.root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"previo")
                self.assertEqual(bootstrap.status(self.root), "existente")
                with self.assertRaisesRegex(BootstrapError, "ya tiene una base"):
                    self.create()
                self.assertEqual(path.read_bytes(), b"previo")
                self.assertEqual(self.drive.calls, [])
                path.unlink()

    def test_existing_base_in_drive_folder_stops_and_explains(self):
        for name in bootstrap.REMOTE_NAMES:
            with self.subTest(name=name):
                self.drive.files["old"] = {"name": name, "parent": "f-am", "mime": "x", "data": b"suyo"}
                with self.assertRaisesRegex(BootstrapError, "Drive ya tiene una base"):
                    self.create()
                self.assertEqual(self.drive.files["old"]["data"], b"suyo")
                self.assertEqual(self.drive.names(), [name])
                self.assertFalse(any(c[0] in ("create", "upload", "write_json") for c in self.drive.calls))
                self.assertEqual(bootstrap.status(self.root), "nueva")
                self.assertFalse((self.root / store.DB_NAME).exists())
                del self.drive.files["old"]

    def test_drive_folder_must_be_found_once_inside_my_drive(self):
        base = Path(DRIVE_ROOT).parents[2]
        cases = {str(base / "Academic Manager"): "Mi unidad", str(base / "Mi unidad"): "no «Mi unidad» entera",
                 str(base / "Mi unidad" / "Academic Manager"): "no encuentra la carpeta «Academic Manager»",
                 str(base / "Mi unidad" / "Otra"): "debe llamarse «Academic Manager»"}
        for root, message in cases.items():
            with self.subTest(root=root), self.assertRaisesRegex(BootstrapError, message):
                self.create(root)
        self.drive.files["dup"] = {"name": "Universidad", "parent": "root", "mime": FOLDER, "data": b""}
        with self.assertRaisesRegex(BootstrapError, "varias carpetas"):
            self.create()
        self.assertEqual(bootstrap.status(self.root), "nueva")
        self.assertEqual(self.drive.names(), [])

    def test_without_write_identity_nothing_is_created(self):
        with self.assertRaisesRegex(BootstrapError, "No hay identidad técnica"):
            bootstrap.create(self.root, DRIVE_ROOT)
        self.assertEqual(bootstrap.status(self.root), "nueva")
        self.assertEqual(self.drive.calls, [])


def tree(root):
    """Huella de todo el árbol (rutas, carpetas y bytes) para comprobar que una negativa no toca nada."""
    import hashlib
    digest = hashlib.sha256()
    for path in sorted(Path(root).rglob("*")):
        digest.update(str(path.relative_to(root)).encode() + (b"/" if path.is_dir() else path.read_bytes()))
    return digest.hexdigest()


class RefusalLeavesNothingTests(Case):
    """Cada negativa deja la carpeta de datos idéntica, byte a byte (sin carpetas ni bloqueos nuevos)."""

    def assert_untouched(self, drive_root=DRIVE_ROOT, api=True):
        before = tree(self.root)
        with self.assertRaises(BootstrapError):
            bootstrap.create(self.root, drive_root, **({"api": object()} if api else {}))
        self.assertEqual(tree(self.root), before)

    def test_every_refusal(self):
        base = Path(DRIVE_ROOT).parents[2]
        for root in (str(base / "Academic Manager"), str(base / "Mi unidad"),
                     str(base / "Mi unidad" / "Otra"), str(base / "Mi unidad" / "Academic Manager")):
            with self.subTest(root=root):
                self.assert_untouched(root)
        with self.subTest("sin identidad"):
            self.assert_untouched(api=False)
        for name in bootstrap.REMOTE_NAMES:
            with self.subTest(remote=name):
                self.drive.files["old"] = {"name": name, "parent": "f-am", "mime": "x", "data": b"suyo"}
                self.assert_untouched()
                del self.drive.files["old"]
        for name in bootstrap.LOCAL_FILES:
            with self.subTest(local=name):
                path = self.root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"previo")
                self.assert_untouched()
                path.unlink()


class ResumeTests(Case):
    def resume_ok(self):
        self.assertEqual(bootstrap.status(self.root), "a_medias")
        self.create()
        return self.assert_installed()

    def test_store_upload_failure_resumes_without_duplicates(self):
        self.drive.fail["create_store"] = CaptureError("No se pudo crear el archivo en Drive.")
        with self.assertRaisesRegex(BootstrapError, "No se borró nada"):
            self.create()
        self.assertFalse((self.root / store.DB_NAME).exists())
        self.assertEqual(self.drive.names(), [])
        self.resume_ok()

    def test_store_created_but_answer_lost_is_recognized_by_hash(self):
        self.drive.after["create_store"] = CaptureError("timeout")
        with self.assertRaises(BootstrapError):
            self.create()
        self.assertEqual(self.drive.names(), [STORE_FILE_NAME])
        self.resume_ok()
        self.assertEqual(sum(c == ("create", STORE_FILE_NAME) for c in self.drive.calls), 1)

    def test_foreign_store_appearing_midway_is_not_touched(self):
        self.drive.fail["create_store"] = CaptureError("caída")
        with self.assertRaises(BootstrapError):
            self.create()
        self.drive.files["foreign"] = {"name": STORE_FILE_NAME, "parent": "f-am", "mime": "x", "data": b"otro"}
        with self.assertRaisesRegex(BootstrapError, "no creó el asistente"):
            self.create()
        self.assertEqual(self.drive.files["foreign"]["data"], b"otro")
        self.assertEqual(bootstrap.status(self.root), "a_medias")

    def test_corrupted_upload_is_uploaded_again_once(self):
        self.drive.fail["corrupt"] = True
        self.create()
        self.assert_installed()
        self.assertEqual(sum(c[0] == "upload" for c in self.drive.calls), 1)

    def test_redownload_failure_resumes(self):
        self.drive.fail["download"] = CaptureError("No se pudo descargar el archivo canónico desde Drive.")
        with self.assertRaisesRegex(BootstrapError, "Google Drive no respondió"):
            self.create()
        self.assertEqual(self.drive.names(), [STORE_FILE_NAME])
        self.resume_ok()

    def test_state_creation_failure_resumes(self):
        self.drive.fail["create_state"] = CaptureError("caída")
        with self.assertRaises(BootstrapError):
            self.create()
        self.assertEqual(self.drive.names(), [STORE_FILE_NAME])
        self.assertFalse((self.root / "control" / "database-work-state.json").exists())
        self.resume_ok()
        self.assertEqual(sum(c == ("create", STORE_FILE_NAME) for c in self.drive.calls), 1)

    def test_state_created_but_answer_lost_is_adopted(self):
        self.drive.after["create_state"] = CaptureError("timeout")
        with self.assertRaises(BootstrapError):
            self.create()
        self.resume_ok()
        self.assertEqual(sum(c == ("create", bootstrap.STATE_NAME) for c in self.drive.calls), 1)

    def test_unexpected_remote_state_installs_nothing(self):
        self.drive.fail["state_readback"] = True
        with self.assertRaisesRegex(BootstrapError, "no quedó como se esperaba"):
            self.create()
        self.assertFalse((self.root / store.DB_NAME).exists())
        self.assertFalse((self.root / "control" / "database-work-state.json").exists())
        self.resume_ok()

    def test_local_install_failure_resumes(self):
        with patch("academic_store.bootstrap._install", side_effect=PermissionError("bloqueado")):
            with self.assertRaisesRegex(BootstrapError, "ya está publicada en su Drive"):
                self.create()
        self.assertEqual(self.drive.names(), [STORE_FILE_NAME, bootstrap.STATE_NAME])
        self.resume_ok()

    def test_remote_base_changed_after_its_state_exists_is_never_overwritten(self):
        # Con el archivo de control ya creado, la base de Drive pudo empezar a usarse: no se sobrescribe.
        with patch("academic_store.bootstrap._install", side_effect=PermissionError("bloqueado")):
            with self.assertRaises(BootstrapError):
                self.create()
        store_id = next(i for i, f in self.drive.files.items() if f["name"] == STORE_FILE_NAME)
        self.drive.files[store_id]["data"] = b"cambiada en Drive"
        uploads = sum(c[0] == "upload" for c in self.drive.calls)
        with self.assertRaisesRegex(BootstrapError, "no coincide"):
            self.create()
        self.assertEqual(sum(c[0] == "upload" for c in self.drive.calls), uploads)
        self.assertEqual(self.drive.files[store_id]["data"], b"cambiada en Drive")

    def test_half_installed_copy_is_completed(self):
        with patch("academic_store.cutover.atomic_json", side_effect=OSError("disco")):
            with self.assertRaises(BootstrapError):
                self.create()
        self.assertTrue((self.root / store.DB_NAME).exists())
        self.resume_ok()

    def test_operations_config_failure_resumes(self):
        original = bootstrap.atomic_json

        def fail_on_operations(path, data):
            if Path(path).name == "academic-operations.json":
                raise OSError("disco")
            return original(path, data)

        with patch("academic_store.bootstrap.atomic_json", side_effect=fail_on_operations):
            with self.assertRaisesRegex(BootstrapError, "copiada a este PC, pero no se pudo guardar") as caught:
                self.create()
        self.assertNotIn("no se pudo copiar", str(caught.exception))
        self.assertTrue((self.root / store.DB_NAME).exists())
        self.assertFalse((self.root / "control" / "academic-operations.json").exists())
        self.resume_ok()

    def test_foreign_operations_config_is_never_replaced(self):
        self.drive.fail["create_state"] = CaptureError("caída")
        with self.assertRaises(BootstrapError):
            self.create()
        path = self.root / "control" / "academic-operations.json"
        path.write_text('{"schema_version": 1, "otro": true}', encoding="utf-8")
        with self.assertRaisesRegex(BootstrapError, "otra configuración para publicar clases"):
            self.create()
        self.assertEqual(path.read_text(encoding="utf-8"), '{"schema_version": 1, "otro": true}')
        self.assertEqual(bootstrap.status(self.root), "a_medias")

    def test_registry_or_xlsx_appearing_midway_stops(self):
        self.drive.fail["create_state"] = CaptureError("caída")
        with self.assertRaises(BootstrapError):
            self.create()
        registry = self.root / "control" / "canvas-sync" / "course-registry.json"
        registry.parent.mkdir(parents=True)
        registry.write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(BootstrapError, "Apareció"):
            self.create()
        self.assertEqual(registry.read_text(encoding="utf-8"), "{}")

    def test_changed_drive_folder_is_refused(self):
        self.drive.fail["create_store"] = CaptureError("caída")
        with self.assertRaises(BootstrapError):
            self.create()
        with self.assertRaisesRegex(BootstrapError, "La carpeta de Drive cambió"):
            self.create(str(Path(DRIVE_ROOT).parent / "Otra"))


class FirstEnrollmentTests(Case):
    """Tras crear la base, el alta y la publicación son las de un usuario existente."""

    COURSES = [{"course_id": 990001, "name": "Cálculo", "course_code": "CAL1"}]

    def test_onboard_and_publish_on_the_new_base(self):
        self.create()
        self.assertTrue(semester.is_empty_base(self.root))
        added = semester.onboard(self.root, "2027-1", [990001], self.COURSES,
                                 course_blocks={990001: [1, 2]}, confirm=True)
        self.assertEqual(added[0]["subject_id"], "MAT-20271-C990001")
        registry = semester.load_registry(self.root)
        self.assertEqual((registry["active_semester"], registry["active_block"]), ("2027-1", 1))
        self.assertFalse(semester.is_empty_base(self.root))
        preview = workbook_enrollment.publish_enrollment(self.root, registry, api=object(),
                                                         catalog=self.COURSES)
        self.assertEqual(preview["mode"], "dry_run")
        result = workbook_enrollment.publish_enrollment(self.root, registry, dry_run=False, api=object(),
                                                        catalog=self.COURSES)
        self.assertEqual(result["mode"], "published")
        db = self.root / store.DB_NAME
        catalog = store.read_catalog(db, ("01 - Materias", "05 - Semestres"))
        self.assertEqual(catalog["01 - Materias"][0]["materia_id"], "MAT-20271-C990001")
        self.assertEqual(catalog["05 - Semestres"][0]["estado"], "Activo")
        promoted = semester.load_registry(self.root)
        self.assertEqual({c["provenance"] for c in promoted["courses"]}, {"workbook"})
        state = json.loads((self.root / "control" / "database-work-state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["published"]["sha256"], sha256(db))


class SharedHelperRegressionTests(unittest.TestCase):
    """Lo que cambió en código compartido sigue igual para quien ya lo usaba."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def test_create_empty_refuses_rows_and_existing_files(self):
        grids = bootstrap.template()
        target = self.dir / "a.sqlite"
        with_rows = dict(grids, **{"01 - Materias": grids["01 - Materias"] + [["MAT-X", "2027-1"]]})
        with self.assertRaisesRegex(store.StoreError, "solo lleva encabezados"):
            store.create_empty(target, with_rows)
        target.write_bytes(b"previo")
        with self.assertRaisesRegex(store.StoreError, "no se sobrescribe"):
            store.create_empty(target, grids)
        self.assertEqual(target.read_bytes(), b"previo")

    def test_migrate_keeps_its_metadata(self):
        from tests.test_academic_store import workbook
        from tests.xlsx_fixture import write_sheets
        source = self.dir / "s.xlsx"
        write_sheets(source, workbook())
        store.migrate(source, self.dir / "m.sqlite")
        meta = store.info(self.dir / "m.sqlite")
        self.assertEqual((meta["revision"], meta["source_sha256"]), (1, sha256(source)))

    def test_find_in_folder_query_is_unchanged_without_type(self):
        from unittest.mock import MagicMock
        from capture.academic_manager import drive_workbook
        api = MagicMock()
        drive_workbook.find_in_folder(api, "folder", "it's")
        self.assertEqual(api.files.return_value.list.call_args.kwargs["q"],
                         "'folder' in parents and name = 'it\\'s' and trashed = false")
        drive_workbook.find_in_folder(api, "root", "AM", mime_type=FOLDER)
        self.assertEqual(api.files.return_value.list.call_args.kwargs["q"],
                         f"'root' in parents and name = 'AM' and trashed = false and mimeType = '{FOLDER}'")

    def test_onboard_on_an_existing_registry_keeps_its_block(self):
        root = self.dir / "datos"
        registry = root / "control" / "canvas-sync" / "course-registry.json"
        registry.parent.mkdir(parents=True)
        registry.write_text(json.dumps({"schema_version": 2, "active_semester": "2027-1", "active_block": None,
                                        "courses": []}), encoding="utf-8")
        semester.onboard(root, "2027-1", [990001], [{"course_id": 990001, "name": "Cálculo",
                                                    "course_code": "C"}],
                         course_blocks={990001: [2]}, confirm=True)
        self.assertIsNone(json.loads(registry.read_text(encoding="utf-8"))["active_block"])


if __name__ == "__main__":
    unittest.main()
