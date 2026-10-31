"""Selective, read-only Canvas PDF retrieval.

Authenticated Canvas GET is used only for file metadata. Download URLs are
short-lived and fetched WITHOUT Authorization headers; they are never saved.
No PDF macros, forms, OCR, embedded scripts, remote content or third-party
URLs are executed. No workbook/Drive writes.
"""
import hashlib
import io
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

from .api import Client, ORIGIN
from .auth import valid
from .common import SyncError, atomic_bytes, atomic_json, safe_dir
from .sync import sha

MAX_FILE_BYTES = 5_000_000
MAX_PAGES = 35
MAX_TEXT_CHARS = 100_000
MAX_REDIRECTS = 4


def url_host_valid(url, *, initial=False):
    try:
        p = urlsplit(url)
        host = (p.hostname or "").lower()
        good = (p.scheme == "https" and p.port in (None, 443)
                and not p.username and not p.password and not p.fragment)
    except ValueError:
        return False
    if not good:
        return False
    if initial:
        return host == "areandina.instructure.com" and p.path.startswith("/files/")
    canvas_storage = {
        "instructure-uploads.s3.amazonaws.com",
        "instructure-uploads-2.s3.amazonaws.com",
        "instructure-uploads-eu.s3.amazonaws.com",
        "instructure-uploads-apse1.s3.amazonaws.com",
        "instructure-uploads-apse2.s3.amazonaws.com",
        "instructure-uploads-fra.s3.amazonaws.com",
        "instructure-uploads-pdx.s3.amazonaws.com",
        "instructure-uploads-yul.s3.amazonaws.com",
    }
    return (host == "areandina.instructure.com"
            or host.endswith(".instructure.com")
            or host.endswith(".canvas-user-content.com")
            or host.endswith(".inscloudgate.net")
            or host in canvas_storage)


class LimitedRedirects(HTTPRedirectHandler):
    """Follow only known Instructure/storage HTTPS hosts, without credentials."""

    def __init__(self):
        super().__init__()
        self.redirects = 0

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self.redirects += 1
        if self.redirects > MAX_REDIRECTS or not url_host_valid(newurl):
            raise SyncError("Descarga denegada: redirección fuera de servidores permitidos.")
        result = super().redirect_request(req, fp, code, msg, headers, newurl)
        if result is not None and result.get_header("Authorization"):
            raise SyncError("Descarga denegada: cabecera de autorización inesperada.")
        return result


def fetch_pdf(download_url, *, max_bytes=MAX_FILE_BYTES, opener=None):
    if not url_host_valid(download_url, initial=True):
        raise SyncError("Canvas entregó un enlace de descarga inválido.")
    redirector = LimitedRedirects()
    transport = opener or build_opener(redirector, HTTPSHandler())
    request = Request(download_url, method="GET", headers={
        "Accept": "application/pdf,application/octet-stream",
        "User-Agent": "AcademicManager-CanvasPDF/0.1",
    })
    if request.get_header("Authorization"):
        raise SyncError("No se permite autenticación en la descarga binaria.")
    try:
        with transport.open(request, timeout=35) as response:
            final_url = response.geturl()
            if not url_host_valid(final_url):
                raise SyncError("Descarga denegada: servidor final no autorizado.")
            result = response.read(max_bytes + 1)
            if len(result) > max_bytes:
                raise SyncError("El PDF supera el tamaño máximo permitido.")
    except SyncError:
        raise
    except HTTPError as exc:
        raise SyncError(f"El servidor de archivos rechazó la lectura (HTTP {exc.code}).") from None
    except (OSError, URLError, TimeoutError, ValueError):
        raise SyncError("No fue posible descargar el PDF mediante un enlace HTTPS permitido.") from None
    if not result.startswith(b"%PDF-"):
        raise SyncError("El archivo recibido no es un PDF válido (cabecera).")
    return result


def extract_pdf_text(content):
    try:
        from pypdf import PdfReader
    except ImportError:
        return {"status": "parser_unavailable", "pages": None, "pages_read": 0,
                "text": None, "truncated": False}
    try:
        reader = PdfReader(io.BytesIO(content), strict=True)
        if reader.is_encrypted:
            return {"status": "encrypted", "pages": None, "pages_read": 0,
                    "text": None, "truncated": False}
        num_pages = len(reader.pages)
        if num_pages > MAX_PAGES:
            return {"status": "page_limit_exceeded", "pages": num_pages, "pages_read": 0,
                    "text": None, "truncated": False}
        parts = []
        for page in reader.pages:
            parts.append((page.extract_text() or "").strip())
        text = "\n\n".join(x for x in parts if x)
        return {"status": "text" if text else "no_text_layer",
                "pages": num_pages, "pages_read": num_pages,
                "text": text[:MAX_TEXT_CHARS],
                "truncated": len(text) > MAX_TEXT_CHARS}
    except Exception:
        return {"status": "parse_error", "pages": None, "pages_read": 0,
                "text": None, "truncated": False}


def source_catalog(root, course_id, file_id):
    """Resolve resource only from a verified, complete local V0.1 course snapshot."""
    if type(course_id) is not int or course_id <= 0 or type(file_id) is not int or file_id <= 0:
        raise SyncError("Se necesitan IDs válidos del curso y archivo.")
    path = Path(root) / "procesamiento" / "canvas-sync" / "v01" / str(course_id) / "latest.json"
    if not path.is_file() or path.is_symlink():
        raise SyncError("Primero sincronice la materia con sync-rich.")
    try:
        previous = json.loads(path.read_text(encoding="utf-8"))
        if previous["sha256"] != sha(previous["data"]):
            raise ValueError("Hash mismatch")
        data = previous["data"]
        if data["course"]["id"] != course_id:
            raise ValueError("Course mismatch")
        catalog = data["sections"]["files"]
        if catalog["status"] != "ok":
            raise SyncError("Canvas no permite listar recursos en esta materia.")
        rows = [x for x in catalog["data"] if x.get("id") == file_id]
        if len(rows) != 1:
            raise SyncError("El recurso no está en el catálogo accesible de esa materia.")
        item = rows[0]
        if item.get("content-type") != "application/pdf":
            raise SyncError("Este flujo solo acepta archivos PDF.")
        name = item.get("display_name") or ""
        if not name.casefold().endswith(".pdf"):
            raise SyncError("Se requiere una extensión .pdf en el catálogo.")
        size = item.get("size")
        if type(size) is not int or not 0 < size <= MAX_FILE_BYTES:
            raise SyncError("El tamaño reportado por Canvas no es admisible.")
        return item
    except SyncError:
        raise
    except (OSError, ValueError, TypeError, KeyError, IndexError):
        raise SyncError("Catálogo Canvas local inválido; repita sync-rich.") from None


def save_pdf(root, course_id, file_id, content, item, parsed):
    base = safe_dir(Path(root), "procesamiento", "canvas-sync", "pdfs",
                    str(course_id), str(file_id))
    digest = hashlib.sha256(content).hexdigest()
    document = base / (digest + ".pdf")
    if document.is_symlink():
        raise SyncError("Ruta de documento redirigida.")
    if not document.exists():
        atomic_bytes(document, content)
    meta = {
        "schema_version": 1, "source": "canvas_api_file_metadata_and_signed_read",
        "course_id": course_id, "file_id": file_id,
        "display_name": item.get("display_name"), "content_type": "application/pdf",
        "source_size": item.get("size"), "download_bytes": len(content),
        "sha256": digest, "last_checked_utc": datetime.now(timezone.utc).isoformat(),
        "extraction_status": parsed["status"], "pages": parsed["pages"],
        "pages_read": parsed["pages_read"], "text_truncated": parsed["truncated"],
        "text_chars": len(parsed["text"] or ""),
        "relative_document": document.name,
    }
    if parsed["text"] is not None:
        text_path = base / (digest + ".txt")
        if not text_path.exists():
            atomic_bytes(text_path, (parsed["text"] + "\n").encode("utf-8"))
        meta["relative_text"] = text_path.name
    atomic_json(base / "latest.json", meta)
    return meta


def retrieve_pdf(runtime, course_id, file_id, client_factory=Client, downloader=fetch_pdf,
                 extractor=extract_pdf_text):
    item = source_catalog(runtime, course_id, file_id)
    record, _ = valid(runtime)
    client = client_factory(record["token"])
    if client.profile() != record["account_id"]:
        raise SyncError("La autorización Canvas no coincide con la cuenta local.")
    metadata, _ = client.get(ORIGIN + f"/api/v1/files/{file_id}")
    if not isinstance(metadata, dict) or metadata.get("id") != file_id:
        raise SyncError("Canvas devolvió metadatos de un archivo diferente.")
    if metadata.get("content-type") != "application/pdf" or metadata.get("locked"):
        raise SyncError("El PDF no es accesible con el permiso actual.")
    size = metadata.get("size")
    if type(size) is not int or not 0 < size <= MAX_FILE_BYTES:
        raise SyncError("El tamaño real del PDF no cumple las restricciones.")
    # The signed URL stays in process memory only. It is never logged or saved.
    data = downloader(metadata.get("url"))
    if not 0 < len(data) <= MAX_FILE_BYTES:
        raise SyncError("Tamaño de descarga inválido.")
    if len(data) != size:
        raise SyncError("Los bytes descargados difieren del tamaño declarado.")
    parsed = extractor(data)
    if not isinstance(parsed, dict) or parsed.get("status") is None:
        raise SyncError("Error de validación del PDF extraído.")
    saved = save_pdf(runtime, course_id, file_id, data, item, parsed)
    print(f"PDF {file_id} del curso {course_id}: {saved['download_bytes']} bytes, "
          f"SHA-256 {saved['sha256'][:12]}, "
          f"extracción={saved['extraction_status']}, "
          f"páginas={saved['pages']}; guardado SOLO localmente.")
    return saved
