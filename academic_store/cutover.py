"""Corte supervisado de database.xlsx a la base SQLite canónica.

Protocolo: estado libre → hash publicado → migración verificada → lock → crear la base como
archivo NUEVO en Drive → re-descarga y validación → liberar con la nueva identidad →
instalar localmente. El xlsx anterior queda intacto, en Drive y en el equipo, como archivo
histórico. Tras un fallo con el lock tomado, el lock se conserva para
recuperación supervisada; nunca se libera a ciegas.
"""
import json
import os
import shutil
from datetime import datetime
from pathlib import Path

from canvas_sync.common import SyncError
from canvas_sync.workbook_blocks import _drive_service, _state_id
from capture.academic_manager import database as db
from capture.academic_manager import drive_workbook as drive
from capture.models import CaptureError
from capture.storage.metadata import atomic_json, sha256

from . import store

STORE_FILE_NAME = "Academic Manager - Database.sqlite"


def _install(root, published_copy, state):
    """Instalación local atómica: base, estado y registro de cursos."""
    db_path = root / store.DB_NAME
    tmp = db_path.with_name(f"{db_path.name}.tmp-{os.getpid()}")
    try:
        shutil.copyfile(published_copy, tmp)
        os.replace(tmp, db_path)
    finally:
        tmp.unlink(missing_ok=True)
    atomic_json(root / "control" / "database-work-state.json", state)
    registry = root / "control" / "canvas-sync" / "course-registry.json"
    if registry.is_file():
        data = json.loads(registry.read_text(encoding="utf-8"))
        data["workbook_sha256"] = state["published"]["sha256"]
        atomic_json(registry, data)


def _install_or_explain(root, published_copy, state):
    try:
        _install(root, published_copy, state)
    except OSError as exc:
        raise SyncError(f"Drive ya tiene la base publicada, pero la instalación local falló ({exc}). "
                        "Repita el corte con --apply para completar la instalación.") from exc


def _resume(api, root, state, folder):
    """Completa la instalación local de un corte ya publicado en Drive."""
    expected = (state.get("published") or {}).get("sha256")
    copy = folder / "resume.sqlite"
    drive.download(api, state["canonical_drive_file_id"], copy)
    if not expected or sha256(copy) != expected:
        raise SyncError("La base publicada no coincide con published.sha256; no se instala.")
    store.validate(copy)
    _install_or_explain(root, copy, state)
    return {"mode": "resumed", "sha256": expected, "rows": store.info(copy)["rows"],
            "store_drive_file_id": state["canonical_drive_file_id"],
            "next": "Reconstruir el índice: scripts\\knowledge.cmd build"}


def cutover(runtime, *, api=None, machine=None, apply=False):
    root = Path(runtime).resolve()
    db_path = root / store.DB_NAME
    xlsx = root / "database.xlsx"
    state_path = root / "control" / "database-work-state.json"
    if db_path.exists():
        raise SyncError("La base SQLite ya existe en el runtime; el corte ya se hizo o quedó a medias.")
    if not xlsx.is_file() or not state_path.is_file():
        raise SyncError("Falta database.xlsx o el estado local de control.")
    local_state = json.loads(state_path.read_text(encoding="utf-8"))
    xlsx_id = local_state.get("canonical_drive_file_id")
    if not xlsx_id:
        raise SyncError("El estado local no identifica el workbook canónico.")
    machine = machine or os.environ.get("COMPUTERNAME") or "unknown"
    folder = root / "procesamiento" / "cutover"
    folder.mkdir(parents=True, exist_ok=True)
    remote = folder / "remote-state.json"
    try:
        api = api or _drive_service(root)
        state_id = _state_id(api, xlsx_id)
        state = drive.read_json_file(api, state_id, remote)
        if state.get("status") != "free" or state.get("lock") is not None:
            raise SyncError("La base compartida está bloqueada; no se hace el corte.")
        if state.get("canonical_drive_file_id") != xlsx_id:
            if state.get("legacy_xlsx_drive_file_id") == xlsx_id and apply:
                return _resume(api, root, state, folder)
            raise SyncError("El estado remoto apunta a otro archivo canónico.")
        expected = (state.get("published") or {}).get("sha256")
        if not expected or sha256(xlsx) != expected:
            raise SyncError("La copia local de database.xlsx no coincide con la versión publicada; "
                            "actualice este equipo antes del corte.")
        base = folder / "base.xlsx"
        drive.download(api, xlsx_id, base)
        if sha256(base) != expected:
            raise SyncError("El workbook de Drive no coincide con published.sha256.")
        candidate = folder / "candidate.sqlite"
        candidate.unlink(missing_ok=True)
        store.migrate(base, candidate)
        rows = store.info(candidate)["rows"]
        parent = (drive.metadata(api, xlsx_id).get("parents") or [None])[0]
        if not parent:
            raise SyncError("El workbook no tiene carpeta canónica verificable.")
        # La cuenta de servicio no tiene cuota propia en un Drive personal: no puede crear
        # archivos, pero sí actualizar uno que el dueño haya creado en la carpeta.
        existing = [f["id"] for f in drive.find_in_folder(api, parent, STORE_FILE_NAME)]
        if len(existing) > 1:
            raise SyncError(f"Hay varios archivos «{STORE_FILE_NAME}» en la carpeta; deje solo uno.")
        if xlsx_id in existing or state_id in existing:
            raise SyncError("El archivo de la base coincide con el workbook o el estado compartido.")
        if not apply:
            return {"mode": "dry_run", "rows": rows, "source_sha256": expected,
                    "store_file": "reuse" if existing else "create",
                    "next": "Repetir con --apply para publicar la base."}

        locked = db._state_lock(state, machine, expected)
        drive.write_json_file(api, state_id, locked, remote)
        confirmed = drive.read_json_file(api, state_id, remote)
        db._ensure_lock(confirmed, machine, expected)
        atomic_json(state_path, confirmed)

        checkpoint = root / "temporales" / f"database-{datetime.now():%Y%m%d-%H%M%S}-before-cutover.xlsx"
        checkpoint.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(base, checkpoint)
        if existing:
            created = {"id": existing[0], **drive.upload(api, existing[0], candidate, db.SQLITE_MIME)}
        else:
            created = drive.create(api, parent, STORE_FILE_NAME, candidate, db.SQLITE_MIME)
        check = folder / "redownloaded.sqlite"
        drive.download(api, created["id"], check)
        new_sha = sha256(check)
        if new_sha != sha256(candidate):
            raise SyncError("La base re-descargada no coincide con la publicada.")
        store.validate(check)

        unchanged = folder / "before-release.xlsx"
        drive.download(api, xlsx_id, unchanged)
        if sha256(unchanged) != expected:
            raise SyncError("El workbook anterior cambió en Drive durante el corte.")
        db._ensure_lock(drive.read_json_file(api, state_id, remote), machine, expected)
        final = json.loads(json.dumps(confirmed))
        final.update({"status": "free", "lock": None, "canonical_drive_file_id": created["id"],
                      "legacy_xlsx_drive_file_id": xlsx_id})
        final["resource"] = STORE_FILE_NAME
        final["published"] = {"sha256": new_sha, "size_bytes": check.stat().st_size,
                              "drive_modified_time": created.get("modifiedTime"),
                              "validation": "cutover-from-xlsx-migrated-verified-redownloaded",
                              "source_xlsx_sha256": expected}
        drive.write_json_file(api, state_id, final, remote)
        checked = drive.read_json_file(api, state_id, remote)
        if (checked.get("status") != "free" or checked.get("lock") is not None
                or checked.get("canonical_drive_file_id") != created["id"]
                or (checked.get("published") or {}).get("sha256") != new_sha):
            raise SyncError("La liberación del estado compartido no quedó confirmada.")
    except CaptureError as exc:
        raise SyncError(str(exc)) from exc
    except store.StoreError as exc:
        raise SyncError(str(exc)) from exc

    _install_or_explain(root, check, checked)
    return {"mode": "cut_over", "rows": rows, "sha256": new_sha,
            "store_drive_file_id": created["id"], "legacy_xlsx_drive_file_id": xlsx_id, "legacy_checkpoint": str(checkpoint),
            "next": "Reconstruir el índice: scripts\\knowledge.cmd build"}
