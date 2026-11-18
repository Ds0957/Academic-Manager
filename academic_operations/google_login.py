"""Renovar la sesión de Google de Academic Manager desde Claude (`iniciar_sesion_google`).

Lanza, en un proceso aparte, el mismo inicio de sesión del asistente (`auth.login(write=True)`):
abre Google en el navegador de este PC y la persona autoriza allí; el chat nunca ve el token.
El resultado queda en procesamiento/google-login/estado.json para consultarlo después.
"""
import argparse
import contextlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from academic_config import paths
from capture.models import CaptureError
from capture.storage.job import job_lock
from capture.storage.metadata import atomic_json

from .actions import OperationError

FOLDER = Path("procesamiento") / "google-login"
STATE = "estado.json"
WAIT_SECONDS = 240  # el navegador espera 180 s la autorización; un poco más para arrancar
START_GRACE = 30  # segundos que el proceso recién lanzado tiene para tomar su bloqueo
PROCESS = "proceso"  # bloqueo que el proceso tiene mientras espera a Google
CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_PROCESS_GROUP = 0x00000200

NO_CLIENT = ("Falta el archivo de acceso de Google en este PC. Abra el asistente de Academic Manager, vaya al "
             "paso de Google, suba el archivo JSON del cliente y pulse «Volver a iniciar sesión».")
NOT_WINDOWS = "Solo se puede iniciar sesión desde Windows, en el PC donde está instalado Academic Manager."
NOT_STARTED = "Windows no dejó abrir el inicio de sesión. Inténtelo otra vez; si sigue igual, pida ayuda."
WAITING = ("Se abrió Google en el navegador de este PC. Elija su cuenta y autorice todos los permisos; "
           "tiene 3 minutos. Después consulte el resultado.")
DONE = "Sesión de Google renovada. Ya puede volver a intentar lo que falló."
FAILED = ("No se completó el inicio de sesión de Google (se cerró la pestaña, pasó el tiempo o no se "
          "aceptaron todos los permisos). Puede volver a intentarlo.")
NONE = "No se ha pedido un inicio de sesión de Google desde el chat."


def _fail(code, message):
    raise OperationError(code, message)


def _path(runtime):
    return Path(runtime) / FOLDER / STATE


def _read(runtime):
    path = _path(runtime)
    if path.is_symlink() or path.parent.is_symlink():  # un estado redirigido no se lee
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _write(runtime, **state):
    path = _path(runtime)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_json(path, state)
    return state


def _client(runtime):
    """El cliente que guarda el asistente (`auth.client_path`), sin crear su carpeta si no existe."""
    return Path(runtime) / "control" / "drive-oauth" / "oauth-client.json"


def _public(state):
    """Al chat solo llegan estos campos, siempre texto propio de este módulo o una fecha."""
    return {k: state[k] for k in ("estado", "mensaje", "inicio", "fin") if k in state}


def _running(runtime):
    """True si el proceso de inicio de sesión sigue vivo (tiene su bloqueo)."""
    try:
        with job_lock(Path(runtime) / FOLDER / PROCESS):
            return False
    except CaptureError:
        return True


def status(runtime, *, now=time.time):
    state = _read(runtime)
    if not state or state.get("estado") not in ("esperando", "listo", "error"):
        return {"estado": "ninguno", "mensaje": NONE}
    if state["estado"] == "esperando":
        started = state.get("inicio_epoch")
        waited = now() - started if isinstance(started, (int, float)) else None
        # sin fecha, pasado el tiempo, o el proceso murió sin llegar a abrir Google
        if waited is None or waited > WAIT_SECONDS or (waited > START_GRACE and not _running(runtime)):
            again = _read(runtime) or {}  # el proceso pudo terminar mientras se miraba su bloqueo
            if again.get("estado") == "esperando" and again.get("inicio_epoch") == started:
                state = _write(runtime, estado="error", mensaje=FAILED)
            else:
                state = again or state
    return _public(state)


def start(runtime, *, popen=subprocess.Popen, now=time.time, os_name=None):
    """Abre el inicio de sesión; si ya hay uno esperando, devuelve ese sin abrir otro."""
    runtime = Path(runtime)
    if (os_name or os.name) != "nt":
        _fail("NOT_WINDOWS", NOT_WINDOWS)
    client = _client(runtime)
    if client.is_symlink() or not client.is_file():
        _fail("NO_CLIENT", NO_CLIENT)
    try:
        with job_lock(runtime / FOLDER):
            current = status(runtime, now=now)
            if current["estado"] == "esperando":
                return current
            state = _write(runtime, estado="esperando", mensaje=WAITING, inicio_epoch=now(),
                           inicio=datetime.now().astimezone().isoformat(timespec="seconds"))
    except CaptureError:  # otro pedido lo está abriendo en este momento
        return {"estado": "esperando", "mensaje": WAITING}
    from academic_setup.whisperx import console_python
    argv = [str(console_python()), "-m", "academic_operations.google_login", "--runtime", str(runtime)]
    try:
        popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
              cwd=str(Path(__file__).resolve().parents[1]), close_fds=True,
              creationflags=CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP)
    except OSError:
        _write(runtime, estado="error", mensaje=NOT_STARTED)
        _fail("NOT_STARTED", NOT_STARTED)
    return _public(state)


def run(runtime, *, login=None):
    """El proceso aparte: inicia sesión y deja el resultado. Nunca guarda el detalle del error."""
    from capture.sources import auth
    login = login or (lambda r, c: auth.login(r, c, write=True))
    runtime = Path(runtime)
    with contextlib.ExitStack() as held:
        try:
            held.enter_context(job_lock(runtime / FOLDER / PROCESS))
        except CaptureError:
            return 1  # ya hay otro proceso esperando a Google; ese deja el resultado
        try:
            login(runtime, _client(runtime))
        except Exception:  # noqa: BLE001 - la persona solo necesita saber que no se completó
            _write(runtime, estado="error", mensaje=FAILED)
            return 1
        _write(runtime, estado="listo", mensaje=DONE,  # con el bloqueo: «status» no lo da por muerto
               fin=datetime.now().astimezone().isoformat(timespec="seconds"))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="Uso interno: inicio de sesión de Google pedido desde el chat.")
    parser.add_argument("--runtime", default=None, help=paths.DATA_HELP)
    args = parser.parse_args(argv)
    return run(paths.runtime(args.runtime, env=("ACADEMIC_MANAGER_DATA",), default=paths.DEFAULT_RUNTIME))


if __name__ == "__main__":
    sys.exit(main())
