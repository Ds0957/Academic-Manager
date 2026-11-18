"""Clases automáticas: encontrar grabaciones nuevas y transcribirlas solas (odd/tasks/clases-automaticas.md).

La persona decidió «Transcribir sola y avisarme»: el índice sigue siendo de Claude con su
aprobación, pero bajar y transcribir una grabación no pide permiso. Corre como un paso más de la
rutina diaria (y de «Sincronizar ahora»):

1. `detect` lee el contenido de Canvas ya sincronizado de cada materia activa
   (`canvas_sync.recordings`) y las grabaciones adjuntas del horario de Calendar, y pregunta a
   Drive, una sola vez por archivo, si es un video o audio y si se puede descargar. Guarda lo
   encontrado en `procesamiento/clases-automaticas/`. Si una grabación está en las dos fuentes,
   valen la materia y la fecha de Canvas.
2. `launch_next` lanza Capture (el mismo proceso aparte de `class_capture`, uno a la vez) sobre
   la grabación descargable más antigua que falte. Al terminar esa, el proceso lanza la siguiente.

Lo que no se puede descargar queda en la lista para el botón «Transcripción de Drive».
"""
import argparse
import contextlib
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from academic_config import paths
from capture.models import CaptureError
from capture.storage.job import job_lock

from . import class_capture as cc
from . import class_state
from .actions import OperationError
from .class_state import FOLDER, MEDIA

STATE = "encontradas.json"
MAX_CHECKS = 40      # consultas a Drive por corrida; lo demás queda para la siguiente
MAX_ATTEMPTS = 2     # una grabación que falló dos veces espera a que alguien la mire
NO_RETRY = {"NOT_MEDIA", "DATE_MISMATCH", "RESUME_MISMATCH", "INVALID_ARGUMENT"}  # rechazos que no cambian solos
LAUNCH_FIELDS = ("intentos", "problema", "rechazada")  # lo que escribe `launch_next` en cada grabación
RECHECK = timedelta(days=1)  # lo que no se podía descargar se vuelve a mirar (el profesor pudo habilitarlo)
LOCK_TRIES = 30


@contextlib.contextmanager
def _locked(runtime):
    """Un solo proceso a la vez lee y escribe la lista (la rutina diaria y la cadena de Capture)."""
    folder = Path(runtime) / FOLDER
    if folder.is_symlink():
        raise CaptureError("La carpeta de clases automáticas está redirigida.")
    with contextlib.ExitStack() as held:
        for attempt in range(LOCK_TRIES):  # solo se reintenta tomar el bloqueo, nunca lo que se hace con él
            try:
                held.enter_context(job_lock(folder / "lista"))
                break
            except CaptureError:
                if attempt + 1 == LOCK_TRIES:
                    raise
                time.sleep(1)
        yield


_stamp = class_state.stamp


def found(runtime):
    """Grabaciones encontradas, por ID de archivo de Drive."""
    records = (class_state.read(runtime, STATE) or {}).get("grabaciones")
    return {k: v for k, v in records.items() if isinstance(v, dict)} if isinstance(records, dict) else {}


def _save(runtime, records):
    class_state.write(runtime, STATE, {"actualizado_utc": _stamp(), "grabaciones": records})


def _courses(runtime):
    """{curso de Canvas: materia} del semestre y bloque activos (el registro de cursos)."""
    from canvas_sync import semester
    return {str(course): subject for course, subject in
            semester.active_mappings(semester.load_registry(Path(runtime))).items()}


def _registered(runtime):
    """{(materia, fecha)} de las clases ya registradas en la base."""
    from academic_access import today as access_today
    from academic_store import store
    try:
        rows = store.read_catalog(Path(runtime) / store.DB_NAME, ("03 - Clases",))["03 - Clases"]
    except Exception:  # noqa: BLE001 - sin base legible no se descarta nada; Capture lo vuelve a comprobar
        return set()
    done = set()
    for row in rows:
        try:
            when = access_today._local_date(row.get("fecha"))
        except Exception:  # noqa: BLE001 - una fecha rara no impide ver las demás
            continue
        if when and row.get("materia_id"):
            done.add((str(row["materia_id"]), when.isoformat()))
    return done


def _check(api, entry):
    """Qué es el archivo según Drive; cambia `entry` y devuelve False si hay que reintentar."""
    try:
        data = api.files().get(fileId=entry["file_id"], supportsAllDrives=True,
                               fields="id,name,mimeType,capabilities/canDownload").execute()
    except Exception:  # noqa: BLE001 - sin permiso o sin conexión: se reintenta en la próxima corrida
        entry["problema"] = "No se pudo consultar esta grabación en Google Drive."
        return False
    mime = str(data.get("mimeType") or "")
    entry.update(nombre=data.get("name"), tipo=mime, verificada=_stamp(), ignorada=not mime.startswith(MEDIA),
                 descargable=bool((data.get("capabilities") or {}).get("canDownload", True)))
    entry.pop("problema", None)
    return True


def detect(runtime, *, api=None, now=None):
    """Busca grabaciones en Canvas y en el horario de Calendar de cada materia activa; devuelve un resumen.

    Las consultas a Drive se hacen sin tener la lista bloqueada, para no hacer esperar a la cadena de
    Capture; el bloqueo solo cubre el momento de guardar. Lo que `launch_next` haya escrito mientras
    tanto en una grabación (`LAUNCH_FIELDS`) se conserva.
    """
    runtime = Path(runtime)
    records = found(runtime)
    before = {file_id: _launch_fields(entry) for file_id, entry in records.items()}
    seen = _sources(runtime, records)
    new = _verify(runtime, [records[f] for f in seen], api, now or datetime.now(timezone.utc))
    with _locked(runtime):
        current = found(runtime)
        for file_id in seen:
            entry, theirs = records[file_id], current.get(file_id)
            if theirs is not None and _launch_fields(theirs) != before.get(file_id):
                for key in LAUNCH_FIELDS:
                    entry.pop(key, None)
                entry.update(_launch_fields(theirs))
            current[file_id] = entry
        _save(runtime, current)
    return {"encontradas": len(seen), "nuevas": new,
            "sin_verificar": sum("verificada" not in records[f] for f in seen)}


def _launch_fields(entry):
    return {key: entry[key] for key in LAUNCH_FIELDS if key in entry}


def _due(entry, now):
    """Sin verificar, o no descargable y verificada hace más de un día."""
    if "verificada" not in entry:
        return True
    if entry.get("descargable") or entry.get("ignorada"):
        return False
    try:
        checked = datetime.strptime(entry["verificada"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return True
    return now - checked > RECHECK


def _note(records, seen, file_id, **fields):
    """Anota una grabación encontrada; la primera fuente que la nombra en esta corrida decide sus datos."""
    if not file_id or file_id in seen:
        return
    seen.append(file_id)
    entry = records.setdefault(file_id, {"detectada_utc": _stamp()})
    if entry.get("rechazada") and (entry.get("materia_id"), entry.get("fecha")) != (fields["materia_id"],
                                                                                   fields["fecha"]):
        for key in ("rechazada", "problema"):  # cambió la materia o la fecha: el rechazo ya no aplica
            entry.pop(key, None)
    entry.update(file_id=file_id, **fields)


def _sources(runtime, records):
    """Anota en `records` lo que hay en Canvas y en Calendar; devuelve los archivos vistos, Canvas primero."""
    from canvas_sync import recordings
    from . import calendar_classes
    seen = []
    for course, subject in sorted(_courses(runtime).items()):
        path = runtime / "procesamiento" / "canvas-sync" / "v01" / course / "latest.json"
        if path.is_symlink() or not path.is_file():
            continue
        try:
            snapshot = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for item in recordings.from_snapshot(snapshot):
            _note(records, seen, item["file_id"], enlace=item["enlace"], fecha=item["fecha"], materia_id=subject,
                  curso=course, fuente=item.get("fuente"))
    for event in calendar_classes.events(runtime):
        for recording in event.get("grabaciones") or []:
            file_id = recording.get("file_id")
            _note(records, seen, file_id, enlace=f"https://drive.google.com/file/d/{file_id}/view",
                  fecha=event.get("fecha"), materia_id=event.get("materia_id"), fuente="Google Calendar")
    return seen


def _verify(runtime, entries, api, now):
    """Pregunta a Drive por lo que falta verificar (hasta `MAX_CHECKS`); devuelve cuántas quedaron verificadas."""
    pending = [entry for entry in entries if _due(entry, now)][:MAX_CHECKS]
    if pending and api is None:
        try:
            from capture.sources import drive
            api = drive.service(runtime=runtime)
        except Exception:  # noqa: BLE001 - sin el inicio de sesión de Drive se espera a la próxima corrida
            for entry in pending:
                entry["problema"] = ("Falta el inicio de sesión de Google Drive en Academic Manager → "
                                     "«Configuración».")
            return 0
    return sum(_check(api, entry) for entry in pending)


def _waiting(runtime, records):
    """Descargables que faltan, de la más antigua a la más reciente."""
    registered = _registered(runtime)
    waiting = []
    for entry in records.values():
        if (not entry.get("descargable") or entry.get("ignorada") or "verificada" not in entry
                or entry.get("intentos", 0) >= MAX_ATTEMPTS or entry.get("rechazada")
                or (entry.get("materia_id"), entry.get("fecha")) in registered):
            continue
        previous = cc._previous_job(runtime, entry["file_id"])
        if previous and previous.get("status") == "completed":
            continue
        waiting.append(entry)
    return sorted(waiting, key=lambda e: (e.get("fecha") or "", e["file_id"]))


def launch_next(runtime, *, popen=None, now=time.time):
    """Lanza Capture sobre la grabación que sigue, si no hay otra en curso; None si no lanza nada.

    Drive se consulta (`cc.plan`) sin tener la lista bloqueada, como en `detect`; solo un proceso
    lanza a la vez (el bloqueo de `cc.start`). Lo que cambia en cada grabación (`LAUNCH_FIELDS`) se
    guarda al final sobre la lista más reciente, también si algo falla a mitad de camino.
    """
    import subprocess
    runtime = Path(runtime)
    current = cc.status(runtime, now=now)
    if current.get("estado") in cc.RUNNING:
        return None
    changes, started = {}, None
    try:
        for entry in _waiting(runtime, found(runtime)):
            try:
                plan = cc.plan(runtime, entry["enlace"], entry["materia_id"], entry["fecha"])
                cc.start(runtime, entry["enlace"], entry["materia_id"], entry["fecha"], plan_id=plan["plan_id"],
                         approved=True, automatic=True, popen=popen or subprocess.Popen, now=now)
            except OperationError as exc:
                if exc.code in ("BUSY", "NOT_STARTED"):
                    break  # no corrió: no gasta un intento
                # no se vuelve a intentar sola si el rechazo no cambia solo; el motivo queda a la vista
                changes[entry["file_id"]] = {"problema": str(exc), "rechazada": True if exc.code in NO_RETRY else None}
                continue
            changes[entry["file_id"]] = {"intento": True, "problema": None}
            started = entry
            break
    finally:
        if changes:
            _apply(runtime, changes)
    if started is not None:
        started = {**started, "intentos": started.get("intentos", 0) + 1}
        started.pop("problema", None)
    return started


def _apply(runtime, changes):
    """Escribe los cambios de `launch_next` sobre la lista guardada más reciente."""
    with _locked(runtime):
        records = found(runtime)
        for file_id, fields in changes.items():
            entry = records.get(file_id)
            if entry is None:
                continue
            if fields.get("intento"):
                entry["intentos"] = entry.get("intentos", 0) + 1
            for key in ("problema", "rechazada"):
                if key in fields:
                    if fields[key] is None:
                        entry.pop(key, None)
                    else:
                        entry[key] = fields[key]
        _save(runtime, records)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Paso de la rutina diaria: busca grabaciones nuevas en Canvas y "
                                                 "transcribe la siguiente con Capture.")
    parser.add_argument("--runtime", default=None, help=paths.DATA_HELP)
    args = parser.parse_args(argv)
    runtime = paths.runtime(args.runtime, env=("ACADEMIC_MANAGER_DATA",), default=paths.DEFAULT_RUNTIME)
    from . import calendar_classes
    try:
        schedule = calendar_classes.fetch(runtime)
    except Exception as exc:  # noqa: BLE001 - Calendar es opcional
        schedule = {"estado": "error", "mensaje": f"Google Calendar falló ({type(exc).__name__})."}
    try:
        summary = detect(runtime)
        started = launch_next(runtime)
    except Exception as exc:  # noqa: BLE001 - este paso nunca hace fallar la sincronización de Canvas
        print(f"No se buscaron grabaciones nuevas ({type(exc).__name__}: {exc}).")
        return 0
    text = f"Clases automáticas: {summary['encontradas']} grabaciones encontradas en Canvas"
    if summary["sin_verificar"]:
        text += f", {summary['sin_verificar']} sin verificar en Drive"
    text += "."
    if schedule["estado"] == "ok":
        text += f" Google Calendar: {schedule['eventos']} clases en el horario."
    elif schedule.get("mensaje"):
        text += " " + schedule["mensaje"]
    if started:
        text += f" Se empezó a transcribir la clase de {started['materia_id']} del {started['fecha']}."
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
