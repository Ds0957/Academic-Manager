"""Escrituras desde el panel con el mismo contrato que el chat (`academic_operations.chat`).

Cada registro pasa por `chat.preview` (solo lee la base local y fija un `plan_id`) y, tras la
aprobación explícita, por `chat.apply`, que vuelve a calcular el plan, publica en Drive y
reconstruye el índice. La publicación corre en segundo plano: la página «Publicando…» se recarga
sola (sin scripts) hasta que termina. Solo hay una publicación a la vez y aprobar otra vez el
mismo plan mientras se publica no publica dos veces. Ningún texto de error del contrato llega a la página: se
traduce por su código y el registro de la ventana solo guarda el tipo de error.
"""
import re
import secrets
import sys
import threading
from dataclasses import dataclass, field

ACTIONS = ("track", "task", "task-status", "note")
FIELDS = {"track": ("actividad_id", "estado", "nota", "puntos", "comentario"),
          "task": ("titulo", "materia_id", "fecha_limite", "detalle"),
          "task-status": ("tarea_id", "estado"),
          "note": ("materia_id", "tipo", "texto")}
TRACK_STATES = {"pendiente": "pendiente", "en_progreso": "en progreso", "entregada": "entregada",
                "calificada": "calificada"}
TASK_STATES = {"pendiente": "pendiente", "en_progreso": "en progreso", "hecha": "hecha",
               "descartada": "descartada"}
NOTE_TYPES = {"decision": "Decisión", "nota": "Nota", "aclaracion": "Aclaración"}
MAX_TITLE, MAX_TEXT = 200, 4000
DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
ID = re.compile(r"[A-Za-z0-9_-]{1,80}")
BACK = re.compile(r"/panel(/[A-Za-z0-9_-]+)*")
KEEP_JOBS = 20

MESSAGES = {
    "NOT_FOUND": ("Lo que intenta registrar ya no está en su base (puede que la sincronización con Canvas "
                  "lo haya cambiado). Vuelva al panel, recargue la página e inténtelo otra vez."),
    "PLAN_CHANGED": ("Sus datos cambiaron desde la vista previa (por ejemplo, con la sincronización de "
                     "Canvas). Vuelva a abrir el formulario y revise la vista previa nueva antes de aprobar."),
    "STORE_BUSY": ("La base de datos está ocupada con otra actualización (por ejemplo, la sincronización "
                   "de Canvas o un registro desde el chat). Espere un momento e inténtelo otra vez."),
    "STORE_UNAVAILABLE": ("La base de datos de este PC no está lista para escribir. Revise «Configuración» "
                          "y, si sigue igual, pida ayuda."),
    "PUBLISH_FAILED": ("No se pudo publicar en Drive. Revise su conexión a internet y que Google Drive esté "
                       "abierto, e inténtelo otra vez."),
}
MESSAGES["CONFLICT"] = MESSAGES["PLAN_CHANGED"]
INVALID = "Algún dato del formulario no es válido. Revíselo e inténtelo otra vez."
UNKNOWN = ("No se pudo completar el registro. Inténtelo otra vez en un momento; si sigue igual, revise "
           "«Configuración» o pida ayuda.")
UNCERTAIN = ("No se sabe si el cambio llegó a publicarse: algo falló a mitad de la publicación. No lo repita "
             "todavía. Abra «Configuración» → «Materias» para revisar el estado de la base o pida ayuda.")
INDEX_PENDING = ("El índice de búsqueda de este PC no se pudo actualizar, así que ChatGPT puede tardar en "
                 "verlo; se actualizará con la sincronización diaria.")
ANOTHER = "Ya hay otra publicación en curso. Espere a que termine e inténtelo otra vez."


class FormError(Exception):
    """Dato del formulario rechazado antes de llegar al contrato; el texto ya es para la persona."""


def _first(form, key):
    value = (form.get(key) or [""])[0]
    return value.strip() if isinstance(value, str) else ""


def back_link(form):
    """Página a la que vuelve «Cancelar»: solo rutas del panel, nunca una dirección externa."""
    value = _first(form, "volver")
    return value if BACK.fullmatch(value) else "/panel"


def _number(value, what):
    try:
        number = float(value.replace(",", "."))
    except ValueError:
        raise FormError(f"{what} debe ser un número, por ejemplo 4.5.") from None
    if number != number or number in (float("inf"), float("-inf")) or number < 0:
        raise FormError(f"{what} debe ser un número de cero o más.")
    return number


def request_from(form):
    """Formulario → (acción, petición del contrato). Valida con mensajes sencillos."""
    action = _first(form, "accion")
    if action not in ACTIONS:
        raise FormError("No se reconoce qué quiere registrar. Vuelva al panel e inténtelo otra vez.")
    request = {key: _first(form, key) for key in FIELDS[action] if _first(form, key)}
    for key in ("actividad_id", "tarea_id", "materia_id"):
        if key in request and not ID.fullmatch(request[key]):
            raise FormError("No se reconoce qué quiere registrar. Vuelva al panel e inténtelo otra vez.")
    if action == "track":
        if request.get("estado") not in TRACK_STATES:
            raise FormError("Elija el estado de la entrega.")
        for key, what in (("nota", "La nota"), ("puntos", "Los puntos")):
            if key in request:
                request[key] = _number(request[key], what)
        if "actividad_id" not in request:
            raise FormError("No se reconoce la actividad. Vuelva al panel e inténtelo otra vez.")
    elif action == "task":
        if "titulo" not in request:
            raise FormError("Escriba el título de la tarea.")
        if "fecha_limite" in request and not DATE.fullmatch(request["fecha_limite"]):
            raise FormError("La fecha límite no es válida. Elíjala en el calendario.")
    elif action == "task-status":
        if request.get("estado") not in TASK_STATES or "tarea_id" not in request:
            raise FormError("Elija el nuevo estado de la tarea.")
    else:
        if request.get("tipo") not in NOTE_TYPES:
            raise FormError("Elija si es una decisión, una nota o una aclaración.")
        if "texto" not in request:
            raise FormError("Escriba el texto que quiere guardar.")
        if "materia_id" not in request:
            raise FormError("No se reconoce la materia. Vuelva al panel e inténtelo otra vez.")
    if len(request.get("titulo", "")) > MAX_TITLE:
        raise FormError(f"El título es demasiado largo (máximo {MAX_TITLE} caracteres).")
    for key in ("texto", "detalle", "comentario"):
        if len(request.get(key, "")) > MAX_TEXT:
            raise FormError(f"El texto es demasiado largo (máximo {MAX_TEXT} caracteres).")
    return action, request


def _amount(value):
    return str(int(value)) if float(value).is_integer() else str(value)


def describe(action, request, *, label=None, subject=None, day_text=str):
    """Qué se va a guardar, en filas (dato, valor) para la vista previa y la página de publicación."""
    if action == "track":
        rows = [("Actividad", label or request["actividad_id"]),
                ("Estado", TRACK_STATES[request["estado"]]),
                ("Nota", _amount(request["nota"]) if "nota" in request else "sin cambios"),
                ("Puntos", _amount(request["puntos"]) if "puntos" in request else "sin cambios")]
        if request.get("comentario"):
            rows.append(("Comentario", request["comentario"]))
        return rows
    if action == "task":
        rows = [("Tarea nueva", request["titulo"]), ("Materia", subject or "Sin materia"),
                ("Fecha límite", day_text(request["fecha_limite"]) if "fecha_limite" in request
                 else "Sin fecha límite")]
        return rows + ([("Detalle", request["detalle"])] if request.get("detalle") else [])
    if action == "task-status":
        return [("Tarea", label or request["tarea_id"]), ("Nuevo estado", TASK_STATES[request["estado"]])]
    return [(NOTE_TYPES[request["tipo"]], request["texto"]), ("Materia", subject or request["materia_id"])]


def problem(exc):
    """Mensaje sencillo para un fallo del contrato o de la publicación; nunca su texto."""
    if getattr(exc, "published", None) == "incierto":
        return UNCERTAIN
    code = getattr(exc, "code", None)
    if code in MESSAGES:
        return MESSAGES[code]
    return INVALID if code == "INVALID_ARGUMENT" else UNKNOWN


def log_failure(exc, when):
    code = getattr(exc, "code", None)
    print(f"Registro desde el panel: falló {when} ({type(exc).__name__}{f', {code}' if code else ''}).",
          file=sys.stderr, flush=True)


@dataclass
class Job:
    ident: str
    plan_id: str
    summary: list
    back: str
    state: str = "publicando"   # publicando | listo | error
    message: str = ""
    notes: list = field(default_factory=list)


class Publisher:
    """Publicaciones en segundo plano: una a la vez; el mismo plan en curso no se publica dos veces."""

    def __init__(self, apply):
        self._apply = apply
        self._lock = threading.Lock()
        self._jobs = {}

    def start(self, runtime, action, request, plan_id, summary, back):
        with self._lock:
            # Solo se agrupa lo que sigue en curso (doble clic o recarga). El plan_id depende del
            # contenido: volver a un estado anterior repite un plan ya publicado y debe aplicarse.
            running = [j for j in self._jobs.values() if j.state == "publicando"]
            same = [j for j in running if j.plan_id == plan_id]
            if same:
                return same[-1].ident
            if running:
                raise FormError(ANOTHER)
            job = Job(secrets.token_urlsafe(12), plan_id, summary, back)
            self._jobs[job.ident] = job
            while len(self._jobs) > KEEP_JOBS:
                oldest = next(k for k, j in self._jobs.items() if j.state != "publicando")
                del self._jobs[oldest]
        try:
            threading.Thread(target=self._run, args=(job, runtime, action, request), daemon=True,
                             name="registro-panel").start()
        except Exception as exc:  # noqa: BLE001 — sin hilo no hay publicación: queda como fallo
            log_failure(exc, "al empezar la publicación")
            self._finish(job, "error", UNKNOWN, [])
        return job.ident

    def _finish(self, job, state, message, notes):
        with self._lock:
            job.state, job.message, job.notes = state, message, notes

    def _run(self, job, runtime, action, request):
        state, message, notes = "error", UNKNOWN, []
        try:
            outcome = self._apply(runtime, action, request, plan_id=job.plan_id, approved=True)
        except Exception as exc:  # noqa: BLE001 — todo fallo termina en un aviso sencillo
            log_failure(exc, "al publicar")
            message = problem(exc)
        else:
            state = "listo"
            message = ("Ya estaba registrado: no hizo falta cambiar nada." if outcome.get("already_applied")
                       else "Listo: quedó guardado y publicado en Drive.")
            notes = [] if outcome.get("index_refreshed", True) else [INDEX_PENDING]
        finally:  # incluso ante un fallo inesperado, la publicación deja de estar «en curso»
            self._finish(job, state, message, notes)

    def get(self, ident):
        with self._lock:
            job = self._jobs.get(ident)
            return None if job is None else Job(job.ident, job.plan_id, list(job.summary), job.back,
                                                job.state, job.message, list(job.notes))
