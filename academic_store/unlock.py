"""Recuperación supervisada de un lock retenido de la base compartida.

Solo libera lo que es seguro liberar: un lock de ESTE equipo, de más de 10 minutos (ninguna
publicación normal dura tanto), sin una escritura del chat en curso, y cuando la base de Drive
sigue teniendo exactamente el hash desde el que se tomó el lock, que es el publicado; es decir,
nada se llegó a subir bajo ese lock. También repara un estado local que quedó en edición cuando
Drive ya está libre. Cualquier otro caso se rechaza con su motivo y no toca nada.
Sin --apply solo describe lo que haría.
"""
import contextlib
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

from canvas_sync.common import SyncError
from canvas_sync.workbook_blocks import _drive_service, _state_id
from capture.academic_manager import drive_workbook as drive
from capture.models import CaptureError
from capture.storage.job import job_lock
from capture.storage.metadata import atomic_json, sha256

from . import store
from .cutover import _install


def _minutes(lock, now):
    try:
        started = datetime.fromisoformat(str(lock.get("started_at")))
    except (TypeError, ValueError):
        return None  # un lock sin fecha no viene de una publicación en curso
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)  # los locks se fechan en UTC
    return (now - started).total_seconds() / 60


def _chat_turn(root):
    from academic_operations import chat
    return chat._turn_dir(root), chat.STALE_LOCK_SECONDS / 60


def _check(api, root, state_id, file_id, machine, now, folder):
    """Valida el lock remoto y devuelve (estado remoto, copia descargada de la base, minutos)."""
    remote = drive.read_json_file(api, state_id, folder / "remote-state.json")
    lock = remote.get("lock") if isinstance(remote.get("lock"), dict) else {}
    published = (remote.get("published") or {}).get("sha256")
    if remote.get("canonical_drive_file_id") != file_id or not published:
        raise SyncError("El estado remoto no identifica la base publicada de este equipo.")
    if lock.get("machine") != machine:
        raise SyncError("El lock es de otro equipo: recupérelo desde ese equipo.")
    _, stale = _chat_turn(root)
    minutes = _minutes(lock, now)
    if minutes is not None and minutes < stale:
        raise SyncError(f"El lock tiene menos de {stale:.0f} minutos: puede haber una publicación "
                        "en curso; espere y repita.")
    if lock.get("base_sha256") != published:
        raise SyncError("El lock no corresponde a la versión publicada: requiere revisión manual.")
    copy = folder / "published.sqlite"
    drive.download(api, file_id, copy)
    if sha256(copy) != published:
        raise SyncError("La base de Drive cambió bajo el lock (posible publicación a medias): "
                        "requiere revisión manual; no se libera.")
    store.validate(copy)
    return remote, copy, minutes


def unlock(runtime, *, api=None, machine=None, apply=False, now=None):
    root = Path(runtime).resolve()
    state_path = root / "control" / "database-work-state.json"
    local = json.loads(state_path.read_text(encoding="utf-8"))
    file_id = local.get("canonical_drive_file_id")
    if not file_id:
        raise SyncError("El estado local no identifica la base canónica.")
    machine = machine or os.environ.get("COMPUTERNAME") or "unknown"
    now = now or datetime.now(timezone.utc)
    folder = root / "procesamiento" / "unlock"
    folder.mkdir(parents=True, exist_ok=True)
    turn, _ = _chat_turn(root)
    try:
        api = api or _drive_service(root)
        state_id = _state_id(api, file_id)
        # Se mantiene el turno del chat durante todo: ninguna escritura empieza mientras tanto.
        with contextlib.ExitStack() as held:
            try:
                held.enter_context(job_lock(turn))
            except CaptureError:
                raise SyncError("Hay una escritura del chat en curso: espere a que termine.") from None
            return _unlock(api, root, state_path, local, state_id, file_id, machine, now,
                           folder, apply)
    except CaptureError as exc:
        raise SyncError(str(exc)) from exc
    except store.StoreError as exc:
        raise SyncError(str(exc)) from exc


def _backup(root, now):
    """Copia de la base local antes de sustituirla: lo no publicado no se pierde."""
    name = f"database-{now.astimezone():%Y%m%d-%H%M%S}-before-unlock.sqlite"
    target = root / "temporales" / name
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(root / store.DB_NAME, target)
    return name


REPLACE_NOTE = (" La base local difiere de la publicada y se sustituirá por la de Drive; antes se "
                "guarda una copia en temporales\\ con lo que solo estaba en este equipo.")


def _unlock(api, root, state_path, local, state_id, file_id, machine, now, folder, apply):
    remote = drive.read_json_file(api, state_id, folder / "remote-state.json")
    if remote.get("status") == "free" and remote.get("lock") is None:
        published = (remote.get("published") or {}).get("sha256")
        matches = bool(published) and sha256(root / store.DB_NAME) == published
        if local.get("status") == "free" and local.get("lock") is None:
            if matches:
                return {"mode": "nothing_to_do", "detalle": "La base y el estado local están libres."}
            raise SyncError("Drive y el estado local están libres, pero la base local no coincide con "
                            "la publicada: actualice el equipo; unlock no la sustituye.")
        if matches:
            if not apply:
                return {"mode": "dry_run", "accion": "reparar_estado_local", "base_local": "coincide",
                        "detalle": "Drive está libre; el estado local quedó en edición y se copiará el remoto."}
            atomic_json(state_path, remote)
            return {"mode": "local_repaired", "sha256": published}
        # Estado local en edición y base distinta: una publicación o un unlock quedaron a medias.
        if remote.get("canonical_drive_file_id") != file_id or not published:
            raise SyncError("El estado remoto no identifica la base publicada de este equipo.")
        copy = folder / "published.sqlite"
        drive.download(api, file_id, copy)
        if sha256(copy) != published:
            raise SyncError("La base de Drive no coincide con published.sha256; no se instala.")
        store.validate(copy)
        if not apply:
            return {"mode": "dry_run", "accion": "instalar_publicada", "base_local": "se_reemplazara",
                    "sha256": published,
                    "detalle": "Drive está libre; el estado local quedó en edición." + REPLACE_NOTE}
        backup = _backup(root, now)
        _install(root, copy, remote)
        return {"mode": "local_installed", "sha256": published, "copia_local": backup}
    remote, copy, minutes = _check(api, root, state_id, file_id, machine, now, folder)
    lock = {"machine": remote["lock"].get("machine"), "started_at": remote["lock"].get("started_at"),
            "minutos": round(minutes) if minutes is not None else None}
    replaces = sha256(root / store.DB_NAME) != remote["published"]["sha256"]
    if not apply:
        detail = "Nada se publicó bajo este lock: se liberará sin cambiar la versión publicada."
        if replaces:
            detail += REPLACE_NOTE
        return {"mode": "dry_run", "accion": "liberar_lock", "lock": lock,
                "sha256": remote["published"]["sha256"],
                "base_local": "se_reemplazara" if replaces else "coincide", "detalle": detail}
    again = drive.read_json_file(api, state_id, folder / "remote-state.json")
    if again != remote:
        raise SyncError("El estado remoto cambió durante la comprobación; repita la simulación.")
    final = json.loads(json.dumps(remote))
    final["status"], final["lock"] = "free", None
    drive.write_json_file(api, state_id, final, folder / "remote-state.json")
    checked = drive.read_json_file(api, state_id, folder / "remote-state.json")
    if (checked.get("status") != "free" or checked.get("lock") is not None
            or checked.get("published") != remote["published"]):
        raise SyncError("La liberación del estado compartido no quedó confirmada.")
    backup = None
    if not replaces:
        atomic_json(state_path, checked)
    else:
        backup = _backup(root, now)
        # Base local, estado y registro de cursos; si falla, repetir unlock termina la instalación.
        _install(root, copy, checked)
    return {"mode": "released", "lock": lock, "sha256": checked["published"]["sha256"],
            "copia_local": backup, "next": "Repetir la escritura que falló con una vista previa nueva."}
