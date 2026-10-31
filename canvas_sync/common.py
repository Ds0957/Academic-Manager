"""Validación de rutas y escritura atómica, sin credenciales en registros."""
import json
import os
import time
from pathlib import Path
from uuid import uuid4


class SyncError(Exception):
    pass


class StoreBusy(SyncError):
    """Otro escritor tiene la base; se detectó antes de tomar el lock, así que no se escribió nada.

    `lock` es el lock del estado que la ocupa (o None), para juzgar si la ocupación es pasajera.
    """

    def __init__(self, message, lock=None):
        super().__init__(message)
        self.lock = lock


def runtime_root(raw):
    root = Path(raw).expanduser().absolute()
    if root == Path(root.anchor) or root.is_symlink():
        raise SyncError("El runtime debe ser una carpeta local absoluta y dedicada.")
    return root


def safe_dir(root, *segments):
    path = root
    for part in segments:
        path = path / part
        if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
            raise SyncError("No se usan directorios redirigidos.")
        path.mkdir(parents=False, exist_ok=True, mode=0o700)
    return path


def atomic_bytes(path, content):
    path = Path(path)
    if path.is_symlink():
        raise SyncError("No se escriben archivos redirigidos.")
    tmp = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    try:
        with os.fdopen(os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), "wb") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        # Windows can hold a destination briefly open without FILE_SHARE_DELETE.
        # Keep the previous ciphertext/snapshot intact on persistent failure.
        for delay in (0.05, 0.15, 0.35, 0.7, None):
            try:
                os.replace(tmp, path)
                break
            except PermissionError:
                if delay is None or not path.is_file():
                    raise
                time.sleep(delay)
    finally:
        tmp.unlink(missing_ok=True)


def atomic_json(path, data):
    atomic_bytes(path, (json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8"))
