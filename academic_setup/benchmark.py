"""`--benchmark`: mide la velocidad real de WhisperX con una muestra generada.

Nunca estima sin medir: sin WhisperX, o si la muestra no activa el detector de voz, informa que la
medición no está disponible. La muestra es una señal sintética con forma de habla (vocales con
formantes y sílabas), sin palabras: mide velocidad, no precisión. Los WAV van a una carpeta
temporal que se borra al terminar; el modelo se descarga (la primera vez) a la misma caché que usa
Capture, `<runtime>/procesamiento/model-cache`.
"""
import contextlib
import math
import random
import sys
import tempfile
import time
import wave
from array import array
from pathlib import Path

from . import hardware

SAMPLE_RATE = 16000
BENCHMARK_SECONDS = 60
WARMUP_SECONDS = 1
CLASS_MINUTES = 90
FALLBACK_MODEL = "small"
# (F1, F2) aproximados de a, e, i, o, u en Hz.
VOWELS = ((800, 1200), (500, 1900), (300, 2300), (500, 900), (320, 800))

clock = time.perf_counter


def _gain(frequency, f1, f2, width=120.0):
    return sum(1 / (1 + ((frequency - f) / width) ** 2) for f in (f1, f2)) / max(frequency / 300, 1)


def _write(path, samples):
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(SAMPLE_RATE)
        out.writeframes(samples.tobytes())


def speech_like(path, seconds, seed=20261025):
    """Palabras de 3 a 6 sílabas vocálicas con pausas breves; determinista."""
    rng = random.Random(seed)
    total = int(seconds * SAMPLE_RATE)
    samples = array("h")
    while len(samples) < total:
        for _ in range(rng.randint(3, 6)):
            period = rng.randint(115, 150)  # tono fundamental de unos 107 a 139 Hz
            f1, f2 = rng.choice(VOWELS)
            harmonics = range(1, int(3500 * period / SAMPLE_RATE) + 1)
            cycle = [sum(_gain(k * SAMPLE_RATE / period, f1, f2) * math.sin(2 * math.pi * k * i / period)
                         for k in harmonics) for i in range(period)]
            peak = max(abs(v) for v in cycle) or 1
            length = int(SAMPLE_RATE * rng.uniform(0.18, 0.3))
            samples.extend(int(12000 * math.sin(math.pi * n / length) ** 2 * cycle[n % period] / peak)
                           for n in range(length))
        samples.extend([0] * int(SAMPLE_RATE * rng.uniform(0.15, 0.4)))
    del samples[total:]
    _write(path, samples)


def unavailable(reason):
    return {"disponible": False, "motivo": reason}


def run(runtime, recommendation):
    if not hardware._module_present("whisperx"):
        return unavailable("WhisperX no está instalado; la velocidad no se adivina.")
    from capture.models import CaptureError
    from capture.transcription.whisperx_engine import transcribe
    model = (recommendation or {}).get("modelo") or FALLBACK_MODEL
    device = (recommendation or {}).get("dispositivo") or "cpu"
    cache = Path(runtime) / "procesamiento" / "model-cache"
    try:
        with tempfile.TemporaryDirectory(prefix="academic-setup-benchmark-") as tmp:
            warmup, sample = Path(tmp) / "warmup.wav", Path(tmp) / "sample.wav"
            _write(warmup, array("h", [0] * (WARMUP_SECONDS * SAMPLE_RATE)))
            speech_like(sample, BENCHMARK_SECONDS)
            # WhisperX escribe en stdout: se desvía a stderr para no romper el JSON.
            with contextlib.redirect_stdout(sys.stderr):
                # Calentamiento: descarga (la primera vez) y carga el modelo fuera de la medición.
                transcribe(warmup, "es", device, cache, model=model)
                started = clock()
                raw, engine = transcribe(sample, "es", device, cache, model=model)
                elapsed = clock() - started
    except CaptureError as exc:
        return unavailable(f"La medición falló: {str(exc)[:200]}")
    except Exception as exc:  # noqa: BLE001 — WhisperX/PyTorch lanzan tipos propios; nunca se adivina
        return unavailable(f"La medición falló ({type(exc).__name__}).")
    if not (raw or {}).get("segments"):
        return unavailable("La muestra sintética no activó el detector de voz de WhisperX; "
                           "no se puede medir sin inventar el resultado.")
    factor = round(elapsed / BENCHMARK_SECONDS, 2)
    return {"disponible": True, "modelo": engine.get("model", model), "dispositivo": engine.get("device"),
            "compute_type": engine.get("compute_type"), "duracion_muestra_s": BENCHMARK_SECONDS,
            "segundos_medidos": round(elapsed, 1), "factor_tiempo_real": factor,
            "estimacion_clase_90_min_min": round(factor * CLASS_MINUTES),
            "nota": "Mide velocidad con una muestra sintética, no precisión; el modelo se cargó antes "
                    "de medir."}
