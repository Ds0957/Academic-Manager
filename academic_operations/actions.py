"""Scoped, resumable class finalization for the Academic Manager Coordinator.

This module is a local operating contract, not an authorization bypass.
All writes reuse Capture and the canonical workbook's existing protocols.
"""
import hashlib
import io
import json
import os
import re
import shutil
from contextlib import redirect_stdout
from pathlib import Path
from uuid import uuid4

from academic_access import core as access, today as access_today
from academic_store import store
from capture.capture import parser as capture_parser, run as capture_run
from capture.models import CaptureError
from capture.storage.metadata import read_json, sha256
from capture.storage.paths import job_paths, safe_name
from capture.storage.publisher import validate
from capture.storage.job import job_lock


class OperationError(Exception):
    def __init__(self, code, message, *, stage=None, substage=None,
                 retryable=False, os_error=None):
        super().__init__(message)
        self.code = code
        self.stage = stage
        self.substage = substage
        self.retryable = retryable
        self.os_error = os_error


def _phase_error(exc, stage):
    """Expose stable diagnostic categories, never an OS path or private contents."""
    os_error = None
    if isinstance(exc, OSError):
        win = getattr(exc, "winerror", None)
        number = win if isinstance(win, int) else getattr(exc, "errno", None)
        if isinstance(number, int):
            os_error = number
    if isinstance(exc, PermissionError):
        category = getattr(exc, "substage", None)
        if category not in ("backup_artifacts", "transaction_journal",
                            "replace_analysis", "replace_metadata",
                            "rename_original", "activate_candidate", "restore_directory"):
            category = None
        return OperationError("FILE_ACCESS_DENIED",
            "El sistema denegó acceso al archivo durante esta etapa.",
            stage=stage, substage=category, retryable=True, os_error=os_error)
    if isinstance(exc, FileNotFoundError):
        return OperationError("FILE_NOT_FOUND",
            "Un archivo requerido dejó de estar disponible durante esta etapa.",
            stage=stage, retryable=True, os_error=os_error)
    if isinstance(exc, FileExistsError):
        return OperationError("FILE_CONFLICT",
            "Existe un archivo inesperado; consulte el estado antes de reintentar.",
            stage=stage, retryable=False, os_error=os_error)
    if isinstance(exc, OSError):
        return OperationError("LOCAL_IO_FAILURE",
            "Falló una operación local de archivos; consulte el estado antes de reintentar.",
            stage=stage, retryable=True, os_error=os_error)
    if isinstance(exc, CaptureError):
        return OperationError("CAPTURE_FAILED",
            "Capture no pudo finalizar la etapa; consulte el estado del job.",
            stage=stage, retryable=False)
    if isinstance(exc, (ValueError, KeyError, TypeError)):
        return OperationError("INVALID_OPERATION_STATE",
            "Se detectó un estado local inesperado durante esta etapa.",
            stage=stage, retryable=False)
    return OperationError("OPERATION_FAILED",
        "No se pudo completar la operación; revise el estado del job.",
        stage=stage, retryable=False)


def _fail(code, message):
    raise OperationError(code, message)


def _config(runtime):
    path = config_path(runtime)
    if path.is_symlink() or not path.is_file():
        _fail("NOT_CONFIGURED", "Falta la configuración local autorizada de operaciones.")
    return check_config(runtime, read_json(path))


def config_path(runtime):
    return Path(runtime) / "control" / "academic-operations.json"


def check_config(runtime, data):
    """Validación única de `control/academic-operations.json` (la usan `_config` y el alta nueva)."""
    runtime = Path(runtime).resolve()
    root = data.get("drive_root")
    state = data.get("database_state_file_id")
    # `subjects` es opcional: sin entrada, la materia se resuelve desde la base (`_subject_id`).
    subjects = data.get("subjects", {})
    if (data.get("schema_version") != 1 or not isinstance(root, str) or
            not Path(root).is_absolute() or not isinstance(state, str) or
            not re.fullmatch(r"[A-Za-z0-9_-]{15,}", state) or
            not isinstance(subjects, dict)):
        _fail("INVALID_CONFIG", "Configuración incompleta o inválida.")
    destination = Path(root).resolve()
    if (destination == runtime or destination.is_relative_to(runtime)
            or runtime.is_relative_to(destination) or
            destination.name.casefold() != "academic manager"):
        _fail("INVALID_CONFIG", "La raíz configurada no corresponde a Academic Manager.")
    if any(not isinstance(k, str) or not isinstance(v, str)
           or not re.fullmatch(r"MAT-[A-Za-z0-9-]+", v)
           for k, v in subjects.items()):
        _fail("INVALID_CONFIG", "Identificadores de materias no válidos.")
    return {"drive_root": str(destination), "database_state_file_id": state,
            "subjects": subjects}


def _subject_id(runtime, mapped, subject):
    """`materia_id` del nombre visible de Capture.

    Una entrada de `subjects` en la configuración manda (compatibilidad); si no hay, se busca
    el nombre en `01 - Materias` del semestre activo de la base local. Ninguna o varias
    coincidencias fallan cerradas.
    """
    if subject in mapped:
        return mapped[subject]
    if not isinstance(subject, str) or not subject.strip():
        _fail("SUBJECT_UNMAPPED", "El job no indica una materia reconocible.")
    try:
        catalog = store.read_catalog(runtime / store.DB_NAME, ("01 - Materias", "05 - Semestres"))
        active = set(access_today._active_subjects(catalog))
    except (store.StoreError, access.AccessError, OSError, KeyError):
        _fail("SUBJECT_UNMAPPED", f"No se pudo consultar la base para identificar la materia «{subject}».")
    wanted = subject.strip().casefold()
    found = sorted({str(r["materia_id"]) for r in catalog["01 - Materias"]
                    if str(r.get("materia_id") or "") in active
                    and str(r.get("nombre") or "").strip().casefold() == wanted})
    if not found:
        _fail("SUBJECT_UNMAPPED", f"«{subject}» no corresponde a ninguna materia del semestre activo "
              "ni a la configuración.")
    if len(found) > 1:
        _fail("SUBJECT_AMBIGUOUS", f"«{subject}» corresponde a varias materias del semestre activo: "
              f"{', '.join(found)}.")
    if not re.fullmatch(r"MAT-[A-Za-z0-9-]+", found[0]):
        _fail("SUBJECT_UNMAPPED", f"«{subject}» tiene un materia_id no válido en la base.")
    return found[0]


def _report(runtime, filename):
    if not filename or not re.fullmatch(r"[A-Za-z0-9_.-]{1,160}\.md", filename, re.I):
        _fail("INVALID_ARGUMENT", "Elija el nombre de un informe .md del área analysis-review.")
    base = runtime / "procesamiento" / "analysis-review"
    path = base / filename
    if base.is_symlink() or path.is_symlink() or not path.is_file() or path.resolve().parent != base.resolve():
        _fail("NOT_FOUND", "El informe no existe en analysis-review o tiene una ruta redirigida.")
    if not 200 <= path.stat().st_size <= 1024 * 1024:
        _fail("INVALID_ANALYSIS", "El informe no cumple el tamaño permitido (200 bytes a 1 MiB).")
    try:
        body = path.read_text(encoding="utf-8-sig")
    except UnicodeError:
        _fail("INVALID_ANALYSIS", "El informe debe estar en UTF-8.")
    if (len(body.strip()) < 200 or not re.search(r"(?m)^#{1,3}\s+\S+", body)
            or "\x00" in body or "pendiente de análisis" in body.lower()
            or "no se ha realizado análisis académico" in body.lower()):
        _fail("INVALID_ANALYSIS", "El informe tiene contenido incompleto o pendiente.")
    return path, sha256(path)


def _snapshot(runtime, job_id, filename):
    config = _config(runtime)
    work, _ = job_paths(runtime, job_id)
    job_file = work / "job.json"
    if work.is_symlink() or job_file.is_symlink() or not job_file.is_file():
        _fail("NOT_FOUND", "No existe un job local válido.")
    job = read_json(job_file)
    if job.get("schema_version") != 1 or job.get("job_id") != job_id:
        _fail("INVALID_JOB", "El job no coincide con su identidad local.")
    hashes = job.get("hashes")
    if not isinstance(hashes, dict):
        _fail("NOT_READY", "Capture no tiene los cuatro artefactos validados.")
    stage = work / "artifacts"
    validate(stage, job_id, hashes)
    metadata = read_json(stage / "metadata.json")
    status = metadata.get("analysis_status")
    if status not in ("pending", "reviewed"):
        _fail("NOT_READY", "El estado del análisis no permite completar la clase.")
    request = job.get("request") or {}
    source = job.get("source_metadata") or {}
    subject = request.get("subject")
    subject_id = _subject_id(runtime, config["subjects"], subject)
    if (not request.get("semester") or not request.get("class_date")
            or request.get("duration") is not None or
            not source.get("original_name") or not source.get("location")):
        _fail("NOT_READY", "Se requiere una clase completa con fecha y procedencia verificadas.")
    published = False
    if job.get("publication_path"):
        target = Path(job["publication_path"])
        parent = (Path(config["drive_root"]) / safe_name(request["semester"]) /
                  safe_name(subject) / "Clases").resolve()
        if target.is_symlink() or target.parent.resolve() != parent:
            _fail("INVALID_PUBLICATION", "El destino del job no corresponde a la carpeta autorizada.")
        if not (target / ".capture-owner.json").exists() and target.is_dir():
            try:
                validate(target, job_id, hashes)
                published = True
            except (CaptureError, OSError, ValueError, KeyError):
                published = False
    registered = bool(job.get("database_registration"))
    if registered and not published:
        _fail("INVALID_PUBLICATION", "Un registro existente no tiene publicación verificable.")
    path = None
    report_hash = hashes["analysis.md"]
    if filename:
        path, report_hash = _report(runtime, filename)
        if published and report_hash != hashes["analysis.md"]:
            _fail("ALREADY_PUBLISHED", "No se puede sustituir el análisis de una clase ya publicada.")
    elif status != "reviewed":
        _fail("ANALYSIS_REQUIRED", "La clase requiere un informe de analysis-review.")
    if published and status != "reviewed":
        _fail("ALREADY_PUBLISHED", "La clase publicada aún tiene análisis pendiente; requiere versionado.")
    content = {
        "job_id": job_id, "semester": request["semester"],
        "subject": subject, "subject_id": subject_id,
        "class_date": request["class_date"], "original_name": source["original_name"],
        "transcript_sha256": hashes["transcript.json"],
        "analysis_sha256": report_hash, "existing_analysis_sha256": hashes["analysis.md"],
        "report": filename, "analysis_status": status,
        "published": published, "registered": registered,
        "drive_root": config["drive_root"], "database_state_file_id": config["database_state_file_id"],
        "hashes": hashes
    }
    digest = hashlib.sha256(json.dumps(content, ensure_ascii=True,
                    sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    overview = {
        "job_id": job_id, "materia": subject, "materia_id": subject_id,
        "semestre": request["semester"], "fecha": request["class_date"],
        "grabacion": source["original_name"],
        "analisis_archivo": filename, "analysis_status": status,
        "transcript_sha256": hashes["transcript.json"],
        "analysis_sha256": report_hash,
        "existing_analysis_sha256": hashes["analysis.md"], "published": published,
        "database_registered": registered,
        "registered_class_id": (job.get("database_registration") or {}).get("class_id"),
        "steps": (["attach_analysis"] if path and report_hash != hashes["analysis.md"] else [])
                 + (["publish"] if not published else [])
                 + (["register_database"] if not registered else [])
                 + ["refresh_index"],
        "plan_id": digest,
        "requires_explicit_approval": True
    }
    return overview, config, path


def preview(runtime, job_id, filename=None):
    result, _, _ = _snapshot(Path(runtime).resolve(), job_id, filename)
    return result


def _write_login(runtime):
    """Inicio de sesión propio del asistente con permiso de escritura, comprobado con su cargador."""
    from capture.models import CaptureError
    from capture.sources import auth
    try:
        return auth.load_write_credentials(runtime) is not None
    except CaptureError as exc:
        _fail("CREDENTIAL_UNAVAILABLE", str(exc))


def _capture(runtime, *args):
    parsed = capture_parser().parse_args(["--runtime", str(runtime), *args])
    with redirect_stdout(io.StringIO()):
        return capture_run(parsed)


def complete(runtime, job_id, *, filename=None, plan_id=None, approved=False,
             index_builder=None):
    """User-approved operation: attach, publish, register, refresh local index.

    Restarting after a partial failure uses a fresh preview; completed stages
    are preserved and the existing workbook publisher performs idempotence.
    """
    runtime = Path(runtime).resolve()
    if not approved:
        _fail("APPROVAL_REQUIRED", "La publicación y el registro requieren aprobación explícita.")
    if not isinstance(plan_id, str) or not re.fullmatch(r"[0-9a-f]{64}", plan_id):
        _fail("INVALID_ARGUMENT", "Indique el plan_id exacto de preview.")
    work, _ = job_paths(runtime, job_id)
    if work.is_symlink() or not work.is_dir():
        _fail("NOT_FOUND", "No existe un job local no redirigido.")
    phase = "acquire_lock"
    try:
        with job_lock(work / ".finalize-operation"):
            phase = "validate_plan"
            state, config, report = _snapshot(runtime, job_id, filename)
            if state["plan_id"] != plan_id:
                _fail("PLAN_CHANGED", "El job, análisis o configuración cambió. Consulte una vista previa nueva.")
            phase = "attach_analysis"
            if report and state["analysis_sha256"] != state["existing_analysis_sha256"]:
                # Copy approved bytes to a private immutable snapshot for Capture.
                copy = work / (".approved-report-" + uuid4().hex + ".md")
                try:
                    with report.open("rb") as src, copy.open("xb") as dest:
                        shutil.copyfileobj(src, dest)
                        dest.flush()
                        os.fsync(dest.fileno())
                    if sha256(copy) != state["analysis_sha256"]:
                        _fail("PLAN_CHANGED", "El informe cambió antes de poder incorporarlo.")
                    _capture(runtime, "--resume", job_id, "--attach-analysis", str(copy),
                             "--analysis-transcript-sha256", state["transcript_sha256"],
                             "--analysis-reviewed")
                finally:
                    copy.unlink(missing_ok=True)
            phase = "verify_analysis"
            current, _, _ = _snapshot(runtime, job_id, None)
            if current["analysis_status"] != "reviewed":
                _fail("ANALYSIS_REQUIRED", "No se ha confirmado un análisis revisado.")
            phase = "publish"
            if not current["published"]:
                if not Path(config["drive_root"]).is_dir():
                    _fail("DRIVE_UNAVAILABLE", "La carpeta Academic Manager de Drive no está montada.")
                _capture(runtime, "--resume", job_id, "--publish", "--drive-root", config["drive_root"])
            phase = "register_database"
            current, _, _ = _snapshot(runtime, job_id, None)
            if not current["database_registered"]:
                credential = runtime / "control" / "service-account-drive.json"
                if credential.is_symlink() or not credential.is_file():
                    # Sin cuenta de servicio (ni enlace a una): el inicio de sesión propio, como en Capture.
                    if credential.exists() or credential.is_symlink() or not _write_login(runtime):
                        _fail("CREDENTIAL_UNAVAILABLE", "No existe la credencial local autorizada de publicación.")
                _capture(runtime, "--resume", job_id, "--register-database", "--subject-id",
                         current["materia_id"], "--database-state-file-id",
                         config["database_state_file_id"])
            phase = "verify_completion"
            current, _, _ = _snapshot(runtime, job_id, None)
            if not current["published"] or not current["database_registered"]:
                _fail("PARTIAL_COMPLETION", "La clase no quedó completamente publicada y registrada.")
            phase = "refresh_index"
            index_warning = None
            try:
                if index_builder is None:
                    from academic_knowledge.index import build
                    index_builder = build
                index_builder(runtime, drive_root=Path(config["drive_root"]))
            except Exception:
                index_warning = "El índice local no pudo reconstruirse; la clase sí fue publicada y registrada."
            return {"job_id": job_id, "class_id": current["registered_class_id"],
                    "analysis_status": current["analysis_status"], "published": True,
                    "database_registered": True, "index_refreshed": index_warning is None,
                    "warning": index_warning}
    except OperationError as exc:
        if exc.stage is None:
            exc.stage = phase
        raise
    except (CaptureError, OSError, ValueError, KeyError, TypeError) as exc:
        raise _phase_error(exc, phase) from exc
