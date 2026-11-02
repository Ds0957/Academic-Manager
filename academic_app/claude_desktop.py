"""Conectar Claude Desktop con el conector de Academic Manager (docs/43-conector-claude.md).

Agrega o quita solo la entrada `mcpServers["academic-manager"]` del archivo de configuración de
Claude Desktop, con una copia de seguridad antes de cada cambio y escritura atómica. Nunca crea la
carpeta de Claude, nunca sigue enlaces simbólicos y nunca muestra ni registra el contenido del archivo
(otros conectores pueden guardar claves en su `env`).
"""
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

CONFIG_NAME = "claude_desktop_config.json"
SERVER_KEY = "academic-manager"
DATA_ENV = "ACADEMIC_MANAGER_DATA"  # lo que lee academic_mcp.tools.resolve_runtime
BACKUP_INFIX = ".academic-manager-"

NOT_INSTALLED = "no_instalado"
NOT_CONNECTED = "sin_conectar"
CONNECTED = "conectado"
OUTDATED = "desactualizado"
UNREADABLE = "no_se_puede_leer"

MESSAGES = {
    NOT_INSTALLED: "Claude Desktop no está instalado en este PC",
    NOT_CONNECTED: "Claude Desktop está instalado, pero todavía no está conectado con Academic Manager.",
    CONNECTED: "Conectado: Claude Desktop puede leer sus datos de Academic Manager.",
    OUTDATED: ("Hay una conexión con Academic Manager, pero apunta a otra carpeta de datos o a otro "
               "programa. Pulse «Actualizar conexión»."),
}
UNCHANGED = " El archivo no se cambió."
NOT_INSTALLED_ACTION = ("Claude Desktop no está instalado en este PC. Instálelo, ábralo una vez, ciérrelo "
                        "y vuelva a intentarlo.")


class ClaudeDesktopError(Exception):
    """Problema en lenguaje sencillo; nunca incluye el contenido del archivo."""


def candidates(env=None):
    """Carpetas posibles de Claude Desktop: la instalación normal y la de Microsoft Store."""
    env = os.environ if env is None else env
    found = []
    if env.get("APPDATA"):
        found.append(Path(env["APPDATA"]) / "Claude")
    if env.get("LOCALAPPDATA"):
        packages = Path(env["LOCALAPPDATA"]) / "Packages"
        try:
            found += sorted(p / "LocalCache" / "Roaming" / "Claude" for p in packages.glob("Claude_*"))
        except OSError:
            pass
    return found


def _rank(folder, order):
    """Preferir la carpeta con otros archivos de Claude, luego la que ya tiene el archivo; en empate,
    la de Microsoft Store (su carpeta desaparece al desinstalarla, así que si está, es la que se usa)."""
    try:
        names = [p.name for p in folder.iterdir()]
    except OSError:
        names = []
    others = any(not n.startswith(CONFIG_NAME) for n in names)
    return (others, CONFIG_NAME in names, order)


def locate(env=None):
    """Ruta del archivo de configuración (puede no existir todavía) o None si Claude no está instalado."""
    folders = [(f, i) for i, f in enumerate(candidates(env)) if f.is_dir() and not f.is_symlink()]
    if not folders:
        return None
    folder = max(folders, key=lambda item: _rank(*item))[0]
    return folder / CONFIG_NAME


def python_command(executable=None):
    """El Python que corre Academic Manager (python.exe, no pythonw.exe: el conector usa stdio)."""
    path = Path(executable or sys.executable)
    if path.name.lower() == "pythonw.exe" and (path.parent / "python.exe").is_file():
        path = path.parent / "python.exe"
    return str(path)


def server_entry(data_folder, executable=None):
    return {"command": python_command(executable), "args": ["-m", "academic_mcp"],
            "env": {DATA_ENV: str(data_folder)}}


def manual_snippet(data_folder, executable=None):
    """Fragmento exacto para agregar a mano dentro del archivo de Claude Desktop."""
    return json.dumps({"mcpServers": {SERVER_KEY: server_entry(data_folder, executable)}},
                      indent=2, ensure_ascii=False)


def _read(path):
    """(bytes originales o None, objeto JSON). Falla cerrado ante cualquier cosa rara."""
    if path.is_symlink():
        raise ClaudeDesktopError("El archivo de configuración de Claude Desktop es un acceso directo "
                                 "(enlace) a otro archivo; por seguridad no se toca." + UNCHANGED)
    if not path.exists():
        return None, {}
    if not path.is_file():
        raise ClaudeDesktopError("La configuración de Claude Desktop no es un archivo normal." + UNCHANGED)
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ClaudeDesktopError("No se pudo leer la configuración de Claude Desktop "
                                 f"(detalle técnico: {type(exc).__name__})." + UNCHANGED) from None
    try:
        data = json.loads(raw.decode("utf-8-sig")) if raw.strip() else {}
    except (UnicodeDecodeError, ValueError):
        raise ClaudeDesktopError("La configuración de Claude Desktop tiene un error de formato (no es JSON "
                                 "válido)." + UNCHANGED) from None
    if not isinstance(data, dict) or not isinstance(data.get("mcpServers", {}), dict):
        raise ClaudeDesktopError("La configuración de Claude Desktop no tiene la forma esperada." + UNCHANGED)
    return raw, data


def _config(env):
    path = locate(env)
    if path is None:
        raise ClaudeDesktopError(NOT_INSTALLED_ACTION)
    return path


def status(data_folder=None, env=None, executable=None):
    """{"estado", "mensaje", "ruta"}; con data_folder=None solo dice si la entrada existe."""
    path = locate(env)
    if path is None:
        return {"estado": NOT_INSTALLED, "mensaje": MESSAGES[NOT_INSTALLED], "ruta": None}
    try:
        _, data = _read(path)
    except ClaudeDesktopError as exc:
        return {"estado": UNREADABLE, "mensaje": str(exc), "ruta": path}
    current = data.get("mcpServers", {}).get(SERVER_KEY)
    if current is None:
        state = NOT_CONNECTED
    elif data_folder is None or current == server_entry(data_folder, executable):
        state = CONNECTED
    else:
        state = OUTDATED
    return {"estado": state, "mensaje": MESSAGES[state], "ruta": path}


def _backup(path, raw, now):
    stamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    for n in range(100):
        suffix = "" if n == 0 else f"-{n}"
        target = path.with_name(f"{CONFIG_NAME}{BACKUP_INFIX}{stamp}{suffix}.bak")
        try:
            with open(target, "xb") as handle:
                handle.write(raw)
            return target
        except FileExistsError:
            continue
    raise ClaudeDesktopError("No se pudo guardar la copia de seguridad." + UNCHANGED)


def _write(path, data):
    text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    handle = tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, prefix=CONFIG_NAME + ".",
                                         suffix=".tmp", delete=False)
    try:
        with handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        if path.is_symlink():  # cambió entre la lectura y la escritura
            raise ClaudeDesktopError("El archivo de configuración de Claude Desktop cambió a un enlace; "
                                     "no se toca." + UNCHANGED)
        os.replace(handle.name, path)
    except BaseException:
        try:
            os.unlink(handle.name)
        except OSError:
            pass
        raise


def _change(env, now, edit):
    """Lee, aplica edit(servidores) -> bool, y si cambió: copia de seguridad y escritura atómica."""
    path = _config(env)
    raw, data = _read(path)
    servers = dict(data.get("mcpServers", {}))
    if not edit(servers):
        return {"cambio": False, "copia": None, "ruta": path}
    try:
        backup = _backup(path, raw, now) if raw is not None else None
        _write(path, {**data, "mcpServers": servers})
    except OSError as exc:
        raise ClaudeDesktopError("No se pudo guardar la configuración de Claude Desktop "
                                 f"(detalle técnico: {type(exc).__name__})." + UNCHANGED) from None
    return {"cambio": True, "copia": backup, "ruta": path}


def connect(data_folder, env=None, now=None, executable=None):
    """Agrega o actualiza solo nuestra entrada; las demás claves y conectores quedan igual."""
    entry = server_entry(data_folder, executable)

    def edit(servers):
        if servers.get(SERVER_KEY) == entry:
            return False
        servers[SERVER_KEY] = entry
        return True
    return _change(env, now, edit)


def disconnect(data_folder=None, env=None, now=None):
    """Quita solo nuestra entrada (data_folder no se usa; está por simetría con connect)."""
    def edit(servers):
        return servers.pop(SERVER_KEY, None) is not None
    return _change(env, now, edit)
