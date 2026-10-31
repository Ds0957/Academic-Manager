"""Canvas study synchronizer: institutional references & reading PDFs, GET-only.

Canvas bearer is sent ONLY to its /api/v1 endpoint. Downloads use a separate,
credential-free HTTPS client. Never fetch external libraries, iframe, media or
activity URLs. Never modify Canvas, Drive or the workbook.
"""
from datetime import datetime, timezone, timedelta
import hashlib
from html.parser import HTMLParser
import io
import json
import logging
from pathlib import Path
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

from capture.storage.job import job_lock
from .api import ORIGIN
from .auth import valid
from .common import SyncError, atomic_bytes, atomic_json, safe_dir
from .enriched import BudgetClient, strict_ids
from .resources import LimitedRedirects, url_host_valid
from .sync import sha
from .semester import load_registry

INSTITUTION = "contenidos.areandina.edu.co"
HTML_LIMIT = 150_000
PDF_LIMIT = 35_000_000
PAGE_LIMIT = 220
TEXT_LIMIT = 700_000
MAX_EJES = 8
MAX_READING_SLOTS = 12
MAX_HTML_TAGS = 100
MAX_COURSES = 12
FULL_REFRESH_DAYS = 7


def safe_reference(value):
    """Public link for cataloguing, never retain URL parameters or credentials."""
    if not isinstance(value, str):
        return None
    try:
        parsed = urlsplit(value.strip())
        if (parsed.scheme != "https" or not parsed.hostname
                or parsed.username or parsed.password or parsed.port not in (None, 443)
                or len(value) > 2048):
            return None
        return urlunsplit(("https", parsed.hostname.lower(), parsed.path, "", ""))
    except ValueError:
        return None


def official_pdf(value):
    link = safe_reference(value)
    if link is None or value != link:
        return False
    try:
        parsed = urlsplit(link)
    except ValueError:
        return False
    return (parsed.hostname == INSTITUTION and parsed.path.startswith("/repo/")
            and parsed.path.lower().endswith(".pdf"))


def get_canvas_html(url, *, downloader=None):
    if not url_host_valid(url, initial=True):
        raise SyncError("Enlace temporal HTML de Canvas inválido.")
    if downloader:
        data = downloader(url)
    else:
        request = Request(url, method="GET",
                          headers={"Accept": "text/html",
                                   "User-Agent": "AcademicManager-StudySync/1.0"})
        try:
            with build_opener(LimitedRedirects(), HTTPSHandler()).open(request, timeout=35) as response:
                if not url_host_valid(response.geturl()):
                    raise SyncError("El servidor final HTML no está permitido.")
                data = response.read(HTML_LIMIT + 1)
        except SyncError:
            raise
        except HTTPError as exc:
            raise SyncError(f"Canvas HTML rechazó descarga (HTTP {exc.code}).") from None
        except (OSError, URLError, TimeoutError, ValueError):
            raise SyncError("No fue posible leer el HTML de Canvas.") from None
    if not isinstance(data, bytes) or not 0 < len(data) <= HTML_LIMIT:
        raise SyncError("HTML Canvas vacío o demasiado grande.")
    return data


class SameHostRedirect(HTTPRedirectHandler):
    def __init__(self):
        super().__init__()
        self.count = 0

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self.count += 1
        u = urlsplit(newurl)
        if (self.count > 3 or u.scheme != "https" or u.hostname != INSTITUTION
                or u.username or u.password or u.port not in (None, 443)):
            raise SyncError("Redirección PDF fuera del dominio institucional permitido.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def get_institutional_pdf(url, *, downloader=None):
    if not official_pdf(url):
        raise SyncError("Solo se admiten PDF públicos referenciados del servidor institucional.")
    if downloader:
        data = downloader(url)
    else:
        req = Request(url, method="GET",
                      headers={"Accept": "application/pdf",
                               "User-Agent": "AcademicManager-StudySync/1.0"})
        try:
            with build_opener(SameHostRedirect(), HTTPSHandler()).open(req, timeout=55) as response:
                if urlsplit(response.geturl()).hostname != INSTITUTION:
                    raise SyncError("PDF devuelto desde otro dominio.")
                data = response.read(PDF_LIMIT + 1)
        except SyncError:
            raise
        except HTTPError as exc:
            raise SyncError(f"Servidor institucional respondió HTTP {exc.code}.") from None
        except (OSError, URLError, TimeoutError, ValueError):
            raise SyncError("Descarga PDF institucional no disponible.") from None
    if not isinstance(data, bytes) or not 0 < len(data) <= PDF_LIMIT or not data.startswith(b"%PDF-"):
        raise SyncError("El documento institucional no es un PDF permitido.")
    return data


class EjeMarkup(HTMLParser):
    """Read custom attribute-based HTML as data, without executing it."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.references = []
        self.readings = []
        self.media = []
        self.title = None
        self.author = None
        self.tag_count = 0

    def handle_starttag(self, tag, attrs):
        self.tag_count += 1
        if self.tag_count > MAX_HTML_TAGS:
            raise SyncError("Demasiados elementos HTML en una página de eje.")
        a = dict(attrs)
        if tag == "contenido":
            self.title = (a.get("titulo-modulo") or "")[:200] or None
            self.author = (a.get("nombre-autor") or "")[:200] or None
            if a.get("referente-completo"):
                self.references.append(a["referente-completo"])
        elif tag == "acordeon" and a.get("url-referente"):
            # Often duplicates the full reference from <contenido>.
            self.references.append(a["url-referente"])
        elif tag == "lectura-recomendada":
            if len(self.readings) >= MAX_READING_SLOTS:
                raise SyncError("Demasiadas lecturas para un eje.")
            link = safe_reference(a.get("url"))
            self.readings.append({
                "title": (a.get("titulo") or "")[:300],
                "author_or_note": (a.get("texto") or "")[:500],
                "type": a.get("tipo-archivo"),
                "url": link,
                "kind": "institutional_pdf" if official_pdf(link) else
                        "external_link" if link else "unresolved",
            })
        elif tag in ("modal", "video-capsula"):
            self.media.append({
                "type": tag,
                "title": (a.get("titulo") or a.get("texto") or "")[:200],
                "url": safe_reference(a.get("url") or a.get("video")),
                "status": "catalogued_only",
            })

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)


def parse_eje(html):
    parser = EjeMarkup()
    try:
        parser.feed(html.decode("utf-8", "replace"))
    except (UnicodeError, ValueError) as exc:
        raise SyncError("HTML del eje no procesable.") from exc
    unique = list(dict.fromkeys(parser.references))
    if len(unique) != 1 or not official_pdf(unique[0]):
        raise SyncError("El eje no contiene un referente institucional único verificable.")
    return {"title": parser.title, "author": parser.author, "referente_url": unique[0],
            "readings": parser.readings, "media": parser.media}


def extract_document(data, *, parser_factory=None):
    if parser_factory is None:
        try:
            from pypdf import PdfReader
        except ImportError:
            raise SyncError("Instale requirements-canvas.txt para leer PDF.") from None
        parser_factory = PdfReader
    try:
        reader = parser_factory(io.BytesIO(data), strict=False)
        pages = len(reader.pages)
        if pages < 1:
            raise SyncError("PDF sin páginas.")
        if pages > PAGE_LIMIT:
            return {"status": "page_limit", "pages": pages, "text": None, "truncated": False}
        text = "\n\n".join((page.extract_text() or "").strip() for page in reader.pages)
        return {"status": "text" if text.strip() else "no_text_layer",
                "pages": pages, "text": text[:TEXT_LIMIT],
                "truncated": len(text) > TEXT_LIMIT}
    except SyncError:
        raise
    except Exception:
        return {"status": "parse_error", "pages": None, "text": None, "truncated": False}


def save_document(base, role, content, extracted, *, url, origin_file):
    digest = hashlib.sha256(content).hexdigest()
    path = base / (role + "-" + digest + ".pdf")
    if path.is_symlink() or (base / "latest.json").is_symlink():
        raise SyncError("Ruta redirigida no permitida.")
    # Existing content-addressed filenames can still be damaged by local
    # corruption. Repair atomically from freshly downloaded, verified bytes.
    if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
        atomic_bytes(path, content)
    textpath = base / (role + "-" + digest + ".txt")
    if textpath.is_symlink():
        raise SyncError("Texto de estudio redirigido.")
    if extracted["text"] is not None and extracted["text"].strip():
        expected_text = extracted["text"].encode("utf-8")
        text_sha = hashlib.sha256(expected_text).hexdigest()
        if not textpath.exists() or hashlib.sha256(textpath.read_bytes()).hexdigest() != text_sha:
            atomic_bytes(textpath, expected_text)
    stable = {"role": role, "source_url": url, "canvas_html_file_id": origin_file,
              "sha256": digest, "bytes": len(content), "pages": extracted["pages"],
              "text_chars": len(extracted["text"] or ""), "extraction_status": extracted["status"],
              "text_sha256": hashlib.sha256(extracted["text"].encode("utf-8")).hexdigest()
                            if extracted["text"] and extracted["text"].strip() else None,
              "truncated": extracted["truncated"],
              "pdf_file": path.name,
              "text_file": textpath.name if extracted["text"] and extracted["text"].strip() else None}
    previous = base / "latest.json"
    same = False
    if previous.exists():
        try:
            old = json.loads(previous.read_text(encoding="utf-8"))
            same = old == stable
        except (ValueError, OSError):
            pass
    if not same:
        atomic_json(previous, stable)
    return stable, not same


def file_html_metadata(client, file_id, expected_size):
    data, _ = client.get(ORIGIN + f"/api/v1/files/{file_id}")
    if (not isinstance(data, dict) or data.get("id") != file_id
            or data.get("content-type") != "text/html"
            or data.get("locked") or data.get("hidden")
            or type(data.get("size")) is not int or data["size"] != expected_size
            or data["size"] <= 0 or data["size"] > HTML_LIMIT):
        raise SyncError("El recurso HTML no coincide o no está accesible.")
    return data


def identify_ejes(files):
    strict_ids(files, "ficheros")
    selected = {}
    for f in files:
        match = re.fullmatch(r"eje([1-4])\.html", (f.get("display_name") or "").casefold())
        if not match:
            continue
        eje = int(match.group(1))
        if eje in selected:
            raise SyncError(f"Existen múltiples HTML para el eje {eje}; se requiere revisión.")
        if (f.get("content-type") != "text/html" or f.get("locked") or f.get("hidden")
                or type(f.get("size")) is not int or not 0 < f["size"] <= HTML_LIMIT):
            raise SyncError(f"El HTML del eje {eje} no es accesible.")
        selected[eje] = f
    return selected


def course_files(client, course_id):
    try:
        raw = client.pages(f"/api/v1/courses/{course_id}/files", {"per_page": 100})
    except SyncError as exc:
        if "HTTP 403" in str(exc):
            return {"status": "forbidden", "data": None}
        if "HTTP 404" in str(exc):
            return {"status": "not_found", "data": None}
        raise
    return {"status": "ok", "data": raw}


def _read_previous_index(directory, course_id):
    path = directory / "index.json"
    if path.is_symlink():
        raise SyncError("Índice de estudio redirigido.")
    if not path.exists():
        return None
    try:
        old = json.loads(path.read_text(encoding="utf-8"))
        data = old["data"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise SyncError("Índice previo de estudio no legible.") from exc
    if (old.get("sha256") != sha(data) or data.get("schema_version") != 1
            or data.get("course_id") != course_id
            or data.get("source") != "canvas_html_and_institutional_get_only"):
        raise SyncError("Índice previo de estudio no supera verificación de integridad.")
    return old


def _read_refresh_date(directory, prior, course_id):
    path = directory / "refresh-state.json"
    if path.is_symlink():
        raise SyncError("Estado de revalidación redirigido.")
    if path.exists():
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
            if state.get("schema_version") != 1 or state.get("course_id") != course_id:
                raise ValueError("Estado de revalidación inconsistente.")
            raw = state["last_full_pdf_refresh_utc"]
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise SyncError("Estado de revalidación incompleto o corrupto.") from exc
    else:
        raw = prior.get("captured_at_utc") if prior else None
    if not raw:
        return None
    try:
        moment = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if moment.tzinfo is None:
            raise ValueError("Zona horaria ausente")
        return moment.astimezone(timezone.utc)
    except (ValueError, TypeError, AttributeError) as exc:
        raise SyncError("Fecha de revalidación inválida.") from exc


def _verified_cached_document(base, previous, *, role, url, origin_file,
                              parser_factory=None):
    """Reuse by content hash; legacy TXT is re-checked against the local PDF."""
    if not isinstance(previous, dict):
        return None
    digest = previous.get("sha256")
    if (not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)
            or previous.get("role") != role
            or previous.get("source_url") != url
            or previous.get("canvas_html_file_id") != origin_file):
        return None
    pdfname = role + "-" + digest + ".pdf"
    if previous.get("pdf_file") != pdfname:
        return None
    pdf = base / pdfname
    if (pdf.is_symlink() or not pdf.is_file()
            or not 0 < pdf.stat().st_size <= PDF_LIMIT):
        return None
    raw = pdf.read_bytes()
    if (len(raw) != previous.get("bytes") or not raw.startswith(b"%PDF-")
            or hashlib.sha256(raw).hexdigest() != digest):
        return None
    metadata = dict(previous)
    textfile = previous.get("text_file")
    if textfile is not None:
        if textfile != role + "-" + digest + ".txt":
            return None
        txt = base / textfile
        if txt.is_symlink() or not txt.is_file() or txt.stat().st_size > TEXT_LIMIT * 4:
            return None
        content = txt.read_bytes()
        try:
            content.decode("utf-8")
        except UnicodeError:
            return None
        checksum = hashlib.sha256(content).hexdigest()
        if previous.get("text_sha256"):
            if previous["text_sha256"] != checksum:
                return None
        else:
            # Existing V1 indices predate TXT checksums. Establish trust from
            # a deterministic extraction of the validated local PDF, no GET.
            extracted = extract_document(raw, parser_factory=parser_factory)
            if (not extracted.get("text") or
                    extracted["text"].encode("utf-8") != content or
                    extracted["status"] != previous.get("extraction_status")):
                return None
            metadata["text_sha256"] = checksum
    elif previous.get("text_sha256") is not None:
        return None
    return metadata


def _save_refresh_date(directory, course_id, timestamp):
    path = directory / "refresh-state.json"
    if path.is_symlink():
        raise SyncError("Estado de revalidación redirigido.")
    atomic_json(path, {"schema_version": 1, "course_id": course_id,
                       "last_full_pdf_refresh_utc": timestamp.astimezone(timezone.utc).isoformat()})


def sync_study(runtime, course_id=82862, all_active=False, *, client_factory=BudgetClient,
               html_downloader=None, pdf_downloader=None, pdf_parser=None,
               full_refresh=False, approved_only=False, now_utc=None):
    """Refresh Canvas HTML metadata; reuse verified local PDFs within TTL.

    A full PDF validation is mandatory at least every FULL_REFRESH_DAYS days,
    or immediately with --full-refresh. Catalog is replaced only on success.
    No automatic Excel import or access to non-institutional links.
    """
    credentials, _ = valid(runtime)
    client = client_factory(credentials["token"])
    if client.profile() != credentials["account_id"]:
        raise SyncError("La cuenta de Canvas cambió; no se procesa material.")
    del credentials
    now = now_utc or datetime.now(timezone.utc)
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise SyncError("Momento de revalidación sin zona horaria.")
    now = now.astimezone(timezone.utc)
    if approved_only and not all_active:
        raise SyncError("--approved-only requiere --all-active.")
    if all_active:
        courses = client.courses()
        strict_ids(courses, "cursos activos")
        ids = sorted(x["id"] for x in courses)
        if approved_only:
            confirmed = {r["course_id"] for r in load_registry(runtime)["courses"]}
            ids = [cid for cid in ids if cid in confirmed]
        if not ids or len(ids) > MAX_COURSES:
            raise SyncError("Cantidad de cursos fuera de límites; indique --course-id.")
    else:
        if type(course_id) is not int or course_id <= 0:
            raise SyncError("Debe indicar ID de curso válido.")
        ids = [course_id]

    results = []
    for cid in ids:
        directory = safe_dir(Path(runtime), "procesamiento", "canvas-sync", "study", str(cid))
        with job_lock(directory):
            prior = _read_previous_index(directory, cid)
            checked_at = _read_refresh_date(directory, prior, cid)
            need_full = (full_refresh or checked_at is None or checked_at > now or
                         now - checked_at >= timedelta(days=FULL_REFRESH_DAYS))
            prior_ejes = {e["eje"]: e for e in prior["data"]["ejes"]} if prior else {}
            reused = downloaded = 0
            listing = course_files(client, cid)
            if listing["status"] != "ok":
                print(f"Curso {cid}: archivos {listing['status']}; sin eludir permisos.", flush=True)
                results.append({"course_id": cid, "status": listing["status"], "references": 0,
                                "readings": 0, "changed": False})
                continue
            selected = identify_ejes(listing["data"])
            if not selected:
                print(f"Curso {cid}: no tiene HTML ejeN.html; estructura de estudio no localizada.",
                      flush=True)
                results.append({"course_id": cid, "status": "not_applicable",
                                "references": 0, "readings": 0, "changed": False})
                continue
            if len(selected) > MAX_EJES:
                raise SyncError("Número de ejes fuera de límites.")
            ejes = []
            for eje, meta in sorted(selected.items()):
                html_meta = file_html_metadata(client, meta["id"], meta["size"])
                raw = get_canvas_html(html_meta.get("url"), downloader=html_downloader)
                if len(raw) != meta["size"]:
                    raise SyncError(f"El HTML del eje {eje} cambió de tamaño inesperadamente.")
                html_sha = hashlib.sha256(raw).hexdigest()
                parsed = parse_eje(raw)
                base = safe_dir(directory, "eje" + str(eje))
                previous_eje = prior_ejes.get(eje) or {}
                unchanged_html = (previous_eje.get("canvas_html_file_id") == meta["id"]
                                  and previous_eje.get("canvas_html_sha256") == html_sha)
                can_reuse = not need_full and unchanged_html
                primary = (_verified_cached_document(
                    base, previous_eje.get("referente"), role="referente",
                    url=parsed["referente_url"], origin_file=meta["id"],
                    parser_factory=pdf_parser) if can_reuse else None)
                if primary is None:
                    pdf = get_institutional_pdf(parsed["referente_url"], downloader=pdf_downloader)
                    primary, _ = save_document(
                        base, "referente", pdf, extract_document(pdf, parser_factory=pdf_parser),
                        url=parsed["referente_url"], origin_file=meta["id"])
                    downloaded += 1
                else:
                    reused += 1
                readings = []
                for index, item in enumerate(parsed["readings"], 1):
                    row = {**item, "index": index}
                    if item["kind"] == "institutional_pdf":
                        reading_dir = safe_dir(base, "lecturas", str(index))
                        old_readings = previous_eje.get("readings") or []
                        previous_item = next((old for old in old_readings
                                              if old.get("index") == index), None)
                        previous_doc = previous_item.get("document") if previous_item else None
                        saved = (_verified_cached_document(
                            reading_dir, previous_doc, role="lectura", url=item["url"],
                            origin_file=meta["id"], parser_factory=pdf_parser)
                            if can_reuse else None)
                        if saved is None:
                            contents = get_institutional_pdf(item["url"], downloader=pdf_downloader)
                            saved, _ = save_document(
                                reading_dir, "lectura", contents,
                                extract_document(contents, parser_factory=pdf_parser),
                                url=item["url"], origin_file=meta["id"])
                            downloaded += 1
                        else:
                            reused += 1
                        row["document"] = saved
                        row["status"] = "downloaded"
                    else:
                        row["document"] = None
                        row["status"] = "catalogued_only"
                    readings.append(row)
                ejes.append({"eje": eje, "canvas_html_file_id": meta["id"],
                             "canvas_html_sha256": html_sha, "course_id": cid,
                             "title": parsed["title"], "author": parsed["author"],
                             "referente": primary, "readings": readings, "media": parsed["media"]})
                print(f"Curso {cid}, eje {eje}: referente {primary['pages']} páginas; "
                      f"{sum(x['status']=='downloaded' for x in readings)} lecturas institucionales.",
                      flush=True)
            # Index is stable across repeated identical runs. No timestamps in digest.
            payload = {"schema_version": 1, "source": "canvas_html_and_institutional_get_only",
                       "course_id": cid, "ejes": ejes}
            latest = directory / "index.json"
            if latest.is_symlink():
                raise SyncError("Índice de materiales redirigido.")
            updated = not latest.is_file()
            if not updated:
                try:
                    prior = json.loads(latest.read_text(encoding="utf-8"))
                    updated = prior.get("sha256") != sha(payload)
                except (ValueError, OSError):
                    updated = True
            if updated:
                stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
                record = {"sha256": sha(payload), "captured_at_utc": datetime.now(timezone.utc).isoformat(),
                          "data": payload}
                history = safe_dir(directory, "snapshots")
                atomic_json(history / (stamp + "-" + record["sha256"][:12] + ".json"), record)
                atomic_json(latest, record)
            # The refresh clock advances only after the *entire* course succeeded
            # and every PDF was revalidated remotely; incremental cache hits
            # must never postpone a full remote validation.
            if need_full:
                _save_refresh_date(directory, cid, now)
            elif not (directory / "refresh-state.json").exists() and checked_at is not None:
                _save_refresh_date(directory, cid, checked_at)
            results.append({"course_id": cid, "status": "ok",
                            "pdf_descargados": downloaded, "pdf_reutilizados": reused,
                            "refresco_completo": need_full, "references": len(ejes),
                            "readings": sum(x["status"] == "downloaded" for e in ejes
                                            for x in e["readings"]), "changed": updated})
            print(f"Curso {cid}: índice {'actualizado' if updated else 'sin cambios'}; "
                  f"PDF reutilizados={reused}, descargados={downloaded}.", flush=True)
    print(f"Canvas Sync Study: {len(results)} cursos revisados; "
          f"{getattr(client, 'requests', '?')} GET a Canvas API; "
          "documentos GET sin credenciales. Sin escrituras a Canvas ni Excel.", flush=True)
    return results
