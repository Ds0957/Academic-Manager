import html
import math
import re
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


FRAGMENT_SECONDS = 30  # the player's phrases last 3-4 s; fragments read like the other classes
CUE = re.compile(r"^((?:\d+:)?\d{2}:\d{2}\.\d{3})\s+-->\s+((?:\d+:)?\d{2}:\d{2}\.\d{3})")
TAG = re.compile(r"<[^>]*>")


def _vtt_seconds(stamp):
    parts = [float(p) for p in stamp.split(":")]
    return round(sum(value * 60 ** i for i, value in enumerate(reversed(parts))), 3)


def _cues(text):
    """(inicio, fin, líneas limpias) de cada bloque con tiempo; lo de antes del primero es la cabecera.

    Una línea suelta justo antes de un tiempo (con una vacía antes) es el número del bloque
    siguiente, no texto. No depende de cómo vengan las líneas con solo espacios.
    """
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    timings = [i for i, line in enumerate(lines) if CUE.match(line.strip())]
    cues = []
    for n, at in enumerate(timings):
        stop = timings[n + 1] if n + 1 < len(timings) else len(lines)
        body = lines[at + 1:stop]
        if n + 1 < len(timings) and len(body) >= 2 and body[-1].strip() and not body[-2].strip():
            body = body[:-1]  # número del bloque siguiente
        timing = CUE.match(lines[at].strip())
        words = [" ".join(html.unescape(TAG.sub("", line)).split()) for line in body]
        cues.append((_vtt_seconds(timing.group(1)), _vtt_seconds(timing.group(2)), [w for w in words if w]))
    return cues


def import_vtt(text):
    """Subtítulos WebVTT (los automáticos del reproductor de Drive) en fragmentos de unos 30 s.

    Esos subtítulos «ruedan»: cada bloque repite la línea anterior antes de la nueva, y otros de
    10 ms repiten la que se acaba de decir. Por eso cada frase se toma una sola vez: se quitan
    las líneas iniciales que repiten una de las dos últimas ya tomadas.
    """
    language = re.search(r"(?m)^Language:\s*([A-Za-z-]+)\s*$", text.split("\n\n", 1)[0])
    phrases, recent = [], []
    for start, end, lines in _cues(text):
        while lines and lines[0] in recent:
            lines = lines[1:]
        if not lines:
            continue
        phrases.append((start, end, " ".join(lines)))
        recent = (recent + lines)[-2:]
    if not phrases:
        raise CaptureError("El archivo .vtt no tiene subtítulos con tiempos.")
    segments = []
    for start, end, words in phrases:
        if segments and start - segments[-1]["start"] < FRAGMENT_SECONDS:
            segments[-1]["end"] = max(segments[-1]["end"], end)
            segments[-1]["text"] += " " + words
        else:
            segments.append({"start": start, "end": end, "text": words})
    return {"segments": segments, "language": language.group(1) if language else None}


def import_transcript(path):
    path = Path(path)
    if path.suffix.lower() == ".json":
        return read_json(path)
    if path.suffix.lower() == ".vtt":
        return import_vtt(path.read_text(encoding="utf-8-sig"))
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