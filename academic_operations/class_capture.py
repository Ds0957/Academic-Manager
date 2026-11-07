"""Procesar y completar una clase desde Claude (odd/tasks/capture-desde-claude.md).

Lo que tarda corre en un proceso aparte, uno a la vez, para que la conversación pueda seguir o
cerrarse: Capture dura lo que dura transcribir, y `actions.complete` publica en Drive. El proceso
recibe solo la carpeta de datos y lee su pedido de `estado.json`, de modo que ningún texto de la
conversación llega a una línea de comandos. El bloqueo del sistema se libera aunque el proceso
muera, y así se distingue un trabajo en curso de uno interrumpido.

No hay rutas ni comandos libres: entra un enlace de Google Drive, y escribir solo reutiliza los
contratos existentes de Capture y `academic_operations`, siempre tras vista previa y aprobación.
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from datetime import date, datetime
from pathlib import Path

from academic_access import core as access, today as access_today
from academic_config import paths
from academic_store import store
from capture.models import CaptureError
from capture.sources import drive
from capture.storage.job import job_lock
from capture.storage.metadata import atomic_json
from capture.storage.paths import job_paths
from . import actions
from .actions import OperationError, _fail

FOLDER = Path("procesamiento") / "claude-capture"
STATE = "estado.json"
START_GRACE = 120  # segundos que un proceso recién lanzado tiene para tomar el bloqueo
LOCK_TRIES = 5  # una consulta de «capturas» puede tener el bloqueo un instante
MAX_READ = 20_000
CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_PROCESS_GROUP = 0x00000200
JOB_PATTERN = r"[0-9a-f]{32}"
RUNNING = ("lanzada", "en_curso")

DRIVE_DENIED = ("Google Drive no dejó abrir esa grabación con la cuenta de Academic Manager. Revise que el enlace "
                "sea correcto y que esa cuenta pueda ver y descargar el archivo.")
DRIVE_FAILED = ("No se pudo consultar Google Drive. Revise la conexión y el inicio de sesión en Academic Manager "
                "→ «Configuración», y vuelva a intentarlo.")
BUSY = ("Ya hay una clase en proceso en este PC. Consulte «capturas» y vuelva a intentarlo cuando termine; se "
        "procesa una a la vez.")
NOT_STARTED = "Windows no dejó empezar el proceso. Inténtelo otra vez; si sigue igual, pida ayuda."
UNEXPECTED = "El proceso terminó con un error inesperado. Consulte «capturas» e inténtelo otra vez."
CAPTURED = "La transcripción está lista. Falta el análisis: use «leer_transcripcion» y luego «completar_clase»."


def _folder(runtime):
    return Path(runtime) / FOLDER


def _read(runtime):
    try:
        data = json.loads((_folder(runtime) / STATE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _write(runtime, state):
    folder = _folder(runtime)
    folder.mkdir(parents=True, exist_ok=True)
    atomic_json(folder / STATE, state)  # con los reintentos de Windows si alguien lo está leyendo
    return state


def _locked(runtime):
    try:
        with job_lock(_folder(runtime)):
            return False
    except CaptureError:
        return True


def _settled(runtime, now=time.time):
    """El estado guardado; si su proceso ya no existe, lo muestra interrumpido sin escribir nada."""
    state = _read(runtime)
    if state is None or state.get("estado") not in RUNNING:
        return state
    if _locked(runtime):
        return state
    state = _read(runtime)  # pudo terminar justo antes de mirar el bloqueo
    if state is None or state.get("estado") not in RUNNING:
        return state
    started = state.get("inicio_epoch")
    if state["estado"] == "lanzada" and isinstance(started, (int, float)) and now() - started <= START_GRACE:
        return state
    return {**state, "estado": "interrumpida",
            "mensaje": "El proceso no terminó (pudo cerrarse el PC). Puede volver a intentarlo."}


def status(runtime, *, now=time.time):
    state = _settled(runtime, now)
    if state is None:
        return {"estado": "ninguna"}
    return {key: value for key, value in state.items() if key not in ("pedido", "inicio_epoch")}


def _subject(runtime, subject_id):
    """(nombre, semestre) de una materia del semestre activo."""
    try:
        catalog = store.read_catalog(Path(runtime) / store.DB_NAME, ("01 - Materias", "05 - Semestres"))
        active = set(access_today._active_subjects(catalog))
    except (store.StoreError, access.AccessError, OSError, KeyError):
        _fail("SUBJECT_UNMAPPED", "No se pudo consultar la base para identificar la materia.")
    for row in catalog["01 - Materias"]:
        if str(row.get("materia_id") or "") == subject_id and subject_id in active:
            return str(row["nombre"]).strip(), str(row["semestre"])
    _fail("SUBJECT_UNMAPPED", f"{subject_id} no es una materia del semestre activo.")


def _drive_link(link):
    """URL canónica del archivo: entra solo un enlace HTTPS de Drive, nunca una ruta o un ID suelto."""
    if not isinstance(link, str) or not link.startswith("https://"):
        _fail("INVALID_ARGUMENT", "Indique el enlace de Google Drive de la grabación.")
    try:
        identity = drive.file_id(link)
    except CaptureError:
        _fail("INVALID_ARGUMENT", "Indique el enlace de Google Drive de la grabación.")
    return f"https://drive.google.com/file/d/{identity}/view"


def _inspect(runtime, source, api):
    try:
        return drive.inspect(source, api if api is not None else drive.service(runtime=runtime))
    except CaptureError as exc:
        denied = isinstance(exc.__cause__, Exception) and getattr(
            getattr(exc.__cause__, "resp", None), "status", None) in (401, 403, 404)
        if denied or "canDownload" in str(exc):
            _fail("DRIVE_DENIED", DRIVE_DENIED)
        if "documento nativo" in str(exc):
            _fail("NOT_MEDIA", "Ese enlace no es una grabación de audio o video.")
        _fail("DRIVE_UNAVAILABLE", DRIVE_FAILED)


def _previous_job(runtime, file_id):
    """El job.json más reciente de esa grabación."""
    found = None
    for path in (Path(runtime) / "procesamiento").glob("*/job.json"):
        if path.is_symlink():
            continue
        try:
            job = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            continue
        if isinstance(job, dict) and (job.get("source_metadata") or {}).get("file_id") == file_id:
            if found is None or str(job.get("created_at") or "") >= str(found.get("created_at") or ""):
                found = job
    return found


def plan(runtime, link, subject_id, day, *, api=None):
    """Vista previa de procesar una grabación: consulta Drive y la base, y no escribe nada."""
    source = _drive_link(link)
    try:
        date.fromisoformat(day)
    except (TypeError, ValueError):
        _fail("INVALID_ARGUMENT", "La fecha debe ser YYYY-MM-DD.")
    subject, semester = _subject(runtime, subject_id)
    meta = _inspect(runtime, source, api)
    if not str(meta.get("mime_type") or "").startswith(("video/", "audio/")):
        _fail("NOT_MEDIA", "Ese enlace no es una grabación de audio o video.")
    named = re.search(r"(20\d{2})[/_-](\d{2})[/_-](\d{2})", meta["original_name"])
    if named and "-".join(named.groups()) != day:
        _fail("DATE_MISMATCH", f"La grabación dice {'-'.join(named.groups())}, no {day}. Confirme la fecha con "
                               "la persona; la clase se registra con la fecha indicada.")
    previous = _previous_job(runtime, meta["file_id"])
    if previous and previous.get("status") == "completed":
        _fail("ALREADY_PROCESSED", f"Esa grabación ya está transcrita (trabajo {previous.get('job_id')}). Consulte "
                                   "«capturas»; si falta registrarla, use «completar_clase».")
    if previous:  # reanudar usa lo que pidió el trabajo: la vista previa no puede mostrar otra cosa
        asked = previous.get("request") or {}
        kept = (asked.get("semester"), asked.get("subject"), asked.get("class_date"))
        if kept != (semester, subject, day):
            _fail("RESUME_MISMATCH", f"Esa grabación ya tiene un trabajo sin terminar de {kept[1]} ({kept[2]}, semestre "
                                     f"{kept[0]}). Se reanuda con esos datos: repita la vista previa con ellos.")
    fields = {"fuente": source, "grabacion": meta["original_name"], "modificado": meta.get("modified_time"),
              "bytes": meta.get("size_bytes"), "materia": subject, "materia_id": subject_id,
              "semestre": semester, "fecha": day, "reanuda": previous.get("job_id") if previous else None}
    digest = hashlib.sha256(json.dumps(fields, ensure_ascii=True, sort_keys=True).encode("utf-8")).hexdigest()
    return {**fields, "plan_id": digest}


def _launch(runtime, order, popen, now):
    runtime = Path(runtime)
    try:
        with job_lock(_folder(runtime) / "lanzar"):
            current = _settled(runtime, now)
            if _locked(runtime) or (current and current.get("estado") in RUNNING):
                _fail("BUSY", BUSY)
            state = _write(runtime, {"estado": "lanzada", "inicio_epoch": now(), "pedido": order,
                                     "inicio": datetime.now().astimezone().isoformat(timespec="seconds")})
    except CaptureError:
        _fail("BUSY", BUSY)
    from academic_setup.whisperx import console_python
    argv = [str(console_python()), "-m", "academic_operations.class_capture", "--runtime", str(runtime)]
    flags = CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    try:
        popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
              cwd=str(Path(__file__).resolve().parents[1]), close_fds=True, creationflags=flags)
    except OSError:
        _write(runtime, {**state, "estado": "error", "mensaje": NOT_STARTED})
        _fail("NOT_STARTED", NOT_STARTED)
    return {"estado": state["estado"], "inicio": state["inicio"]}


def _approved(plan_id, expected, approved):
    if not approved:
        _fail("APPROVAL_REQUIRED", "Empezar requiere la aprobación explícita de la persona.")
    if plan_id != expected:
        _fail("PLAN_CHANGED", "Algo cambió desde la vista previa. Pida una vista previa nueva y vuelva a pedir "
                              "aprobación.")


def start(runtime, link, subject_id, day, *, plan_id, approved, api=None, popen=subprocess.Popen, now=time.time):
    """Con la aprobación de la persona, lanza Capture sobre lo que mostró la vista previa."""
    if not approved:
        _approved(plan_id, plan_id, False)
    current = plan(runtime, link, subject_id, day, api=api)
    _approved(plan_id, current["plan_id"], approved)
    return _launch(runtime, {"tipo": "capturar", "fuente": current["fuente"], "semestre": current["semestre"],
                             "materia": current["materia"], "fecha": current["fecha"],
                             "reanuda": current["reanuda"]}, popen, now)


def _job_id(job_id):
    if not isinstance(job_id, str) or not re.fullmatch(JOB_PATTERN, job_id):
        _fail("INVALID_ARGUMENT", "Indique el identificador del trabajo, como lo devuelve «capturas».")
    return job_id


def transcript(runtime, job_id, offset=0, length=12_000):
    """Trozo de la transcripción de un trabajo, registrado o no."""
    work, _ = job_paths(Path(runtime), _job_id(job_id))
    path = work / "artifacts" / "transcript.md"
    if work.is_symlink() or path.parent.is_symlink() or path.is_symlink() or not path.is_file():
        _fail("NOT_FOUND", "Ese trabajo no tiene transcripción.")
    text = path.read_text(encoding="utf-8-sig")
    piece = text[offset:offset + min(length, MAX_READ)]
    following = offset + len(piece)
    return {"trabajo": job_id, "texto": piece, "offset": offset, "siguiente_offset": following,
            "hay_mas": following < len(text), "total": len(text)}


def report_name(job_id):
    return f"claude-{job_id}.md"


def _store_report(runtime, job_id, analysis):
    folder = Path(runtime) / "procesamiento" / "analysis-review"
    if folder.is_symlink():
        _fail("NOT_FOUND", "La carpeta de informes está redirigida.")
    folder.mkdir(parents=True, exist_ok=True)
    name = report_name(job_id)
    temporary = folder / (name + ".tmp")
    temporary.write_text(analysis, encoding="utf-8")
    os.replace(temporary, folder / name)
    return name


def complete_preview(runtime, job_id, analysis):
    """Guarda el borrador del análisis y comprueba la clase; no publica ni registra."""
    runtime = Path(runtime)
    work, _ = job_paths(runtime, _job_id(job_id))
    if work.is_symlink() or not (work / "job.json").is_file():
        _fail("NOT_FOUND", "Ese trabajo no existe. Consulte «capturas».")
    return actions.preview(runtime, job_id, _store_report(runtime, job_id, analysis))


def complete_start(runtime, job_id, analysis, *, plan_id, approved, popen=subprocess.Popen, now=time.time):
    """Con la aprobación de la persona, publica y registra en segundo plano."""
    if not approved:
        _approved(plan_id, plan_id, False)
    current = complete_preview(runtime, job_id, analysis)
    _approved(plan_id, current["plan_id"], approved)
    return _launch(runtime, {"tipo": "completar", "job_id": job_id, "informe": report_name(job_id),
                             "plan_id": plan_id}, popen, now)


def _work(runtime, order):
    """Hace el pedido guardado y devuelve los campos del resultado."""
    if order["tipo"] == "completar":
        done = actions.complete(runtime, order["job_id"], filename=order["informe"], plan_id=order["plan_id"],
                                approved=True)
        text = f"La clase quedó publicada y registrada como {done.get('class_id')}."
        if done.get("warning"):
            text += " " + done["warning"]
        return {"job_id": order["job_id"], "clase_id": done.get("class_id"), "mensaje": text}
    if order.get("reanuda"):
        job = actions._capture(runtime, "--resume", order["reanuda"])
    else:
        job = actions._capture(runtime, "--source=" + order["fuente"], "--semester=" + order["semestre"],
                               "--subject=" + order["materia"], "--class-date=" + order["fecha"])
    return {"job_id": job["job_id"], "mensaje": CAPTURED}


def _serve(runtime):
    state = _read(runtime)
    if not state or state.get("estado") != "lanzada":
        return 1
    state = _write(runtime, {**state, "estado": "en_curso"})
    try:
        _write(runtime, {**state, "estado": "listo", **_work(runtime, state["pedido"])})
        return 0
    except (OperationError, CaptureError) as exc:
        message = str(exc)
    except Exception:  # noqa: BLE001 - nada interno llega a la persona
        message = UNEXPECTED
    _write(runtime, {**state, "estado": "error", "mensaje": message})
    return 1


def run(runtime):
    """Proceso aparte: un solo pedido, con el bloqueo tomado hasta terminar."""
    runtime = Path(runtime)
    for attempt in range(LOCK_TRIES):
        try:
            with job_lock(_folder(runtime)):
                return _serve(runtime)
        except CaptureError:  # `_serve` ya atiende los suyos: aquí solo llega el bloqueo ocupado
            if attempt + 1 < LOCK_TRIES:
                time.sleep(1)
    return 1


def main(argv=None):
    parser = argparse.ArgumentParser(description="Uso interno: atiende el pedido guardado por el conector.")
    parser.add_argument("--runtime", default=None, help=paths.DATA_HELP)
    args = parser.parse_args(argv)
    return run(paths.runtime(args.runtime, env=("ACADEMIC_MANAGER_DATA",), default=paths.DEFAULT_RUNTIME))


if __name__ == "__main__":
    sys.exit(main())
