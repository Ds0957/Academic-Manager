"""Instalación de WhisperX según la comprobación del equipo (asistente, paso 2; docs/39).

El veredicto de `python -m academic_setup check` decide (sin copiar su lógica):
- `apto_gpu`: PyTorch con CUDA y WhisperX;
- `solo_cpu`: PyTorch para el procesador y WhisperX;
- `no_apto`: no se instala nada.

Con WhisperX se instala también FFmpeg (versión y SHA-256 fijados, solo si el equipo es apto):
ffmpeg.exe y ffprobe.exe quedan junto al python.exe de esta copia, donde Windows los busca primero
al lanzar un programa sin ruta (Capture y WhisperX llaman a «ffmpeg»/«ffprobe»), sin tocar el PATH.

La instalación corre en un proceso aparte que sigue aunque se cierre el asistente, con su
registro en <datos>/procesamiento/whisperx-install/ y un bloqueo del sistema para no correr dos
veces. Se puede repetir: pip no vuelve a descargar lo que ya quedó instalado.

Uso interno: python -m academic_setup.whisperx --runtime RUTA --veredicto apto_gpu|solo_cpu
"""
import argparse
import contextlib
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path

WHISPERX = "whisperx==3.8.6"
# whisperx 3.8.6 pide torch~=2.8.0, torchaudio~=2.8.0 y torchvision~=0.23.0.
TORCH = ("torch==2.8.0", "torchaudio==2.8.0", "torchvision==0.23.0")
VARIANTS = {"apto_gpu": ("cuda", "https://download.pytorch.org/whl/cu128", "+cu128"),
            "solo_cpu": ("cpu", "https://download.pytorch.org/whl/cpu", "+cpu")}
# Build «essentials» de gyan.dev (enlazado desde ffmpeg.org), en su espejo de GitHub. El SHA-256 es el
# que publican gyan.dev (<archivo>.sha256) y el digest del asset de la release; un valor que no sea un
# hash detiene la instalación sin descargar nada.
FFMPEG = {"version": "9.0.2",
          "url": "https://github.com/GyanD/codexffmpeg/releases/download/9.0.2/ffmpeg-9.0.2-essentials_build.zip",
          "sha256": "60f467265b1e312373dbcd92200c2618a74850f98d3d078e94296bb3fa2047ba"}
FFMPEG_FILES = ("ffmpeg.exe", "ffprobe.exe")
LOCK_ATTEMPTS = 20         # el sondeo de estado del asistente puede tener el bloqueo un instante
LOCK_WAIT_SECONDS = 0.5
STARTING_GRACE_SECONDS = 120  # el proceso recién lanzado todavía no tomó el bloqueo
FOLDER = Path("procesamiento") / "whisperx-install"
REPO = Path(__file__).resolve().parents[1]
PIP = ("-m", "pip", "install", "--disable-pip-version-check", "--no-warn-script-location", "--no-input")
CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_PROCESS_GROUP = 0x00000200


class InstallError(Exception):
    """Paso de la instalación que no se completó; el mensaje va al registro."""


def plan(verdict):
    """Qué PyTorch instalar para el veredicto; None cuando no se instala WhisperX."""
    entry = VARIANTS.get(verdict)
    if entry is None:
        return None
    variant, index, local = entry
    return {"variante": variant, "indice": index, "paquetes": [name + local for name in TORCH]}


def commands(python, verdict):
    """Dos pasos: PyTorch del índice oficial que corresponde y luego WhisperX desde PyPI."""
    chosen = plan(verdict)
    if chosen is None:
        raise ValueError(f"Con el veredicto {verdict!r} no se instala WhisperX.")
    pip = [str(python), *PIP]
    return [pip + ["--index-url", chosen["indice"], *chosen["paquetes"]], pip + [WHISPERX]]


def console_python(executable=None):
    """python.exe junto a pythonw.exe: pip y los registros necesitan una salida de consola."""
    exe = Path(executable or sys.executable)
    if exe.name.lower() == "pythonw.exe" and exe.with_name("python.exe").is_file():
        return exe.with_name("python.exe")
    return exe


def folder(runtime):
    return Path(runtime) / FOLDER


def log_file(runtime):
    return folder(runtime) / "pip.log"


def _state_file(runtime):
    return folder(runtime) / "estado.json"


def _write(runtime, **data):
    from capture.storage.metadata import atomic_json
    atomic_json(_state_file(runtime), data)
    return data


def _running(runtime):
    from capture.models import CaptureError
    from capture.storage import job
    try:
        with job.job_lock(folder(runtime)):
            return False
    except CaptureError:
        return True


def status(runtime):
    """{"estado": sin_iniciar|instalando|instalado|fallo|interrumpida, ...}; solo lee."""
    try:
        data = json.loads(_state_file(runtime).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"estado": "sin_iniciar"}
    except (OSError, ValueError):
        return {"estado": "fallo", "motivo": "El registro de la instalación no se puede leer."}
    if not isinstance(data, dict) or "estado" not in data:
        return {"estado": "fallo", "motivo": "El registro de la instalación no se puede leer."}
    if data["estado"] == "instalando" and not _running(runtime):
        started = data.get("inicio_epoch")
        if not isinstance(started, (int, float)) or time.time() - started > STARTING_GRACE_SECONDS:
            data = {**data, "estado": "interrumpida"}
    return data


def _stamp():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def start(runtime, verdict, *, python=None, popen=subprocess.Popen, now=time.time):
    """Lanza la instalación en segundo plano; si ya hay una en curso, no lanza otra."""
    chosen = plan(verdict)
    if chosen is None:
        raise ValueError(f"Con el veredicto {verdict!r} no se instala WhisperX.")
    current = status(runtime)
    if current["estado"] == "instalando":
        return current
    python = console_python(python)
    folder(runtime).mkdir(parents=True, exist_ok=True)
    state = _write(runtime, estado="instalando", variante=chosen["variante"], veredicto=verdict,
                   inicio=_stamp(), inicio_epoch=now())
    options = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL,
               "cwd": str(REPO), "close_fds": True}
    if os.name == "nt":  # sin ventana y fuera de la consola del asistente: sobrevive a cerrarla
        options["creationflags"] = CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP
    else:
        options["start_new_session"] = True
    try:
        popen([str(python), "-m", "academic_setup.whisperx", "--runtime", str(runtime),
               "--veredicto", verdict], **options)
    except OSError as exc:
        _write(runtime, estado="fallo", variante=chosen["variante"], veredicto=verdict, fin=_stamp(),
               motivo=f"No se pudo lanzar la instalación ({type(exc).__name__}).")
        raise
    return state


def _download(url, path):
    with urllib.request.urlopen(url, timeout=60) as response, open(path, "wb") as stream:
        while chunk := response.read(1024 * 1024):
            stream.write(chunk)


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def install_ffmpeg(target, cache, *, fetch=_download):
    """ffmpeg.exe y ffprobe.exe del paquete fijado en `target`; no hace nada si ya están."""
    target, cache = Path(target), Path(cache)
    if all((target / name).is_file() for name in FFMPEG_FILES):
        return
    expected = FFMPEG["sha256"]
    if not (isinstance(expected, str) and len(expected) == 64 and all(c in "0123456789abcdef" for c in expected)):
        raise InstallError("FFmpeg no tiene una huella SHA-256 verificada; no se descarga.")
    cache.mkdir(parents=True, exist_ok=True)
    archive = cache / f"ffmpeg-{FFMPEG['version']}.zip"
    if not archive.is_file() or _sha256(archive) != expected:
        part = archive.with_name(archive.name + ".part")
        try:
            fetch(FFMPEG["url"], part)
            if _sha256(part) != expected:
                raise InstallError("La descarga de FFmpeg no coincide con su huella SHA-256; se descartó.")
            os.replace(part, archive)
        finally:
            part.unlink(missing_ok=True)
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as bundle:
        members = {Path(n).name: n for n in bundle.namelist() if Path(n).parent.name == "bin"}
        missing = [name for name in FFMPEG_FILES if name not in members]
        if missing:
            raise InstallError(f"El paquete de FFmpeg no trae {', '.join(missing)}.")
        for name in FFMPEG_FILES:
            temporary = target / (name + ".part")
            with bundle.open(members[name]) as source, open(temporary, "wb") as stream:
                shutil.copyfileobj(source, stream, 1024 * 1024)
            os.replace(temporary, target / name)


def _install(runtime, verdict, python, runner):
    chosen = plan(verdict)
    steps = commands(python, verdict)
    code = 0
    with log_file(runtime).open("a", encoding="utf-8") as log:
        log.write(f"\n== {_stamp()} instalación de WhisperX ({chosen['variante']}) ==\n")
        for cmd in steps:
            log.write("$ " + " ".join(cmd) + "\n")
            log.flush()
            code = runner(cmd, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT).returncode
            if code != 0:
                break
        if code == 0:
            log.write(f"FFmpeg {FFMPEG['version']} junto a {python.parent}\n")
            try:
                install_ffmpeg(python.parent, folder(runtime))
            except (InstallError, OSError, zipfile.BadZipFile) as exc:
                log.write(f"FFmpeg no se instaló: {exc}\n")
                code = 4
        log.write(f"== fin: código {code} ==\n")
    _write(runtime, estado="instalado" if code == 0 else "fallo", variante=chosen["variante"],
           veredicto=verdict, fin=_stamp(), codigo=code)
    return code


def run(runtime, verdict, *, python=None, runner=subprocess.run):
    """Instala (proceso de fondo). Devuelve 0 si WhisperX y FFmpeg quedaron instalados."""
    from capture.models import CaptureError
    from capture.storage import job
    python = console_python(python)
    commands(python, verdict)  # un veredicto sin instalación falla antes de tocar nada
    with contextlib.ExitStack() as stack:
        for attempt in range(LOCK_ATTEMPTS):
            try:
                stack.enter_context(job.job_lock(folder(runtime)))
                break
            except CaptureError:
                if attempt == LOCK_ATTEMPTS - 1:
                    raise
                time.sleep(LOCK_WAIT_SECONDS)
        try:
            return _install(runtime, verdict, python, runner)
        except Exception as exc:  # sin consola: el motivo queda en el estado que lee el asistente
            _write(runtime, estado="fallo", veredicto=verdict, fin=_stamp(),
                   motivo=f"La instalación se detuvo por un error inesperado ({type(exc).__name__}).")
            raise


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m academic_setup.whisperx",
                                description="Instala WhisperX según la comprobación del equipo (uso interno "
                                            "del asistente).")
    p.add_argument("--runtime", required=True)
    p.add_argument("--veredicto", required=True, choices=sorted(VARIANTS))
    args = p.parse_args(argv)
    from capture.models import CaptureError
    try:
        return 0 if run(Path(args.runtime), args.veredicto) == 0 else 1
    except CaptureError:  # otra instalación tiene el bloqueo
        return 3
    except Exception:  # ya quedó registrado como «fallo» en el estado
        return 1


if __name__ == "__main__":
    sys.exit(main())
