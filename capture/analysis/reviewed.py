"""Reviewed-analysis attachment for unpublished Capture jobs."""
import hashlib
import os
import re
import shutil
import time
from pathlib import Path

from ..models import ARTIFACTS, CaptureError, now
from ..storage.metadata import atomic_json, read_json, sha256
from ..storage.job import save
from ..storage.publisher import validate

TX = "analysis-transaction.json"
NEXT = ".analysis-next"
PREVIOUS = ".analysis-previous"


class ReviewedAccessDenied(PermissionError):
    """Safe category for a denied canonical artifact operation (no private paths)."""

    def __init__(self, substage, cause):
        super().__init__(getattr(cause, "errno", 13), "Acceso denegado en Capture")
        self.substage = substage
        self.winerror = getattr(cause, "winerror", None)


def _rename_with_retry(source, target, *, substage="directory_swap"):
    """Windows can temporarily deny renaming a directory with open file handles.

    Retry only access-denied errors, preserving the journal and rollback on failure.
    """
    for delay in (0.05, 0.15, 0.35, 0.7, None):
        try:
            source.rename(target)
            return
        except PermissionError:
            if delay is None:
                raise
            time.sleep(delay)


def _valid(directory, job_id, hashes):
    if directory.is_symlink() or not directory.is_dir():
        return False
    try:
        validate(directory, job_id, hashes)
        return True
    except (CaptureError, OSError, KeyError, ValueError):
        return False


def _discard(directory, job_id, hashes):
    if directory.exists() or directory.is_symlink():
        if not _valid(directory, job_id, hashes):
            raise CaptureError("Respaldo de análisis alterado; requiere revisión manual.")
        shutil.rmtree(directory)


# Windows may deny renaming a directory while a reader keeps a child open.
# The fallback updates only the two changed files (never the transcript).
_CHANGED = ("analysis.md", "metadata.json")


def _replace_from(source, target):
    import uuid
    target = Path(target)
    temporary = target.with_name(".analysis-write-" + uuid.uuid4().hex + ".tmp")
    try:
        shutil.copyfile(source, temporary)
        for delay in (0.05, 0.15, 0.35, 0.7, None):
            try:
                os.replace(temporary, target)
                return
            except PermissionError as exc:
                if delay is None:
                    label = ("replace_analysis" if target.name == "analysis.md"
                             else "replace_metadata")
                    raise ReviewedAccessDenied(label, exc) from exc
                time.sleep(delay)
    finally:
        temporary.unlink(missing_ok=True)


def _replace_two(stage, candidate):
    for name in _CHANGED:
        # On rollback, avoid rewriting a still-original file held open by Windows.
        if sha256(stage / name) != sha256(candidate / name):
            _replace_from(candidate / name, stage / name)


def _fallback_files(work, job, old, new):
    """Directory renaming was denied: safe two-file swap with validated backup."""
    stage, candidate, previous, journal = (
        work / "artifacts", work / NEXT, work / PREVIOUS, work / TX)
    identity = job["job_id"]
    if (previous.exists() or previous.is_symlink() or
            not _valid(stage, identity, old) or not _valid(candidate, identity, new)):
        raise CaptureError("No es seguro iniciar el intercambio alternativo.")
    try:
        try:
            shutil.copytree(stage, previous, symlinks=False)
        except PermissionError as exc:
            raise ReviewedAccessDenied("backup_artifacts", exc) from exc
        if not _valid(previous, identity, old):
            raise CaptureError("La copia de recuperación no conserva la integridad.")
    except Exception:
        if previous.is_dir() and not previous.is_symlink():
            shutil.rmtree(previous)
        raise
    # Durable journal transition before the first canonical file is replaced.
    try:
        try:
            atomic_json(journal, {"job_id": identity, "old_hashes": old,
                                  "new_hashes": new, "mode": "files"})
        except PermissionError as exc:
            raise ReviewedAccessDenied("transaction_journal", exc) from exc
    except Exception:
        _discard(previous, identity, old)
        raise
    _replace_two(stage, candidate)
    validate(stage, identity, new)
    job["hashes"] = new
    save(work, job)


def recover(work, job):
    """Repair an interrupted directory swap under the exclusive job lock."""
    journal = work / TX
    if journal.is_symlink():
        raise CaptureError("Diario de análisis redirigido.")
    if not journal.exists():
        return
    entry = read_json(journal)
    job_id = job["job_id"]
    old, new = entry.get("old_hashes"), entry.get("new_hashes")
    if entry.get("job_id") != job_id or job.get("hashes") not in (old, new):
        raise CaptureError("Diario de análisis incompatible con el job.")
    mode = entry.get("mode", "directory")
    if mode not in ("directory", "files"):
        raise CaptureError("Modo de transacción desconocido.")
    stage, next_dir, previous = work / "artifacts", work / NEXT, work / PREVIOUS
    if mode == "files" and not _valid(stage, job_id, new):
        if not _valid(previous, job_id, old) or stage.is_symlink() or not stage.is_dir():
            raise CaptureError("Falta el respaldo válido para recuperar análisis.")
        # Works for an unmodified stage or a crash between two file replacements.
        if not _valid(stage, job_id, old):
            _replace_two(stage, previous)
        validate(stage, job_id, old)
        if job["hashes"] != old:
            job["hashes"] = old
            save(work, job)
    if _valid(stage, job_id, new):
        if job["hashes"] != new:
            job["hashes"] = new
            save(work, job)
        _discard(previous, job_id, old)
        _discard(next_dir, job_id, new)
        journal.unlink()
        return
    if _valid(stage, job_id, old) and (mode == "files" or
            (not previous.exists() and not previous.is_symlink()) or
            _valid(previous, job_id, old)):
        if previous.exists():
            _discard(previous, job_id, old)
        _discard(next_dir, job_id, new)
        journal.unlink()
        return
    if not stage.exists() and not stage.is_symlink() and _valid(previous, job_id, old):
        _rename_with_retry(previous, stage, substage="restore_directory")
        _discard(next_dir, job_id, new)
        journal.unlink()
        return
    raise CaptureError("Transacción de análisis inconsistente; respaldos preservados.")


def attach(work, job, path, transcript_sha256, reviewed):
    """Store a Markdown report explicitly approved against a specific transcript hash."""
    job_id = job["job_id"]
    stage, next_dir, previous, journal = (
        work / "artifacts", work / NEXT, work / PREVIOUS, work / TX)
    if job.get("publication_path") or job.get("database_registration"):
        raise CaptureError("No se reemplaza el análisis de una clase publicada o registrada.")
    if not job.get("hashes"):
        raise CaptureError("La transcripción todavía no tiene artefactos validados.")
    validate(stage, job_id, job["hashes"])
    if not reviewed:
        raise CaptureError("Confirme la revisión humana con --analysis-reviewed.")
    expected = job["hashes"]["transcript.json"]
    if not re.fullmatch(r"[0-9a-f]{64}", transcript_sha256 or "") or expected != transcript_sha256:
        raise CaptureError("La huella no corresponde al transcript.json validado.")
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 1024 * 1024:
        raise CaptureError("Indique un Markdown local (máximo 1 MiB) no redirigido.")
    try:
        report = path.read_text(encoding="utf-8-sig")
    except UnicodeError as exc:
        raise CaptureError("El informe debe estar en UTF-8.") from exc
    stripped = report.strip()
    if (len(stripped) < 200 or not re.search(r"(?m)^#{1,3}\s+\S+", stripped) or
            "\x00" in stripped or "pendiente de análisis" in stripped.lower() or
            "no se ha realizado análisis académico" in stripped.lower()):
        raise CaptureError("El informe es vacío, marcador pendiente o demasiado incompleto.")
    metadata = read_json(stage / "metadata.json")
    if metadata.get("analysis_status") not in ("pending", "reviewed"):
        raise CaptureError("Estado de análisis no reconocido.")
    data = report.encode("utf-8")
    digest = hashlib.sha256(data).hexdigest()
    binding = metadata.get("analysis_source") or {}
    if (metadata["analysis_status"] == "reviewed" and
            binding.get("transcript_sha256") == expected and
            binding.get("analysis_sha256") == digest):
        return False
    if any(p.exists() or p.is_symlink() for p in (next_dir, previous, journal)):
        raise CaptureError("Queda una transacción de análisis sin resolver.")
    next_dir.mkdir()
    try:
        for name in ARTIFACTS:
            shutil.copyfile(stage / name, next_dir / name)
        (next_dir / "analysis.md").write_bytes(data)
        metadata["analysis_status"] = "reviewed"
        metadata["analysis_source"] = {
            "job_id": job_id, "transcript_sha256": expected,
            "analysis_sha256": digest, "reviewed_at": now(),
            "approval": "human_confirmed"}
        atomic_json(next_dir / "metadata.json", metadata)
        new = validate(next_dir, job_id)
        old = dict(job["hashes"])
        atomic_json(journal, {"job_id": job_id, "old_hashes": old, "new_hashes": new})
    except Exception:
        if not journal.exists():
            shutil.rmtree(next_dir)
        raise
    try:
        try:
            _rename_with_retry(stage, previous, substage="rename_original")
        except PermissionError:
            # Validated in-place fallback; keep the transcript files untouched.
            _fallback_files(work, job, old, new)
        else:
            _rename_with_retry(next_dir, stage, substage="activate_candidate")
            job["hashes"] = new
            save(work, job)
        _discard(previous, job_id, old)
        if next_dir.exists():
            _discard(next_dir, job_id, new)
        journal.unlink()
    except Exception:
        recover(work, job)
        raise
    return True
