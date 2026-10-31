from dataclasses import dataclass
from pathlib import Path
import os

from academic_config import paths
from .models import CaptureError


@dataclass(frozen=True)
class Config:
    runtime: Path
    ffmpeg: str = "ffmpeg"
    ffprobe: str = "ffprobe"

    @classmethod
    def default(cls):
        try:
            configured = paths.load_config().get("runtime")  # Se valida siempre, como en las CLI.
        except paths.ConfigError as exc:
            raise CaptureError(str(exc)) from None
        value = os.environ.get("ACADEMIC_MANAGER_RUNTIME") or configured
        if not value and os.name != "nt":
            raise CaptureError("Fuera de Windows indique --runtime con una ruta local absoluta.")
        return cls(Path(value or r"C:\AcademicManagerData"))

    def prepare(self):
        root = self.runtime
        if not root.is_absolute() or root == Path(root.anchor):
            raise CaptureError("El runtime debe ser una carpeta absoluta dedicada.")
        for name in ("procesamiento", "temporales"):
            (root / name).mkdir(parents=True, exist_ok=True)