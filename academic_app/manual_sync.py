"""«Sincronizar ahora»: la misma rutina que la tarea diaria, lanzada a mano desde el panel.

Ejecuta scripts/canvas-daily.ps1 de esta copia (Canvas → base y Drive, material de estudio e
índice) como proceso aparte y sin ventana, con la carpeta de datos explícita: deja el mismo
latest-status.json y el mismo historial que la tarea programada, así que el panel se actualiza
solo. El resultado se reconoce cuando latest-status.json es posterior al inicio. Nunca se
muestra el texto del registro: solo un mensaje sencillo según el resultado.
"""
import json
import os
import subprocess
import time
from datetime import datetime
from pathlib import Path

STATE = Path("procesamiento") / "canvas-sync" / "manual-sync.json"
LATEST = Path("procesamiento") / "canvas-sync" / "logs" / "latest-status.json"
SCRIPT = Path("scripts") / "canvas-daily.ps1"
MAX_SECONDS = 45 * 60  # más que una corrida lenta (Canvas, Drive, PDF e índice)
CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_PROCESS_GROUP = 0x00000200

NOT_WINDOWS = "Solo se puede sincronizar desde Windows, en el PC donde está instalado Academic Manager."
NOT_STARTED = "Windows no dejó empezar la sincronización. Inténtelo otra vez; si sigue igual, pida ayuda."
DONE = "Listo: Canvas está al día. Lo nuevo ya se ve en el panel."
TOKEN = ("El token de Canvas venció o ya no es válido. Cree uno nuevo y péguelo en «Configuración» → «Canvas»; "
         "después vuelva a sincronizar.")
BUSY = ("La base estaba ocupada con otra actualización (por ejemplo, la sincronización programada o un registro "
        "desde el chat). Espere unos minutos y vuelva a sincronizar.")
FAILED = ("La sincronización terminó con un error. Inténtelo otra vez en un rato; si se repite, revise "
          "«Configuración» → «Canvas» y que Google Drive esté abierto.")
INTERRUPTED = ("La sincronización no terminó (pudo cerrarse el PC o el proceso). Puede volver a "
               "intentarlo.")


class SyncUnavailable(Exception):
    """No se pudo empezar; el texto ya es para la persona."""


def _read(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None


def _write(runtime, **state):
    path = Path(runtime) / STATE
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)
    return state


def plain(code, message):
    """Mensaje sencillo para el resultado de la rutina; el registro nunca se muestra."""
    if code == 0:
        return DONE
    text = (message or "").casefold()
    if "token vencido" in text or "http 401" in text or "autorización rechazada" in text:
        return TOKEN
    if "no está libre" in text or "ocupad" in text or "lock" in text:
        return BUSY
    return FAILED


def status(runtime, *, now=time.time):
    state = _read(Path(runtime) / STATE)
    if not isinstance(state, dict) or state.get("estado") not in ("sincronizando", "listo", "error",
                                                                     "interrumpida"):
        return {"estado": "ninguna"}
    if state["estado"] != "sincronizando":
        return state
    started = state.get("inicio_epoch")
    if not isinstance(started, (int, float)):
        return _write(runtime, estado="interrumpida", mensaje=INTERRUPTED)
    latest = Path(runtime) / LATEST
    try:
        fresh = latest.is_file() and not latest.is_symlink() and latest.stat().st_mtime >= started
    except OSError:
        fresh = False
    if fresh:
        result = _read(latest) or {}
        code = result.get("exit_code")
        if isinstance(code, bool) or type(code) not in (int, float):
            code = 1
        return _write(runtime, estado="listo" if code == 0 else "error", mensaje=plain(code, result.get("message")),
                      inicio_epoch=started, fin=datetime.now().astimezone().isoformat(timespec="seconds"))
    if now() - started > MAX_SECONDS:
        return _write(runtime, estado="interrumpida", mensaje=INTERRUPTED, inicio_epoch=started)
    return state


def command(repo, python, runtime):
    return ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-File", str(Path(repo) / SCRIPT), "-Repo", str(repo), "-Python", str(python),
            "-Runtime", str(runtime)]


def start(runtime, *, repo=None, python=None, popen=subprocess.Popen, now=time.time, os_name=os.name):
    """Lanza la rutina; si ya hay una manual en curso, devuelve esa sin lanzar otra."""
    if os_name != "nt":
        raise SyncUnavailable(NOT_WINDOWS)
    current = status(runtime, now=now)
    if current["estado"] == "sincronizando":
        return current
    from academic_setup.whisperx import console_python
    from .daily_task import repo_root
    repo = Path(repo or repo_root())
    state = _write(runtime, estado="sincronizando", inicio_epoch=now(),
                   inicio=datetime.now().astimezone().isoformat(timespec="seconds"))
    try:
        popen(command(repo, console_python(python), Path(runtime)), stdin=subprocess.DEVNULL,
              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=str(repo), close_fds=True,
              creationflags=CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP)
    except OSError:
        _write(runtime, estado="error", mensaje=NOT_STARTED)
        raise SyncUnavailable(NOT_STARTED) from None
    return state
