"""`system`: comprobación en vivo del sistema real, solo lectura (S3).

Lo que los tests con Drive simulado no pueden ver: identidad y permisos reales de Drive, estado
y lock compartidos, versión publicada frente a la local, racha de Canvas Sync, token, índice y
tarea programada. Nunca escribe en la base, el estado ni Drive; solo descarga el estado
compartido a `procesamiento/system-check/` para leerlo.
"""
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from canvas_sync.common import StoreBusy, SyncError

from . import core
from .summary import _index, _token
from .today import local_today

TASK_NAME = "AcademicManager-CanvasDaily"
LEVELS = ("ok", "no_disponible", "aviso", "error")
READY = ("ready", "listo", "en ejecución", "running")


def _item(name, state, detail):
    return {"nombre": name, "estado": state, "detalle": detail}


def _minutes(lock):
    try:
        started = datetime.fromisoformat(str((lock or {}).get("started_at")))
        return round((datetime.now(timezone.utc) - started).total_seconds() / 60)
    except (TypeError, ValueError):
        return None


def _local(runtime):
    try:
        return _item("base_local", "ok", f"Base local íntegra y publicada ({core.workbook_version(runtime)[:12]}).")
    except StoreBusy as exc:
        minutes = _minutes(exc.lock)
        age = f" desde hace {minutes} minutos" if minutes is not None else " sin fecha"
        return _item("base_local", "aviso", f"El estado local está en edición{age}. Si no hay una "
                     "publicación en curso: scripts\\academic-store.cmd unlock.")
    except SyncError as exc:
        return _item("base_local", "error", core.LOCAL_PATH.sub("<ruta>", str(exc))[:200])


def _drive(runtime):
    try:
        from canvas_sync import workbook_blocks
        from capture.academic_manager import drive_workbook as drive
    except ImportError:
        return _item("drive", "no_disponible", "Faltan las librerías de Google en este Python.")
    try:
        local = json.loads((Path(runtime) / "control" / "database-work-state.json").read_text(encoding="utf-8"))
        file_id = local.get("canonical_drive_file_id")
        if not file_id:
            return _item("drive", "error", "El estado local no identifica la base canónica en Drive.")
        api = workbook_blocks._drive_service(runtime)
        caps = drive.metadata(api, file_id).get("capabilities") or {}
        if not caps.get("canDownload") or not caps.get("canEdit"):
            return _item("drive", "error", "La identidad de publicación no tiene lectura y edición sobre la base.")
        folder = Path(runtime) / "procesamiento" / "system-check"
        folder.mkdir(parents=True, exist_ok=True)
        remote = drive.read_json_file(api, workbook_blocks._state_id(api, file_id), folder / "remote-state.json")
    except Exception as exc:  # noqa: BLE001 — la librería de Google lanza tipos propios: todo fallo se informa como fallo de Drive
        return _item("drive", "error", "Drive no responde como se espera: "
                     + core.LOCAL_PATH.sub("<ruta>", str(exc))[:160])
    if remote.get("status") != "free" or remote.get("lock") is not None:
        minutes = _minutes(remote.get("lock"))
        age = f"desde hace {minutes} minutos" if minutes is not None else "sin fecha"
        return _item("drive", "aviso", f"La base compartida está bloqueada {age}. Si no hay una "
                     "publicación en curso: scripts\\academic-store.cmd unlock (desde el equipo del lock).")
    remote_sha = (remote.get("published") or {}).get("sha256")
    local_sha = (local.get("published") or {}).get("sha256")
    if remote_sha != local_sha:
        return _item("drive", "aviso", "Drive tiene una versión publicada por otro equipo que este "
                     "equipo aún no tiene; actualizar antes de escribir.")
    return _item("drive", "ok", "Identidad con lectura y edición; estado compartido libre y en la misma versión.")


def _canvas(runtime, day):
    history = core.sync_history(runtime)
    if history["ultimo_intento"] is None:
        return _item("canvas_sync", "no_disponible", "No hay logs de la tarea diaria.")
    warning = core.sync_warning(history, day)
    if warning:
        return _item("canvas_sync", "aviso", warning)
    return _item("canvas_sync", "ok", f"Última ejecución {history['ultimo_intento']}; último éxito "
                 f"{history['ultimo_ok'] or 'sin registro'}.")


def _token_check(runtime, day):
    token = _token(runtime, day)
    if token["vence"] is None:
        return _item("token_canvas", "aviso", token["motivo"])
    if token["aviso"]:
        return _item("token_canvas", "aviso", token["aviso"])
    return _item("token_canvas", "ok", f"Vence el {token['vence']} ({token['dias_restantes']} días).")


def _index_check(runtime):
    try:
        actual = core.workbook_version(runtime)
    except SyncError:
        return _item("indice", "no_disponible", "Sin base local válida no se puede comparar el índice.")
    index = _index(runtime, actual)
    if index["fresh"] is True:
        return _item("indice", "ok", f"Al día (generado {index['generated_at_utc']}).")
    if index["fresh"] is False:
        return _item("indice", "aviso", "Desactualizado: ejecutar scripts\\knowledge.cmd build.")
    return _item("indice", "no_disponible", "No hay índice local legible.")


def _parse_task(raw):
    """Fila CSV de `schtasks /FO CSV /NH`: (estado, próxima ejecución), o None si no tiene esa forma."""
    lines = raw.strip().splitlines()
    fields = [" ".join(f.strip('"').split()) for f in lines[0].split('","')] if lines else []
    return (fields[2], fields[1]) if len(fields) >= 3 else None


def _scheduled_task():
    if os.name != "nt":
        return "no_disponible"
    try:
        result = subprocess.run(["schtasks", "/Query", "/TN", TASK_NAME, "/FO", "CSV", "/NH"],
                                capture_output=True, encoding="oem", errors="replace", timeout=20)
    except (OSError, subprocess.SubprocessError) as exc:
        return f"No se pudo consultar la tarea programada ({type(exc).__name__})."
    if result.returncode != 0:
        # «no existe» y «acceso denegado» comparten código; el texto localizado de schtasks lo aclara.
        reason = " ".join((result.stderr or "").split())[:160]
        return (f"No se pudo consultar la tarea {TASK_NAME} (código {result.returncode})"
                + (f": {reason}" if reason else "."))
    return _parse_task(result.stdout) or "La consulta de la tarea programada devolvió un formato desconocido."


def _task_check(task):
    if task == "no_disponible":
        return _item("tarea_diaria", "no_disponible", "Solo se comprueba en Windows.")
    if task is None:
        return _item("tarea_diaria", "error", f"No existe la tarea programada {TASK_NAME}.")
    if isinstance(task, str):
        return _item("tarea_diaria", "error", task)
    status, next_run = task
    if status.casefold() not in READY:
        return _item("tarea_diaria", "error", f"La tarea {TASK_NAME} está «{status}»: no se ejecutará.")
    return _item("tarea_diaria", "ok", f"Activa; próxima ejecución: {next_run}")


def system(runtime):
    day = local_today()
    checks = [_local(runtime), _drive(runtime), _canvas(runtime, day), _token_check(runtime, day),
              _index_check(runtime), _task_check(_scheduled_task())]
    worst = max(LEVELS.index(c["estado"]) for c in checks)
    return {"estado": "ok" if worst <= 1 else LEVELS[worst], "comprobaciones": checks,
            "nota": "Comprobación en vivo y de solo lectura; no repara nada."}
