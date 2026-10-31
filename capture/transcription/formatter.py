import math
from pathlib import Path

from ..models import CaptureError
from ..storage.metadata import read_json


def normalize(raw, offset=0, duration=None):
    if not isinstance(raw, dict) or not isinstance(raw.get("segments"), list):
        raise CaptureError("La transcripción JSON debe contener una lista segments.")
    segments = []
    previous = -1
    for entry in raw["segments"]:
        if not isinstance(entry, dict) or not isinstance(entry.get("text"), str):
            raise CaptureError("Cada segmento debe contener text.")
        text = " ".join(entry["text"].split())
        if not text:
            continue
        start, end = entry.get("start"), entry.get("end")
        if start is None and end is None:
            if not raw.get("timestamps_unavailable"):
                raise CaptureError("Faltan timestamps; use texto/Markdown o indique timestamps_unavailable.")
        else:
            if any(isinstance(v, bool) or not isinstance(v, (int, float)) or
                   not math.isfinite(v) for v in (start, end)):
                raise CaptureError("Timestamps inválidos.")
            if start < 0 or end < start or start < previous:
                raise CaptureError("Los segmentos deben estar ordenados y tener tiempos válidos.")
            if duration is not None and end > duration + 1:
                raise CaptureError("Un segmento excede la duración procesada.")
            previous = start
            start, end = round(start + offset, 3), round(end + offset, 3)
        segments.append({"start": start, "end": end, "text": text})
    if not segments:
        raise CaptureError("No hay texto transcrito; revise audio, idioma y fragmento.")
    return {"schema_version": 1, "language": raw.get("language"),
            "time_reference": "original_media", "timestamps_unavailable":
            any(s["start"] is None for s in segments), "segments": segments}


def import_transcript(path):
    path = Path(path)
    if path.suffix.lower() == ".json":
        return read_json(path)
    text = path.read_text(encoding="utf-8-sig").strip()
    return {"segments": [{"start": None, "end": None, "text": text}],
            "timestamps_unavailable": True, "language": None}


def timestamp(seconds):
    seconds = int(seconds)
    return f"{seconds // 3600:02d}:{seconds // 60 % 60:02d}:{seconds % 60:02d}"


def markdown(transcript, title):
    lines = ["# Transcripción", "", f"Fuente: {title}", "",
             "Tiempos referidos al medio original; revise el texto antes de usarlo como evidencia.", ""]
    for item in transcript["segments"]:
        prefix = (f"[{timestamp(item['start'])}–{timestamp(item['end'])}] "
                  if item["start"] is not None else "")
        lines += [prefix + item["text"], ""]
    return "\n".join(lines)