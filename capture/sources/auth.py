"""User-consented OAuth for the local CLI. Tokens never enter jobs or Git."""
import ctypes
import json
import os
from pathlib import Path
from uuid import uuid4

from ..models import CaptureError
from ..storage.job import job_lock
from ..storage.metadata import read_json

SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]
# Inicio de sesión del asistente: además de leer (Capture), permite publicar la base en su Drive.
# Se piden los dos alcances para que el mismo token siga sirviendo a Capture con SCOPES.
WRITE_SCOPE = "https://www.googleapis.com/auth/drive"
WRITE_SCOPES = [*SCOPES, WRITE_SCOPE]


def libraries():
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from google_auth_oauthlib.flow import InstalledAppFlow
        return Credentials, Request, InstalledAppFlow
    except ImportError as exc:
        raise CaptureError("Instale requirements-drive.txt en el Python que ejecuta Capture.") from exc


def token_path(runtime):
    directory = Path(runtime).resolve() / "control" / "drive-oauth"
    if directory.resolve() != directory:
        raise CaptureError("La carpeta OAuth no puede estar redirigida.")
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    return directory / ("drive-token.dpapi" if os.name == "nt" else "drive-token.json")


def write_token_path(runtime):
    """Autorización con permiso de escritura, junto a la de Capture y con la misma protección."""
    path = token_path(runtime)
    return path.with_name("drive-write-token" + path.suffix)


def dpapi(data, decrypt=False):
    """Windows user-scoped DPAPI; works only as the Windows account that authorized."""
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]

    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
    output = Blob()
    crypt = ctypes.WinDLL("Crypt32.dll", use_last_error=True)
    kernel = ctypes.WinDLL("Kernel32.dll", use_last_error=True)
    operation = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    operation.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                          ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    operation.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    # UI forbidden; no CRYPTPROTECT_LOCAL_MACHINE flag: scope is the current user.
    if not operation(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output)):
        raise CaptureError("Windows no pudo proteger/leer la autorización OAuth de este usuario.")
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        kernel.LocalFree(ctypes.cast(output.pbData, ctypes.c_void_p))


def store_token(path, credentials):
    path = Path(path)
    if path.is_symlink() or path.resolve().parent != path.parent.resolve():
        raise CaptureError("No se guardan tokens en una ruta redirigida.")
    data = credentials.to_json().encode("utf-8")
    if os.name == "nt":
        data = dpapi(data)
    temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def check_scopes(credentials):
    granted = credentials.granted_scopes
    available = granted if granted is not None else credentials.scopes
    if not set(SCOPES).issubset(set(available or [])):
        raise CaptureError("No se concedió lectura de Drive. Vuelva a autorizar y acepte ese permiso.")


def check_write_scopes(credentials):
    granted = credentials.granted_scopes
    available = granted if granted is not None else credentials.scopes
    if not set(WRITE_SCOPES).issubset(set(available or [])):
        raise CaptureError("No se concedió permiso para ver y guardar archivos de Drive. Vuelva a iniciar "
                           "sesión y marque todos los permisos que pide Google.")


def check_client_config(config):
    """Solo clientes OAuth de tipo Desktop con los endpoints oficiales de Google."""
    installed = config.get("installed")
    if not isinstance(installed, dict) or not installed.get("client_id", "").endswith(".apps.googleusercontent.com"):
        raise CaptureError("El JSON debe corresponder a un cliente OAuth de tipo Desktop (installed).")
    if (installed.get("auth_uri") != "https://accounts.google.com/o/oauth2/auth" or
            installed.get("token_uri") != "https://oauth2.googleapis.com/token"):
        raise CaptureError("El cliente debe usar los endpoints oficiales de Google.")
    return config


def client_path(runtime):
    """Cliente OAuth propio guardado por el asistente, junto a la autorización de Drive."""
    return token_path(runtime).parent / "oauth-client.json"


def save_client(runtime, raw):
    """Valida el JSON descargado de Google Cloud como login y lo guarda solo en client_path."""
    try:
        config = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise CaptureError("No se pudo leer el JSON del cliente OAuth; no pegue su contenido en el chat.") from exc
    installed = config.get("installed") if isinstance(config, dict) else None
    if not isinstance(installed, dict) or not isinstance(installed.get("client_id", ""), str):
        raise CaptureError("El JSON debe corresponder a un cliente OAuth de tipo Desktop (installed).")
    check_client_config(config)
    path = client_path(runtime)
    if path.is_symlink():
        raise CaptureError("No se guarda el cliente OAuth en una ruta redirigida.")
    temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(json.dumps(config, indent=2).encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return path


def login(runtime, client_file, *, write=False):
    """Inicio de sesión en el navegador. `write=True` (asistente) también autoriza publicar la base."""
    if not client_file:
        raise CaptureError("Indique --oauth-client con el JSON de un cliente Google de tipo Desktop.")
    try:
        config = read_json(client_file)
    except (OSError, ValueError) as exc:
        raise CaptureError("No se pudo leer el JSON del cliente OAuth; no pegue su contenido en el chat.") from exc
    check_client_config(config)
    _, _, Flow = libraries()
    path = token_path(runtime)
    with job_lock(path.parent):
        try:
            flow = Flow.from_client_config(config, scopes=WRITE_SCOPES if write else SCOPES,
                                           autogenerate_code_verifier=True)
            if write:
                print("Se abrirá Google en el navegador de este PC. Elija su cuenta y autorice ver, "
                      "descargar y guardar archivos de Drive: Capture lee sus grabaciones y Academic "
                      "Manager publica su base de datos en su carpeta.", flush=True)
            else:
                print("Se abrirá Google en el navegador de este PC. Elija la cuenta universitaria "
                      "y autorice lectura/descarga de Drive. El permiso abarca los archivos accesibles "
                      "por esa cuenta; Capture procesará solo los enlaces que indique.", flush=True)
            credentials = flow.run_local_server(
                host="127.0.0.1", port=0, open_browser=True, timeout_seconds=180,
                authorization_prompt_message="",
                success_message="Academic Manager Capture autorizado. Puede cerrar esta pestaña.",
                access_type="offline", prompt="consent")
            check_scopes(credentials)
            if write:
                check_write_scopes(credentials)
            if not credentials.refresh_token:
                raise CaptureError("Google no entregó autorización renovable. Vuelva a conceder acceso.")
            if write:
                store_token(write_token_path(runtime), credentials)
            store_token(path, credentials)
        except CaptureError:
            raise
        except Exception as exc:
            raise CaptureError("La autorización no se completó. Revise el consentimiento, usuario "
                               "de prueba y políticas universitarias, y vuelva a intentarlo.") from exc
    print("Cuenta autorizada localmente. Ya puede pasar una URL Drive a Capture.", flush=True)


def load_credentials(runtime=None, explicit=None):
    """Return None only when no local authorization exists (ADC compatibility)."""
    path = Path(explicit) if explicit else token_path(runtime) if runtime else None
    if path is None or (not explicit and not path.exists()):
        return None
    Credentials, Request, _ = libraries()
    # Explicit credentials keep their original, externally managed lifecycle.
    if explicit:
        try:
            credentials = Credentials.from_authorized_user_file(str(path), scopes=SCOPES)
            check_scopes(credentials)
            if not credentials.valid:
                credentials.refresh(Request())
            return credentials
        except Exception as exc:
            raise CaptureError("Las credenciales externas no son válidas. Renueve la autorización Drive.") from exc
    return _load_stored(path, SCOPES, check_scopes,
                        "La autorización necesita renovarse con --login-drive.",
                        "La autorización local caducó, fue revocada o no se pudo leer. "
                        "Repita --login-drive con su cliente OAuth.")


def load_write_credentials(runtime):
    """Autorización de escritura del asistente; None si este equipo no la tiene (sin crear carpetas)."""
    if not (Path(runtime).resolve() / "control" / "drive-oauth").is_dir():
        return None
    path = write_token_path(runtime)
    if not path.exists() and not path.is_symlink():
        return None
    relogin = ("La sesión de Google para publicar caducó o fue revocada. Abra el asistente y pulse "
               "«Volver a iniciar sesión» en el paso de Google.")
    return _load_stored(path, WRITE_SCOPES, check_write_scopes, relogin, relogin, require=WRITE_SCOPE,
                        missing=MISSING_WRITE)


MISSING_WRITE = ("Su sesión de Google no tiene permiso para guardar en Drive. Abra el asistente y pulse "
                 "«Volver a iniciar sesión» en el paso de Google; marque todos los permisos que pide Google.")


def _load_stored(path, scopes, check, renew, failure, require=None, missing=None):
    """Lee, descifra, comprueba y renueva un token guardado con store_token."""
    Credentials, Request, _ = libraries()
    directory = path.parent
    with job_lock(directory):
        try:
            if path.is_symlink() or path.resolve().parent != directory.resolve():
                raise CaptureError("La autorización local está redirigida.")
            data = path.read_bytes()
            if os.name == "nt":
                data = dpapi(data, decrypt=True)
            info = json.loads(data)
            if require is not None and require not in (info.get("scopes") or []):
                raise CaptureError(missing or renew)
            credentials = Credentials.from_authorized_user_info(info, scopes=scopes)
            check(credentials)
            if not credentials.valid:
                if not credentials.refresh_token:
                    raise CaptureError(renew)
                credentials.refresh(Request())
                store_token(path, credentials)
            return credentials
        except CaptureError:
            raise
        except Exception as exc:
            raise CaptureError(failure) from exc
