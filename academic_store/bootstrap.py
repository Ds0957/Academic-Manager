"""Base nueva para una persona que empieza de cero: estructura vacía publicada en su Drive.

Solo en una carpeta de datos sin base, sin estado de control y sin registro de materias.
Protocolo: comprobar que la carpeta de Drive no tiene base ni estado → crear la base vacía
y verificarla → anotar en el diario → crear la base en Drive → re-descargar y comparar →
crear el estado compartido (libre, con el hash publicado) → releerlo → instalar localmente.

Nunca sobrescribe, fusiona ni borra archivos que ya existían, ni en el PC ni en Drive. Tras
cualquier fallo, el diario (`procesamiento/bootstrap/journal.json`) permite retomar: los
archivos que este proceso ya creó en Drive se reconocen por su identificador o por su hash.
"""
import json
import re
from pathlib import Path

from canvas_sync.common import SyncError
from capture.academic_manager import database as db
from capture.academic_manager import drive_workbook as drive
from capture.models import CaptureError
from capture.storage.metadata import atomic_json, sha256

from . import store
from .cutover import STORE_FILE_NAME, _install

STATE_NAME = "database-work-state.json"
FOLDER_MIME = "application/vnd.google-apps.folder"
DRIVE_HOMES = ("mi unidad", "my drive")
# Lo que hace que una carpeta de datos (o de Drive) ya no sea «nueva».
LOCAL_FILES = (store.DB_NAME, "database.xlsx", "control/database-work-state.json",
               "control/canvas-sync/course-registry.json", "control/academic-operations.json")
REMOTE_NAMES = (STORE_FILE_NAME, STATE_NAME, "Academic Manager - Database.xlsx", "database.xlsx")
RESUME = " No se borró nada: puede volver a intentarlo y el asistente continúa donde quedó."


class BootstrapError(SyncError):
    """Motivo en lenguaje sencillo; nada de lo que existía antes se cambió."""


def template():
    """Encabezados de cada hoja (contrato V2.1), tomados de quienes las escriben."""
    from canvas_sync.workbook_activities import EXTRA_HEADERS, FIRST_HEADERS
    from canvas_sync.workbook_enrollment import SUBJECT_HEADERS, TERM_HEADERS
    resources = ("recurso_id", "materia_id", "tipo", "titulo", "descripcion", "ruta", "url", "fecha",
                 "origen", "lms", "objeto_externo_id", "verificacion", "procesado", "notas")
    deliverables = ("entregable_id", "actividad_id", "tipo", "modalidad", "descripcion", "formato",
                    "duracion_min", "duracion_max", "fecha_limite", "ruta", "url", "estado",
                    "alternativa_grupo", "notas")
    return {"00 - Dashboard": [["Academic Manager"]],
            "01 - Materias": [list(SUBJECT_HEADERS)],
            "02 - Actividades": [list(FIRST_HEADERS + EXTRA_HEADERS)],
            "03 - Clases": [list(db.CLASS_HEADERS)],
            "04 - Recursos": [list(resources)],
            "05 - Semestres": [list(TERM_HEADERS)],
            "06 - Entregables": [list(deliverables)]}


def _folder(root):
    return root / "procesamiento" / "bootstrap"


def _journal_path(root):
    return _folder(root) / "journal.json"


def _read_journal(root):
    path = _journal_path(root)
    if path.is_symlink():
        raise BootstrapError("Los datos guardados de una creación anterior de la base no son válidos; no se "
                             "cambió nada. Pida ayuda para revisarlos.")
    if not path.exists():
        return None
    try:
        journal = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(journal, dict) or journal.get("schema_version") != 1:
            raise ValueError("versión")
        return journal
    except (OSError, ValueError) as exc:
        raise BootstrapError("Los datos guardados de una creación anterior de la base están dañados; no se "
                             "cambió nada. Pida ayuda para revisarlos.") from exc


def status(runtime):
    """«nueva» (se puede crear), «a_medias» (se puede retomar) o «existente» (no aplica)."""
    root = Path(runtime).resolve()
    journal = _read_journal(root)
    if journal is not None and journal.get("step") != "done":
        return "a_medias"
    if any((root / name).exists() or (root / name).is_symlink() for name in LOCAL_FILES):
        return "existente"
    return "nueva"


def _drive_names(drive_root):
    parts = [p for p in re.split(r"[\\/]+", str(drive_root or "")) if p]
    homes = [i for i, p in enumerate(parts) if p.casefold() in DRIVE_HOMES]
    if not homes:
        raise BootstrapError("La carpeta de Drive del paso 5 debe estar dentro de «Mi unidad» de Google "
                             "Drive, por ejemplo G:\\Mi unidad\\Academic Manager.")
    names = parts[homes[0] + 1:]
    if not names:
        raise BootstrapError("Elija en el paso 5 una carpeta dentro de «Mi unidad» (por ejemplo «Academic "
                             "Manager»), no «Mi unidad» entera.")
    return names


def _resolve_folder(api, drive_root):
    """Identificador en Drive de la carpeta del paso 5, carpeta por carpeta desde «Mi unidad»."""
    parent = "root"
    for name in _drive_names(drive_root):
        found = drive.find_in_folder(api, parent, name, mime_type=FOLDER_MIME)
        if not found:
            raise BootstrapError(f"Google Drive no encuentra la carpeta «{name}». Revise el paso 5 y que "
                                 "Google Drive para escritorio haya terminado de sincronizar.")
        if len(found) > 1:
            raise BootstrapError(f"En su Drive hay varias carpetas llamadas «{name}» en el mismo lugar; "
                                 "cambie el nombre de las que sobran y vuelva a intentarlo.")
        parent = found[0]["id"]
    return parent


def _refuse_existing_remote(api, folder_id):
    for name in REMOTE_NAMES:
        if drive.find_in_folder(api, folder_id, name):
            raise BootstrapError("Su carpeta de Drive ya tiene una base de Academic Manager (o parte de ella). "
                                 "No se creó ni se cambió nada. Si es suya, pida ayuda para conectarla a este "
                                 "PC; si no, elija otra carpeta en el paso 5.")


def _operations(root, drive_root, state_file_id):
    """`control/academic-operations.json` validado con la misma regla que lo lee (`check_config`)."""
    from academic_operations.actions import OperationError, check_config
    data = {"schema_version": 1, "drive_root": str(drive_root), "database_state_file_id": state_file_id}
    try:
        check_config(root, data)
    except OperationError as exc:
        raise BootstrapError("La carpeta de Drive del paso 5 debe llamarse «Academic Manager» y no puede "
                             "estar dentro de la carpeta de datos (ni contenerla); no se creó nada.") from exc
    return data


def _install_operations(root, journal):
    """Configuración de operaciones (Capture → base); nunca sustituye una que no sea la suya."""
    from academic_operations.actions import config_path
    wanted = _operations(root, journal["drive_root"], journal["state_file_id"])
    path = config_path(root)
    if path.exists() or path.is_symlink():
        try:
            current = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            current = None
        if path.is_symlink() or current != wanted:
            raise BootstrapError("Ya existe otra configuración para publicar clases en su carpeta de datos; no "
                                 "se toca. Pida ayuda para revisarla.")
        return
    atomic_json(path, wanted)


def _new_candidate(root):
    _folder(root).mkdir(parents=True, exist_ok=True)
    candidate = _folder(root) / "candidate.sqlite"
    candidate.unlink(missing_ok=True)  # copia de trabajo propia de un intento anterior sin publicar
    store.create_empty(candidate, template())
    store.validate(candidate)
    return candidate


def _save(root, journal):
    atomic_json(_journal_path(root), journal)


def _check_local(root, journal):
    """Sin diario: nada de Academic Manager. Con diario: solo lo que este proceso instaló."""
    if journal is None:
        present = [n for n in LOCAL_FILES if (root / n).exists() or (root / n).is_symlink()]
        if present:
            raise BootstrapError("Su carpeta de datos ya tiene una base de Academic Manager (o parte de ella); "
                                 "no se crea otra ni se cambia nada.")
        return
    for name in ("database.xlsx", "control/canvas-sync/course-registry.json"):
        if (root / name).exists() or (root / name).is_symlink():
            raise BootstrapError("Apareció un archivo de Academic Manager en su carpeta de datos mientras se "
                                 "creaba la base; no se cambió nada. Pida ayuda para revisarlo.")
    local_db = root / store.DB_NAME
    if local_db.exists() and (local_db.is_symlink() or sha256(local_db) != journal.get("candidate_sha256")):
        raise BootstrapError("La base de su carpeta de datos no es la que creó el asistente; no se cambió nada.")
    state_path = root / "control" / "database-work-state.json"
    if state_path.exists():
        try:
            local_state = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            local_state = {}
        if state_path.is_symlink() or local_state.get("canonical_drive_file_id") != journal.get("db_file_id"):
            raise BootstrapError("Un archivo de control de su carpeta de datos no es el que creó el asistente; "
                                 "no se cambió nada.")


def _remote_store(api, root, journal, candidate):
    """Crea la base en Drive una sola vez; al retomar reconoce la que ya subió por su hash."""
    folder = _folder(root)
    if journal.get("db_file_id"):
        return journal["db_file_id"]
    found = drive.find_in_folder(api, journal["folder_id"], STORE_FILE_NAME)
    if len(found) > 1:
        raise BootstrapError(f"Hay varios «{STORE_FILE_NAME}» en su carpeta de Drive; no se cambió nada. "
                             "Pida ayuda para revisarlo.")
    if found:
        probe = folder / "found.sqlite"
        drive.download(api, found[0]["id"], probe)
        if sha256(probe) != journal["candidate_sha256"]:
            raise BootstrapError(f"En su carpeta de Drive apareció un «{STORE_FILE_NAME}» que no creó el "
                                 "asistente; no se toca. Pida ayuda para revisarlo.")
        journal["db_file_id"] = found[0]["id"]
    else:
        if candidate is None:
            raise BootstrapError("Falta la copia de la base preparada en este PC." + RESUME)
        journal["db_file_id"] = drive.create(api, journal["folder_id"], STORE_FILE_NAME, candidate,
                                             db.SQLITE_MIME)["id"]
    journal["step"] = "store_created"
    _save(root, journal)
    return journal["db_file_id"]


def _remote_state(api, root, journal, published):
    """Estado compartido libre que apunta a la base recién publicada (docs/13)."""
    folder = _folder(root)
    remote = folder / "remote-state.json"
    wanted = {"schema_version": 1, "resource": STORE_FILE_NAME,
              "canonical_drive_file_id": journal["db_file_id"], "status": "free", "lock": None,
              "published": published}
    if not journal.get("state_file_id"):
        found = drive.find_in_folder(api, journal["folder_id"], STATE_NAME)
        if len(found) > 1:
            raise BootstrapError("Hay archivos de control repetidos en su carpeta de Drive; no se cambió nada. "
                                 "Pida ayuda para revisarlo.")
        if found:
            other = drive.read_json_file(api, found[0]["id"], remote)
            if (other.get("canonical_drive_file_id") != journal["db_file_id"]
                    or (other.get("published") or {}).get("sha256") != published["sha256"]):
                raise BootstrapError("En su carpeta de Drive apareció un archivo de control de Academic Manager "
                                     "que no creó el asistente; no se toca. Pida ayuda para revisarlo.")
            journal["state_file_id"] = found[0]["id"]
        else:
            local = folder / "new-state.json"
            atomic_json(local, wanted)
            journal["state_file_id"] = drive.create(api, journal["folder_id"], STATE_NAME, local,
                                                    "application/json")["id"]
        journal["step"] = "state_created"
        _save(root, journal)
    checked = drive.read_json_file(api, journal["state_file_id"], remote)
    if (checked.get("status") != "free" or checked.get("lock") is not None
            or checked.get("canonical_drive_file_id") != journal["db_file_id"]
            or (checked.get("published") or {}).get("sha256") != published["sha256"]):
        raise BootstrapError("El archivo de control que el asistente guardó en Drive no quedó como se "
                             "esperaba; no se instaló nada en este PC. Pida ayuda para revisarlo.")
    return checked


def create(runtime, drive_root, *, api=None):
    """Crea, publica e instala la base vacía; retoma un intento anterior desde su diario."""
    from canvas_sync.workbook_blocks import _drive_service, _state_id
    root = Path(runtime).resolve()
    journal = _read_journal(root)
    if journal is not None and journal.get("step") == "done":
        raise BootstrapError("La base de datos ya se creó en esta carpeta de datos.")
    _check_local(root, journal)
    if journal is not None and journal.get("drive_root") != str(drive_root):
        raise BootstrapError("La carpeta de Drive cambió desde que empezó a crear la base. Vuelva a poner "
                             f"en el paso 5 la carpeta {journal.get('drive_root')} y reintente.")
    if journal is None:  # comprobaciones sin Drive, antes de leer credenciales o crear nada
        _drive_names(drive_root)
        _operations(root, drive_root, "pending-state-file-id")
    folder = _folder(root)  # se crea solo al preparar la base, después de todas las negativas
    try:
        api = api or _drive_service(root)
        candidate = folder / "candidate.sqlite"
        if journal is None:
            folder_id = _resolve_folder(api, drive_root)
            _refuse_existing_remote(api, folder_id)
            candidate = _new_candidate(root)
            journal = {"schema_version": 1, "drive_root": str(drive_root), "folder_id": folder_id,
                       "candidate_sha256": sha256(candidate), "db_file_id": None, "state_file_id": None,
                       "step": "started"}
            _save(root, journal)
        elif not journal.get("db_file_id"):
            if not candidate.is_file() or sha256(candidate) != journal["candidate_sha256"]:
                if drive.find_in_folder(api, journal["folder_id"], STORE_FILE_NAME):
                    raise BootstrapError(f"En su carpeta de Drive hay un «{STORE_FILE_NAME}» y la copia de "
                                         "este PC ya no coincide; no se toca. Pida ayuda para revisarlo.")
                candidate = _new_candidate(root)
                journal["candidate_sha256"] = sha256(candidate)
                _save(root, journal)
        if not candidate.is_file() or sha256(candidate) != journal["candidate_sha256"]:
            candidate = None
        file_id = _remote_store(api, root, journal, candidate)
        check = folder / "redownloaded.sqlite"
        meta = drive.download(api, file_id, check)
        if (sha256(check) != journal["candidate_sha256"] and candidate is not None
                and not journal.get("state_file_id")):
            # El archivo es el que creó este proceso (su id está en el diario) y todavía no tiene archivo de
            # control, así que nadie pudo usarlo: se vuelve a subir una vez. Con el control ya creado, nunca.
            drive.upload(api, file_id, candidate, db.SQLITE_MIME)
            meta = drive.download(api, file_id, check)
        if sha256(check) != journal["candidate_sha256"]:
            raise BootstrapError("La base subida a Drive no coincide con la preparada en este PC." + RESUME)
        store.validate(check)
        published = {"sha256": journal["candidate_sha256"], "size_bytes": check.stat().st_size,
                     "drive_modified_time": (meta or {}).get("modifiedTime"),
                     "validation": "bootstrap-empty-store-redownloaded-verified"}
        state = _remote_state(api, root, journal, published)
        if _state_id(api, file_id) != journal["state_file_id"]:
            raise BootstrapError("Google Drive no muestra el archivo de control junto a la base." + RESUME)
    except CaptureError as exc:
        raise BootstrapError("Google Drive no respondió como se esperaba; revise su conexión a internet."
                             + RESUME) from exc
    except store.StoreError as exc:
        raise BootstrapError("La base preparada en este PC no superó la comprobación." + RESUME) from exc
    except BootstrapError:
        raise
    except SyncError as exc:
        raise BootstrapError(str(exc)) from exc
    try:
        _install(root, check, state)
        journal["step"] = "installed"
        _save(root, journal)
    except OSError as exc:
        raise BootstrapError("La base ya está publicada en su Drive, pero no se pudo copiar a este PC."
                             + RESUME + f" Detalle técnico: {type(exc).__name__}.") from exc
    try:
        _install_operations(root, journal)
    except OSError as exc:
        raise BootstrapError("La base ya está publicada en su Drive y copiada a este PC, pero no se pudo guardar "
                             "su configuración para publicar clases." + RESUME
                             + f" Detalle técnico: {type(exc).__name__}.") from exc
    journal["step"] = "done"
    _save(root, journal)
    return {"mode": "created", "sha256": journal["candidate_sha256"], "store_drive_file_id": file_id,
            "state_drive_file_id": journal["state_file_id"]}
