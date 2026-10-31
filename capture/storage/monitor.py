"""Read-only Capture job monitoring. Never acquires job lock or resumes a job."""
from datetime import datetime, timezone
import json
import os
import re
from pathlib import Path

from ..models import CaptureError, now
from .metadata import read_json, sha256
from .paths import job_paths


def record_event(directory, job_id, stage, at=None):
    """Persist only operational stage data; never log credentials or full requests."""
    path = Path(directory) / "events.jsonl"
    if path.is_symlink():
        raise CaptureError("El archivo de eventos del job no puede ser un enlace.")
    row = {"at": at or now(), "job_id": job_id, "stage": stage}
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(row, ensure_ascii=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def job_ids(runtime):
    folder = Path(runtime) / "procesamiento"
    if not folder.is_dir():
        return []
    entries = [p for p in folder.iterdir()
               if p.is_dir() and not p.is_symlink()
               and re.fullmatch(r"[0-9a-f]{32}", p.name)
               and (p / "job.json").is_file() and not (p / "job.json").is_symlink()]
    return [p.name for p in sorted(entries, key=lambda p:(p / "job.json").stat().st_mtime, reverse=True)]


def inspect_job(runtime, identity):
    """Read status safely while another process owns the job lock; never mutate it."""
    runtime = Path(runtime)
    if identity == "latest":
        choices = job_ids(runtime)
        if not choices:
            raise CaptureError("No hay jobs de Capture en este runtime.")
        identity = choices[0]

    work, _ = job_paths(runtime, identity)
    if work.is_symlink() or work.resolve().parent != (runtime / "procesamiento").resolve():
        raise CaptureError("El directorio del job está redirigido.")
    path = work / "job.json"
    if not path.is_file() or path.is_symlink():
        raise CaptureError("No existe un job válido para consultar.")
    job = read_json(path)
    if job.get("schema_version") != 1 or job.get("job_id") != identity:
        raise CaptureError("El job consultado tiene identidad o versión inválida.")

    events_path = work / "events.jsonl"
    if events_path.is_symlink():
        raise CaptureError("El historial del job es un enlace no permitido.")
    events = []
    if events_path.is_file():
        with events_path.open(encoding="utf-8") as stream:
            for line in stream:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if row.get("job_id") == identity:
                    events.append(row)
    return job, events[-8:]


def _datetime(value):
    if not isinstance(value,str):
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z","+00:00"))
        return result.astimezone(timezone.utc) if result.tzinfo else None
    except ValueError:
        return None


def job_summary(runtime, identity):
    job, events = inspect_job(runtime, identity)
    work,_ = job_paths(Path(runtime),job["job_id"])
    phase = events[-1]["stage"] if events else job.get("status","unknown")
    terminal = job.get("status") in ("completed", "failed")
    if terminal:
        phase = job["status"]
    started = next((item["at"] for item in reversed(events)
                    if item.get("stage") == phase), None)
    summary = {"job_id":job["job_id"],"subject":(job.get("request") or {}).get("subject"),
               "recording":(job.get("source_metadata") or {}).get("original_name"),
               "class_date":(job.get("request") or {}).get("class_date"),
               "status":job.get("status"),"current_phase":phase,
               "last_update_utc":job.get("updated_at"),
               "duration_seconds":(job.get("media") or {}).get("duration_seconds"),
               "phase_percent":None, "phase_elapsed_seconds":None,
               "estimated_remaining_seconds":None, "estimate_quality":"unavailable",
               "signal_recent":False,"published":bool(job.get("publication_path")),
               "database_registered":bool(job.get("database_registration")),
               "analysis_status":"unknown",
               "recent_error":(job.get("last_error") or {}).get("type") if job.get("status")=="failed" else None}
    metadata_path=work/"artifacts"/"metadata.json"
    try:
        expected=(job.get("hashes") or {}).get("metadata.json")
        if (expected and metadata_path.is_file() and not metadata_path.is_symlink() and
                sha256(metadata_path)==expected):
            state=read_json(metadata_path).get("analysis_status")
            if state in ("pending","reviewed"):
                summary["analysis_status"]=state
    except (OSError,ValueError,TypeError,KeyError):
        pass  # The journal/swap may be in progress; no inference from partial metadata.
    now_utc=datetime.now(timezone.utc)
    if phase=="transcribing" and not terminal:
        path=work/"progress.json"
        if path.is_symlink():
            raise CaptureError("El estado de progreso no puede ser un enlace.")
        if path.is_file():
            try:
                data=read_json(path)
                if (data.get("schema_version")==1 and data.get("job_id")==job["job_id"]
                    and data.get("stage")=="transcribing" and
                    type(data.get("percent_phase")) in (int,float)):
                    pct=float(data["percent_phase"])
                    if 0<=pct<=100:
                        summary["phase_percent"]=pct
                        eta=data.get("estimated_remaining_seconds")
                        if type(eta) in (float,int) and 0<=eta<86400*7:
                            summary["estimated_remaining_seconds"]=round(eta)
                            summary["estimate_quality"]="orientative_vad_segments"
                        stamp=_datetime(data.get("updated_at_utc"))
                        if stamp:
                            summary["signal_recent"]=0<=(now_utc-stamp).total_seconds()<120
                        summary["phase_elapsed_seconds"]=data.get("elapsed_seconds")
            except (ValueError,TypeError,KeyError,OSError):
                pass  # Partial/legacy monitor data cannot invalidate the job.
        if summary["phase_elapsed_seconds"] is None:
            moment=_datetime(started)
            if moment:
                summary["phase_elapsed_seconds"]=max(0,round((now_utc-moment).total_seconds()))
        if not summary["signal_recent"]:
            summary["estimated_remaining_seconds"]=None
            summary["estimate_quality"]="unavailable"
    return summary


def list_jobs(runtime, limit=20):
    if type(limit) is not int or not 1<=limit<=100:
        raise CaptureError("limit de jobs debe estar entre 1 y 100.")
    return [job_summary(runtime,ident) for ident in job_ids(runtime)[:limit]]


def print_status(runtime, identity):
    if identity=="all":
        rows=list_jobs(runtime)
        if not rows:
            print("Sin trabajos de Capture.")
            return
        for row in rows:
            info=f"{row['subject'] or 'Sin materia'} | {row['job_id'][:8]} | {row['current_phase']}"
            if row["phase_percent"] is not None:
                info+=f" | ASR {row['phase_percent']:.1f}%"
            print(info)
        return
    details=job_summary(runtime,identity)
    job,events=inspect_job(runtime,identity)
    work,_=job_paths(Path(runtime),job["job_id"])
    print("Job:",job["job_id"])
    print("Materia:",details["subject"] or "sin especificar")
    print("Estado:",job.get("status","desconocido"))
    print("Etapa actual:",details["current_phase"])
    if details["phase_percent"] is not None:
        print(f"Transcripción (fase ASR): {details['phase_percent']:.1f}%")
    if details["phase_elapsed_seconds"] is not None:
        print("Tiempo en fase:",details["phase_elapsed_seconds"],"segundos")
    if details["estimated_remaining_seconds"] is not None:
        print("Tiempo restante ORIENTATIVO solo para la transcripción:",
              details["estimated_remaining_seconds"],"segundos")
    if details["current_phase"]=="transcribing" and not details["signal_recent"]:
        print("Progreso reciente: no confirmado (el proceso podría estar cargando el modelo o detenido).")
    print("Actualizado:",job.get("updated_at","sin fecha"))
    print("Publicación:","sí" if job.get("publication_path") else "no")
    print("Base de datos:",(job.get("database_registration") or {}).get("class_id","pendiente"))
    print("Análisis:",details["analysis_status"])
    if job.get("hashes") and job["hashes"].get("transcript.json"):
        print("SHA-256 transcript.json:",job["hashes"]["transcript.json"])
    if details["recent_error"]:
        print("Error del intento actual:",details["recent_error"])
    print("Últimas etapas:")
    if not events:
        print("  Sin eventos históricos; el job puede ser anterior a esta versión.")
    for row in events:
        print(" ",row["at"],row["stage"])
