from contextlib import contextmanager
from pathlib import Path
import os

from .metadata import atomic_json
from .monitor import record_event
from ..models import CaptureError, now


@contextmanager
def job_lock(directory):
    """OS lock is released even after process death; job.json remains recoverable."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / ".lock"
    with path.open("a+b") as stream:
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"\0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise CaptureError("El job ya está siendo procesado por otra sesión.") from exc
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def save(directory, job, status=None):
    if status:
        job["status"] = status
    job["updated_at"] = now()
    atomic_json(directory / "job.json", job)
    record_event(directory, job["job_id"], job["status"], at=job["updated_at"])
