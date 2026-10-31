import json
import math
import subprocess

from ..models import CaptureError


def probe(path, executable="ffprobe"):
    try:
        result = subprocess.run([executable, "-v", "error", "-show_format", "-show_streams",
                                 "-of", "json", str(path)], capture_output=True, text=True,
                                encoding="utf-8", errors="replace", check=True, timeout=120)
        data = json.loads(result.stdout)
        if not any(s.get("codec_type") == "audio" for s in data.get("streams", [])):
            raise CaptureError("El medio no contiene una pista de audio.")
        duration = float(data["format"]["duration"])
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError("duration")
        return {"duration_seconds": duration,
                "format": data["format"].get("format_name"),
                "audio_streams": sum(s.get("codec_type") == "audio" for s in data["streams"])}
    except CaptureError:
        raise
    except FileNotFoundError as exc:
        raise CaptureError("No se encontró FFprobe. Instale FFmpeg y agréguelo al PATH.") from exc
    except (ValueError, KeyError, subprocess.SubprocessError) as exc:
        raise CaptureError("FFprobe no pudo validar el medio o su duración.") from exc