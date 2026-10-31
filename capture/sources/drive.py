import io
import re
from urllib.parse import urlparse, parse_qs

from ..models import CaptureError


def file_id(source):
    if re.fullmatch(r"[-\w]{10,}", source):
        return source
    url = urlparse(source)
    if url.scheme != "https" or url.hostname not in ("drive.google.com", "docs.google.com"):
        raise CaptureError("Use una URL HTTPS de Google Drive o un ID de archivo.")
    match = re.search(r"/(?:file/)?d/([-\w]+)", url.path)
    value = match.group(1) if match else parse_qs(url.query).get("id", [""])[0]
    if not re.fullmatch(r"[-\w]{10,}", value):
        raise CaptureError("No se pudo extraer el ID de Google Drive.")
    return value


def service(credentials=None, runtime=None):
    try:
        import google.auth
        from .auth import load_credentials, SCOPES
        from googleapiclient.discovery import build
        creds = load_credentials(runtime, credentials)
        if creds is None:
            creds = google.auth.default(scopes=SCOPES)[0]
        return build("drive", "v3", credentials=creds, cache_discovery=False)
    except CaptureError:
        raise
    except Exception as exc:
        raise CaptureError("Autorice su cuenta con --login-drive --oauth-client ARCHIVO_JSON. "
                           "También se admiten ADC o --drive-credentials. No se requiere contraseña en el chat.") from exc


def failure(exc):
    status = getattr(getattr(exc, "resp", None), "status", None)
    if status in (401, 403, 404):
        return CaptureError("Drive no permite acceder/descargar la grabación (HTTP "
                            f"{status}; puede incluir cannotDownloadFile). Use una identidad "
                            "universitaria autorizada, archivo local o --transcript. "
                            "Capture no evita las restricciones del propietario.")
    return CaptureError("Falló la operación Drive; revise conexión y permisos y reintente el job.")


def inspect(source, api):
    identity = file_id(source)
    try:
        data = api.files().get(fileId=identity, supportsAllDrives=True,
                              fields="id,name,mimeType,size,modifiedTime,webViewLink,capabilities/canDownload").execute()
    except Exception as exc:
        raise failure(exc) from exc
    if not data.get("capabilities", {}).get("canDownload", True):
        raise CaptureError("Drive indica canDownload=false. Use archivo local o --transcript.")
    if data["mimeType"].startswith("application/vnd.google-apps."):
        raise CaptureError("La fuente Drive debe ser audio/video, no un documento nativo.")
    return {"kind": "drive", "file_id": identity, "location": data.get("webViewLink", source),
            "original_name": data.get("name") or identity,
            "mime_type": data["mimeType"], "size_bytes": int(data["size"]) if "size" in data else None,
            "modified_time": data.get("modifiedTime")}


def fetch(metadata, target, api):
    from googleapiclient.http import MediaIoBaseDownload
    current = inspect(metadata["file_id"], api)
    if any(current.get(key) != metadata.get(key) for key in ("modified_time", "size_bytes", "original_name")):
        raise CaptureError("La fuente Drive cambió desde que se creó el job.")
    part = target.with_name(target.name + ".part")
    try:
        with part.open("wb") as stream:
            request = api.files().get_media(fileId=metadata["file_id"], supportsAllDrives=True)
            download = MediaIoBaseDownload(stream, request, chunksize=20 * 1024 * 1024)
            complete = False
            while not complete:
                progress, complete = download.next_chunk(num_retries=3)
                if progress:
                    print(f"Descarga: {progress.progress():.0%}", flush=True)
        if metadata["size_bytes"] is not None and part.stat().st_size != metadata["size_bytes"]:
            raise CaptureError("La descarga Drive tiene un tamaño inesperado.")
        # Check metadata again: never accept a source modified during the download.
        final = inspect(metadata["file_id"], api)
        if final.get("modified_time") != metadata.get("modified_time"):
            raise CaptureError("Drive cambió durante la descarga.")
        part.replace(target)
    except CaptureError:
        raise
    except Exception as exc:
        raise failure(exc) from exc