"""Servidor HTTP local del asistente: solo 127.0.0.1, clave por apertura y CSRF.

Controles en cada petición, antes de llegar a un paso:
- Host exactamente 127.0.0.1:<puerto> (protege contra DNS rebinding);
- Origin y Sec-Fetch-Site, si llegan, del mismo origen; en POST, Origin obligatorio;
- clave secreta de esta apertura: en el enlace inicial y luego en una cookie
  HttpOnly + SameSite=Strict; comparación en tiempo constante;
- token CSRF en todo POST; cuerpo limitado a MAX_BODY bytes;
- el registro solo guarda método, ruta sin parámetros y código: nunca la clave ni datos.
El servidor se detiene con «Cerrar» o tras IDLE_SECONDS sin peticiones.
"""
import hmac
import secrets
import socketserver
import sys
import threading
import time
from email import policy
from email.parser import BytesParser
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from . import pages

HOST = "127.0.0.1"
COOKIE = "am_sesion"
MAX_BODY = 64 * 1024        # formularios y el JSON del cliente OAuth (~1 KB)
DRAIN_LIMIT = 1024 * 1024   # se descarta hasta 1 MB de un cuerpo rechazado para responder limpio
IDLE_SECONDS = 30 * 60
MAX_FIELDS = 200
CSP = ("default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; "
       "frame-ancestors 'none'; base-uri 'none'")


def _same(given, expected):
    return isinstance(given, str) and hmac.compare_digest(given.encode("utf-8"), expected.encode("utf-8"))


class Rejected(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


class Handler(BaseHTTPRequestHandler):
    server_version = "AcademicManager"
    sys_version = ""
    timeout = 15  # segundos de espera de lectura: un envío incompleto no deja el hilo colgado

    def do_GET(self):
        self._serve("GET")

    def do_POST(self):
        self._serve("POST")

    # --- registro sin datos sensibles -------------------------------------------------
    def _safe_path(self):
        path = urlsplit(getattr(self, "path", None) or "").path[:120]
        return "".join(c if c.isprintable() else "?" for c in path)

    def log_request(self, code="-", size="-"):
        self.server.log(f"{self.command} {self._safe_path()} {code}")

    def log_message(self, format, *args):  # noqa: A002 - firma de BaseHTTPRequestHandler
        # Una conexión que vence sin enviar nada aún no tiene command ni path.
        self.server.log(f"{getattr(self, 'command', None) or '-'} {self._safe_path() or '-'} aviso HTTP")

    # --- comprobaciones ---------------------------------------------------------------
    def _check_origin(self, method):
        port = self.server.server_address[1]
        hosts = self.headers.get_all("Host") or []
        if hosts != [f"{HOST}:{port}"]:
            raise Rejected(403, "Dirección no permitida.")
        origin = self.headers.get("Origin")
        if origin is not None and origin != f"http://{HOST}:{port}":
            raise Rejected(403, "Petición de otro sitio rechazada.")
        if method == "POST" and origin is None:
            raise Rejected(403, "Petición sin origen rechazada.")
        site = self.headers.get("Sec-Fetch-Site")
        if site is not None and site not in ("same-origin", "none"):
            raise Rejected(403, "Petición de otro sitio rechazada.")

    def _session(self):
        try:
            cookie = SimpleCookie(self.headers.get("Cookie") or "")
        except CookieError:
            return False
        morsel = cookie.get(COOKIE)
        return morsel is not None and _same(morsel.value, self.server.secret)

    def _read_body(self):
        if self.headers.get("Transfer-Encoding"):
            raise Rejected(411, "Envío no admitido.")
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            raise Rejected(411, "Falta el tamaño del envío.") from None
        if length < 0:
            raise Rejected(400, "Envío no válido.")
        if length > MAX_BODY:
            self._drain(length)
            raise Rejected(413, "El envío es demasiado grande.")
        try:
            body = self.rfile.read(length)
        except TimeoutError:
            raise Rejected(408, "El envío no llegó completo. Vuelva a intentarlo.") from None
        if len(body) != length:
            raise Rejected(400, "El envío no llegó completo. Vuelva a intentarlo.")
        return body

    def _drain(self, length):
        remaining = min(length, DRAIN_LIMIT)
        while remaining > 0:
            chunk = self.rfile.read(min(remaining, 65536))
            if not chunk:
                break
            remaining -= len(chunk)

    def _parse(self, body):
        kind = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        form, files = {}, {}
        if kind == "application/x-www-form-urlencoded":
            try:
                form = parse_qs(body.decode("utf-8"), keep_blank_values=True, max_num_fields=MAX_FIELDS)
            except (UnicodeDecodeError, ValueError):
                raise Rejected(400, "Formulario no válido.") from None
        elif kind == "multipart/form-data":
            header = self.headers.get("Content-Type", "").encode("latin-1", "replace")
            message = BytesParser(policy=policy.HTTP).parsebytes(
                b"Content-Type: " + header + b"\r\nMIME-Version: 1.0\r\n\r\n" + body)
            if not message.is_multipart():
                raise Rejected(400, "Formulario no válido.")
            for count, part in enumerate(message.iter_parts()):
                name = part.get_param("name", header="content-disposition")
                if count >= MAX_FIELDS or not name:
                    raise Rejected(400, "Formulario no válido.")
                payload = part.get_payload(decode=True) or b""
                if part.get_filename() is not None:
                    files[name] = payload
                else:
                    try:
                        form.setdefault(name, []).append(payload.decode("utf-8"))
                    except UnicodeDecodeError:
                        raise Rejected(400, "Formulario no válido.") from None
        elif body:
            raise Rejected(400, "Formulario no válido.")
        return form, files

    # --- despacho ---------------------------------------------------------------------
    def _serve(self, method):
        srv = self.server
        srv.touch(+1)
        try:
            try:
                self._check_origin(method)
                parts = urlsplit(self.path)
                query = parse_qs(parts.query)
                if method == "GET" and parts.path == "/" and "clave" in query:
                    if not _same(query["clave"][0], srv.secret):
                        raise Rejected(403, "Enlace no válido.")
                    cookie = f"{COOKIE}={srv.secret}; HttpOnly; SameSite=Strict; Path=/"
                    return self._send(pages.Response(303, location="/"), cookie=cookie)
                if not self._session():
                    raise Rejected(403, "Abra el asistente con el acceso directo de Academic Manager.")
                form, files = {}, {}
                if method == "POST":
                    form, files = self._parse(self._read_body())
                    if not _same((form.get("csrf") or [""])[0], srv.csrf):
                        raise Rejected(403, "El formulario caducó. Vuelva a la página e inténtelo de nuevo.")
                    if parts.path == "/cerrar":  # cerrar no espera a un paso en curso (p. ej. Google)
                        response = srv.wizard.handle(method, parts.path, form, files, srv.csrf)
                    else:
                        with srv.action_lock:
                            response = srv.wizard.handle(method, parts.path, form, files, srv.csrf)
                else:
                    response = srv.wizard.handle(method, parts.path, form, files, srv.csrf)
            except Rejected as exc:
                response = pages.Response(exc.status, pages.message_page(str(exc)))
            self._send(response)
            if response.shutdown:
                threading.Thread(target=srv.shutdown, daemon=True).start()
        except Exception as exc:  # nunca mostrar detalles internos ni datos
            srv.log(f"{method} {self._safe_path()} fallo interno {type(exc).__name__}")
            try:
                self._send(pages.Response(500, pages.message_page(
                    "Algo falló dentro del asistente. Cierre la página y vuelva a abrirlo.")))
            except OSError:
                pass
        finally:
            srv.touch(-1)

    def _send(self, response, cookie=None):
        body = response.body.encode("utf-8")
        self.send_response(response.status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", CSP)
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("X-Content-Type-Options", "nosniff")
        # same-origin: con no-referrer el navegador enviaría «Origin: null» en los POST.
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        if response.location:
            self.send_header("Location", response.location)
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)


class WizardServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, wizard, *, port=0, idle_seconds=IDLE_SECONDS, log_stream=None):
        self.wizard = wizard
        self.secret = secrets.token_urlsafe(32)
        self.csrf = secrets.token_urlsafe(32)
        self.idle_seconds = idle_seconds
        self.log_stream = log_stream
        self.action_lock = threading.Lock()  # un paso a la vez: no hay escrituras concurrentes
        self._state_lock = threading.Lock()
        self._active = 0
        self._last = time.monotonic()
        self._stopped = threading.Event()
        super().__init__((HOST, port), Handler)

    def server_bind(self):
        # Sin getfqdn: no se consulta DNS para el nombre del servidor.
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = HOST, self.server_address[1]

    @property
    def url(self):
        return f"http://{HOST}:{self.server_address[1]}/?clave={self.secret}"

    def log(self, line):
        stream = self.log_stream if self.log_stream is not None else sys.stderr
        try:
            stream.write(line + "\n")
        except (OSError, ValueError):
            pass

    def touch(self, delta):
        with self._state_lock:
            self._active += delta
            self._last = time.monotonic()

    def _idle(self):
        with self._state_lock:
            return self._active == 0 and time.monotonic() - self._last > self.idle_seconds

    def _watch(self):
        interval = max(0.05, min(self.idle_seconds / 4, 5))
        while not self._stopped.wait(interval):
            if self._idle():
                self.log("Asistente cerrado por inactividad.")
                self.shutdown()
                return

    def serve_forever(self, poll_interval=0.5):
        watcher = threading.Thread(target=self._watch, daemon=True)
        watcher.start()
        try:
            super().serve_forever(poll_interval=min(poll_interval, 0.1))
        finally:
            self._stopped.set()


def create(wizard, *, port=0, idle_seconds=IDLE_SECONDS, log_stream=None):
    """Servidor listo para serve_forever(); url incluye la clave de esta apertura."""
    return WizardServer(wizard, port=port, idle_seconds=idle_seconds, log_stream=log_stream)
