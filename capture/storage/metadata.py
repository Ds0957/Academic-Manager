import hashlib
import json
import os
import time
from pathlib import Path
from uuid import uuid4


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        # Windows can temporarily deny replacing a file open without FILE_SHARE_DELETE.
        # Do not overwrite in place: on persistent failure, keep the previous valid JSON.
        for attempt, delay in enumerate((0.05, 0.15, 0.35, 0.7, None)):
            try:
                os.replace(temporary, path)
                break
            except PermissionError:
                if delay is None or not path.is_file():
                    raise
                time.sleep(delay)
    finally:
        temporary.unlink(missing_ok=True)


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
