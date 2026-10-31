import re
import unicodedata
from pathlib import Path
from .metadata import read_json
from ..models import CaptureError

RESERVED = re.compile(r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(\.|$)", re.I)


def safe_name(value, limit=100):
    value = unicodedata.normalize("NFC", str(value))
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).strip().rstrip(". ")
    value = value[:limit].rstrip(". ")
    if not value or value in (".", ".."):
        value = "sin-nombre"
    if RESERVED.match(value):
        value = "_" + value
    return value


def within(path, root):
    path, root = Path(path).resolve(), Path(root).resolve()
    if path == root or not path.is_relative_to(root):
        raise CaptureError("Ruta fuera del espacio privado del job.")
    return path


def job_paths(runtime, job_id):
    if not re.fullmatch(r"[0-9a-f]{32}", job_id):
        raise CaptureError("ID de job inválido.")
    return (runtime / "procesamiento" / job_id, runtime / "temporales" / job_id)


def clean_media(temporary, runtime, job_id):
    """Delete only the allowlisted files created by this job, never source files."""
    expected = runtime.resolve() / "temporales" / job_id
    if temporary.is_symlink() or temporary.resolve() != expected:
        raise CaptureError("No se limpia un temporal redirigido.")
    owned = []
    for name in ("source.media", "source.media.part", "audio.wav", "audio.wav.part"):
        path = temporary / name
        if path.is_symlink():
            raise CaptureError("No se limpia un enlace simbólico.")
        within(path, expected)
        owned.append(path)
    for path in owned:
        path.unlink(missing_ok=True)