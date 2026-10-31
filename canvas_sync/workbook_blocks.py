"""Publicación controlada del bloque activo y pertenencia de materias en V2.1.

Solo altera las columnas nuevas en Materias/Semestres y metadatos Canvas
verificados para una materia existente. Reutiliza el protocolo Drive de Capture.
La base publicada es la SQLite canónica; los planificadores la leen con la vista
compatible de `academic_store.sheetview`.
"""
import json
import os
import shutil
from datetime import datetime
from pathlib import Path

from academic_store import store
from academic_store.sheetview import open_book
from capture.academic_manager import database as db
from capture.academic_manager import drive_workbook as drive
from capture.models import CaptureError
from capture.storage.metadata import sha256, atomic_json
from .common import StoreBusy, SyncError
from .semester import _validate, _location

SUBJECT_FIELDS = ("materia_id", "semestre", "lms", "curso_externo_id")
BLOCK_COLUMN = 16
ACTIVE_BLOCK_COLUMN = 7
SQLITE_MIME = "application/vnd.sqlite3"


def _id(value):
    if value is None or str(value).strip() == "":
        return None
    s = str(value).strip()
    if s.endswith(".0") and s[:-2].isdigit():
        s = s[:-2]
    if not s.isdigit():
        raise SyncError("El Excel tiene un curso_externo_id inválido.")
    return int(s)


def _blocks(value):
    if value is None or str(value).strip() == "":
        return None
    value = str(value).strip().replace(" ", "")
    if value not in ("1", "2", "1,2"):
        raise SyncError("Pertenencia a bloques inválida en el Excel.")
    return [int(i) for i in value.split(",")]


def _block_number(value):
    if value is None or str(value).strip() == "":
        return None
    if type(value) is bool or str(value).strip() not in ("1", "1.0", "2", "2.0"):
        raise SyncError("bloque_activo inválido en el Excel.")
    return int(float(value))


def _sheet_data(sheet, required, optional_name, optional_column):
    header_count = optional_column - 1
    headers = [str(sheet.Cells.Item(1, i).Value or "").strip() for i in range(1, header_count + 1)]
    if not set(required).issubset(headers) or len(set(headers)) != len(headers):
        raise SyncError("Encabezados del Excel no coinciden con V2.")
    actual = sheet.Cells.Item(1, optional_column).Value
    if actual not in (None, "", optional_name):
        raise SyncError("La columna reservada para bloques ya está ocupada.")
    headers.append(optional_name)
    values = sheet.UsedRange.Value2
    if not isinstance(values, tuple):
        raise SyncError("La hoja V2 está vacía.")
    data = {}
    for excel_row, record in enumerate(values[1:], start=2):
        if not isinstance(record, tuple):
            continue
        row = dict(zip(headers, record))
        ident = row.get(required[0])
        if ident is None or str(ident).strip() == "":
            continue
        ident = str(ident).strip()
        if ident in data:
            raise SyncError("IDs duplicados en hoja académica.")
        data[ident] = (excel_row, row)
    return data, actual


def plan(path, registry):
    """Devuelve cambios exactos sin tocar el XLSX."""
    _validate(registry)
    if registry["active_block"] is None:
        raise SyncError("No hay bloque activo verificado.")
    if not registry["courses"]:
        raise SyncError("El registro académico no contiene cursos.")
    app = book = None
    try:
        app, book = open_book(path)
        subj, subj_col = _sheet_data(
            book.Worksheets.Item("01 - Materias"), SUBJECT_FIELDS, "bloques", BLOCK_COLUMN)
        terms, term_col = _sheet_data(
            book.Worksheets.Item("05 - Semestres"), ("semestre_id", "estado"),
            "bloque_activo", ACTIVE_BLOCK_COLUMN)
        active = registry["active_semester"]
        if active not in terms or str(terms[active][1].get("estado") or "").casefold() != "activo":
            raise SyncError("El semestre activo del registro y el Excel no coinciden.")
        if sum(str(r["estado"] or "").casefold() == "activo" for _, r in terms.values()) != 1:
            raise SyncError("El Excel debe tener exactamente un semestre activo.")
        changes = []
        if subj_col is None:
            changes.append(("01 - Materias", 1, BLOCK_COLUMN, None, "bloques"))
        if term_col is None:
            changes.append(("05 - Semestres", 1, ACTIVE_BLOCK_COLUMN, None, "bloque_activo"))
        for course in registry["courses"]:
            sid = course["subject_id"]
            # Datos todavía no publicados de nuevas matrículas: NO inventar materia.
            if sid not in subj:
                raise SyncError(f"Falta la materia {sid} en el Excel: publicación de altas pendiente.")
            row_idx, values = subj[sid]
            if str(values.get("semestre") or "") != course["semester"]:
                raise SyncError(f"Semestre contradictorio en {sid}.")
            stored_id = _id(values.get("curso_externo_id"))
            if stored_id is not None and stored_id != course["course_id"]:
                raise SyncError(f"Curso Canvas en conflicto para {sid}.")
            if not course["blocks"]:
                raise SyncError(f"Falta confirmar bloques para {sid}.")
            old = _blocks(values.get("bloques"))
            if old != course["blocks"]:
                changes.append(("01 - Materias", row_idx, BLOCK_COLUMN, values.get("bloques"),
                                ",".join(map(str, course["blocks"]))))
            if stored_id is None:
                changes.append(("01 - Materias", row_idx, 13, values.get("curso_externo_id"),
                                str(course["course_id"])))
                if not values.get("lms"):
                    changes.append(("01 - Materias", row_idx, 12, values.get("lms"), "Canvas"))
                elif str(values.get("lms")).casefold() != "canvas":
                    raise SyncError(f"LMS contradictorio en {sid}.")
            elif str(values.get("lms") or "").casefold() != "canvas":
                raise SyncError(f"LMS de {sid} no coincide con Canvas.")
        for term_id, (row_num, data) in terms.items():
            wanted = registry["active_block"] if term_id == active else None
            if wanted is not None and _block_number(data.get("bloque_activo")) != wanted:
                changes.append(("05 - Semestres", row_num, ACTIVE_BLOCK_COLUMN,
                                data.get("bloque_activo"), wanted))
        return changes
    finally:
        if book is not None:
            book.Close(False)
        if app is not None:
            app.Quit()


def mutate(path, changes):
    """Aplicación transaccional de un diff previamente validado sobre una copia de trabajo."""
    try:
        store.apply_cells(path, changes)
    except store.StoreError as exc:
        raise SyncError(str(exc)) from exc


def validate_store(path):
    try:
        return store.validate(path)
    except store.StoreError as exc:
        raise SyncError(str(exc)) from exc


def _drive_service(runtime):
    """Identidad técnica restringida, archivo local nunca enviado al chat/Git.

    Sin archivo de cuenta de servicio, usa el inicio de sesión propio con permiso de escritura
    del asistente (`capture.sources.auth.write_token_path`), si existe.
    """
    secret = Path(runtime) / "control" / "service-account-drive.json"
    if not secret.exists() and not secret.is_symlink():
        try:
            api = drive.user_service(runtime)
        except CaptureError as exc:
            raise SyncError(str(exc)) from exc
        if api is not None:
            return api
    if not secret.is_file() or secret.is_symlink():
        raise SyncError("No hay identidad técnica local válida para publicar.")
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    creds = service_account.Credentials.from_service_account_file(
        str(secret), scopes=drive.SCOPES)
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def _state_id(api, workbook_id):
    meta = drive.require_editable(api, workbook_id)
    parent = (meta.get("parents") or [None])[0]
    if not parent:
        raise SyncError("El workbook no tiene carpeta canónica verificable.")
    results = api.files().list(
        q="name = 'database-work-state.json' and trashed = false",
        fields="nextPageToken,files(id,name,parents,mimeType)",pageSize=100,
    ).execute()
    if results.get("nextPageToken"):
        raise SyncError("La búsqueda del estado compartido está incompleta.")
    candidates = [x for x in results.get("files", []) if parent in x.get("parents", [])]
    if len(candidates) != 1:
        raise SyncError("No existe un estado de workbook único en la carpeta canónica.")
    drive.require_editable(api, candidates[0]["id"])
    return candidates[0]["id"]


def _enrollment(active, block, courses):
    return active, block, sorted((c["course_id"], c["subject_id"], c["semester"], tuple(c.get("blocks") or []))
                                 for c in courses)


def _require_same_enrollment(base, registry):
    """El registro se hizo sobre otra versión publicada (p. ej. tras una clase de Capture).

    Solo sigue si la base publicada tiene exactamente sus materias, bloques y semestre; si no,
    el registro está desactualizado de verdad y no se adivina.
    """
    from .semester import workbook_courses
    active, mapped, block = workbook_courses(None, workbook_path=base)
    if _enrollment(active, block, mapped) != _enrollment(
            registry.get("active_semester"), registry.get("active_block"), registry.get("courses") or []):
        raise SyncError("El registro local es más antiguo que el workbook publicado.")


def publish(runtime, registry, *, dry_run=True, api=None, planner=plan, writer=mutate):
    """Lock→check→checkpoint→edit→verify→publish→redownload→unlock.

    En fallo posterior al lock, dejarlo para recuperación supervisada;
    jamás liberarlo cegadamente ni sustituir local antes de revalidar.
    """
    runtime = Path(runtime).resolve()
    registry = _validate(registry)
    local_state = json.loads((runtime / "control" / "database-work-state.json").read_text(encoding="utf-8"))
    file_id = local_state.get("canonical_drive_file_id")
    if not file_id:
        raise SyncError("Estado local sin identificador de Excel canónico.")
    api = api or _drive_service(runtime)
    state_id = _state_id(api, file_id)
    folder = runtime / "procesamiento" / "block-publication"
    folder.mkdir(parents=True, exist_ok=True)
    remote_state_file = folder / "remote-state.json"
    state = drive.read_json_file(api, state_id, remote_state_file)
    if state.get("status") != "free" or state.get("lock") is not None:
        raise StoreBusy("El libro compartido está bloqueado.", lock=state.get("lock"))
    if state.get("canonical_drive_file_id") != file_id:
        raise SyncError("El estado remoto apunta a otro workbook.")
    expected = (state.get("published") or {}).get("sha256")
    if not expected:
        raise SyncError("Falta hash de la versión canónica.")
    base = folder / "base.sqlite"
    drive.download(api, file_id, base)
    if sha256(base) != expected:
        raise SyncError("El workbook de Drive no coincide con el hash compartido.")
    validate_store(base)
    if registry.get("workbook_sha256") and registry["workbook_sha256"] != expected:
        _require_same_enrollment(base, registry)
    changes = planner(base, registry)
    if dry_run:
        return {"mode": "dry_run", "changes": changes, "sha256": expected}
    if not changes:
        shutil.copyfile(base, runtime / store.DB_NAME)
        atomic_json(runtime / "control" / "database-work-state.json", state)
        confirmed_registry = json.loads(json.dumps(registry))
        confirmed_registry["workbook_sha256"] = expected
        atomic_json(_location(runtime), confirmed_registry)
        return {"mode": "unchanged", "changes": [], "sha256": expected}
    machine = os.environ.get("COMPUTERNAME") or "unknown"
    locked = db._state_lock(state, machine, expected)
    drive.write_json_file(api, state_id, locked, remote_state_file)
    confirmed = drive.read_json_file(api, state_id, remote_state_file)
    db._ensure_lock(confirmed, machine, expected)
    atomic_json(runtime / "control" / "database-work-state.json", confirmed)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    checkpoint = runtime / "temporales" / f"database-{stamp}-before-blocks.sqlite"
    checkpoint.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(base, checkpoint)
    work = folder / "working.sqlite"
    shutil.copyfile(base, work)
    writer(work, changes)
    validate_store(work)
    if planner(work, registry):
        raise SyncError("Los cambios de bloques no quedaron aplicados correctamente.")
    new_sha = sha256(work)
    before_publish = folder / "before-upload.sqlite"
    drive.download(api, file_id, before_publish)
    if sha256(before_publish) != expected:
        raise SyncError("El workbook publicado cambió desde el lock.")
    db._ensure_lock(drive.read_json_file(api, state_id, remote_state_file), machine, expected)
    uploaded = drive.upload(api, file_id, work, SQLITE_MIME)
    redownloaded = folder / "redownloaded.sqlite"
    drive.download(api, file_id, redownloaded)
    if sha256(redownloaded) != new_sha:
        raise SyncError("Error de integridad en la publicación del Excel.")
    validate_store(redownloaded)
    if planner(redownloaded, registry):
        raise SyncError("Verificación semántica del workbook publicado falló.")
    shutil.copyfile(redownloaded, runtime / store.DB_NAME)
    final = json.loads(json.dumps(confirmed))
    final["status"], final["lock"] = "free", None
    final["published"] = {"sha256": new_sha, "size_bytes": redownloaded.stat().st_size,
                           "drive_modified_time": uploaded.get("modifiedTime"),
                           "validation": "drive-api-redownload-hash-sqlite-verified"}
    drive.write_json_file(api, state_id, final, remote_state_file)
    checked = drive.read_json_file(api, state_id, remote_state_file)
    if (checked.get("status") != "free" or checked.get("lock") is not None
            or (checked.get("published") or {}).get("sha256") != new_sha):
        raise SyncError("La liberación del estado compartido no quedó confirmada.")
    atomic_json(runtime / "control" / "database-work-state.json", checked)
    updated = json.loads(json.dumps(registry))
    updated["workbook_sha256"] = new_sha
    atomic_json(_location(runtime), updated)
    return {"mode": "published", "changes": changes, "sha256": new_sha,
            "checkpoint": str(checkpoint)}



def refresh_from_published(runtime, *, api=None):
    """Traer versión canónica y reconstruir el registro sin pisar decisiones locales.

    No escribe Drive. Bloquea si existen cambios locales preparatorios o
    una sesión remota de edición; exige versión y hash coherentes.
    """
    runtime = Path(runtime).resolve()
    current_book = runtime / store.DB_NAME
    control_path = runtime / "control" / "database-work-state.json"
    if not current_book.is_file() or not control_path.is_file():
        raise SyncError("Falta la instalación local de Academic Manager.")
    from .routine import workbook_version
    workbook_version(runtime)
    local_state = json.loads(control_path.read_text(encoding="utf-8"))
    file_id = local_state.get("canonical_drive_file_id")
    if not file_id:
        raise SyncError("No se conoce el workbook canónico.")
    regpath = _location(runtime)
    if regpath.exists():
        from .semester import load_registry
        current_registry = load_registry(runtime)
        if any(c.get("provenance") == "aprobado_local_pendiente_excel"
               for c in current_registry["courses"]):
            raise SyncError("Hay matrículas locales sin publicar; reconciliar antes de actualizar.")
        if current_registry.get("workbook_sha256") and current_registry["workbook_sha256"] != sha256(current_book):
            raise SyncError("El registro local está asociado con otra versión del workbook.")
        if plan(current_book, current_registry):
            raise SyncError("Hay cambios locales de bloques sin publicar; no se sobrescribirán.")
    api = api or _drive_service(runtime)
    state_id = _state_id(api, file_id)
    folder = runtime / "procesamiento" / "block-publication"
    folder.mkdir(parents=True, exist_ok=True)
    remote_path = folder / "refresh-state.json"
    state = drive.read_json_file(api, state_id, remote_path)
    if state.get("status") != "free" or state.get("lock") is not None:
        raise SyncError("La base compartida está siendo editada; no se actualizó el equipo.")
    expected = (state.get("published") or {}).get("sha256")
    if state.get("canonical_drive_file_id") != file_id or not expected:
        raise SyncError("La identidad de la base publicada no coincide.")
    downloaded = folder / "refresh.sqlite"
    drive.download(api, file_id, downloaded)
    if sha256(downloaded) != expected:
        raise SyncError("La descarga no coincide con el estado compartido.")
    validate_store(downloaded)
    from .semester import workbook_courses, _validate as valid_registry
    active, mapped, block = workbook_courses(runtime, workbook_path=downloaded)
    if block == "legacy":
        raise SyncError("La base publicada todavía no contiene campos de bloques.")
    fresh = valid_registry({"schema_version": 2, "active_semester": active,
                            "active_block": block, "courses": mapped,
                            "workbook_sha256": expected})
    again = drive.read_json_file(api, state_id, remote_path)
    if ((again.get("published") or {}).get("sha256") != expected
            or again.get("status") != "free" or again.get("lock") is not None):
        raise SyncError("La base remota cambió durante la descarga.")
    # Validar que la publicación tiene ambos campos completos antes de
    # reemplazar la copia local.
    checkpoint = folder / "local-before-refresh.sqlite"
    shutil.copyfile(current_book, checkpoint)
    shutil.copyfile(downloaded, current_book)
    atomic_json(control_path, again)
    atomic_json(regpath, fresh)
    return {"mode": "refreshed", "sha256": expected, "courses": len(mapped),
            "active_semester": active, "active_block": block}
