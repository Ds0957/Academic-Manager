"""Pasos del asistente de configuración. Cada paso reutiliza el código existente:

- carpetas: academic_config.paths (config.json del usuario);
- equipo: academic_setup (misma comprobación que `check --texto`) y academic_setup.whisperx
  (instala WhisperX solo si esa comprobación lo permite);
- Canvas: canvas_sync.auth.save_token (misma validación y DPAPI que login/renew);
- Google: capture.sources.auth (cliente Desktop propio y el mismo login del navegador, con
  permiso de escritura para publicar la base cuando no hay cuenta de servicio);
- materias: academic_store.bootstrap (base nueva para quien empieza de cero), canvas_sync.semester
  (lista, onboard) y workbook_enrollment (publicación);
- tarea diaria: scripts/install-canvas-daily-task.ps1 vía academic_app.daily_task;
- panel del día a día (solo lectura): academic_access today/summary/subject_brief y
  academic_projects.render_all, vía academic_app.panel.

El estado de cada paso se lee de los archivos reales, así que el asistente se puede
cerrar y retomar. Nunca se muestran tokens, secretos ni códigos de Google.
"""
import argparse
import functools
import importlib
import json
import os
import re
import sys
import threading
from datetime import datetime
from pathlib import Path

from academic_config import paths

from . import forms, panel, registrar
from .pages import Response, badge, csrf_field, esc, layout, notice

SEMESTER = re.compile(r"20\d{2}-[1-3]")
STEPS = (("datos", "Carpeta de datos", "/paso/datos"),
         ("equipo", "Comprobación del equipo", "/paso/equipo"),
         ("canvas", "Canvas", "/paso/canvas"),
         ("google", "Google Drive", "/paso/google"),
         ("drive", "Carpeta de Drive", "/paso/drive"),
         ("materias", "Materias", "/paso/materias"),
         ("tarea", "Sincronización diaria", "/paso/tarea"))
# Configuración completa para abrir el panel (equipo y tarea solo se conocen al abrirlos).
COMPLETE_STEPS = ("datos", "canvas", "google", "drive", "materias")
SUBJECT_PAGE = "/panel/materia/"
CANVAS_INBOX = "https://areandina.instructure.com/conversations"  # bandeja: abrirla no marca mensajes como leídos
PUBLISHING_PAGE = "/panel/publicando/"
RELOGIN_HINT_DAYS = 6
NEED_DATA = "Primero elija la carpeta de datos (paso 1)."


BASE_HALF = ("La creación de la base de datos quedó a medias. Abra el paso y pulse «Retomar la creación»: "
             "continúa donde quedó sin borrar nada.")
PENDING = "aprobado_local_pendiente_excel"
PENDING_TEXT = ("Hay materias guardadas en este PC pendientes de publicar en la base de datos de Drive. "
                "Abra el paso y pulse «Revisar y publicar».")

# Mensajes técnicos de los módulos existentes (sus CLI no cambian) dichos en lenguaje sencillo.
PLAIN = (
    ("tipo Desktop (installed)", "Ese archivo no es el correcto. En Google Cloud cree el acceso con el tipo "
                                 "«App de escritorio» y descargue su archivo."),
    ("endpoints oficiales", "Ese archivo no parece venir de Google. Descárguelo de nuevo desde Google Cloud."),
    ("No se pudo leer el JSON", "No se pudo leer ese archivo. Elija el que descargó de Google Cloud "
                                "(su nombre empieza por «client_secret»)."),
    ("Vencimiento inválido", "Elija la fecha y la hora de vencimiento del token (las mismas que puso en Canvas)."),
    ("requiere Windows DPAPI", "Esta parte solo funciona en Windows: el token se guarda cifrado con su "
                               "usuario de Windows."),
    ("No hay base local y estado de control",
     "Su carpeta de datos todavía no tiene la base de datos de Academic Manager."),
    ("No hay identidad técnica local válida", "Falta iniciar sesión con Google con permiso para guardar en "
                                              "Drive (paso 4)."),
    ("No existe autorización local", "Falta conectar Canvas (paso 3)."),
    ("identidad de publicación no tiene", "Su cuenta de Google no puede ver o guardar la base de datos en la "
                                          "carpeta de Drive. Revise que la carpeta sea suya."),
    ("estado compartido", "La base de datos de Drive no respondió como se esperaba. Pida ayuda para revisarla "
                          "antes de volver a publicar."),
    ("estado de workbook", "La base de datos de Drive no respondió como se esperaba. Pida ayuda para revisarla "
                           "antes de volver a publicar."),
    ("requirements-drive.txt", "Falta instalar la parte de Google Drive de Academic Manager. Vuelva a "
                               "ejecutar el instalador."),
)


def plain(exc):
    """Mensaje para la persona; un error del sistema solo deja como detalle el tipo de error."""
    if isinstance(exc, OSError):
        return ("No se pudo leer o escribir un archivo. Revise que la carpeta exista y que Google Drive "
                f"esté abierto. Detalle técnico: {type(exc).__name__}.")
    text = str(exc)
    for needle, message in PLAIN:
        if needle in text:
            return message
    return text


class StepError(Exception):
    """Problema que la persona puede corregir; el mensaje se muestra tal cual (escapado)."""


def run_check(runtime):
    """La comprobación de `python -m academic_setup check --texto`, sin copiarla."""
    from academic_setup import __main__ as setup_cli, check
    data = setup_cli.run(argparse.Namespace(runtime=str(runtime), benchmark=False))
    return data, check.text(data)


NO_APTO = ("Este PC no es apto para transcribir clases grabadas con WhisperX, así que no se instala WhisperX. "
           "Todo lo demás funciona igual, incluido lo que aparece arriba en «Funciona siempre».")
WHISPERX_STARTED = ("La instalación de WhisperX empezó. Puede tardar bastante (descarga varios archivos grandes) "
                    "y sigue aunque cierre el asistente. Vuelva a abrir este paso para ver cómo va.")


def _defaults():
    from academic_setup import whisperx
    from academic_store import bootstrap
    from . import daily_task
    from canvas_sync.api import Client
    from canvas_sync import semester, workbook_enrollment
    from capture.sources import auth as drive_auth
    from . import updates
    from academic_access import brief, canvas, summary, today
    from academic_projects.__main__ import render_all
    from canvas_sync.routine import workbook_version
    from academic_operations import chat
    from . import manual_sync
    return {"write_preview": chat.preview, "write_apply": chat.apply,
            "sync_start": manual_sync.start, "sync_status": manual_sync.status,
            "panel_today": today.today, "panel_summary": summary.summary,
            "panel_brief": brief.subject_brief, "panel_instructions": render_all,
            "panel_canvas": canvas.canvas_extras,
            "workbook_version": workbook_version,
            "check_update": updates.check, "download_update": updates.download,
            "launch_update": updates.launch,
            "canvas_client": Client, "canvas_courses": semester.list_canvas_courses,
            "drive_login": functools.partial(drive_auth.login, write=True), "check": run_check,
            "load_registry": semester.load_registry, "onboard": semester.onboard,
            "base_status": bootstrap.status, "create_base": bootstrap.create,
            "empty_base": semester.is_empty_base, "build_index": build_index,
            "publish_enrollment": workbook_enrollment.publish_enrollment,
            "whisperx_status": whisperx.status, "whisperx_start": whisperx.start,
            "task_status": daily_task.status, "register_task": daily_task.register}


INSIDE_PROGRAM = ("{} está dentro de la carpeta del programa Academic Manager, que se borra al desinstalar o "
                  "actualizar. Elija una carpeta fuera de ella, por ejemplo {}.")


def _code_root():
    """Carpeta del código que está corriendo ({app}\\app en una copia instalada)."""
    return Path(__file__).resolve().parents[1]


def program_folders():
    """Carpetas que el instalador reemplaza o borra: nunca deben contener datos."""
    local = os.environ.get("LOCALAPPDATA")
    base = Path(local) if local else Path.home() / "AppData" / "Local"
    code = _code_root()
    folders = [base / "Programs" / "AcademicManager", code]
    if code.name.lower() == "app" and (code.parent / "python").is_dir():
        folders.append(code.parent)  # copia instalada en otra carpeta
    return folders


def _key(path):
    return os.path.normcase(os.path.realpath(os.path.abspath(path)))


def inside_program(folder):
    target = _key(folder)
    for base in map(_key, program_folders()):
        if target == base or target.startswith(base.rstrip(os.sep) + os.sep):
            return True
    return False


def _count(number, singular, plural):
    return f"{number} {singular if number == 1 else plural}"


def build_index(runtime):
    """El índice de búsqueda, con la misma función que `complete` y la tarea diaria."""
    from academic_knowledge.index import build
    drive_root = paths.load_config().get("drive_root")
    return build(runtime, drive_root=Path(drive_root)) if drive_root else build(runtime)


INDEX_WARNING = ("Las materias quedaron publicadas, pero el índice de búsqueda de este PC no se pudo "
                 "actualizar, así que la consulta de materias puede no verlas todavía.")


def _first(form, name):
    values = form.get(name) or [""]
    return values[0].strip()


class Wizard:
    def __init__(self, **deps):
        self.deps = {**_defaults(), **deps}
        self._lock = threading.Lock()
        self._flash = None
        self._check = None  # (estado, detalle, texto) de la última comprobación de esta apertura
        self._task = None   # (estado, detalle) de la tarea diaria, consultada al abrir el paso 7
        self._update = None  # release más nueva conocida en esta apertura (o None)
        self._landing = None  # «/panel» si el enlace de esta apertura debe llevar al panel
        self.update_thread = None
        self._request = threading.local()  # .phone: this request came from the phone listener
        # Las publicaciones del panel leen la dependencia al correr: las pruebas pueden sustituirla.
        self.publisher = registrar.Publisher(lambda *a, **kw: self.deps["write_apply"](*a, **kw))

    # --- versión nueva --------------------------------------------------------------------
    def start_update_check(self):
        """Pregunta por una versión nueva en segundo plano: la primera página no espera."""
        self.update_thread = threading.Thread(target=self._check_update, daemon=True)
        self.update_thread.start()
        return self.update_thread

    def _check_update(self):
        from . import version
        try:
            release = self.deps["check_update"](version())
        except Exception as exc:  # nunca bloquea ni avisa; solo el tipo de error en el registro
            print(f"No se pudo comprobar si hay una versión nueva ({type(exc).__name__}).",
                  file=sys.stderr, flush=True)
            release = None
        with self._lock:
            self._update = release

    def _update_card(self, csrf):
        from . import version
        if self._on_phone():
            return ""  # updating is done on the PC
        with self._lock:
            release = self._update
        if release is None:
            return ""
        try:
            current = version()
        except OSError:
            current = "anterior"
        return (f'<div class="card msg"><h2>Hay una versión nueva: {esc(release.version)}</h2>'
                f'<p>Usted tiene la versión {esc(current)}. Al pulsar el botón se descarga el instalador, '
                'se comprueba que es el original, este asistente se cierra y se abre el instalador: pulse '
                '«Siguiente» hasta terminar. Sus datos, su configuración y WhisperX se conservan.</p>'
                f'<form method="post" action="/actualizar">{csrf_field(csrf)}'
                '<button>Actualizar ahora</button></form></div>')

    def update_now(self, csrf, **_):
        from .updates import UpdateError
        with self._lock:  # se toma una sola vez: un doble clic no descarga ni abre el instalador dos veces
            release, self._update = self._update, None
        if release is None:
            return Response(303, location="/")
        try:
            path = self.deps["download_update"](release)
            self.deps["launch_update"](path)
        except UpdateError as exc:
            with self._lock:  # se puede volver a intentar desde la tarjeta
                self._update = release
            return self.page_home(csrf, error=str(exc), status=400)
        body = notice("Se abrió el instalador de la versión nueva. Este asistente se cierra ahora; siga los "
                      "pasos del instalador (pulse «Siguiente») y al terminar Academic Manager se abre solo.")
        return Response(200, layout("Actualizando Academic Manager", body, back=False), shutdown=True)

    # --- estado -------------------------------------------------------------------------
    def runtime(self):
        value = paths.load_config().get("runtime")
        return Path(value) if value else None

    def _need_runtime(self):
        try:
            runtime = self.runtime()
        except paths.ConfigError as exc:
            raise StepError(str(exc)) from None
        if runtime is None or not runtime.is_dir():
            raise StepError(NEED_DATA)
        from canvas_sync.common import SyncError, runtime_root
        try:
            return runtime_root(runtime)
        except SyncError as exc:
            raise StepError(str(exc)) from None

    def status(self):
        """{paso: (hecho|falta|error, explicación)} leído del estado real."""
        try:
            config = paths.load_config()
        except paths.ConfigError as exc:
            return {key: ("error", str(exc)) for key, _, _ in STEPS}
        runtime = Path(config["runtime"]) if config.get("runtime") else None
        result = {"datos": self._status_data(runtime)}
        ready = result["datos"][0] == "hecho"
        result["equipo"] = (self._check[:2] if self._check else
                            ("falta", "Ábralo para comprobar este PC.") if ready else ("falta", NEED_DATA))
        result["canvas"] = self._status_canvas(runtime) if ready else ("falta", NEED_DATA)
        result["google"] = self._status_google(runtime) if ready else ("falta", NEED_DATA)
        result["drive"] = self._status_drive(config.get("drive_root"))
        result["materias"] = self._status_courses(runtime) if ready else ("falta", NEED_DATA)
        result["tarea"] = (self._task or ("falta", "Ábralo para comprobar si está activa.")) if ready else \
            ("falta", NEED_DATA)
        return result

    @staticmethod
    def _status_data(runtime):
        if runtime is None:
            return "falta", "Todavía no se eligió la carpeta donde Academic Manager guarda sus datos."
        if not runtime.is_dir():
            return "error", f"La carpeta {runtime} no existe. Elíjala de nuevo."
        return "hecho", f"Carpeta de datos: {runtime}"

    @staticmethod
    def _status_canvas(runtime):
        from canvas_sync import auth
        from canvas_sync.common import SyncError
        if not (runtime / "control" / "canvas-sync" / auth.FILE).is_file():
            return "falta", "Falta conectar Canvas con un token personal."
        try:
            expiry = auth.read_meta(runtime)
        except SyncError:
            return "hecho", "Token guardado (sin fecha de vencimiento registrada)."
        now = datetime.now().astimezone()
        when = expiry.strftime("%Y-%m-%d %H:%M")
        if expiry <= now:
            return "error", f"El token de Canvas venció el {when}. Cree uno nuevo y péguelo aquí."
        days = (expiry.date() - now.date()).days
        if days <= auth.WARNING_DAYS:
            return "hecho", f"Token guardado; vence el {when} (en {days} días): prepare uno nuevo."
        return "hecho", f"Token guardado; vence el {when}."

    @staticmethod
    def _drive_files(runtime):
        from capture.sources import auth
        folder = Path(runtime).resolve() / "control" / "drive-oauth"
        if not folder.is_dir():
            return None, None
        return auth.client_path(runtime), auth.token_path(runtime)

    @staticmethod
    def _can_publish(runtime):
        """Hay con qué publicar la base: cuenta de servicio o la sesión propia con permiso de escritura."""
        from capture.sources import auth
        if (Path(runtime) / "control" / "service-account-drive.json").exists():
            return True
        return (Path(runtime).resolve() / "control" / "drive-oauth").is_dir() and \
            auth.write_token_path(runtime).is_file()

    def _status_google(self, runtime):
        client, token = self._drive_files(runtime)
        if token is not None and token.is_file() and not self._can_publish(runtime):
            return "falta", ("Su sesión de Google solo permite leer Drive. Pulse «Volver a iniciar sesión» "
                             "para permitir también guardar la base de datos en su Drive.")
        if token is not None and token.is_file():
            when = datetime.fromtimestamp(token.stat().st_mtime)
            text = f"Sesión de Google iniciada el {when:%Y-%m-%d %H:%M}."
            if (datetime.now() - when).days >= RELOGIN_HINT_DAYS:
                text += " Si Capture dice que la autorización caducó, pulse «Volver a iniciar sesión»."
            return "hecho", text
        if client is not None and client.is_file():
            return "falta", "Archivo de acceso guardado; falta iniciar sesión con Google."
        return "falta", "Falta crear su acceso de Google y subir su archivo."

    @staticmethod
    def _status_drive(value):
        if not value:
            return "falta", "Falta indicar la carpeta de Academic Manager en Google Drive."
        if not Path(value).is_dir():
            return "error", f"La carpeta {value} no existe en este PC. ¿Está abierto Google Drive?"
        return "hecho", f"Carpeta de Drive: {value}"

    def _base(self, runtime):
        """Estado de la base nueva: «nueva», «a_medias» o «existente»."""
        from canvas_sync.common import SyncError
        try:
            return self.deps["base_status"](runtime)
        except SyncError as exc:
            raise StepError(str(exc)) from None

    def _status_courses(self, runtime):
        from canvas_sync.common import SyncError
        from canvas_sync.semester import upgrade_registry
        path = runtime / "control" / "canvas-sync" / "course-registry.json"
        try:
            base = self._base(runtime)
        except StepError as exc:
            return "error", str(exc)
        if base == "a_medias":
            return "falta", BASE_HALF
        if base == "nueva":
            return "falta", "Falta crear la base de datos de Academic Manager (se hace en este paso)."
        if not path.is_file():
            return "falta", "Falta elegir las materias de este semestre."
        try:
            registry = upgrade_registry(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError, SyncError):
            return "error", f"El registro de materias ({path.name}) está dañado y no se puede leer."
        if any(c.get("provenance") == PENDING for c in registry["courses"]):
            return "falta", PENDING_TEXT
        current = [c for c in registry["courses"] if c["semester"] == registry["active_semester"]]
        if not current:
            return "falta", "Falta elegir las materias de este semestre."
        return "hecho", f"{_count(len(current), 'materia', 'materias')} en el semestre {registry['active_semester']}."

    # --- despacho -----------------------------------------------------------------------
    def handle(self, method, path, form, files, csrf, *, phone=False):
        """phone=True: request from the phone listener (phone.py), shown without PC-only parts."""
        self._request.phone = phone
        try:
            return self._dispatch(method, path, form, files, csrf)
        finally:
            self._request.phone = False

    def _on_phone(self):
        return getattr(self._request, "phone", False)

    def _dispatch(self, method, path, form, files, csrf):
        routes = {("GET", "/"): self.entry, ("POST", "/cerrar"): self.close,
                  ("GET", "/panel"): self.panel_today, ("GET", "/panel/resumen"): self.panel_summary,
                  ("GET", "/panel/materias"): self.panel_subjects,
                  ("GET", "/panel/instrucciones"): self.panel_instructions,
                  ("POST", "/panel/sincronizar"): self.sync_now,
                  ("GET", "/panel/sincronizar"): self.sync_page,
                  ("POST", "/panel/registrar"): self.register_preview,
                  ("POST", "/panel/registrar/aprobar"): self.register_approve,
                  ("POST", "/actualizar"): self.update_now,
                  ("GET", "/paso/datos"): self.page_data, ("POST", "/paso/datos"): self.save_data,
                  ("GET", "/paso/equipo"): self.page_check,
                  ("POST", "/paso/equipo/whisperx"): self.install_whisperx,
                  ("GET", "/paso/canvas"): self.page_canvas, ("POST", "/paso/canvas"): self.save_canvas,
                  ("GET", "/paso/google"): self.page_google,
                  ("POST", "/paso/google/cliente"): self.save_client,
                  ("POST", "/paso/google/login"): self.google_login,
                  ("GET", "/paso/drive"): self.page_drive, ("POST", "/paso/drive"): self.save_drive,
                  ("GET", "/paso/materias"): self.page_courses,
                  ("POST", "/paso/materias/crear-base"): self.create_base,
                  ("POST", "/paso/materias/vista-previa"): self.preview_courses,
                  ("POST", "/paso/materias/confirmar"): self.confirm_courses,
                  ("POST", "/paso/materias/pendiente"): self.pending_courses,
                  ("POST", "/paso/materias/publicar"): self.publish_courses,
                  ("POST", "/paso/materias/indice"): self.retry_index,
                  ("GET", "/paso/tarea"): self.page_task, ("POST", "/paso/tarea"): self.save_task,
                  ("GET", "/celular"): self.page_phone, ("POST", "/celular/activar"): self.phone_enable,
                  ("POST", "/celular/iniciar"): self.phone_start, ("POST", "/celular/qr"): self.phone_qr,
                  ("POST", "/celular/quitar"): self.phone_revoke, ("POST", "/celular/pin"): self.phone_pin,
                  ("POST", "/celular/desactivar"): self.phone_disable}
        action = routes.get((method, path))
        if action is None and method == "GET" and path.startswith(SUBJECT_PAGE):
            action = functools.partial(self.panel_subject, subject=path[len(SUBJECT_PAGE):])
        if action is None and method == "GET" and path.startswith(PUBLISHING_PAGE):
            action = functools.partial(self.register_status, job=path[len(PUBLISHING_PAGE):])
        if action is None:
            return Response(404, layout("No encontrado", notice("Esta página no existe.", bad=True)))
        return action(form=form, files=files, csrf=csrf)

    def _done(self, location, message):
        with self._lock:
            self._flash = message
        return Response(303, location=location)

    def _take_flash(self):
        with self._lock:
            message, self._flash = self._flash, None
        return notice(message) if message else ""

    def _page(self, key, body, csrf, *, error=None, status=200):
        title = dict((k, t) for k, t, _ in STEPS)[key]
        state, detail = self.status()[key]
        head = (f'<div class="card"><p>{badge(state)} {esc(detail)}</p></div>'
                + self._take_flash() + (notice(error, bad=True) if error else ""))
        return Response(status, layout(title, head + body))

    def _fail(self, page, exc, **kw):
        return page(error=str(exc), status=400, **kw)

    # --- resumen y cierre ---------------------------------------------------------------
    def complete(self, steps=None):
        """Pasos datos, canvas, google, drive y materias hechos y la base igual a la publicada."""
        steps = steps or self.status()
        if any(steps[key][0] != "hecho" for key in COMPLETE_STEPS):
            return False
        try:
            self.deps["workbook_version"](self._need_runtime())
        except Exception as exc:  # noqa: BLE001 — si no se puede comprobar, se abre el asistente
            print(f"La base no coincide con la publicada ({type(exc).__name__}).", file=sys.stderr, flush=True)
            return False
        return True

    def land_on_panel(self):
        """La primera visita a «/» de esta apertura (tras el enlace con la clave) lleva al panel."""
        with self._lock:
            self._landing = "/panel"

    def entry(self, csrf, **kw):
        with self._lock:
            landing, self._landing = self._landing, None
        if landing:
            return Response(303, location=landing)
        return self.page_home(csrf, **kw)

    def page_home(self, csrf, error=None, status=200, **_):
        steps = self.status()
        rows = []
        for number, (key, title, link) in enumerate(STEPS, 1):
            state, detail = steps[key]
            rows.append(f'<div class="card step"><div><strong>{number}. {esc(title)}</strong> '
                        f'{badge(state)}<br><span class="muted">{esc(detail)}</span></div>'
                        f'<a href="{esc(link)}">Abrir</a></div>')
        done = self.complete(steps)
        ready = ('<div class="card msg"><p>La configuración está completa. '
                 '<a href="/panel">Abrir el panel del día a día</a></p></div>') if done else ""
        body = (self._update_card(csrf) + (notice(error, bad=True) if error else "") + ready
                + '<p>Este asistente prepara Academic Manager en este PC paso a paso. Puede cerrarlo '
                'cuando quiera: lo que ya hizo queda guardado y al volver verá en qué paso va.</p>'
                + self._take_flash() + "".join(rows) + self._phone_row()
                + f'<form method="post" action="/cerrar">{csrf_field(csrf)}'
                  '<button class="secondary">Cerrar el asistente</button></form>'
                + _version_line())
        return Response(status, layout("Configuración de Academic Manager", body, back=False,
                                       nav=panel.nav("configuracion") if done else ""))

    def close(self, **_):
        body = notice("Asistente cerrado. Puede cerrar esta pestaña.")
        return Response(200, layout("Academic Manager", body, back=False), shutdown=True)

    # --- 1. carpeta de datos ------------------------------------------------------------
    def page_data(self, csrf, value=None, error=None, status=200, **_):
        if value is None:
            try:
                current = paths.load_config().get("runtime")
            except paths.ConfigError:
                current = None
            value = current or paths.DEFAULT_RUNTIME
        warning = ""
        env = os.environ.get("ACADEMIC_MANAGER_DATA")
        if env:
            warning = notice(f"Aviso: este PC ya tiene indicada otra carpeta de datos ({env}) en su "
                             "configuración de Windows; Academic Manager usará esa antes que la elegida aquí.")
        body = (warning + '<div class="card"><p>Academic Manager guarda en esta carpeta sus datos: '
                'la base de datos, las autorizaciones cifradas y los archivos de trabajo. Le recomendamos '
                f'<strong>{esc(paths.DEFAULT_RUNTIME)}</strong>. Si la carpeta no existe, se crea.</p>'
                f'<form method="post" action="/paso/datos">{csrf_field(csrf)}'
                '<label for="ruta">Carpeta de datos</label>'
                f'<input type="text" id="ruta" name="ruta" value="{esc(value)}" required>'
                '<button>Guardar carpeta</button></form></div>')
        return self._page("datos", body, csrf, error=error, status=status)

    def save_data(self, form, csrf, **_):
        raw = _first(form, "ruta")
        try:
            paths.load_config()  # un archivo inválido se corrige antes de escribir nada
            folder = Path(os.path.normpath(raw)) if raw else Path()
            if not raw or not folder.is_absolute() or folder == Path(folder.anchor):
                raise StepError(f"«{raw}» no es una ruta completa de carpeta. Escriba la ruta completa, "
                                f"por ejemplo {paths.DEFAULT_RUNTIME}.")
            if inside_program(folder):
                raise StepError(INSIDE_PROGRAM.format(raw, paths.DEFAULT_RUNTIME))
            try:
                folder.mkdir(parents=True, exist_ok=True)
            except OSError:
                raise StepError(f"No se pudo crear la carpeta {raw}. Elija otra ubicación.") from None
            if folder.is_symlink() or not folder.is_dir():
                raise StepError(f"{raw} no es una carpeta normal. Elija otra ubicación.")
            paths.save_config(runtime=str(folder))
        except (StepError, paths.ConfigError) as exc:
            return self.page_data(csrf, value=raw, error=str(exc), status=400)
        return self._done("/paso/datos", "Carpeta de datos guardada.")

    # --- 2. comprobación del equipo -----------------------------------------------------
    def _run_check(self, runtime):
        """Comprobación de esta apertura; None si falló (el paso queda en «Error»)."""
        try:
            data, text = self.deps["check"](runtime)
            from academic_setup.check import HEADLINES
            self._check = ("hecho", HEADLINES.get(data.get("veredicto"), "Comprobación terminada."), text)
            return data
        except (OSError, ValueError, TypeError, KeyError, paths.ConfigError):
            self._check = ("error", "No se pudo comprobar el equipo. Vuelva a intentarlo más tarde.", None)
            return None

    def page_check(self, csrf, error=None, status=200, **_):
        try:
            runtime = self._need_runtime()
        except StepError as exc:
            return self._page("equipo", "", csrf, error=error or str(exc), status=status)
        importlib.invalidate_caches()  # WhisperX recién instalado por el proceso de fondo
        data = self._run_check(runtime)
        body = ('<div class="card"><p>La comprobación solo mira el PC y no cambia nada: dice si Capture '
                'puede transcribir las clases grabadas en este equipo. Instalar WhisperX, si el PC es apto, '
                'es un botón aparte más abajo.</p>'
                + (f"<pre>{esc(self._check[2])}</pre>" if self._check[2] else "")
                + '<p><a href="/paso/equipo">Comprobar de nuevo</a></p></div>'
                + (self._whisperx_card(runtime, data, csrf) if data else ""))
        return self._page("equipo", body, csrf, error=error, status=status)

    @staticmethod
    def _detected(data, name):
        return ((data.get("programas") or {}).get(name) or {}).get("valor") is True

    def _whisperx_installed(self, data):
        """WhisperX y FFmpeg (que se instala con él) detectados por la comprobación."""
        return self._detected(data, "whisperx") and self._detected(data, "ffmpeg")

    def _whisperx_card(self, runtime, data, csrf):
        """WhisperX según el veredicto: instalar, en curso, reintentar, instalado o no apto."""
        from academic_setup import whisperx
        verdict = data.get("veredicto")
        if verdict == "no_apto":
            return f'<div class="card"><h2>WhisperX</h2><p>{esc(NO_APTO)}</p></div>'
        if whisperx.plan(verdict) is None:
            return ""
        if self._whisperx_installed(data):
            return ('<div class="card"><h2>WhisperX</h2><p>WhisperX ya está instalado: Capture puede '
                    'transcribir clases grabadas en este PC.</p></div>')
        state = self.deps["whisperx_status"](runtime).get("estado")
        if state == "instalando":
            return ('<div class="card msg"><h2>WhisperX</h2><p>Instalando WhisperX… Puede seguir usando el '
                    'asistente o cerrarlo: la instalación continúa sola. Vuelva a abrir este paso para ver '
                    'cómo va.</p></div>')
        how = ("con la tarjeta gráfica (la descarga ocupa varios GB)" if verdict == "apto_gpu"
               else "con el procesador (la descarga ocupa alrededor de 1 GB)")
        label = "Reintentar la instalación"
        if self._detected(data, "whisperx"):
            intro = ("WhisperX ya está instalado, pero falta FFmpeg, el programa que lee el audio de las "
                     "grabaciones. Pulse el botón para completar la instalación; necesita internet.")
            label = "Completar la instalación"
        elif state == "instalado":
            intro = ("WhisperX se instaló, pero este PC todavía no lo encuentra. Pulse el botón para repararlo: "
                     "vuelve a comprobar lo instalado y completa lo que falte.")
        elif state in ("fallo", "interrumpida"):
            intro = ("La instalación anterior de WhisperX no terminó. Revise que el PC tenga internet y espacio "
                     "libre y vuelva a intentarlo: continúa con lo que ya se descargó.")
        else:
            intro = (f"Este PC puede transcribir clases grabadas {how}. Para eso hace falta instalar WhisperX "
                     "(y FFmpeg, que lee el audio); necesita internet y puede tardar bastante. Lo demás ya "
                     "funciona sin esperar.")
            label = "Instalar WhisperX"
        return (f'<div class="card"><h2>WhisperX</h2><p>{esc(intro)}</p>'
                f'<form method="post" action="/paso/equipo/whisperx">{csrf_field(csrf)}'
                f'<button>{esc(label)}</button></form></div>')

    def install_whisperx(self, csrf, **_):
        """El veredicto sale de una comprobación nueva, nunca del formulario."""
        from academic_setup import whisperx
        try:
            runtime = self._need_runtime()
            data = self._run_check(runtime)
            if data is None:
                raise StepError("No se pudo comprobar el equipo; WhisperX no se instaló. Vuelva a intentarlo.")
            verdict = data.get("veredicto")
            if whisperx.plan(verdict) is None:
                raise StepError(NO_APTO)
            if self._whisperx_installed(data):
                return self._done("/paso/equipo", "WhisperX ya está instalado.")
            self.deps["whisperx_start"](runtime, verdict)
        except StepError as exc:
            return self.page_check(csrf, error=str(exc), status=400)
        except (OSError, ValueError):
            return self.page_check(csrf, error="No se pudo empezar la instalación de WhisperX. Vuelva a "
                                               "intentarlo; si sigue igual, pida ayuda.", status=400)
        return self._done("/paso/equipo", WHISPERX_STARTED)

    # --- 3. Canvas ----------------------------------------------------------------------
    def page_canvas(self, csrf, error=None, status=200, **_):
        try:
            self._need_runtime()
        except StepError as exc:
            return self._page("canvas", "", csrf, error=error or str(exc), status=status)
        body = ('<div class="card"><h2>Cómo crear su token de Canvas</h2><ol>'
                '<li>Entre a Canvas con su usuario de la universidad.</li>'
                '<li>En el menú de la izquierda pulse <strong>Cuenta</strong> y luego '
                '<strong>Configuración</strong>.</li>'
                '<li>Baje hasta <strong>Integraciones aprobadas</strong> y pulse '
                '<strong>+ Nuevo token de acceso</strong>.</li>'
                '<li>En <strong>Propósito</strong> escriba «Academic Manager». En <strong>Fecha de '
                'vencimiento</strong> elija una fecha (máximo 90 días desde hoy) y una hora. Anótelas: '
                'las necesita abajo.</li>'
                '<li>Pulse <strong>Generar token</strong> y copie el texto largo que aparece. Canvas lo '
                'muestra una sola vez.</li>'
                '<li>Péguelo abajo con la misma fecha y hora de vencimiento y pulse Guardar.</li></ol>'
                '<p class="muted">El token se comprueba con Canvas y se guarda cifrado para su usuario de '
                'Windows. Nunca se muestra de nuevo ni se envía a otro sitio. Cuando venza, repita estos '
                'pasos.</p>'
                f'<form method="post" action="/paso/canvas" autocomplete="off">{csrf_field(csrf)}'
                '<label for="token">Token de Canvas</label>'
                '<input type="password" id="token" name="token" autocomplete="off" required>'
                '<label for="vence">Fecha y hora de vencimiento (las mismas que eligió en Canvas)</label>'
                '<input type="datetime-local" id="vence" name="vence" required>'
                '<button>Comprobar y guardar</button></form></div>')
        return self._page("canvas", body, csrf, error=error, status=status)

    def save_canvas(self, form, csrf, **_):
        from canvas_sync import auth
        from canvas_sync.common import SyncError
        token = (form.get("token") or [""])[0]  # sin strip: un token mal copiado se rechaza
        expiry = _first(form, "vence").replace("T", " ")[:16]
        try:
            runtime = self._need_runtime()
            auth.save_token(runtime, token, expiry, client_factory=self.deps["canvas_client"])
        except (StepError, SyncError) as exc:
            return self.page_canvas(csrf, error=plain(exc), status=400)
        finally:
            token = None
        return self._done("/paso/canvas", "Canvas conectado: el token se comprobó y quedó guardado cifrado.")

    # --- 4. Google Drive ----------------------------------------------------------------
    def page_google(self, csrf, error=None, status=200, **_):
        try:
            runtime = self._need_runtime()
        except StepError as exc:
            return self._page("google", "", csrf, error=error or str(exc), status=status)
        client, token = self._drive_files(runtime)
        has_client = client is not None and client.is_file()
        has_token = token is not None and token.is_file()
        guide = ('<div class="card"><h2>Crear su propio acceso de Google (una sola vez)</h2>'
                 '<p>Academic Manager lee sus archivos de Drive y guarda allí su base de datos con un <strong>archivo de acceso de Google</strong> propio (Google lo llama «ID de cliente de OAuth»). '
                 'Se crea gratis en Google Cloud con la misma cuenta que usa en Drive.</p><ol>'
                 '<li>Abra <strong>console.cloud.google.com</strong> e inicie sesión. Arriba, en el selector '
                 'de proyectos, pulse <strong>Proyecto nuevo</strong>, llámelo «Academic Manager» y pulse '
                 '<strong>Crear</strong>.</li>'
                 '<li>Con ese proyecto elegido, vaya a <strong>APIs y servicios → Biblioteca</strong>, busque '
                 '<strong>Google Drive API</strong> y pulse <strong>Habilitar</strong>.</li>'
                 '<li>Vaya a <strong>APIs y servicios → Pantalla de consentimiento de OAuth</strong> '
                 '(puede llamarse «Google Auth Platform») y pulse <strong>Comenzar</strong>. Escriba el nombre '
                 '«Academic Manager» y su correo. En <strong>Público</strong> elija <strong>Externo</strong>. '
                 'Acepte y pulse <strong>Crear</strong>.</li>'
                 '<li>En <strong>Público → Usuarios de prueba</strong> pulse <strong>Agregar usuarios</strong> '
                 'y añada su propio correo: usted es el único usuario de prueba.</li>'
                 '<li>Vaya a <strong>Clientes</strong> (o <strong>Credenciales → Crear credenciales → ID de '
                 'cliente de OAuth</strong>). En tipo de aplicación elija <strong>App de escritorio</strong>, '
                 'póngale un nombre y pulse <strong>Crear</strong>.</li>'
                 '<li>Pulse <strong>Descargar JSON</strong>. Se guarda un archivo que empieza por '
                 '«client_secret». Súbalo aquí abajo. No lo envíe por chat ni correo.</li></ol></div>')
        upload = ('<div class="card"><h2>Subir el archivo de acceso</h2>'
                  + ('<p>Ya hay un archivo guardado. Suba otro solo si creó un cliente nuevo.</p>'
                     if has_client else '')
                  + '<form method="post" action="/paso/google/cliente" enctype="multipart/form-data">'
                  f'{csrf_field(csrf)}<label for="cliente">Archivo descargado de Google Cloud</label>'
                  '<input type="file" id="cliente" name="cliente" accept=".json,application/json" required>'
                  '<button>Subir archivo</button></form></div>')
        login = ""
        if has_client:
            label = "Volver a iniciar sesión" if has_token else "Iniciar sesión con Google"
            login = ('<div class="card"><h2>Iniciar sesión</h2><p>Al pulsar el botón se abre Google en otra '
                     'pestaña. Elija su cuenta. Como la app es suya y está «en prueba», Google avisa que no '
                     'está verificada: pulse <strong>Continuar</strong>. Marque todos los permisos: ver, '
                     'descargar y guardar archivos de Drive (Academic Manager publica allí su base de datos). '
                     'Esta página espera hasta que termine (máximo 3 minutos).</p>'
                     f'<form method="post" action="/paso/google/login">{csrf_field(csrf)}'
                     f'<button>{esc(label)}</button></form></div>')
        caveat = ('<div class="card msg"><p><strong>Importante:</strong> mientras su app de Google esté en modo '
                  'de prueba, Google puede pedirle iniciar sesión otra vez cada 7 días aproximadamente. Cuando '
                  'Capture diga que la autorización caducó, abra este asistente y pulse «Volver a iniciar '
                  'sesión». No hace falta repetir lo de Google Cloud.</p></div>')
        return self._page("google", guide + upload + login + caveat, csrf, error=error, status=status)

    def save_client(self, files, csrf, **_):
        from capture.models import CaptureError
        from capture.sources import auth
        raw = files.get("cliente") or b""
        try:
            runtime = self._need_runtime()
            if not raw:
                raise StepError("Elija el archivo que descargó de Google Cloud.")
            auth.save_client(runtime, raw)
        except (StepError, CaptureError) as exc:
            return self.page_google(csrf, error=plain(exc), status=400)
        finally:
            raw = None
        return self._done("/paso/google", "Archivo de acceso guardado. Ahora inicie sesión con Google.")

    def google_login(self, csrf, **_):
        from capture.models import CaptureError
        try:
            runtime = self._need_runtime()
            client, _ = self._drive_files(runtime)
            if client is None or not client.is_file():
                raise StepError("Primero suba el archivo de acceso de Google.")
            self.deps["drive_login"](runtime, client)
        except (StepError, CaptureError) as exc:
            return self.page_google(csrf, error=plain(exc), status=400)
        return self._done("/paso/google", "Sesión de Google iniciada.")

    # --- 5. carpeta de Drive ------------------------------------------------------------
    def page_drive(self, csrf, value=None, error=None, status=200, **_):
        if value is None:
            try:
                value = paths.load_config().get("drive_root") or ""
            except paths.ConfigError:
                value = ""
        body = ('<div class="card"><p>Academic Manager guarda las clases y materiales en una carpeta de su '
                'Google Drive. Necesita <strong>Google Drive para escritorio</strong> instalado y con su '
                'cuenta abierta: así Drive aparece como una unidad, por ejemplo <strong>G:</strong>.</p><ol>'
                '<li>Abra el Explorador de archivos y entre a <strong>Google Drive → Mi unidad</strong>.</li>'
                '<li>Cree una carpeta llamada «Academic Manager» (puede estar dentro de otra, por ejemplo '
                '«Universidad»).</li>'
                '<li>Entre a esa carpeta, haga clic en la barra de direcciones y copie la ruta, por ejemplo '
                '<strong>G:\\Mi unidad\\Universidad\\Academic Manager</strong>. Péguela abajo.</li></ol>'
                f'<form method="post" action="/paso/drive">{csrf_field(csrf)}'
                '<label for="ruta">Carpeta de Academic Manager en Drive</label>'
                f'<input type="text" id="ruta" name="ruta" value="{esc(value)}" required>'
                '<button>Guardar carpeta</button></form></div>')
        return self._page("drive", body, csrf, error=error, status=status)

    def save_drive(self, form, csrf, **_):
        raw = _first(form, "ruta")
        try:
            paths.load_config()
            folder = Path(os.path.normpath(raw)) if raw else Path()
            if not raw or not folder.is_absolute():
                raise StepError(f"«{raw}» no es una ruta completa. Copie la ruta desde la barra de "
                                "direcciones del Explorador.")
            if inside_program(folder):
                raise StepError(INSIDE_PROGRAM.format(raw, "G:\\Mi unidad\\Academic Manager"))
            if not folder.is_dir():
                raise StepError(f"La carpeta {raw} no existe en este PC. Revise que Google Drive esté "
                                "abierto y que la carpeta esté creada.")
            paths.save_config(drive_root=str(folder))
        except (StepError, paths.ConfigError) as exc:
            return self.page_drive(csrf, value=raw, error=str(exc), status=400)
        return self._done("/paso/drive", "Carpeta de Drive guardada.")

    # --- 6. materias --------------------------------------------------------------------
    def _registered(self, runtime):
        path = runtime / "control" / "canvas-sync" / "course-registry.json"
        try:
            return {c["course_id"] for c in json.loads(path.read_text(encoding="utf-8"))["courses"]}
        except (OSError, ValueError, KeyError, TypeError):
            return set()

    def _live_courses(self, runtime):
        from canvas_sync.common import SyncError
        try:
            return self.deps["canvas_courses"](runtime)
        except SyncError as exc:
            raise StepError(f"No se pudieron leer sus materias de Canvas: {plain(exc)} "
                            "Revise el paso de Canvas.") from None

    def _base_page(self, runtime, base, csrf, error, status):
        """Primer paso de una persona nueva: crear la base vacía y publicarla en su Drive."""
        missing = []
        try:
            if not paths.load_config().get("drive_root"):
                missing.append("indique la carpeta de Drive (paso 5)")
        except paths.ConfigError as exc:
            missing.append(str(exc))
        if self.status()["google"][0] != "hecho":
            missing.append("inicie sesión con Google con permiso para guardar en Drive (paso 4)")
        half = base == "a_medias"
        text = ('<div class="card"><h2>Crear su base de datos</h2>'
                '<p>Su carpeta de datos todavía no tiene la base de datos de Academic Manager. Este paso la '
                'crea vacía (solo la estructura: materias, actividades, clases, recursos, semestres y '
                'entregables, sin datos) en su carpeta de datos y la publica por primera vez en su carpeta '
                'de Drive. Después elige sus materias aquí mismo.</p>'
                '<p>Si su carpeta de Drive ya tiene una base de Academic Manager, no se crea otra ni se '
                'cambia nada: el asistente se lo dice.</p>')
        if half:
            text += '<p>' + esc(BASE_HALF) + '</p>'
        if missing:
            text += '<p>Antes: ' + esc("; ".join(missing)) + '.</p></div>'
        else:
            label = "Retomar la creación" if half else "Crear la base de datos"
            text += (f'<form method="post" action="/paso/materias/crear-base">{csrf_field(csrf)}'
                     f'<button>{esc(label)}</button></form></div>')
        return self._page("materias", text, csrf, error=error, status=status)

    def create_base(self, csrf, **_):
        from canvas_sync.common import SyncError
        from capture.models import CaptureError
        try:
            runtime = self._need_runtime()
            if self._base(runtime) == "existente":
                raise StepError("Su carpeta de datos ya tiene la base de datos; no se creó otra.")
            drive_root = paths.load_config().get("drive_root")
            if not drive_root:
                raise StepError("Primero indique la carpeta de Drive (paso 5).")
            self.deps["create_base"](runtime, drive_root)
        except paths.ConfigError as exc:
            return self.page_courses(csrf, error=str(exc), status=400)
        except (StepError, SyncError, CaptureError, OSError) as exc:
            message = str(exc) if isinstance(exc, StepError) else plain(exc)
            return self.page_courses(csrf, error=f"No se creó la base: {message}", status=400)
        return self._done("/paso/materias", "Base de datos creada en su carpeta de datos y publicada en su "
                                            "Drive. Ahora elija sus materias.")

    def page_courses(self, csrf, error=None, status=200, **_):
        try:
            runtime = self._need_runtime()
            base = self._base(runtime)
            if base != "existente":
                return self._base_page(runtime, base, csrf, error, status)
            courses = self._live_courses(runtime)
        except StepError as exc:
            return self._page("materias", "", csrf, error=error or str(exc), status=status)
        known = self._registered(runtime)
        rows = []
        for c in courses:
            cid = c["course_id"]
            title = f'{esc(c["name"])} <span class="muted">{esc(c.get("course_code"))}</span>'
            if cid in known:
                rows.append(f"<tr><td></td><td>{title}</td><td class=\"muted\">Ya registrada</td></tr>")
                continue
            blocks = "".join(f'<label class="inline"><input type="checkbox" name="bloques-{cid}" '
                             f'value="{b}"> Bloque {b}</label>' for b in (1, 2))
            rows.append(f'<tr><td><input type="checkbox" name="curso" value="{cid}" '
                        f'id="c{cid}"></td><td><label for="c{cid}" style="font-weight:normal">{title}'
                        f'</label></td><td>{blocks}</td></tr>')
        table = ("<table><tr><th></th><th>Materia en Canvas</th><th>Bloque</th></tr>"
                 + "".join(rows) + "</table>") if rows else "<p>Canvas no muestra materias activas.</p>"
        pending = ""
        if self._has_pending(runtime):
            pending = ('<div class="card msg"><p>' + esc(PENDING_TEXT) + '</p>'
                       f'<form method="post" action="/paso/materias/pendiente">{csrf_field(csrf)}'
                       '<button>Revisar y publicar</button></form></div>')
        body = pending + ('<div class="card"><p>Estas son sus materias activas en Canvas. Marque las que quiere en '
                'Academic Manager y en qué bloque se dictan (cada semestre tiene dos bloques; si la materia '
                'dura todo el semestre, marque los dos).</p>'
                f'<form method="post" action="/paso/materias/vista-previa">{csrf_field(csrf)}'
                '<label for="semestre">Semestre (año-número, por ejemplo 2027-1)</label>'
                '<input type="text" id="semestre" name="semestre" required>'
                f'{table}<button>Ver resumen antes de guardar</button></form></div>')
        return self._page("materias", body, csrf, error=error, status=status)

    def _selection(self, form, courses):
        semester = _first(form, "semestre")
        if not SEMESTER.fullmatch(semester):
            raise StepError("Escriba el semestre como año-número, por ejemplo 2027-1.")
        raw = [v.strip() for v in form.get("curso") or [] if v.strip()]
        if not raw:
            raise StepError("Marque al menos una materia.")
        try:
            ids = [int(v) for v in raw]
        except ValueError:
            raise StepError("La selección de materias no es válida.") from None
        if len(ids) != len(set(ids)):
            raise StepError("La selección de materias no es válida.")
        visible = {c["course_id"]: c for c in courses}
        blocks = {}
        for cid in ids:
            if cid not in visible:
                raise StepError(f"La materia {cid} no aparece en su lista actual de Canvas.")
            try:
                chosen = sorted({int(v) for v in form.get(f"bloques-{cid}") or []})
            except ValueError:
                chosen = []
            if not chosen or any(b not in (1, 2) for b in chosen):
                raise StepError(f"Marque el bloque de «{visible[cid]['name']}».")
            blocks[cid] = chosen
        return semester, ids, blocks, visible

    def _ready_registry(self, runtime):
        """El registro existente; None cuando falta la base de datos (usuario nuevo)."""
        from academic_store.store import DB_NAME
        from canvas_sync.common import SyncError
        registry_file = runtime / "control" / "canvas-sync" / "course-registry.json"
        if not registry_file.is_file() and not (runtime / DB_NAME).is_file():
            return None
        if not registry_file.is_file() and self.deps["empty_base"](runtime):
            return {"courses": []}  # base nueva: el alta crea el registro con su primer semestre
        try:
            return self.deps["load_registry"](runtime)
        except ValueError:  # JSON dañado
            raise StepError(f"El registro de materias ({registry_file.name}) está dañado y no se puede "
                            "leer. Pida ayuda para revisarlo; no se cambió nada.") from None
        except SyncError as exc:
            raise StepError(f"No se puede preparar el alta todavía: {plain(exc)}") from None

    @staticmethod
    def _summary(semester, ids, blocks, visible):
        rows = "".join(f"<tr><td>{esc(visible[c]['name'])}</td><td>{esc(visible[c].get('course_code'))}"
                       f"</td><td>{esc(', '.join(map(str, blocks[c])))}</td></tr>" for c in ids)
        return (f"<p>Semestre <strong>{esc(semester)}</strong></p><table><tr><th>Materia</th>"
                f"<th>Código</th><th>Bloques</th></tr>{rows}</table>")

    @staticmethod
    def _hidden(semester, ids, blocks):
        fields = [f'<input type="hidden" name="semestre" value="{esc(semester)}">']
        for cid in ids:
            fields.append(f'<input type="hidden" name="curso" value="{esc(cid)}">')
            fields += [f'<input type="hidden" name="bloques-{esc(cid)}" value="{esc(b)}">'
                       for b in blocks[cid]]
        return "".join(fields)

    def preview_courses(self, form, csrf, **_):
        try:
            runtime = self._need_runtime()
            courses = self._live_courses(runtime)
            semester, ids, blocks, visible = self._selection(form, courses)
            registry = self._ready_registry(runtime)
            if registry is not None:
                known = {c["course_id"] for c in registry["courses"]}
                repeated = [visible[c]["name"] for c in ids if c in known]
                if repeated:
                    raise StepError(f"«{repeated[0]}» ya está registrada.")
        except StepError as exc:
            return self.page_courses(csrf, error=str(exc), status=400)
        summary = self._summary(semester, ids, blocks, visible)
        if registry is None:
            body = ('<div class="card"><h2>Resumen</h2>' + summary + '</div>'
                    + notice("Su carpeta de datos todavía no tiene la base de datos de Academic Manager, "
                             "así que estas materias no se pueden guardar aún. Este paso se completará "
                             "cuando la base de datos esté creada en su carpeta de datos y en su Drive; por "
                             "ahora no se cambió nada.", bad=True))
            return self._page("materias", body, csrf)
        body = ('<div class="card"><h2>Resumen</h2>' + summary
                + '<p>Al confirmar, estas materias se guardan en la carpeta de datos de este PC. Después verá '
                  'los cambios que se harían en la base de datos de Drive antes de publicarlos.</p>'
                  f'<form method="post" action="/paso/materias/confirmar">{csrf_field(csrf)}'
                  f'{self._hidden(semester, ids, blocks)}<button>Confirmar materias</button></form>'
                  '<p><a href="/paso/materias">Cambiar la selección</a></p></div>')
        return self._page("materias", body, csrf)

    def confirm_courses(self, form, csrf, **_):
        from canvas_sync.common import SyncError
        try:
            runtime = self._need_runtime()
            courses = self._live_courses(runtime)
            semester, ids, blocks, _ = self._selection(form, courses)
            if self._ready_registry(runtime) is None:
                raise StepError("Su carpeta de datos todavía no tiene la base de datos de Academic Manager.")
            try:
                added = self.deps["onboard"](runtime, semester, ids, courses,
                                             course_blocks=blocks, confirm=True)
            except SyncError as exc:
                raise StepError(plain(exc)) from None
        except StepError as exc:
            return self.page_courses(csrf, error=str(exc), status=400)
        saved = _count(len(added), "materia guardada", "materias guardadas")
        return self._publication_preview(runtime, csrf, f"{saved} en este PC.")

    def _publication_preview(self, runtime, csrf, done):
        from canvas_sync.common import SyncError
        from capture.models import CaptureError
        try:
            result = self.deps["publish_enrollment"](runtime, self.deps["load_registry"](runtime), dry_run=True)
        except (SyncError, CaptureError, OSError, ValueError) as exc:
            body = notice(f"{done} La publicación en la base de datos de Drive queda pendiente: {plain(exc)} "
                          "Puede retomarla desde este paso con «Revisar y publicar».")
            return self._page("materias", body, csrf)
        count = len(result["changes"])
        if not count:
            return self._page("materias", notice(f"{done} La base de datos ya estaba al día."), csrf)
        word = "cambio" if count == 1 else "cambios"
        body = (notice(done) + '<div class="card"><p>La base de datos de Drive recibiría '
                f'{count} {word} para registrar estas materias. Se publican una sola vez y se comprueban '
                'al terminar.</p>'
                f'<form method="post" action="/paso/materias/publicar">{csrf_field(csrf)}'
                '<button>Publicar en la base de datos</button></form>'
                '<p class="muted">Si no publica ahora, las materias quedan guardadas en este PC y puede '
                'publicarlas más tarde.</p></div>')
        return self._page("materias", body, csrf)

    @staticmethod
    def _has_pending(runtime):
        path = runtime / "control" / "canvas-sync" / "course-registry.json"
        try:
            courses = json.loads(path.read_text(encoding="utf-8"))["courses"]
            return any(isinstance(c, dict) and c.get("provenance") == PENDING for c in courses)
        except (OSError, ValueError, KeyError, TypeError):
            return False

    def pending_courses(self, csrf, **_):
        try:
            runtime = self._need_runtime()
            if self._ready_registry(runtime) is None or not self._has_pending(runtime):
                raise StepError("No hay materias pendientes de publicar.")
        except StepError as exc:
            return self.page_courses(csrf, error=str(exc), status=400)
        return self._publication_preview(runtime, csrf, "Materias guardadas en este PC.")

    def publish_courses(self, csrf, **_):
        from canvas_sync.common import SyncError
        from capture.models import CaptureError
        try:
            runtime = self._need_runtime()
            result = self.deps["publish_enrollment"](runtime, self.deps["load_registry"](runtime),
                                                     dry_run=False)
        except (StepError, SyncError, CaptureError, OSError, ValueError) as exc:
            return self._page("materias", "", csrf, error=f"No se publicó: {plain(exc)}", status=400)
        text = ("Materias publicadas en la base de datos de Drive." if result.get("mode") == "published"
                else "La base de datos ya estaba al día.")
        return self._page("materias", notice(text) + self._index(runtime, csrf), csrf)

    def _index(self, runtime, csrf):
        """Reconstruye el índice; si falla, la publicación queda y se ofrece reintentar."""
        try:
            self.deps["build_index"](runtime)
        except Exception:  # noqa: BLE001 — como `complete`: un fallo del índice no deshace la publicación
            return ('<div class="card msg"><p>' + esc(INDEX_WARNING) + '</p>'
                    f'<form method="post" action="/paso/materias/indice">{csrf_field(csrf)}'
                    '<button>Reintentar índice</button></form></div>')
        return notice("Índice de búsqueda de este PC actualizado.")

    def retry_index(self, csrf, **_):
        try:
            runtime = self._need_runtime()
        except StepError as exc:
            return self._page("materias", "", csrf, error=str(exc), status=400)
        return self._page("materias", self._index(runtime, csrf), csrf)

    # --- 7. sincronización diaria -------------------------------------------------------
    def _task_state(self):
        item = self.deps["task_status"]()
        state, detail = item.get("estado"), item.get("detalle") or ""
        if state == "ok":
            return "hecho", detail
        if state == "no_disponible":
            return "falta", "La sincronización diaria solo funciona en Windows."
        if detail.startswith("No existe la tarea"):
            return "falta", "Todavía no está activada en este PC."
        if "no se ejecutará" in detail:
            return "error", ("La sincronización diaria está desactivada en Windows. Pulse «Volver a activar» "
                             "para encenderla otra vez.")
        return "error", ("No se pudo comprobar la sincronización diaria en Windows. Vuelva a abrir este paso; "
                         "si sigue igual, pulse «Volver a activar».")

    def page_task(self, csrf, error=None, status=200, **_):
        try:
            self._need_runtime()
        except StepError as exc:
            return self._page("tarea", "", csrf, error=error or str(exc), status=status)
        self._task = self._task_state()
        label = ("Activar la sincronización diaria" if self._task[0] == "falta" else "Volver a activar")
        body = ('<div class="card"><p>Academic Manager puede revisar Canvas solo, dos veces al día '
                '(a las <strong>08:00</strong> y a las <strong>18:00</strong>): trae lo nuevo de sus materias, '
                'lo guarda en su base de datos y actualiza el índice de búsqueda. No necesita ChatGPT.</p>'
                '<p>Funciona con el PC encendido y su sesión de Windows iniciada; si el PC estaba apagado, lo '
                'hace en cuanto se encienda. Antes conecte Canvas (paso 3) y elija sus materias (paso 6).</p>'
                '<p class="muted">Cada ejecución deja un registro de lo que hizo dentro de su carpeta de datos.</p>'
                f'<form method="post" action="/paso/tarea">{csrf_field(csrf)}'
                f'<button>{esc(label)}</button></form></div>')
        return self._page("tarea", body, csrf, error=error, status=status)

    def save_task(self, csrf, **_):
        from .daily_task import TaskError
        try:
            self._need_runtime()
            self.deps["register_task"]()
        except (StepError, TaskError) as exc:
            return self.page_task(csrf, error=str(exc), status=400)
        self._task = None
        return self._done("/paso/tarea", "Sincronización diaria activada.")

    # --- panel del día a día (solo lectura) -------------------------------------------
    def _panel(self, key, title, body, csrf, status=200, **head):
        return Response(status, layout(title, self._update_card(csrf) + body, back=False,
                                       nav=panel.nav(key, phone=self._on_phone()), wide=True, **head))

    def _panel_runtime(self):
        try:
            return self._need_runtime()
        except StepError:
            return None

    def _panel_setup(self, key, title, csrf):
        link = "" if self._on_phone() else '<p><a href="/">Ir a «Configuración»</a></p>'
        return self._panel(key, title, notice(panel.NEED_SETUP, bad=True) + link, csrf)

    def _problem_card(self, reading, csrf=None):
        return panel.problem_card(reading, csrf, phone=self._on_phone())

    def _read(self, dep, *args):
        return panel.read(self.deps[dep], *args)

    def _canvas(self, runtime):
        """Datos de Canvas (notas, comentarios, mensajes sin leer); si fallan, la página sigue sin ellos."""
        return self._read("panel_canvas", runtime).value or {}

    @staticmethod
    def _new_grades(extras, names, tones):
        rows = "".join(
            f'<li><div class="grow">{esc(r["titulo"])}<small>{panel.subject_link(r["materia_id"], names, tones)}'
            f' · {esc(panel.day_text(r["fecha"]) or "sin fecha")}</small></div>'
            f'{panel.chip(panel.score_text(r["nota"], r.get("de")), "k-ok")}</li>'
            for r in extras.get("notas_nuevas") or [])
        if not rows:
            return ""
        return ('<div class="card"><h2>Notas nuevas en Canvas</h2><p class="muted">Calificadas en los últimos '
                f'{canvas_days()} días, según la última sincronización.</p><ul class="rows">{rows}</ul></div>')

    def panel_today(self, csrf, **_):
        runtime = self._panel_runtime()
        if runtime is None:
            return self._panel_setup("hoy", "Hoy", csrf)
        today = self._read("panel_today", runtime)
        summary = self._read("panel_summary", runtime)
        names, tones = panel.names_of(summary.value), panel.tones_of(summary.value)
        body = "".join(notice(text) for text in (panel.warnings(summary.value) if summary.value else []))
        if summary.value:
            body += self._sync_card(runtime, csrf, summary.value.get("sistema"), compact=True)
        subtitle = ""
        if today.problem:
            body += self._problem_card(today)
        else:
            day = today.value["fecha"]
            subtitle = (panel.day_text(day) or "").capitalize()
            items = today.value["items"]
            spread = [panel.bucket(i, day) for i in items]
            body += panel.stats((spread.count("atrasado"), "Atrasado", "k-bad"),
                                (sum(b in ("hoy", "manana", "semana") for b in spread), "Esta semana", "k-info"),
                                (spread.count("despues"), "Más adelante", "k-task"),
                                (spread.count("sin"), "Sin fecha", "k-todo"))
            if names:
                body += self._new_grades(self._canvas(runtime), names, tones)
            if items:
                body += ('<p class="muted">Agrupado por fecha; dentro de cada grupo, primero lo más urgente. '
                         'Para marcar algo como hecho, use los botones de cada tarjeta: antes de guardar verá una vista '
                         'previa.</p>'
                         + panel.items_list(items, names, tones, day, grouped=True,
                                            actions=forms.item_actions(csrf, "/panel")))
            else:
                days = panel.count(today.value.get("dias", 14), "día", "días")
                body += panel.empty("Todo al día", f"No hay nada pendiente para los próximos {days}.")
        return self._panel("hoy", "Hoy", body, csrf, subtitle=subtitle)

    def panel_summary(self, csrf, **_):
        runtime = self._panel_runtime()
        if runtime is None:
            return self._panel_setup("resumen", "Resumen", csrf)
        summary = self._read("panel_summary", runtime)
        if summary.problem:
            return self._panel("resumen", "Resumen", self._problem_card(summary), csrf)
        data = summary.value
        day = data["fecha"]
        names, tones = panel.names_of(data), panel.tones_of(data)
        term = data["semestre"]
        block = f" · bloque {term['bloque_activo']}" if term.get("bloque_activo") else ""
        kinds = data["urgente"].get("resumen") or {}
        hero = (f'<section class="card hero"><div><p class="eyebrow">Semestre activo</p>'
                f'<h2>{esc(term.get("nombre") or term["semestre_id"])}</h2>'
                f'<p class="muted">{esc(term["semestre_id"])}{esc(block)} · '
                f'{esc(panel.count(len(data["materias"]), "materia", "materias"))}</p></div>'
                + panel.stats((kinds.get("actividad_vencida", 0), "Vencidas", "k-bad"),
                              (kinds.get("actividad_proxima", 0), "Por entregar", "k-info"),
                              (kinds.get("tarea", 0) + kinds.get("tarea_sin_fecha", 0), "Tareas", "k-task"),
                              (kinds.get("clase_sin_procesar", 0), "Clases sin procesar", "k-todo"))
                + "</section>")
        urgent = data["urgente"]["items"]
        main = ('<h2>Lo más urgente</h2>'
                + (panel.items_list(urgent, names, tones, day) if urgent
                   else panel.empty("Nada urgente", "No hay entregas ni tareas que apremien."))
                + '<a class="more" href="/panel">Ver todo en «Hoy» →</a>')
        extras = self._canvas(runtime)
        subjects = "".join(
            f'<li>{panel.avatar(names[m["materia_id"]], tones[m["materia_id"]])}<div class="grow">'
            f'<a href="/panel/materia/{esc(m["materia_id"])}">{esc(names[m["materia_id"]])}</a>'
            f'<small>Última clase: {esc(_last_class(m.get("ultima_clase"), day))}</small></div>'
            f'{panel.canvas_chips(panel.canvas_subject(extras, m["materia_id"]))}</li>'
            for m in data["materias"])
        notes = "".join(f'<li class="{esc(tones.get(n["materia_id"], ""))}">{esc(n["texto"])}'
                        f'<small>{esc(names.get(n["materia_id"], n["materia_id"]))} · '
                        f'{esc(panel.day_text(n.get("fecha")) or "sin fecha")}</small></li>'
                        for n in data["reciente"]["notas"])
        tasks = "".join(f'<li><div class="grow">{esc(t["titulo"])}<small>'
                        + esc(f"fecha límite {panel.day_text(t['fecha_limite'])}" if t.get("fecha_limite")
                              else "sin fecha límite") + "</small></div></li>"
                        for t in data["reciente"]["tareas"])
        side = (f'<div class="card"><h2>Sus materias</h2><ul class="rows">{subjects}</ul></div>'
                + (f'<div class="card"><h2>Notas recientes</h2><ul class="timeline">{notes}</ul></div>'
                   if notes else "")
                + (f'<div class="card"><h2>Tareas abiertas</h2><ul class="rows">{tasks}</ul></div>' if tasks else "")
                + self._sync_card(runtime, csrf, data["sistema"]))
        body = ("".join(notice(text) for text in panel.warnings(data)) + hero
                + f'<div class="grid"><div>{main}</div><aside>{side}</aside></div>')
        return self._panel("resumen", "Resumen", body, csrf, subtitle=f"Hoy es {panel.day_text(day)}.")

    def panel_subjects(self, csrf, **_):
        runtime = self._panel_runtime()
        if runtime is None:
            return self._panel_setup("materias", "Materias", csrf)
        summary = self._read("panel_summary", runtime)
        if summary.problem:
            return self._panel("materias", "Materias", self._problem_card(summary), csrf)
        data = summary.value
        names, tones = panel.names_of(data), panel.tones_of(data)
        today = self._read("panel_today", runtime)  # solo para contar pendientes; si falla, no se cuentan
        items = today.value["items"] if today.value else None
        extras = self._canvas(runtime)
        cards = []
        for m in data["materias"]:
            mid = m["materia_id"]
            chips = ""
            if items is not None:
                mine = [i for i in items if i.get("materia_id") == mid and i["tipo"] != "materia_descuidada"]
                late = sum(i["tipo"] == "actividad_vencida" for i in mine)
                chips = (panel.chip(panel.count(late, "vencida", "vencidas"), "k-bad") if late else "") + (
                    panel.chip(panel.count(len(mine), "pendiente", "pendientes"), "k-info") if mine
                    else panel.chip("Nada pendiente", "k-ok"))
            chips += panel.canvas_chips(panel.canvas_subject(extras, mid))
            cards.append(f'<a class="card subject-card {esc(tones[mid])}" href="/panel/materia/{esc(mid)}">'
                         f'<div class="head">{panel.avatar(names[mid], tones[mid])}<div><h3>{esc(names[mid])}</h3>'
                         f'<span class="muted">{esc(mid)}</span></div></div>'
                         f'<span class="muted">Última clase: {esc(_last_class(m.get("ultima_clase"), data["fecha"]))}'
                         f'</span><div class="foot">{chips}</div></a>')
        count = panel.count(len(data["materias"]), "materia", "materias")
        return self._panel("materias", "Materias", f'<div class="cards">{"".join(cards)}</div>', csrf,
                           subtitle=f"{count} en el semestre {data['semestre']['semestre_id']}.")

    def panel_subject(self, csrf, subject, **_):
        runtime = self._panel_runtime()
        if runtime is None:
            return self._panel_setup("materias", "Materia", csrf)
        summary = self._read("panel_summary", runtime)
        if summary.problem:
            return self._panel("materias", "Materia", self._problem_card(summary), csrf)
        names, tones = panel.names_of(summary.value), panel.tones_of(summary.value)
        if subject not in names:  # solo materias del semestre activo; nada de la ruta llega a la página
            body = notice("Esa materia no está en el semestre activo.", bad=True) + \
                '<p><a href="/panel/materias">Ver sus materias</a></p>'
            return self._panel("materias", "Materia no encontrada", body, csrf, status=404)
        title = names[subject]
        brief = self._read("panel_brief", runtime, subject)
        if brief.problem:
            return self._panel("materias", title, self._problem_card(brief), csrf)
        data = brief.value
        day = data["fecha"]
        pending = data["pendientes"]["items"]
        graded = [r for r in data["entregas"] if r.get("nota") not in (None, "")]
        state_kind = {"pendiente": "k-todo", "en_progreso": "k-info", "entregada": "k-task", "calificada": "k-ok"}
        back = SUBJECT_PAGE + subject
        grades = "".join(
            f'<li><div class="grow">{esc(r.get("titulo") or r["actividad_id"])}'
            + (f'<small>{esc(r["comentario"])}</small>' if r.get("comentario") else "")
            + forms.grade_form(csrf, r["actividad_id"], r.get("titulo") or r["actividad_id"], back, current=r,
                               ident=f'entrega-{r["actividad_id"]}', summary="Cambiar")
            + "</div>" + panel.chip(panel.TRACK.get(r.get("estado"), r.get("estado") or "sin estado"),
                                    state_kind.get(r.get("estado"), ""))
            + (f' {panel.chip(_grade_label(r))}' if _grade_label(r) else "") + "</li>"
            for r in data["entregas"])
        tasks = "".join(f'<li><div class="grow">{esc(t["titulo"])}<small>'
                        + esc(f"fecha límite {panel.day_text(t['fecha_limite'])}" if t.get("fecha_limite")
                              else "sin fecha límite") + "</small>"
                        + forms.task_buttons(csrf, t["tarea_id"], t["titulo"], back, state=t["estado"],
                                             ident=f'abierta-{t["tarea_id"]}') + "</div>"
                        + panel.chip(panel.TASK.get(t["estado"], t["estado"]), "k-task") + "</li>"
                        for t in data["tareas"])
        tone = tones[subject]
        canvas = panel.canvas_subject(self._canvas(runtime), subject)
        feedback = canvas.get("retroalimentacion") or []
        unread = canvas.get("sin_leer")
        inbox = (notice(f'Tiene {panel.count(unread, "mensaje sin leer", "mensajes sin leer")} de esta materia '
                        'en Canvas.') + f'<p><a href="{esc(CANVAS_INBOX)}" target="_blank" rel="noopener">'
                 'Abrir la bandeja de Canvas</a></p>') if unread else ""

        def timeline(rows):
            return "".join(f'<li class="{esc(tone)}">{esc(r["texto"])}<small>'
                           f'{esc(panel.utc_day_text(r.get("creado_utc")) or "sin fecha")}</small></li>' for r in rows)
        decisions, classes = timeline(data["decisiones"]), timeline(data["resumenes_clase"])
        main = ("<h2>Pendientes y fechas</h2>"
                + (panel.items_list(pending, names, tones, day, actions=forms.item_actions(csrf, back))
                   if pending
                   else panel.empty("Nada pendiente", "Esta materia está al día."))
                + (f'<div class="card"><h2>Entregas y notas</h2><ul class="rows">{grades}</ul></div>' if grades else "")
                + ('<div class="card"><h2>Comentarios del profesor</h2><p class="muted">De Canvas, según la última '
                   'sincronización.</p>' + panel.feedback_list(feedback) + "</div>" if feedback else "")
                + '<div class="card"><h2>Copiar contexto</h2><p>Copie este texto y péguelo al empezar el chat del '
                  'Project de esta materia en ChatGPT.</p>'
                + panel.copy_box(f"contexto-{subject}", "Contexto de la materia", panel.brief_text(data, canvas))
                + "</div>")
        side = ('<div class="card"><h2>Nueva tarea</h2>' + forms.new_task_form(csrf, subject, back) + "</div>"
                + '<div class="card"><h2>Nueva decisión o nota</h2>'
                + forms.new_note_form(csrf, subject, back) + "</div>"
                + (f'<div class="card"><h2>Tareas abiertas</h2><ul class="rows">{tasks}</ul></div>' if tasks else "")
                + (f'<div class="card"><h2>Decisiones vigentes</h2><ul class="timeline">{decisions}</ul></div>'
                   if decisions else "")
                + (f'<div class="card"><h2>Resúmenes de clase</h2><ul class="timeline">{classes}</ul></div>'
                   if classes else ""))
        body = (panel.stats((len(pending), "Pendientes", "k-bad" if any(
                                i["tipo"] == "actividad_vencida" for i in pending) else "k-info"),
                            (len(data["entregas"]), "Entregas", "k-task"),
                            (len(graded), "Con nota", ""),
                            (len(data["tareas"]), "Tareas abiertas", "k-todo"),
                            *([(panel.percent_text(canvas["nota"]), "Nota en Canvas", "k-ok")]
                              if canvas.get("nota") is not None else []))
                + inbox + f'<div class="grid"><div>{main}</div><aside>{side}</aside></div>')
        subtitle = (f'{subject} · Semestre {data["materia"].get("semestre_id") or ""} · última clase: '
                    f'{panel.day_text(data["materia"].get("ultima_clase")) or "sin clases"}')
        return self._panel("materias", title, body, csrf, subtitle=subtitle,
                           icon=panel.avatar(title, tone, big=True))

    def panel_instructions(self, csrf, **_):
        title = "Instrucciones de los Projects"
        runtime = self._panel_runtime()
        if runtime is None:
            return self._panel_setup("instrucciones", title, csrf)
        files = self._read("panel_instructions", runtime)
        if files.problem:
            return self._panel("instrucciones", title, self._problem_card(files, csrf), csrf)
        summary = self._read("panel_summary", runtime).value
        names, tones = panel.names_of(summary), panel.tones_of(summary)
        boxes = []
        for name in sorted(files.value, key=lambda n: (n != "coordinador.md", n)):
            if name == "coordinador.md":
                ident, label, tone, note = "coordinador", "Coordinador", "", "Project general"
            else:
                ident = name.removeprefix("materia-").removesuffix(".md")
                label, tone, note = names.get(ident, ident), tones.get(ident, ""), ident
            boxes.append(f'<div class="card"><div class="page-head">{panel.avatar(label, tone)}'
                         f'<div><h2 style="margin:0">{esc(label)}</h2><span class="muted">{esc(note)}</span></div></div>'
                         + panel.copy_box(f"instrucciones-{ident}", "Copiar instrucciones", files.value[name])
                         + "</div>")
        body = f'<div class="cards wide-cards">{"".join(boxes)}</div>'
        return self._panel("instrucciones", title, body, csrf,
                           subtitle="Cada cuadro tiene las instrucciones de un Project de ChatGPT. Cópielas en el "
                                    "campo «Instrucciones» del Project con el mismo nombre.")


    # --- sincronizar ahora (la misma rutina que la tarea diaria) ------------------------
    def _sync_running(self, runtime):
        try:
            return self.deps["sync_status"](runtime).get("estado") == "sincronizando"
        except Exception as exc:  # noqa: BLE001 — si no se sabe, se ofrece el botón
            print(f"Sincronización manual: estado ilegible ({type(exc).__name__}).", file=sys.stderr, flush=True)
            return False

    def _sync_card(self, runtime, csrf, system, *, compact=False):
        """Última sincronización y el botón «Sincronizar ahora» (o el enlace al progreso); no en el celular."""
        if self._on_phone():
            return ""
        last = (system or {}).get("ultimo_canvas_sync") or {}
        state = {"ok": " ok", "error": " bad"}.get(last.get("status"), "")
        if self._sync_running(runtime):
            action = '<a class="more" href="/panel/sincronizar">Sincronizando… ver progreso →</a>'
        else:
            action = (f'<form method="post" action="/panel/sincronizar" class="inline-form">{csrf_field(csrf)}'
                      '<button class="small">Sincronizar ahora</button></form>')
        line = (f'<div class="sync{state}"><span class="dot"></span><p>{esc(_sync_text(system))}</p></div>')
        if compact:
            return f'<div class="card sync-bar">{line}{action}</div>'
        return (f'<div class="card"><h2>Sincronización con Canvas</h2>{line}'
                f'<div class="actions">{action}</div></div>')

    def sync_now(self, csrf, **_):
        from .manual_sync import SyncUnavailable
        runtime = self._panel_runtime()
        if runtime is None:
            return self._register_problem(panel.NEED_SETUP, "/", csrf)
        try:
            self.deps["sync_start"](runtime)
        except SyncUnavailable as exc:
            return self._register_problem(str(exc), "/panel", csrf)
        return Response(303, location="/panel/sincronizar")

    def sync_page(self, csrf, **_):
        runtime = self._panel_runtime()
        if runtime is None:
            return self._panel_setup("hoy", "Sincronizar con Canvas", csrf)
        state = self.deps["sync_status"](runtime)
        back = '<p><a href="/panel">← Volver a «Hoy»</a></p>'
        if state.get("estado") == "sincronizando":
            body = ('<div class="card msg" role="status"><p><span class="spinner" aria-hidden="true"></span>'
                    'Sincronizando con Canvas… Trae actividades, notas y material, publica la base en Drive y '
                    'actualiza el índice. Suele tardar unos minutos; esta página se actualiza sola y puede '
                    'cerrarla: la sincronización sigue.</p></div>' + back)
            return self._register_page("Sincronizando…", body, csrf, head='<meta http-equiv="refresh" content="3">')
        if state.get("estado") == "listo":
            body = f'<div class="card msg ok" role="status"><p>{esc(state.get("mensaje"))}</p></div>' + back
            return self._register_page("Listo", body, csrf)
        if state.get("estado") in ("error", "interrumpida"):
            return self._register_page("No se pudo sincronizar", notice(state.get("mensaje") or "", bad=True)
                                       + back, csrf)
        body = ('<p>Trae de Canvas lo nuevo (actividades, notas, material), lo publica en Drive y actualiza el '
                'índice, igual que la sincronización programada.</p>'
                f'<form method="post" action="/panel/sincronizar">{csrf_field(csrf)}'
                '<button>Sincronizar ahora</button></form>' + back)
        return self._register_page("Sincronizar con Canvas", body, csrf)

    # --- registrar desde el panel (mismo contrato que el chat) -------------------------------
    def _register_page(self, title, body, csrf, status=200, head=""):
        return Response(status, layout(title, self._update_card(csrf) + body, back=False,
                                       nav=panel.nav(None, phone=self._on_phone()), head=head))

    def _register_problem(self, text, back, csrf, status=400):
        body = (notice(text, bad=True)
                + f'<p><a href="{esc(back)}">← Volver</a></p>')
        return self._register_page("No se pudo registrar", body, csrf, status=status)

    def _register_summary(self, runtime, form, action, request):
        names = panel.names_of(self._read("panel_summary", runtime).value)
        label = registrar._first(form, "etiqueta")[:registrar.MAX_TITLE] or None
        return registrar.describe(action, request, label=label, subject=names.get(request.get("materia_id")),
                                  day_text=panel.day_text)

    @staticmethod
    def _summary_html(rows):
        return ('<dl class="summary-list">'
                + "".join(f"<dt>{esc(k)}</dt><dd>{esc(v)}</dd>" for k, v in rows) + "</dl>")

    def register_preview(self, form, csrf, **_):
        back = registrar.back_link(form)
        runtime = self._panel_runtime()
        if runtime is None:
            return self._register_problem(panel.NEED_SETUP, "/", csrf)
        try:
            action, request = registrar.request_from(form)
        except registrar.FormError as exc:
            return self._register_problem(str(exc), back, csrf)
        try:
            plan = self.deps["write_preview"](runtime, action, request)
        except Exception as exc:  # noqa: BLE001 — se muestra un aviso sencillo, nunca el error
            registrar.log_failure(exc, "en la vista previa")
            return self._register_problem(registrar.problem(exc), back, csrf)
        rows = self._register_summary(runtime, form, action, request)
        if plan.get("already_applied"):
            body = (f'<div class="card">{self._summary_html(rows)}</div>'
                    + notice("Esto ya está registrado así: no hace falta publicar nada.")
                    + f'<p><a href="{esc(back)}">← Volver</a></p>')
            return self._register_page("Ya estaba registrado", body, csrf)
        hidden = "".join(f'<input type="hidden" name="{esc(k)}" value="{esc(registrar._first(form, k))}">'
                         for k in ("accion", "volver", "etiqueta", *registrar.FIELDS[action])
                         if registrar._first(form, k))
        body = ('<p class="muted">Revise lo que se va a guardar. Al aprobar, se escribe en su base y se publica '
                'en Drive; ChatGPT lo verá en la próxima consulta.</p>'
                f'<div class="card">{self._summary_html(rows)}'
                f'<form method="post" action="/panel/registrar/aprobar">{csrf_field(csrf)}{hidden}'
                f'<input type="hidden" name="plan_id" value="{esc(plan["plan_id"])}">'
                '<div class="actions"><button>Aprobar y publicar</button>'
                f'<a href="{esc(back)}">Cancelar</a></div></form></div>')
        return self._register_page("Revisar antes de publicar", body, csrf)

    def register_approve(self, form, csrf, **_):
        back = registrar.back_link(form)
        runtime = self._panel_runtime()
        if runtime is None:
            return self._register_problem(panel.NEED_SETUP, "/", csrf)
        plan_id = registrar._first(form, "plan_id")
        try:
            action, request = registrar.request_from(form)
            if not re.fullmatch(r"[0-9a-f]{64}", plan_id):
                raise registrar.FormError(registrar.MESSAGES["PLAN_CHANGED"])
            rows = self._register_summary(runtime, form, action, request)
            job = self.publisher.start(runtime, action, request, plan_id, rows, back)
        except registrar.FormError as exc:
            return self._register_problem(str(exc), back, csrf)
        return Response(303, location=PUBLISHING_PAGE + job)

    def register_status(self, csrf, job, **_):
        current = self.publisher.get(job)
        if current is None:
            return self._register_problem("Esa publicación ya no está en la lista. Revise el panel para ver si "
                                          "quedó registrada.", "/panel", csrf, status=404)
        details = f'<div class="card">{self._summary_html(current.summary)}</div>'
        back = f'<p><a href="{esc(current.back)}">← Volver</a></p>'
        if current.state == "publicando":
            body = ('<div class="card msg" role="status"><p><span class="spinner" aria-hidden="true"></span>'
                    'Publicando en Drive… Esta página se actualiza sola; puede tardar hasta un par de minutos '
                    'si la base está ocupada. No hace falta pulsar nada más.</p></div>' + details)
            return self._register_page("Publicando…", body, csrf, head='<meta http-equiv="refresh" content="2">')
        if current.state == "listo":
            body = (f'<div class="card msg ok" role="status"><p>{esc(current.message)}</p>'
                    + "".join(f"<p>{esc(n)}</p>" for n in current.notes) + "</div>" + details + back)
            return self._register_page("Listo", body, csrf)
        return self._register_page("No se pudo publicar", notice(current.message, bad=True) + details + back, csrf)

    # --- acceso desde el celular (opcional; docs/42-acceso-celular.md) ---------------------
    def data_folder(self):
        """Carpeta de datos lista, o None (entonces no hay acceso desde el celular en esta apertura)."""
        return self._panel_runtime()

    def _phone(self):
        return self.deps.get("phone")  # PhoneController, puesto por __main__ si hay carpeta de datos

    def _phone_row(self):
        controller = self._phone()
        state = ("Activado" if controller.access.is_active() else "Apagado") if controller else "Apagado"
        return ('<div class="card step"><div><strong>Acceso desde el celular</strong> '
                f'<span class="muted">(opcional) · {esc(state)}</span><br><span class="muted">Ver el panel y '
                'registrar desde el celular, en el mismo Wi-Fi que este PC.</span></div>'
                '<a href="/celular">Abrir</a></div>')

    def _phone_layout(self, body, csrf, error=None, status=200):
        head = self._take_flash() + (notice(error, bad=True) if error else "")
        return Response(status, layout("Acceso desde el celular", head + body))

    def page_phone(self, csrf, error=None, status=200, **_):
        controller = self._phone()
        if controller is None:
            return self._phone_layout(notice(PHONE_NO_DATA, bad=True), csrf, error, status)
        if not controller.access.is_active():
            body = (f'<div class="card">{PHONE_INTRO}'
                    f'<form method="post" action="/celular/activar">{csrf_field(csrf)}{_pin_fields()}'
                    '<button>Activar</button></form></div>' + PHONE_SAFETY)
            return self._phone_layout(body, csrf, error, status)
        state = controller.status()
        if state["escuchando"]:
            where = (f'<div class="card"><h2>Activado</h2><p>Dirección para el celular: '
                     f'<strong>{esc(state["direccion"])}</strong></p><p>Para autorizar un celular, muestre el '
                     'código QR y escanéelo con la cámara del celular; después escriba el PIN en el celular.</p>'
                     f'<form method="post" action="/celular/qr">{csrf_field(csrf)}'
                     '<button>Mostrar código QR</button></form></div>')
        else:
            problem = state["problema"] or "El acceso desde el celular no está escuchando."
            where = (f'<div class="card msg bad"><p>{esc(problem)}</p>'
                     f'<form method="post" action="/celular/iniciar">{csrf_field(csrf)}'
                     '<button>Volver a intentar</button></form></div>')
        body = (where + self._phone_devices(controller, csrf)
                + f'<div class="card"><h2>Cambiar PIN</h2><p>Los celulares tendrán que escribir el PIN nuevo.</p>'
                  f'<form method="post" action="/celular/pin">{csrf_field(csrf)}{_pin_fields()}'
                  '<button>Cambiar PIN</button></form></div>'
                + f'<div class="card"><h2>Desactivar</h2><p>El PC deja de escuchar la red y ningún celular '
                  'puede entrar. Los celulares autorizados se conservan para cuando lo vuelva a activar.</p>'
                  f'<form method="post" action="/celular/desactivar">{csrf_field(csrf)}'
                  '<button class="secondary">Desactivar</button></form></div>' + PHONE_SAFETY)
        return self._phone_layout(body, csrf, error, status)

    @staticmethod
    def _phone_devices(controller, csrf):
        rows = "".join(
            f'<li><div class="grow">{esc(d["nombre"])}<small>autorizado el {esc(_moment_text(d["creado"]))} · '
            f'visto por última vez el {esc(_moment_text(d["visto"]))}</small></div>'
            f'<form method="post" action="/celular/quitar" class="inline-form">{csrf_field(csrf)}'
            f'<input type="hidden" name="dispositivo" value="{esc(d["id"])}">'
            '<button class="small secondary">Quitar</button></form></li>'
            for d in controller.access.devices())
        listing = f'<ul class="rows">{rows}</ul>' if rows else '<p class="muted">Todavía no hay celulares autorizados.</p>'
        return f'<div class="card"><h2>Celulares autorizados</h2>{listing}</div>'

    def _phone_action(self, csrf, action, message):
        """Corre action(controller); un PhoneError vuelve a la página con su mensaje sencillo."""
        from .phone import PhoneError
        controller = self._phone()
        if controller is None:
            return self.page_phone(csrf, status=400)
        try:
            problem = action(controller)
        except PhoneError as exc:
            return self.page_phone(csrf, error=str(exc), status=400)
        if problem:
            return self.page_phone(csrf, error=problem, status=400)
        return self._done("/celular", message)

    @staticmethod
    def _new_pin(form):
        from .phone import PhoneError
        pin, again = _first(form, "pin"), _first(form, "pin2")
        if pin != again:
            raise PhoneError("Los dos PIN no coinciden. Escríbalo otra vez en los dos campos.")
        return pin

    def phone_enable(self, form, csrf, **_):
        def enable(controller):
            controller.access.enable(self._new_pin(form))
            return controller.start()
        return self._phone_action(csrf, enable, "Acceso desde el celular activado.")

    def phone_start(self, csrf, **_):
        return self._phone_action(csrf, lambda c: c.start(), "El acceso desde el celular está escuchando.")

    def phone_pin(self, form, csrf, **_):
        return self._phone_action(csrf, lambda c: c.access.change_pin(self._new_pin(form)),
                                  "PIN cambiado. Los celulares deben escribir el PIN nuevo.")

    def phone_revoke(self, form, csrf, **_):
        return self._phone_action(csrf, lambda c: c.access.revoke(_first(form, "dispositivo")) and None,
                                  "Se quitó el celular: ya no puede entrar.")

    def phone_disable(self, csrf, **_):
        def disable(controller):
            controller.access.disable()
            controller.stop()
        return self._phone_action(csrf, disable, "Acceso desde el celular desactivado.")

    def phone_qr(self, csrf, **_):
        from . import qr
        from .phone import PhoneError
        controller = self._phone()
        if controller is None:
            return self.page_phone(csrf, status=400)
        try:
            url = controller.pairing_url(controller.access.new_pairing())
        except PhoneError as exc:
            return self.page_phone(csrf, error=str(exc), status=400)
        if url is None:
            return self.page_phone(csrf, error=PHONE_NO_NETWORK, status=400)
        # El código solo va en esta página (dentro del QR o como texto); el registro guarda la ruta.
        try:
            picture = f'<div class="qr">{qr.svg(url)}</div>'
        except qr.QRUnavailable as exc:
            picture = (notice(str(exc), bad=True) + '<p>Escriba esta dirección en el navegador del celular:</p>'
                       f'<pre>{esc(url)}</pre>')
        body = (f'<div class="card">{picture}<p><strong>Código válido 5 minutos, un solo uso.</strong> '
                'Escanéelo con la cámara del celular (conectado al mismo Wi-Fi que este PC) y después escriba '
                'el PIN en el celular. Si vence, vuelva y genere otro.</p>'
                '<p><a href="/celular">← Volver a «Acceso desde el celular»</a></p></div>')
        return Response(200, layout("Código QR para el celular", body, back=False))


def _pin_fields():
    return ('<label for="pin">PIN (de 6 a 12 números)</label><input type="password" id="pin" name="pin" '
            'inputmode="numeric" autocomplete="new-password" maxlength="12" required>'
            '<label for="pin2">Repita el PIN</label><input type="password" id="pin2" name="pin2" '
            'inputmode="numeric" autocomplete="new-password" maxlength="12" required>')


def _moment_text(stamp):
    """Marca de tiempo (segundos) → 'lunes 2 de noviembre de 2026 a las 18:05', hora de Colombia."""
    try:
        moment = datetime.fromtimestamp(int(stamp), panel.LOCAL_ZONE)
    except (TypeError, ValueError, OverflowError, OSError):
        return "fecha desconocida"
    return f"{panel.day_text(moment.date().isoformat())} a las {moment:%H:%M}"


PHONE_NO_DATA = ("El acceso desde el celular necesita la carpeta de datos. " + NEED_DATA
                 + " Después cierre Academic Manager y vuelva a abrirlo.")
PHONE_NO_NETWORK = ("El PC no está conectado a una red privada (como el Wi-Fi de casa). "
                    "Conéctelo y vuelva a intentarlo.")
PHONE_INTRO = ('<p>Permite abrir el panel (Hoy, Resumen, Materias e Instrucciones) y registrar entregas, notas '
               'y tareas desde su celular, conectado al <strong>mismo Wi-Fi</strong> que este PC. El PC debe '
               'estar encendido y con Academic Manager abierto; al cerrarlo, el celular deja de entrar. La '
               'configuración se cambia solo aquí, en el PC.</p><p>Elija un PIN: el celular lo pedirá para '
               'entrar. Luego autorice cada celular con un código QR.</p>')
PHONE_SAFETY = ('<div class="card msg"><h2>Seguridad</h2><p>Dentro del Wi-Fi la conexión no está cifrada '
                '(es http, no https). Úselo solo en su casa o en redes de su confianza; nunca en un Wi-Fi '
                'público ni en el de la universidad.</p><p>La primera vez, Windows puede preguntar si permite '
                'a Python usar la red: permítalo solo en <strong>redes privadas</strong>, nunca en redes '
                'públicas.</p></div>')


def _last_class(value, today):
    """'hace 3 días (miércoles 28 de octubre de 2026)' o 'sin clases'."""
    text = panel.day_text(value)
    if not text:
        return "sin clases"
    rel = panel.relative(value, today)
    return f"{rel} ({text})" if rel else text


def canvas_days():
    from academic_access.canvas import NEW_DAYS
    return NEW_DAYS


def _grade_label(row):
    parts = []
    if row.get("nota") not in (None, ""):
        parts.append(f"nota {panel.number_text(row['nota'])}")
    if row.get("puntos") not in (None, ""):
        parts.append(f"{panel.number_text(row['puntos'])} puntos")
    return " · ".join(parts)


def _sync_text(system):
    last = (system or {}).get("ultimo_canvas_sync") or {}
    stamp = last.get("timestamp_local")
    if last.get("status") not in ("ok", "error") or not isinstance(stamp, str) or not panel.day_text(stamp[:10]):
        return "Todavía no hay registro de la sincronización con Canvas en este PC."
    when = panel.day_text(stamp[:10]) + (f" a las {stamp[11:16]}" if len(stamp) >= 16 else "")
    result = "sin errores" if last["status"] == "ok" else "con errores"
    return f"Última sincronización: {when} ({result})."


def _version_line():
    from . import version
    try:
        return f'<p class="muted">Versión {esc(version())}</p>'
    except OSError:
        return ""
