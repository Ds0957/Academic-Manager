"""Register processed classes in the canonical Academic Manager SQLite store."""
import json
import os
import shutil
from datetime import datetime, timedelta
from pathlib import Path

from academic_store import store

from ..models import CaptureError, now
from ..storage.metadata import atomic_json, sha256
from . import drive_workbook

SQLITE_MIME = "application/vnd.sqlite3"
EXCEL_EPOCH = datetime(1899, 12, 30)
CLASS_HEADERS = (
    "clase_id", "materia_id", "fecha", "hora_inicio", "hora_fin", "titulo",
    "tema_principal", "estado_sesion", "url_sesion", "url_grabacion",
    "estado_grabacion", "transcripcion", "analisis", "procesada", "fuente",
    "verificacion", "notas",
)


def _store_error(exc):
    return CaptureError(str(exc))


def validate_workbook(path, expected_class_id=None):
    """Valida la base SQLite canónica y el contrato V2 de 03 - Clases."""
    try:
        store.validate(path)
        headers = tuple(store.headers(path, "03 - Clases"))
        if headers != CLASS_HEADERS:
            raise CaptureError("03 - Clases no coincide con el contrato V2.")
        if expected_class_id:
            ids = {str(r["clase_id"]).strip()
                   for r in store.read_catalog(path, ("03 - Clases",))["03 - Clases"]}
            if expected_class_id not in ids:
                raise CaptureError("La clase esperada no aparece en la base validada.")
    except store.StoreError as exc:
        raise _store_error(exc) from exc
    return True


def class_id_prefix(subject_id, class_date):
    if not subject_id.startswith("MAT-"):
        raise CaptureError("materia_id debe usar un ID interno MAT-*.")
    code = subject_id[4:].strip().upper()
    if not code or not code.replace("-", "").isalnum():
        raise CaptureError("materia_id no puede convertirse a un ID de clase estable.")
    try:
        date_value = datetime.strptime(class_date, "%Y-%m-%d")
    except (TypeError, ValueError) as exc:
        raise CaptureError("La clase necesita fecha verificada YYYY-MM-DD.") from exc
    return f"CLS-{code}-{date_value:%Y%m%d}-"


def next_class_id(class_ids, subject_id, class_date):
    prefix = class_id_prefix(subject_id, class_date)
    maximum = 0
    for value in class_ids:
        value = str(value).strip()
        if value.startswith(prefix):
            suffix = value[len(prefix):]
            if suffix.isdigit():
                maximum = max(maximum, int(suffix))
    return prefix + f"{maximum + 1:02d}"


def _date_text(value):
    # Equivale al texto de una celda con formato yyyy-mm-dd: solo cuenta el día.
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return (EXCEL_EPOCH + timedelta(days=value)).strftime("%Y-%m-%d")
    return str(value or "").strip()[:10]


def build_class_record(job, subject_id):
    request = job.get("request") or {}
    source = job.get("source_metadata") or {}
    publication = job.get("publication_path")
    if not publication:
        raise CaptureError("La clase debe tener artefactos publicados antes de registrarse.")
    if request.get("duration") is not None:
        raise CaptureError("Un fragmento de prueba no puede registrarse como clase completa.")
    class_date = request.get("class_date")
    if not class_date:
        raise CaptureError("La clase no tiene fecha verificada.")
    if not source.get("original_name") or not source.get("location"):
        raise CaptureError("Falta procedencia verificable de la grabación.")
    folder = Path(publication).name
    relative = Path(request["semester"]) / request["subject"] / "Clases" / folder
    analysis_status = job.get("analysis_status") or ""
    if not analysis_status.startswith("completed"):
        raise CaptureError("El análisis debe estar completado/revisado antes del registro académico.")
    engine = job.get("engine") or {}
    duration = (job.get("media") or {}).get("duration_seconds")
    segment_note = ""
    normalized = Path(job.get("_work_dir", "")) / "normalized.json" if job.get("_work_dir") else None
    if normalized and normalized.is_file():
        try:
            segments = len(json.loads(normalized.read_text(encoding="utf-8"))["segments"])
            segment_note = f"; {segments} segmentos"
        except Exception:
            pass
    notes = (
        f"Capture job {job['job_id']}. "
        + (f"Duración {duration} s" if duration is not None else "Duración no verificada")
        + (f". {engine.get('engine')} {engine.get('model') or ''}".rstrip() if engine else "")
        + segment_note
        + f". analysis_status={analysis_status}."
    )
    return {
        "materia_id": subject_id,
        "fecha": class_date,
        "hora_inicio": None,
        "hora_fin": None,
        "titulo": source["original_name"],
        "tema_principal": None,
        "estado_sesion": "realizada",
        "url_sesion": None,
        "url_grabacion": source["location"],
        "estado_grabacion": "procesada",
        "transcripcion": (relative / "transcript.md").as_posix(),
        "analisis": (relative / "analysis.md").as_posix(),
        "procesada": True,
        "fuente": "Google Drive" if source.get("kind") == "drive" else "archivo local",
        "verificacion": "Grabación completa procesada con Capture V0; transcripción y análisis publicados y validados por SHA-256.",
        "notas": notes,
    }


def find_existing_class(path, record):
    """Return the existing class ID when the same verified recording is already registered."""
    try:
        classes = store.read_catalog(path, ("03 - Clases",))["03 - Clases"]
    except store.StoreError as exc:
        raise _store_error(exc) from exc
    for row in classes:
        if (str(row.get("materia_id") or "").strip() == record["materia_id"] and
                _date_text(row.get("fecha")) == record["fecha"] and
                str(row.get("url_grabacion") or "").strip() == record["url_grabacion"]):
            return str(row["clase_id"]).strip()
    return None


def insert_class(path, record):
    try:
        if tuple(store.headers(path, "03 - Clases")) != CLASS_HEADERS:
            raise CaptureError("03 - Clases no coincide con el contrato V2.")
        classes = store.read_catalog(path, ("03 - Clases",))["03 - Clases"]
        class_id = next_class_id([r["clase_id"] for r in classes],
                                 record["materia_id"], record["fecha"])
        used = store.rows_in_use(path, "03 - Clases")
        target = next((row for row in range(2, 501) if row not in used), None)
        if target is None:
            raise CaptureError("03 - Clases no tiene filas libres en el rango V2.")
        day = datetime.strptime(record["fecha"], "%Y-%m-%d")
        values = {**record, "clase_id": class_id, "fecha": float((day - EXCEL_EPOCH).days)}
        columns = store.column_map(path, "03 - Clases")
        store.apply_cells(path, [("03 - Clases", target, columns[h], None, values[h])
                                 for h in CLASS_HEADERS if values[h] is not None])
    except store.StoreError as exc:
        raise _store_error(exc) from exc
    return class_id


def _state_lock(state, machine, base_sha):
    locked = json.loads(json.dumps(state))
    locked["status"] = "editing"
    locked["lock"] = {"machine": machine, "started_at": now(), "base_sha256": base_sha}
    return locked


def _ensure_lock(state, machine, base_sha):
    lock = state.get("lock") or {}
    if state.get("status") != "editing" or lock.get("machine") != machine or lock.get("base_sha256") != base_sha:
        raise CaptureError("El lock compartido cambió; no se publicará el workbook.")


def register_class(runtime, job, subject_id, state_file_id, machine=None, api=None):
    """Execute the complete one-writer publication protocol.

    On any failure after acquiring the shared lock, the lock is intentionally left
    in place for manual recovery; it is never released blindly.
    """
    runtime = Path(runtime).resolve()
    machine = machine or os.environ.get("COMPUTERNAME") or "unknown"
    control = runtime / "control"
    processing = runtime / "procesamiento"
    temporary = runtime / "temporales"
    for directory in (control, processing, temporary):
        directory.mkdir(parents=True, exist_ok=True)
    # `api` llega ya construido con el inicio de sesión propio; si no, ADC (cuenta de servicio).
    api = api or drive_workbook.service(runtime)
    state_tmp = processing / "database-work-state.remote.json"
    state = drive_workbook.read_json_file(api, state_file_id, state_tmp)
    if state.get("status") != "free" or state.get("lock") is not None:
        raise CaptureError("El workbook está bloqueado por otra sesión.")
    file_id = state.get("canonical_drive_file_id")
    expected = (state.get("published") or {}).get("sha256")
    if not file_id or not expected:
        raise CaptureError("El estado compartido no identifica una versión publicada válida.")

    incoming = processing / "database-base.sqlite"
    drive_workbook.download(api, file_id, incoming)
    if sha256(incoming) != expected:
        raise CaptureError("El workbook descargado no coincide con published.sha256.")
    validate_workbook(incoming)
    local = runtime / store.DB_NAME
    shutil.copyfile(incoming, local)

    record = build_class_record(job, subject_id)
    existing = find_existing_class(incoming, record)
    if existing:
        return {
            "class_id": existing,
            "already_registered": True,
            "sha256": expected,
            "size_bytes": local.stat().st_size,
            "drive_modified_time": (state.get("published") or {}).get("drive_modified_time"),
            "checkpoint": None,
        }

    locked = _state_lock(state, machine, expected)
    drive_workbook.write_json_file(api, state_file_id, locked, state_tmp)
    confirmed = drive_workbook.read_json_file(api, state_file_id, state_tmp)
    _ensure_lock(confirmed, machine, expected)
    atomic_json(control / "database-work-state.json", confirmed)

    checkpoint = temporary / f"database-{datetime.now():%Y%m%d-%H%M%S}-before-class.sqlite"
    shutil.copyfile(local, checkpoint)

    class_id = insert_class(local, record)
    validate_workbook(local, class_id)
    new_sha = sha256(local)

    before_publish = processing / "database-before-publish.sqlite"
    drive_workbook.download(api, file_id, before_publish)
    if sha256(before_publish) != expected:
        raise CaptureError("Drive cambió después de adquirir el lock; reconciliar antes de publicar.")
    current_lock = drive_workbook.read_json_file(api, state_file_id, state_tmp)
    _ensure_lock(current_lock, machine, expected)

    uploaded = drive_workbook.upload(api, file_id, local, SQLITE_MIME)
    redownload = processing / "database-redownloaded.sqlite"
    drive_workbook.download(api, file_id, redownload)
    if sha256(redownload) != new_sha:
        raise CaptureError("La versión re-descargada no coincide con lo publicado.")
    validate_workbook(redownload, class_id)
    shutil.copyfile(redownload, local)

    final_state = json.loads(json.dumps(current_lock))
    final_state["status"] = "free"
    final_state["lock"] = None
    final_state["published"] = {
        "sha256": new_sha,
        "size_bytes": local.stat().st_size,
        "drive_modified_time": uploaded.get("modifiedTime"),
        "validation": "drive-api-update-redownloaded-hash-matched-sqlite-validated",
    }
    drive_workbook.write_json_file(api, state_file_id, final_state, state_tmp)
    verified_state = drive_workbook.read_json_file(api, state_file_id, state_tmp)
    if verified_state.get("status") != "free" or verified_state.get("lock") is not None:
        raise CaptureError("La publicación terminó pero no se pudo confirmar la liberación del lock.")
    if (verified_state.get("published") or {}).get("sha256") != new_sha:
        raise CaptureError("El estado publicado final no coincide con el workbook.")
    atomic_json(control / "database-work-state.json", verified_state)
    return {
        "class_id": class_id,
        "already_registered": False,
        "sha256": new_sha,
        "size_bytes": local.stat().st_size,
        "drive_modified_time": uploaded.get("modifiedTime"),
        "checkpoint": str(checkpoint),
    }
