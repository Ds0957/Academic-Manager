"""Resolución única de la ruta de datos (runtime) y de la raíz de Drive.

Precedencia: argumento explícito > variable de entorno que el módulo ya leía >
archivo de configuración del usuario > valor por defecto de siempre. Sin archivo
ni variable, cada comando resuelve exactamente la misma ruta que antes.
"""
import json
import os
from pathlib import Path
from uuid import uuid4

CONFIG_ENV = "ACADEMIC_MANAGER_CONFIG"  # Ruta alternativa del archivo (pruebas, instalador).
KEYS = ("runtime", "drive_root")
DEFAULT_RUNTIME = r"C:\AcademicManagerData"


# Ayuda común de las CLI que leen ACADEMIC_MANAGER_DATA sin opción --runtime.
DATA_HELP = ("Carpeta de datos: ACADEMIC_MANAGER_DATA; si no está, runtime del archivo de configuración "
             "del usuario (ACADEMIC_MANAGER_CONFIG indica otra ubicación); si no, C:\\AcademicManagerData.")


class ConfigError(ValueError):
    """Archivo de configuración presente pero inválido: el comando se detiene."""


def _is_windows():
    return os.name == "nt"


def windows_default():
    """El valor por defecto de los módulos que fuera de Windows no tenían ninguno."""
    return DEFAULT_RUNTIME if _is_windows() else None


def config_path():
    """%APPDATA%\\AcademicManager\\config.json en Windows; ~/.config/academic-manager/config.json fuera."""
    override = os.environ.get(CONFIG_ENV)
    if override:
        return Path(override)
    if _is_windows():
        base = os.environ.get("APPDATA")
        base = Path(base) if base else Path.home() / "AppData" / "Roaming"
        return base / "AcademicManager" / "config.json"
    return Path.home() / ".config" / "academic-manager" / "config.json"


def load_config():
    """Claves válidas del archivo; {} si no existe. Cualquier otro problema falla cerrado."""
    path = config_path()
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return {}
    except OSError:
        raise ConfigError(f"Configuración inválida en {path}: el archivo no puede leerse. "
                          "Corríjalo o bórrelo.") from None
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError):
        raise ConfigError(f"Configuración inválida en {path}: no es JSON válido. "
                          "Corríjalo o bórrelo.") from None
    if not isinstance(data, dict):
        raise ConfigError(f"Configuración inválida en {path}: debe ser un objeto JSON "
                          f"con {' y/o '.join(KEYS)}.")
    return _checked(path, data)


def _checked(path, data):
    unknown = sorted(set(data) - set(KEYS))
    if unknown:
        raise ConfigError(f"Configuración inválida en {path}: clave desconocida {', '.join(unknown)}; "
                          f"solo se admiten {' y '.join(KEYS)}.")
    for key, value in data.items():
        if not isinstance(value, str) or not value or not Path(value).is_absolute():
            raise ConfigError(f"Configuración inválida en {path}: {key} debe ser una ruta absoluta.")
    return data


def save_config(**values):
    """Añade o cambia claves del archivo del usuario (asistente de configuración).

    Misma validación que load_config; un archivo presente e inválido no se sobrescribe.
    Escritura atómica: temporal en la misma carpeta y reemplazo.
    """
    path = config_path()
    merged = _checked(path, {**load_config(), **values})
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(merged, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return merged


def runtime(explicit=None, *, env=(), default=None):
    """Ruta de datos como texto (o None cuando el módulo no tenía valor por defecto).

    El archivo se valida siempre que exista, aunque gane el argumento o la variable.
    Una variable presente pero vacía cuenta como indicada, igual que os.environ.get.
    """
    config = load_config()
    if explicit is not None:
        return explicit
    for name in env:
        if name in os.environ:
            return os.environ[name]
    return config.get("runtime", default)


def drive_root(default):
    """Raíz de Academic Manager en Drive: archivo del usuario o la ruta de siempre."""
    value = load_config().get("drive_root")
    return Path(value) if value is not None else Path(default)
