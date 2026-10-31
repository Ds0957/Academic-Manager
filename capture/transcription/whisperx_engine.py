"""WhisperX ASR only. No diarization or paid API is used."""
import gc
import os
from pathlib import Path

from ..models import CaptureError


def retryable(exc):
    message = str(exc).lower()
    return any(term in message for term in ("out of memory", "cuda", "cudnn", "cublas",
                                           "float16", "compute type"))


def transcribe(audio_path, language="es", device="auto", cache_dir=None, progress_callback=None,
               model="large-v3"):
    """`model`: large-v3 (por defecto), medium o small; queda registrado en los metadatos."""
    if cache_dir:
        cache_dir = Path(cache_dir)
        cache_dir.mkdir(parents=True, exist_ok=True)
        os.environ["HF_HOME"] = str(cache_dir / "huggingface")
        os.environ["TORCH_HOME"] = str(cache_dir / "torch")
    try:
        import torch
        import whisperx
    except ImportError as exc:
        raise CaptureError("Falta WhisperX/PyTorch. Instale requirements-capture.txt en el entorno de Capture.") from exc
    cuda = torch.cuda.is_available()
    if device == "cuda" and not cuda:
        raise CaptureError("CUDA no está disponible; use --device auto o cpu.")
    attempts = ([("cuda", "float16", n) for n in (4, 2, 1)] +
                [("cuda", "int8_float16", 1)] if cuda and device != "cpu" else [])
    if device != "cuda":
        attempts.append(("cpu", "int8", 1))
    audio = whisperx.load_audio(str(audio_path))
    failures = []
    name = model
    for selected, compute, batch in attempts:
        model = None
        print(f"WhisperX {name}: {selected}, {compute}, batch={batch}", flush=True)
        try:
            model = whisperx.load_model(name, selected, compute_type=compute,
                                       language=language, vad_method="silero",
                                       download_root=str(cache_dir) if cache_dir else None)
            result = model.transcribe(audio, batch_size=batch,
                                      progress_callback=progress_callback)
            return result, {"engine": "whisperx", "model": name, "device": selected,
                            "compute_type": compute, "batch_size": batch,
                            "alignment": False, "diarization": False,
                            "fallback_attempts": failures}
        except Exception as exc:
            if not retryable(exc):
                raise CaptureError("WhisperX falló; revise modelos/conexión/dependencias. "
                                   "El job conserva el medio para reintentar.") from exc
            failures.append({"device": selected, "compute_type": compute, "batch_size": batch,
                             "reason": type(exc).__name__})
        finally:
            del model
            gc.collect()
            if cuda:
                try:
                    torch.cuda.empty_cache()
                except RuntimeError:
                    pass
    raise CaptureError("Se agotaron los intentos CUDA/CPU. Revise el entorno; el job puede recuperarse.")