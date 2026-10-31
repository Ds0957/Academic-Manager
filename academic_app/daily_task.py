"""Tarea diaria de Canvas para esta copia de Academic Manager (asistente, paso 7).

Reutiliza scripts/install-canvas-daily-task.ps1 (mismo nombre de tarea, mismos horarios) con
-Repo y -Python de esta copia: así la tarea ejecuta este código con este Python, y la carpeta de
datos la decide config.json (canvas-daily.ps1 pregunta a academic_config.paths). El estado se
consulta igual que `academic_access system`.
"""
import os
import subprocess
from pathlib import Path

SCRIPT = Path("scripts") / "install-canvas-daily-task.ps1"
# schtasks /Query sale con código 1 y este texto (según el idioma de Windows) cuando la tarea no existe.
MISSING = ("no puede encontrar el archivo especificado", "cannot find the file specified")


class TaskError(Exception):
    """La tarea no se pudo crear; el mensaje se muestra tal cual."""


def repo_root():
    return Path(__file__).resolve().parents[1]


def command(repo, python):
    return ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-File", str(Path(repo) / SCRIPT), "-Repo", str(repo), "-Python", str(python)]


def register(*, repo=None, python=None, run=subprocess.run):
    if os.name != "nt":
        raise TaskError("La sincronización diaria solo se puede activar en Windows.")
    from academic_setup.whisperx import console_python
    cmd = command(repo or repo_root(), python or console_python())
    try:
        result = run(cmd, capture_output=True, encoding="oem", errors="replace", timeout=120)
    except (OSError, subprocess.SubprocessError):
        raise TaskError("Windows no dejó crear la tarea diaria. Vuelva a intentarlo; si sigue igual, pida "
                        "ayuda.") from None
    if result.returncode != 0:
        raise TaskError("Windows no dejó crear la tarea diaria. Vuelva a intentarlo con su sesión de Windows "
                        "iniciada; si sigue igual, pida ayuda.")


def status():
    """Fila `tarea_diaria` de academic_access (estado ok|error|no_disponible y detalle).

    academic_access informa «no existe» solo cuando schtasks no da error; la tarea ausente llega como
    «No se pudo consultar … (código 1): ERROR: …». Aquí ese caso se reconoce como tarea ausente, sin
    cambiar la salida de academic_access.
    """
    from academic_access.system import _scheduled_task, _task_check
    task = _scheduled_task()
    if isinstance(task, str) and "(código 1)" in task and any(m in task.casefold() for m in MISSING):
        task = None
    return _task_check(task)
