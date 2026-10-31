"""Aviso de versión nueva y descarga verificada del instalador (solo biblioteca estándar).

- check(): pregunta a GitHub la última release del repositorio público. Ante cualquier problema
  (sin internet, tiempo agotado, límite de consultas, respuesta rara) devuelve None: no hay aviso
  y nada se bloquea.
- download(): baja el instalador solo por HTTPS desde GitHub, con tope de tamaño, y comprueba su
  SHA-256 contra el «digest» publicado en la release. Si algo falla borra el archivo y no se ejecuta.
- launch(): abre el instalador normal de Windows (la persona pulsa «Siguiente»).
"""
import hashlib
import json
import os
import re
import sys
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

REPO = "Ds0957/Academic-Manager"
LATEST_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
TIMEOUT = 5  # segundos; la consulta corre aparte y nunca retrasa la página
DOWNLOAD_TIMEOUT = 60
MAX_JSON = 1024 * 1024
MAX_BYTES = 300 * 1024 * 1024  # el instalador pesa bastante menos
CHUNK = 64 * 1024
ASSET_HOSTS = frozenset({"github.com"})
REDIRECT_HOSTS = frozenset({"github.com", "objects.githubusercontent.com",
                            "release-assets.githubusercontent.com"})
TAG = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
VERSION = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
DIGEST = re.compile(r"^sha256:([0-9a-f]{64})$")
FOLDER_NAME = "AcademicManager-actualizacion"

FAILED = ("No se pudo descargar la versión nueva. Revise su conexión a internet e inténtelo de nuevo más "
          "tarde. No se instaló nada.")
NOT_ORIGINAL = ("El archivo descargado no es idéntico al publicado, así que se borró y no se ejecutó. "
                "Inténtelo de nuevo más tarde.")
TOO_BIG = "La descarga es más grande de lo esperado, así que se canceló y no se instaló nada."
NOT_GITHUB = "La versión nueva no viene del sitio esperado, así que no se descargó ni se instaló nada."
ONLY_WINDOWS = "La actualización automática funciona solo en Windows."
NOT_OPENED = ("No se pudo abrir el instalador. Inténtelo de nuevo o instale la versión nueva a mano "
              "descargándola desde la página de Academic Manager.")


class UpdateError(Exception):
    """Problema al actualizar; el mensaje es para la persona (sin detalles técnicos)."""


@dataclass(frozen=True)
class Release:
    version: str
    url: str
    sha256: str
    size: int


def _numbers(text, pattern):
    match = pattern.match(text) if isinstance(text, str) else None
    return tuple(int(part) for part in match.groups()) if match else None


def _https_on(url, hosts):
    parts = urlsplit(url) if isinstance(url, str) else None
    return bool(parts and parts.scheme == "https" and parts.hostname in hosts and not parts.username
                and parts.port in (None, 443))


def _fetch(url, headers, timeout):
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read(MAX_JSON + 1)


def _log(line):
    try:
        sys.stderr.write(line + "\n")
    except (OSError, ValueError, AttributeError):
        pass


def _parse(body, current):
    data = json.loads(body)
    if not isinstance(data, dict):
        return None
    newest = _numbers(data.get("tag_name"), TAG)
    if newest is None or newest <= current:
        return None
    version = ".".join(map(str, newest))
    for asset in data.get("assets") or []:
        if not isinstance(asset, dict) or asset.get("name") != f"AcademicManager-Setup-{version}.exe":
            continue
        digest = asset.get("digest")
        match = DIGEST.match(digest) if isinstance(digest, str) else None
        url, size = asset.get("browser_download_url"), asset.get("size")
        if match and _https_on(url, ASSET_HOSTS) and isinstance(size, int) and not isinstance(size, bool) \
                and 0 < size:
            return Release(version, url, match.group(1), size)
    return None


def check(current, *, fetch=_fetch, log=_log):
    """Release más nueva que `current`, o None si está al día o no se pudo saber."""
    numbers = _numbers(current, VERSION)
    if numbers is None:
        return None
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
               "User-Agent": f"AcademicManager/{current}"}
    try:
        body = fetch(LATEST_URL, headers, TIMEOUT)
        if body is None or len(body) > MAX_JSON:
            return None
        return _parse(body, numbers)
    except Exception as exc:  # sin aviso; el registro solo nombra el tipo de error
        log(f"No se pudo comprobar si hay una versión nueva ({type(exc).__name__}).")
        return None


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    """Solo sigue redirecciones HTTPS a los servidores de descargas de GitHub."""
    max_redirections = 5

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _https_on(newurl, REDIRECT_HOSTS):
            raise UpdateError(NOT_GITHUB)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _opener():
    return urllib.request.build_opener(SafeRedirect())


def download_folder():
    """Carpeta temporal del usuario; nunca dentro de la carpeta del programa."""
    return Path(tempfile.gettempdir()) / FOLDER_NAME


def _receive(response, stream, size):
    length = response.headers.get("Content-Length")
    if length is not None and (not str(length).isdigit() or int(length) > size):
        raise UpdateError(TOO_BIG)
    digest, total = hashlib.sha256(), 0
    while True:
        chunk = response.read(CHUNK)
        if not chunk:
            return digest.hexdigest(), total
        total += len(chunk)
        if total > size:
            raise UpdateError(TOO_BIG)
        digest.update(chunk)
        stream.write(chunk)


def download(release, folder=None, *, opener=None):
    """Baja y verifica el instalador; devuelve la ruta del .exe o lanza UpdateError."""
    if not _https_on(release.url, ASSET_HOSTS) or _numbers(release.version, VERSION) is None:
        raise UpdateError(NOT_GITHUB)
    if not 0 < release.size <= MAX_BYTES:
        raise UpdateError(TOO_BIG)
    folder = Path(folder) if folder is not None else download_folder()
    final = folder / f"AcademicManager-Setup-{release.version}.exe"
    part = final.with_name(final.name + ".part")
    opener = opener or _opener()
    try:
        folder.mkdir(parents=True, exist_ok=True)
        request = urllib.request.Request(release.url, headers={"User-Agent": "AcademicManager",
                                                               "Accept": "application/octet-stream"})
        with opener.open(request, timeout=DOWNLOAD_TIMEOUT) as response:
            if not _https_on(response.geturl(), REDIRECT_HOSTS):
                raise UpdateError(NOT_GITHUB)
            with open(part, "wb") as stream:
                digest, total = _receive(response, stream, release.size)
        if total != release.size or digest != release.sha256:
            raise UpdateError(NOT_ORIGINAL)
        os.replace(part, final)
        return final
    except UpdateError:
        _remove(part)
        raise
    except Exception:  # red, disco o respuesta rara: el mensaje no lleva detalles técnicos
        _remove(part)
        raise UpdateError(FAILED) from None


def _remove(path):
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        _log(f"No se pudo borrar la descarga incompleta ({path.name}).")


def launch(path, *, start=None):
    """Abre el instalador con su ventana normal."""
    if start is None:
        if os.name != "nt":
            raise UpdateError(ONLY_WINDOWS)
        start = os.startfile  # noqa: S606 - el instalador verificado, con su interfaz normal
    try:
        start(str(path))
    except OSError:
        raise UpdateError(NOT_OPENED) from None
