"""Panel access from the phone on the same private network (off by default).

State (control/celular/acceso.json) keeps only PBKDF2 of the PIN and SHA-256 of each paired
device token; pairing codes and PIN sessions live in memory only. The phone listener binds the
PC's private LAN address, accepts only private clients (never loopback), checks Host/Origin like
the PC listener, and serves only the panel pages and the register flow.
"""
import hashlib
import hmac
import ipaddress
import json
import secrets
import socket
import socketserver
import sys
import threading
import time
from http.cookies import CookieError, SimpleCookie
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from canvas_sync.common import SyncError, atomic_json, safe_dir

from . import pages
from .server import Handler, Rejected, _same

PORT = 47321
FILE = "acceso.json"
DEVICE_COOKIE = "am_celular"
SESSION_COOKIE = "am_celular_sesion"
DEVICE_MAX_AGE = 180 * 24 * 3600
PAIRING_SECONDS = 300
SESSION_SECONDS = 12 * 3600
SEEN_INTERVAL = 3600
MAX_FAILURES = 5
LOCKOUT_SECONDS = 900
ITERATIONS = 200_000

ALLOWED_GET = {"/panel", "/panel/resumen", "/panel/materias", "/panel/instrucciones"}
ALLOWED_GET_PREFIXES = ("/panel/materia/", "/panel/publicando/")
ALLOWED_POST = {"/panel/registrar", "/panel/registrar/aprobar"}

NOT_AVAILABLE = "No disponible desde el celular. Use el PC para esa parte."
NOT_PAIRED = ("Este celular no está autorizado. En el PC, abra Configuración y escanee "
              "el código QR del acceso desde el celular.")


class PhoneError(Exception):
    """Plain message for the person; never carries codes, tokens or the PIN."""


def _sha256(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _pin_hash(pin, salt, iterations):
    return hashlib.pbkdf2_hmac("sha256", pin.encode("ascii"), salt, iterations).hex()


def _valid_pin(pin):
    if not (isinstance(pin, str) and pin.isascii() and pin.isdigit() and 6 <= len(pin) <= 12):
        raise PhoneError("El PIN debe tener entre 6 y 12 números.")
    return pin


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _valid_pin_record(record):
    """None (no PIN yet) or the exact shape written by PhoneAccess._new_pin."""
    if record is None:
        return True
    if not (isinstance(record, dict) and isinstance(record.get("salt"), str)
            and isinstance(record.get("hash"), str) and record["hash"].isascii()
            and _is_int(record.get("iteraciones"))
            and record["iteraciones"] > 0):
        return False
    try:
        bytes.fromhex(record["salt"])
    except ValueError:
        return False
    return True


def _valid_device(item):
    return (isinstance(item, dict) and all(isinstance(item.get(k), str) for k in ("id", "token_sha256", "nombre"))
            and item["token_sha256"].isascii() and _is_int(item.get("creado")) and _is_int(item.get("visto")))


def device_name(user_agent):
    """Short, printable name for the device list (never the full User-Agent)."""
    text = user_agent or ""
    for key, label in (("iPhone", "iPhone"), ("iPad", "iPad"), ("Android", "Android")):
        if key in text:
            return label
    clean = "".join(c for c in text if c.isprintable()).strip()[:60]
    return clean or "Celular"


class PhoneAccess:
    """Thread-safe phone access state: switch, PIN, paired devices, pairing code, sessions."""

    def __init__(self, runtime, *, clock=time.time):
        self._runtime = Path(runtime)
        self._clock = clock
        self._lock = threading.RLock()
        self._state = self._load()
        self._pending = None          # (sha256 of the code, expires)
        self._sessions = {}           # sha256 of the token -> (device_id, csrf, expires)
        self._failures = 0
        self._locked_until = 0.0

    # --- persistence ----------------------------------------------------------------------
    def _load(self):
        empty = {"schema": 1, "activo": False, "pin": None, "dispositivos": []}
        path = self._runtime / "control" / "celular" / FILE
        try:
            if path.is_symlink() or path.parent.is_symlink() or not path.is_file():
                return empty
            data = json.loads(path.read_text(encoding="utf-8"))
            devices = data.get("dispositivos")
            if (data.get("schema") != 1 or not isinstance(devices, list)
                    or not _valid_pin_record(data.get("pin")) or not all(map(_valid_device, devices))):
                return empty  # a malformed record is as unreadable as a broken file
            return {"schema": 1, "activo": data.get("activo") is True, "pin": data.get("pin"),
                    "dispositivos": [dict(d) for d in devices]}
        except (OSError, ValueError, AttributeError, TypeError):
            return empty  # unreadable state fails closed: off, no PIN

    def _save(self, state):
        """Write first, then adopt: a failed write leaves the previous state in force."""
        try:
            folder = safe_dir(self._runtime, "control", "celular")
            atomic_json(folder / FILE, state)
        except (SyncError, OSError) as exc:
            raise PhoneError("No se pudo guardar el acceso desde el celular en la carpeta de datos.") from exc
        self._state = state

    def _changed(self, **fields):
        state = {**self._state, "dispositivos": [dict(d) for d in self._state["dispositivos"]]}
        state.update(fields)
        return state

    # --- switch and PIN -------------------------------------------------------------------
    def is_active(self):
        with self._lock:
            return bool(self._state["activo"] and self._state["pin"])

    def _new_pin(self, pin):
        salt = secrets.token_bytes(16)
        return {"salt": salt.hex(), "hash": _pin_hash(pin, salt, ITERATIONS), "iteraciones": ITERATIONS}

    def enable(self, pin):
        _valid_pin(pin)
        with self._lock:
            self._save(self._changed(activo=True, pin=self._new_pin(pin)))
            self._sessions.clear()

    def change_pin(self, pin):
        _valid_pin(pin)
        with self._lock:
            self._save(self._changed(pin=self._new_pin(pin)))
            self._sessions.clear()

    def disable(self):
        with self._lock:
            self._sessions.clear()
            self._pending = None
            self._save(self._changed(activo=False))

    # --- pairing and devices --------------------------------------------------------------
    def new_pairing(self):
        with self._lock:
            if not self.is_active():
                raise PhoneError("Active primero el acceso desde el celular.")
            code = secrets.token_urlsafe(32)
            self._pending = (_sha256(code), self._clock() + PAIRING_SECONDS)
            return code

    def pair(self, code, name):
        with self._lock:
            pending, self._pending = self._pending, None
            valid = (self.is_active() and pending is not None and isinstance(code, str)
                     and hmac.compare_digest(_sha256(code), pending[0]))
            if not valid:
                self._pending = pending  # a wrong guess does not burn the real code
                raise PhoneError("El código QR no es válido o ya se usó. Genere uno nuevo en el PC.")
            if self._clock() > pending[1]:
                raise PhoneError("El código QR venció. Genere uno nuevo en el PC.")
            token = secrets.token_urlsafe(32)
            now = int(self._clock())
            clean = "".join(c for c in (name or "") if c.isprintable()).strip()[:60] or "Celular"
            state = self._changed()
            state["dispositivos"].append({"id": secrets.token_hex(8), "token_sha256": _sha256(token),
                                          "nombre": clean, "creado": now, "visto": now})
            self._save(state)
            return token

    def device(self, token):
        if not isinstance(token, str) or not token:
            return None
        digest = _sha256(token)
        with self._lock:
            found = None
            for item in self._state["dispositivos"]:
                if hmac.compare_digest(digest, str(item.get("token_sha256", ""))) and found is None:
                    found = item
            if found is None:
                return None
            now = int(self._clock())
            if now - int(found.get("visto") or 0) >= SEEN_INTERVAL:
                state = self._changed()
                for item in state["dispositivos"]:
                    if item.get("id") == found.get("id"):
                        item["visto"] = now
                try:
                    self._save(state)
                except PhoneError:
                    pass  # "last seen" is informative; never lock the person out over it
            return found.get("id")

    def devices(self):
        with self._lock:
            return [{key: item.get(key) for key in ("id", "nombre", "creado", "visto")}
                    for item in self._state["dispositivos"]]

    def revoke(self, device_id):
        with self._lock:
            state = self._changed()
            state["dispositivos"] = [d for d in state["dispositivos"] if d.get("id") != device_id]
            removed = len(state["dispositivos"]) != len(self._state["dispositivos"])
            if removed:
                self._save(state)
            self._sessions = {k: v for k, v in self._sessions.items() if v[0] != device_id}
            return removed

    # --- PIN sessions (memory only) -------------------------------------------------------
    def login(self, device_id, pin):
        with self._lock:
            now = self._clock()
            if now < self._locked_until:
                raise PhoneError("Demasiados intentos con un PIN equivocado. Espere 15 minutos y vuelva a intentarlo.")
            known = any(d.get("id") == device_id for d in self._state["dispositivos"])
            if not self.is_active() or not known:
                raise PhoneError(NOT_PAIRED)
            record = self._state["pin"]
            given = pin if isinstance(pin, str) and pin.isascii() else ""
            expected = _pin_hash(given, bytes.fromhex(record["salt"]), int(record["iteraciones"]))
            if not hmac.compare_digest(expected, record["hash"]):
                self._failures += 1
                if self._failures >= MAX_FAILURES:
                    self._failures = 0
                    self._locked_until = now + LOCKOUT_SECONDS
                    raise PhoneError("Demasiados intentos con un PIN equivocado. Espere 15 minutos y vuelva a intentarlo.")
                raise PhoneError("El PIN no es correcto.")
            self._failures = 0
            token = secrets.token_urlsafe(32)
            self._sessions[_sha256(token)] = (device_id, secrets.token_urlsafe(32), now + SESSION_SECONDS)
            return token

    def session(self, token):
        if not isinstance(token, str) or not token:
            return None
        key = _sha256(token)
        with self._lock:
            found = self._sessions.get(key)
            if found is None:
                return None
            if self._clock() > found[2]:
                del self._sessions[key]
                return None
            return found[0], found[1]

    def logout(self, token):
        if isinstance(token, str):
            with self._lock:
                self._sessions.pop(_sha256(token), None)


def lan_address():
    """The PC's private LAN IPv4 (UDP connect sends no packets), or None."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("192.0.2.1", 9))  # documentation range: only selects the route
            ip = probe.getsockname()[0]
        address = ipaddress.ip_address(ip)
    except (OSError, ValueError):
        return None
    if address.is_private and not (address.is_loopback or address.is_link_local or address.is_unspecified):
        return str(address)
    return None


def _client_allowed(ip):
    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return address.is_private and not (address.is_loopback or address.is_link_local or address.is_unspecified)


def _pin_page(message=""):
    body = (f'{pages.notice(message, bad=True) if message else ""}<div class="card">'
            '<form method="post" action="/entrar"><label for="pin">PIN de Academic Manager</label>'
            '<input type="password" id="pin" name="pin" inputmode="numeric" autocomplete="current-password" '
            'pattern="[0-9]*" maxlength="12" required autofocus>'
            '<button type="submit">Entrar</button></form></div>')
    return pages.layout("Entrar", body, back=False, subtitle="Escriba el PIN que eligió en el PC.")


class PhoneHandler(Handler):
    """Reuses the PC handler's body parsing, sending and logging; own checks and allowlist."""

    def _check_origin(self, method):
        ip, port = self.server.server_address[:2]
        if not self.server.client_allowed(self.client_address[0]):
            raise Rejected(403, "Solo se puede entrar desde un celular conectado a la misma red privada.")
        if (self.headers.get_all("Host") or []) != [f"{ip}:{port}"]:
            raise Rejected(403, "Dirección no permitida.")
        origin = self.headers.get("Origin")
        if origin is not None and origin != f"http://{ip}:{port}":
            raise Rejected(403, "Petición de otro sitio rechazada.")
        if method == "POST" and origin is None:
            raise Rejected(403, "Petición sin origen rechazada.")
        site = self.headers.get("Sec-Fetch-Site")
        if site is not None and site not in ("same-origin", "none"):
            raise Rejected(403, "Petición de otro sitio rechazada.")

    def _cookie(self, name):
        try:
            morsel = SimpleCookie(self.headers.get("Cookie") or "").get(name)
        except CookieError:
            return None
        return morsel.value if morsel is not None else None

    def _serve(self, method):
        srv = self.server
        try:
            cookie = None
            try:
                self._check_origin(method)
                # Only requests that pass the client/Host/Origin checks keep the PC app from idling out.
                srv.on_activity()
                access = srv.access
                if not access.is_active():
                    raise Rejected(403, "El acceso desde el celular está apagado. Actívelo en el PC, en Configuración.")
                parts = urlsplit(self.path)
                path = parts.path
                if method == "GET" and path == "/emparejar":
                    code = (parse_qs(parts.query).get("codigo") or [""])[0]
                    try:
                        token = access.pair(code, device_name(self.headers.get("User-Agent")))
                    except PhoneError as exc:
                        raise Rejected(403, str(exc)) from None
                    cookie = (f"{DEVICE_COOKIE}={token}; HttpOnly; SameSite=Strict; Path=/; "
                              f"Max-Age={DEVICE_MAX_AGE}")
                    response = pages.Response(303, location="/entrar")
                elif path == "/entrar":
                    device_id = access.device(self._cookie(DEVICE_COOKIE))
                    if device_id is None:
                        raise Rejected(403, NOT_PAIRED)
                    if method == "GET":
                        response = pages.Response(200, _pin_page())
                    else:
                        form, _ = self._parse(self._read_body())
                        try:
                            session = access.login(device_id, (form.get("pin") or [""])[0])
                        except PhoneError as exc:
                            response = pages.Response(403, _pin_page(str(exc)))
                        else:
                            cookie = f"{SESSION_COOKIE}={session}; HttpOnly; SameSite=Strict; Path=/"
                            response = pages.Response(303, location="/panel")
                elif method == "POST" and path == "/salir":
                    access.logout(self._cookie(SESSION_COOKIE))
                    cookie = f"{SESSION_COOKIE}=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0"
                    response = pages.Response(303, location="/entrar")
                else:
                    response = self._panel(method, path)
            except Rejected as exc:
                response = pages.Response(exc.status, pages.message_page(str(exc)))
            self._send(response, cookie=cookie)
        except Exception as exc:  # never show internal details or data
            srv.log(f"{method} {self._safe_path()} fallo interno {type(exc).__name__}")
            try:
                self._send(pages.Response(500, pages.message_page(
                    "Algo falló dentro de Academic Manager. Vuelva a abrir la página.")))
            except OSError:
                pass

    def _panel(self, method, path):
        srv = self.server
        device_id = srv.access.device(self._cookie(DEVICE_COOKIE))
        session = srv.access.session(self._cookie(SESSION_COOKIE))
        if device_id is None or session is None or session[0] != device_id:
            return pages.Response(303, location="/entrar")
        csrf = session[1]
        if method == "GET" and (path in ALLOWED_GET or path.startswith(ALLOWED_GET_PREFIXES)):
            return srv.wizard.handle(method, path, {}, {}, csrf, phone=True)
        if method == "POST" and path in ALLOWED_POST:
            form, files = self._parse(self._read_body())
            if not _same((form.get("csrf") or [""])[0], csrf):
                raise Rejected(403, "El formulario caducó. Vuelva a la página e inténtelo de nuevo.")
            with srv.action_lock:
                return srv.wizard.handle(method, path, form, files, csrf, phone=True)
        raise Rejected(403, NOT_AVAILABLE)


class PhoneServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False  # never share the port with another program
    client_allowed = staticmethod(_client_allowed)

    def __init__(self, address, wizard, access, action_lock, on_activity, *, log_stream=None):
        self.wizard = wizard
        self.access = access
        self.action_lock = action_lock
        self.on_activity = on_activity
        self.log_stream = log_stream
        super().__init__(address, PhoneHandler)

    def server_bind(self):
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):  # Windows: refuse to share a busy port
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        socketserver.TCPServer.server_bind(self)  # no getfqdn: no DNS lookup
        self.server_name, self.server_port = self.server_address[:2]

    def log(self, line):
        stream = self.log_stream if self.log_stream is not None else sys.stderr
        try:
            stream.write("Celular: " + line + "\n")
        except (OSError, ValueError):
            pass


class PhoneController:
    """Starts and stops the phone listener while access is on and the PC app is open."""

    def __init__(self, wizard, access, action_lock, on_activity, *, port=PORT, address=lan_address,
                 log_stream=None):
        self.wizard, self.access, self.action_lock = wizard, access, action_lock
        self.on_activity = on_activity
        self.port = port
        self.address = address
        self.log_stream = log_stream
        self._lock = threading.Lock()
        self._server = None
        self._thread = None
        self._problem = None

    def start(self):
        """None when listening; otherwise a plain message saying why not."""
        with self._lock:
            if self._server is not None:
                return None
            self._problem = self._start()
            return self._problem

    def _start(self):
        if not self.access.is_active():
            return "El acceso desde el celular está apagado."
        ip = self.address()
        if not ip:
            return ("El PC no está conectado a una red privada (como el Wi-Fi de casa). "
                    "Conéctelo y vuelva a intentarlo.")
        try:
            server = PhoneServer((ip, self.port), self.wizard, self.access, self.action_lock,
                                 self.on_activity, log_stream=self.log_stream)
        except OSError:
            return (f"El puerto {self.port} está ocupado por otro programa. "
                    "Cierre ese programa o reinicie el PC y vuelva a intentarlo.")
        self._server = server
        self._thread = threading.Thread(target=server.serve_forever, daemon=True)
        self._thread.start()
        return None

    def stop(self):
        with self._lock:
            server, thread = self._server, self._thread
            self._server = self._thread = None
        if server is not None:
            server.shutdown()
            server.server_close()
            thread.join(5)

    def _base(self):
        server = self._server
        if server is None:
            return None
        ip, port = server.server_address[:2]
        return f"http://{ip}:{port}"

    def status(self):
        with self._lock:
            return {"activo": self.access.is_active(), "escuchando": self._server is not None,
                    "direccion": self._base(), "problema": self._problem}

    def pairing_url(self, code):
        with self._lock:
            base = self._base()
        if base is None:
            ip = self.address()
            base = f"http://{ip}:{self.port}" if ip else None
        return f"{base}/emparejar?codigo={code}" if base else None
