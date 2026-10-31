import subprocess
import wave

from ..models import CaptureError


def extract(source, target, start=0, duration=None, executable="ffmpeg"):
    part = target.with_name(target.name + ".part")
    command = [executable, "-nostdin", "-v", "error", "-y", "-ss", str(start), "-i", str(source)]
    if duration is not None:
        command += ["-t", str(duration)]
    command += ["-map", "0:a:0", "-vn", "-ac", "1", "-ar", "16000",
                "-c:a", "pcm_s16le", "-f", "wav", str(part)]
    try:
        subprocess.run(command, check=True, capture_output=True)
        with wave.open(str(part), "rb") as audio:
            if (audio.getnchannels(), audio.getframerate(), audio.getsampwidth()) != (1, 16000, 2):
                raise CaptureError("El WAV generado no cumple mono/16 kHz/PCM16.")
            seconds = audio.getnframes() / audio.getframerate()
            if seconds <= 0:
                raise CaptureError("FFmpeg generó un WAV vacío.")
        part.replace(target)
        return seconds
    except FileNotFoundError as exc:
        raise CaptureError("No se encontró FFmpeg. Instálelo y agréguelo al PATH.") from exc
    except (subprocess.SubprocessError, wave.Error) as exc:
        raise CaptureError("Falló la extracción de audio; se conserva el job para reintentar.") from exc