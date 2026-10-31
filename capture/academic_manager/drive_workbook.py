"""Drive adapter for the canonical Academic Manager workbook.

Workbook publication uses Google Application Default Credentials (ADC). The
runtime is expected to provide a service-account identity that only has the
Academic Manager folder shared as Editor. A runtime without that identity can
publish with the user's own write-scoped login from the setup wizard
(`user_service`), stored next to Capture's read-only token.
"""
import json
import os
from pathlib import Path
from uuid import uuid4

from ..models import CaptureError
from ..storage.metadata import atomic_json

SCOPES = ["https://www.googleapis.com/auth/drive"]


def credentials():
    import google.auth  # import diferido: solo quien publica en Drive necesita las librerías
    try:
        creds, _ = google.auth.default(scopes=SCOPES)
        return creds
    except Exception as exc:
        raise CaptureError(
            "No hay credenciales ADC para el Drive personal. "
            "Configure una Service Account con acceso Editor solo a Academic Manager."
        ) from exc


def service(runtime=None):
    from googleapiclient.discovery import build
    return build("drive", "v3", credentials=credentials(), cache_discovery=False)


def user_service(runtime):
    """Cliente Drive con el inicio de sesión propio (alcance drive); None si no existe."""
    from ..sources import auth
    creds = auth.load_write_credentials(runtime)
    if creds is None:
        return None
    from googleapiclient.discovery import build
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def metadata(api, file_id):
    try:
        return api.files().get(
            fileId=file_id, supportsAllDrives=True,
            fields="id,name,mimeType,size,modifiedTime,parents,capabilities(canEdit,canDownload)"
        ).execute()
    except Exception as exc:
        raise CaptureError("No se pudo leer metadata del archivo canónico en Drive.") from exc


def require_editable(api, file_id):
    meta = metadata(api, file_id)
    caps = meta.get("capabilities") or {}
    if not caps.get("canDownload") or not caps.get("canEdit"):
        raise CaptureError("La identidad de publicación no tiene lectura y edición sobre Academic Manager.")
    return meta


def download(api, file_id, target):
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_name(target.name + "." + uuid4().hex + ".part")
    try:
        with part.open("wb") as stream:
            request = api.files().get_media(fileId=file_id, supportsAllDrives=True)
            from googleapiclient.http import MediaIoBaseDownload
            downloader = MediaIoBaseDownload(stream, request, chunksize=8 * 1024 * 1024)
            done = False
            while not done:
                _, done = downloader.next_chunk(num_retries=3)
        os.replace(part, target)
        return metadata(api, file_id)
    except CaptureError:
        raise
    except Exception as exc:
        raise CaptureError("No se pudo descargar el archivo canónico desde Drive.") from exc
    finally:
        part.unlink(missing_ok=True)


def upload(api, file_id, source, mime_type):
    require_editable(api, file_id)
    try:
        from googleapiclient.http import MediaFileUpload
        media = MediaFileUpload(str(source), mimetype=mime_type, resumable=True)
        return api.files().update(
            fileId=file_id, media_body=media, supportsAllDrives=True,
            fields="id,name,mimeType,size,modifiedTime"
        ).execute()
    except Exception as exc:
        raise CaptureError("Falló la publicación del archivo canónico por Drive API.") from exc


def create(api, folder_id, name, source, mime_type):
    """Crea un archivo nuevo en la carpeta indicada; nunca reemplaza uno existente."""
    try:
        from googleapiclient.http import MediaFileUpload
        media = MediaFileUpload(str(source), mimetype=mime_type, resumable=True)
        return api.files().create(
            body={"name": name, "parents": [folder_id]}, media_body=media,
            supportsAllDrives=True, fields="id,name,mimeType,size,modifiedTime"
        ).execute()
    except Exception as exc:
        raise CaptureError("No se pudo crear el archivo en Drive.") from exc


def find_in_folder(api, folder_id, name, mime_type=None):
    """Archivos no eliminados con ese nombre exacto dentro de la carpeta (y ese tipo, si se indica)."""
    quoted = name.replace("\\", "\\\\").replace("'", "\\'")
    kind = f" and mimeType = '{mime_type}'" if mime_type else ""
    try:
        return api.files().list(
            q=f"'{folder_id}' in parents and name = '{quoted}' and trashed = false{kind}",
            fields="files(id,name)", supportsAllDrives=True, includeItemsFromAllDrives=True
        ).execute().get("files", [])
    except Exception as exc:
        raise CaptureError("No se pudo listar la carpeta canónica en Drive.") from exc


def read_json_file(api, file_id, target):
    download(api, file_id, target)
    try:
        return json.loads(Path(target).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise CaptureError("El estado compartido no contiene JSON válido.") from exc


def write_json_file(api, file_id, data, target):
    atomic_json(target, data)
    return upload(api, file_id, target, "application/json")
