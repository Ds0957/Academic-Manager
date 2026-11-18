"""Archivos de estado de las clases automáticas (`procesamiento/clases-automaticas/`).

Lo comparten la lista de grabaciones encontradas (`auto_classes`) y el horario de Calendar
(`calendar_classes`): una sola definición de qué es una grabación y una sola forma de guardar.
"""
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from capture.models import CaptureError

FOLDER = Path("procesamiento") / "clases-automaticas"
MEDIA = ("video/", "audio/")  # tipos de archivo de Drive que cuentan como grabación


def stamp():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read(runtime, name):
    """El contenido del archivo, o None si no existe, está dañado o es un enlace."""
    path = Path(runtime) / FOLDER / name
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if not path.is_symlink() else None
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def write(runtime, name, data):
    """Reemplaza el archivo de una vez; nunca escribe a través de una carpeta o un temporal redirigidos."""
    folder = Path(runtime) / FOLDER
    if folder.is_symlink():
        raise CaptureError("La carpeta de clases automáticas está redirigida.")
    folder.mkdir(parents=True, exist_ok=True)
    temporary = folder / (name + ".tmp")
    if temporary.is_symlink():
        temporary.unlink()
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(temporary, folder / name)
