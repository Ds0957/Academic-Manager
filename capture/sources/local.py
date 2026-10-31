import mimetypes
import shutil
from pathlib import Path

from ..models import CaptureError
from ..storage.metadata import sha256


def inspect(source):
    path = Path(source).expanduser().resolve()
    if not path.is_file():
        raise CaptureError("El archivo local no existe.")
    stat = path.stat()
    return {"kind": "local", "location": str(path), "original_name": path.name,
            "size_bytes": stat.st_size, "mime_type": mimetypes.guess_type(path.name)[0],
            "modified_ns": stat.st_mtime_ns, "sha256": sha256(path)}


def fetch(metadata, target):
    source = Path(metadata["location"])
    if sha256(source) != metadata["sha256"]:
        raise CaptureError("La fuente local cambió desde que se creó el job.")
    part = target.with_name(target.name + ".part")
    shutil.copyfile(source, part)
    if sha256(part) != metadata["sha256"]:
        raise CaptureError("La copia temporal no coincide con la fuente.")
    part.replace(target)