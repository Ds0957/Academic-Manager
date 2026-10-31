"""Publish text artifacts to an explicitly selected Drive Desktop root."""
import os
import shutil
from pathlib import Path

from .metadata import atomic_json, read_json, sha256
from .paths import safe_name
from ..models import ARTIFACTS, CaptureError


def validate(directory, job_id, expected=None):
    directory = Path(directory)
    if directory.is_symlink() or not directory.is_dir():
        raise CaptureError("Directorio de artefactos inválido o redirigido.")
    for name in ARTIFACTS:
        path = directory / name
        if path.is_symlink() or not path.is_file() or path.stat().st_size == 0:
            raise CaptureError(f"Artefacto ausente/vacío/redirigido: {name}")
    metadata = read_json(directory / "metadata.json")
    transcript = read_json(directory / "transcript.json")
    if metadata.get("job_id") != job_id:
        raise CaptureError("Los artefactos pertenecen a otro job.")
    if transcript.get("schema_version") != 1 or not transcript.get("segments"):
        raise CaptureError("Transcripción estructurada inválida.")
    from ..transcription.formatter import normalize, markdown
    if normalize(transcript) != transcript:
        raise CaptureError("La transcripción no cumple el formato normalizado.")
    if (directory / "transcript.md").read_text(encoding="utf-8") != markdown(
            transcript, metadata["source"]["original_name"]):
        raise CaptureError("La transcripción Markdown no coincide con JSON.")
    status = metadata.get("analysis_status")
    if status not in ("pending", "reviewed"):
        raise CaptureError("Estado de análisis inválido.")
    if status == "reviewed":
        provenance = metadata.get("analysis_source") or {}
        if (provenance.get("job_id") != job_id or
                provenance.get("approval") != "human_confirmed" or
                provenance.get("transcript_sha256") != sha256(directory / "transcript.json") or
                provenance.get("analysis_sha256") != sha256(directory / "analysis.md")):
            raise CaptureError("El análisis revisado no coincide con su transcripción o procedencia.")
    elif metadata.get("analysis_source"):
        raise CaptureError("Análisis pendiente con procedencia revisada inconsistente.")
    hashes = {name: sha256(directory / name) for name in ARTIFACTS}
    if expected is not None and hashes != expected:
        raise CaptureError("Cambió un artefacto desde su validación; se conserva el medio.")
    return hashes


def publish(stage, root, semester, subject, title, job, checkpoint, folder="Clases"):
    root = Path(root).resolve()
    if not root.is_dir():
        raise CaptureError("La raíz Drive no existe o no está montada.")
    parent = root / safe_name(semester) / safe_name(subject) / folder
    if not parent.resolve().is_relative_to(root):
        raise CaptureError("El destino sale de la raíz Drive.")
    parent.mkdir(parents=True, exist_ok=True)
    target = Path(job["publication_path"]) if job.get("publication_path") else None
    if target is None:
        base = safe_name(title, 80)
        target = parent / base
        try:
            target.mkdir()
        except FileExistsError:
            target = parent / (base + "--" + job["job_id"])
            try:
                target.mkdir()
            except FileExistsError as exc:
                raise CaptureError("Colisión de destino; no se sobrescriben clases existentes.") from exc
        atomic_json(target / ".capture-owner.json", {"job_id": job["job_id"]})
        job["publication_path"] = str(target)
        checkpoint()
    if target.is_symlink() or target.parent.resolve() != parent.resolve():
        raise CaptureError("Destino de publicación inválido.")
    marker = target / ".capture-owner.json"
    if not marker.exists():
        validate(target, job["job_id"], job["hashes"])
        return target
    if read_json(marker).get("job_id") != job["job_id"]:
        raise CaptureError("El destino está reservado por otro job.")
    for name in ARTIFACTS:
        output = target / name
        if output.is_symlink():
            raise CaptureError("Destino redirigido.")
        if output.exists():
            if sha256(output) != job["hashes"][name]:
                raise CaptureError("Publicación parcial modificada; reconciliar antes de reintentar.")
            continue
        part = target / (name + ".part")
        if part.is_symlink():
            raise CaptureError("Archivo parcial redirigido.")
        with part.open("wb") as stream, (stage / name).open("rb") as source:
            shutil.copyfileobj(source, stream)
            stream.flush()
            os.fsync(stream.fileno())
        if sha256(part) != job["hashes"][name]:
            raise CaptureError("Falló la copia del artefacto.")
        # Hard link atomically creates the final name without replacing an existing file.
        # Drive Desktop may not support hard links: rename is exclusive on Windows.
        if os.name == "nt":
            part.rename(output)
        else:
            os.link(part, output)
            part.unlink()
    validate(target, job["job_id"], job["hashes"])
    marker.unlink()
    return target