"""Cliente Canvas REST exclusivamente GET, con paginación y origen restringido."""
import json
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import build_opener, HTTPSHandler, HTTPRedirectHandler, Request
from .common import SyncError

ORIGIN = "https://areandina.instructure.com"
MAX_PAGES = 30
MAX_BYTES = 8_000_000


class BlockRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise SyncError("Canvas intentó redirigir una solicitud autenticada; se bloqueó.")


def check_url(url):
    try:
        u = urlsplit(url)
        permitted = (u.scheme == "https" and u.hostname == "areandina.instructure.com"
                     and u.port in (None, 443) and not u.username and not u.password
                     and not u.fragment and u.path.startswith("/api/v1/"))
    except ValueError:
        permitted = False
    if not permitted:
        raise SyncError("La paginación apunta fuera de Canvas Areandina.")
    return url


def next_page(link):
    for part in (link or "").split(","):
        if re.search(r'\brel\s*=\s*"?next"?', part, re.I):
            match = re.match(r'\s*<([^>]+)>', part)
            if not match:
                raise SyncError("La cabecera Link es inválida.")
            return check_url(match.group(1))
    return None


class Client:
    def __init__(self, token, opener=None):
        if not isinstance(token, str) or not token.strip():
            raise SyncError("La autorización Canvas está vacía.")
        self.token = token
        self.opener = opener or build_opener(BlockRedirect(), HTTPSHandler())

    def get(self, url):
        check_url(url)
        req = Request(url, method="GET", headers={
            "Authorization": "Bearer " + self.token,
            "Accept": "application/json",
            "User-Agent": "AcademicManager-CanvasSync/0.1",
        })
        try:
            with self.opener.open(req, timeout=25) as resp:
                if resp.status != 200:
                    raise SyncError("La API devolvió un estado inesperado.")
                data = resp.read(MAX_BYTES + 1)
                if len(data) > MAX_BYTES:
                    raise SyncError("Respuesta demasiado grande.")
                if "json" not in resp.headers.get("Content-Type", "").lower():
                    raise SyncError("Canvas no devolvió JSON.")
                try:
                    return json.loads(data), resp.headers.get("Link", "")
                except (ValueError, UnicodeError):
                    raise SyncError("Respuesta JSON inválida.") from None
        except SyncError:
            raise
        except HTTPError as exc:
            if exc.code == 401:
                raise SyncError("Autorización rechazada (HTTP 401). Ejecute renew.") from None
            if exc.code == 403:
                raise SyncError("Sin permisos para esta consulta (HTTP 403).") from None
            if exc.code == 429:
                raise SyncError("Límite temporal de Canvas (HTTP 429); reintente después.") from None
            raise SyncError(f"Canvas respondió HTTP {exc.code}.") from None
        except (URLError, TimeoutError, OSError):
            raise SyncError("Conexión segura con Canvas no disponible.") from None

    def pages(self, path, params=None):
        query = "?" + urlencode(params, doseq=True) if params else ""
        url = check_url(ORIGIN + path + query)
        result, visited = [], set()
        for _ in range(MAX_PAGES):
            if url in visited:
                raise SyncError("Paginación circular; no se guarda un resultado parcial.")
            visited.add(url)
            page, link = self.get(url)
            if not isinstance(page, list):
                raise SyncError("Canvas devolvió una página no válida.")
            result.extend(page)
            url = next_page(link)
            if not url:
                return result
        raise SyncError("Límite de páginas alcanzado; no se guarda información parcial.")

    def profile(self):
        data, _ = self.get(ORIGIN + "/api/v1/users/self/profile")
        if not isinstance(data, dict) or not isinstance(data.get("id"), int):
            raise SyncError("Perfil Canvas sin identificador válido.")
        return data["id"]

    def course(self, course_id):
        data, _ = self.get(ORIGIN + f"/api/v1/courses/{course_id}")
        if not isinstance(data, dict) or data.get("id") != course_id:
            raise SyncError("El curso recibido no corresponde al solicitado.")
        return data

    def courses(self):
        return self.pages("/api/v1/courses", {"enrollment_state": "active", "per_page": 100})

    def assignments(self, course_id):
        return self.pages(f"/api/v1/courses/{course_id}/assignments",
                          {"per_page": 100, "include[]": ["submission"]})
