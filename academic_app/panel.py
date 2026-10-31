"""Panel del día a día: solo lectura, con los mismos lectores que usa el chat.

Llama en el mismo proceso a `academic_access` (`today`, `summary`, `subject_brief`) y a
`academic_projects.render_all`; nunca a `system` (Drive, tareas de Windows) ni a las CLI que
escriben archivos. Un fallo de un lector se convierte en un aviso en lenguaje sencillo: la
página nunca muestra el texto del error y el registro solo guarda el tipo de error.
"""
import sqlite3
import sys
from dataclasses import dataclass
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from .pages import esc

LOCAL_ZONE = ZoneInfo("America/Bogota")
DAYS = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")
MONTHS = ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre",
          "octubre", "noviembre", "diciembre")
TYPES = {"actividad_vencida": "Actividad vencida", "actividad_proxima": "Actividad por entregar",
         "tarea": "Tarea personal", "tarea_sin_fecha": "Tarea sin fecha",
         "clase_sin_procesar": "Clase sin procesar", "materia_descuidada": "Materia sin movimiento"}
TRACK = {"pendiente": "pendiente", "en_progreso": "en progreso", "entregada": "entregada",
         "calificada": "calificada"}
TASK = {"pendiente": "pendiente", "en_progreso": "en progreso", "hecha": "hecha", "descartada": "descartada"}

CONFIG = "Abra «Configuración» y revise el paso «Materias»."
NO_DATA = ("Todavía no se pueden mostrar sus materias: la base de datos de este PC no está lista o no "
           "coincide con la publicada en Drive. " + CONFIG)
BUSY = ("La base de datos se está actualizando en este momento (por ejemplo, con la sincronización de "
        "Canvas). Espere un momento y recargue la página.")
NO_SEMESTER = ("Su base de datos no tiene un semestre activo con materias. Abra «Configuración» → «Materias» "
               "y elija las materias de este semestre.")
STALE = ("El índice de búsqueda de este PC está desactualizado, así que no se pueden preparar las "
         "instrucciones de los Projects. Actualícelo con el botón de abajo o en «Configuración» → «Materias».")
NO_FILE = ("No se pudo leer un archivo de sus datos. Revise que la carpeta de datos exista y que Google "
           "Drive esté abierto, y recargue la página.")
BROKEN = ("Los datos de sus materias no se pudieron leer. " + CONFIG + " Si sigue igual, pida ayuda.")
NEED_SETUP = "Primero termine la configuración de Academic Manager en «Configuración»."


@dataclass
class Reading:
    value: object = None
    problem: str = None
    stale_index: bool = False


def _problem(exc):
    """Mensaje para la persona según el tipo de fallo; el texto del error nunca se muestra."""
    from academic_access.core import AccessError
    from canvas_sync.common import StoreBusy, SyncError
    if isinstance(exc, AccessError):
        if exc.code == "INDEX_STALE":
            return STALE
        if exc.code == "INVALID_SOURCE" and "semestre activo" in str(exc):
            return NO_SEMESTER
        return NO_DATA if exc.code == "SOURCE_UNAVAILABLE" else BROKEN
    if isinstance(exc, StoreBusy):
        return BUSY
    if isinstance(exc, SyncError):
        return NO_DATA
    if isinstance(exc, OSError):
        return NO_FILE
    if isinstance(exc, StopIteration) or (
            type(exc) is ValueError and "semestre activo" in str(exc)):  # render_all sin materias
        return NO_SEMESTER
    return BROKEN  # StoreError, ValueError, KeyError, sqlite3.Error y cualquier otro dato ilegible


def read(reader, *args, **kw):
    """Llama a un lector; un fallo conocido queda como aviso sencillo y no rompe la página."""
    from academic_access.core import AccessError
    from canvas_sync.common import SyncError
    try:
        return Reading(reader(*args, **kw))
    except (AccessError, SyncError, OSError, ValueError, KeyError, StopIteration, sqlite3.Error) as exc:
        print(f"Panel: no se pudo leer ({type(exc).__name__}).", file=sys.stderr, flush=True)
        stale = isinstance(exc, AccessError) and exc.code == "INDEX_STALE"
        return Reading(problem=_problem(exc), stale_index=stale)


# --- fechas y textos -------------------------------------------------------------------
def count(number, singular, plural):
    return f"{number} {singular if number == 1 else plural}"


def day_text(value):
    """'2026-11-03' → 'martes 3 de noviembre de 2026'; vacío → None."""
    if not value:
        return None
    try:
        day = date.fromisoformat(str(value)[:10])
    except ValueError:
        return str(value)
    return f"{DAYS[day.weekday()]} {day.day} de {MONTHS[day.month - 1]} de {day.year}"


def utc_day_text(stamp):
    """Marca UTC de las tablas propias → día en la hora de Colombia."""
    try:
        moment = datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None
    return day_text(moment.astimezone(LOCAL_ZONE).date().isoformat())


def _number(value):
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def grade_text(row):
    parts = [TRACK.get(row.get("estado"), row.get("estado") or "sin estado")]
    if row.get("nota") not in (None, ""):
        parts.append(f"nota {_number(row['nota'])}")
    if row.get("puntos") not in (None, ""):
        parts.append(f"{_number(row['puntos'])} puntos")
    return ", ".join(parts)


def _item_line(item, names):
    subject = names.get(item.get("materia_id")) or item.get("materia_id")
    pieces = [f"[{TYPES.get(item['tipo'], 'Pendiente')}] {item.get('titulo') or 'Sin título'}"]
    if subject and item["tipo"] != "materia_descuidada":
        pieces.append(subject)
    if item.get("fecha"):
        pieces.append(day_text(item["fecha"]))
    if item.get("motivo"):
        pieces.append(item["motivo"])
    return "- " + " — ".join(pieces)


def brief_text(brief):
    """El resumen de una materia en texto plano, listo para pegar en el chat de su Project."""
    subject = brief["materia"]
    name = subject.get("nombre") or subject["materia_id"]
    term = subject.get("semestre_id") or "sin semestre"
    lines = [f"Contexto de la materia: {name} ({subject['materia_id']})",
             f"Semestre: {term}" + ("" if subject.get("semestre_activo") else " (no es el semestre activo)"),
             f"Fecha de hoy: {day_text(brief['fecha'])} (hora de Colombia)"]
    if subject.get("ultima_clase"):
        lines.append(f"Última clase: {day_text(subject['ultima_clase'])}")
    names = {subject["materia_id"]: name}
    sections = [
        ("Pendientes", [_item_line(i, names) for i in brief["pendientes"]["items"]]),
        ("Entregas y notas", [f"- {r.get('titulo') or r['actividad_id']}: {grade_text(r)}"
                              + (f" — {r['comentario']}" if r.get("comentario") else "")
                              for r in brief["entregas"]]),
        ("Tareas abiertas", [f"- {r['titulo']} — " + (f"fecha límite {day_text(r['fecha_limite'])}"
                                                      if r.get("fecha_limite") else "sin fecha límite")
                             + f" — {TASK.get(r['estado'], r['estado'])}" for r in brief["tareas"]]),
        ("Decisiones vigentes", [f"- {utc_day_text(r.get('creado_utc')) or 'sin fecha'}: {r['texto']}"
                                 for r in brief["decisiones"]]),
        ("Resúmenes de clase recientes", [f"- {utc_day_text(r.get('creado_utc')) or 'sin fecha'}: {r['texto']}"
                                          for r in brief["resumenes_clase"]]),
    ]
    for title, rows in sections:
        if rows:
            lines += ["", f"{title}:", *rows]
    return "\n".join(lines) + "\n"


def warnings(summary):
    """Avisos del sistema dichos para la persona (sin comandos ni errores internos)."""
    system = summary.get("sistema") or {}
    day = date.fromisoformat(summary["fecha"])
    result = []
    history = system.get("canvas_sync_historial") or {}
    try:  # el historial lo escribe la tarea diaria; una fecha rara no rompe la página
        last_day = date.fromisoformat(str(history.get("ultimo_intento") or "")[:10])
    except ValueError:
        last_day = None
    last = history.get("ultimo_intento")
    if last_day and (day - last_day).days >= 2:
        result.append(f"La sincronización con Canvas no se ejecuta desde el {day_text(last)}: lo que ve aquí "
                      "puede no estar al día. Revise «Configuración» → «Sincronización diaria».")
    elif (history.get("fallos_seguidos") or 0) >= 2:
        since = day_text(history.get("fallando_desde")) or "hace varios días"
        result.append(f"La sincronización con Canvas está fallando desde el {since} "
                      f"({count(history['fallos_seguidos'], 'intento', 'intentos')} seguidos): lo que ve aquí "
                      "puede no estar al día. Revise «Configuración» → «Canvas».")
    token = system.get("token_canvas") or {}
    if token.get("aviso"):
        days = token.get("dias_restantes")
        if isinstance(days, int) and days > 0 and "vencido" not in token["aviso"]:
            result.append(f"Su token de Canvas vence en {count(days, 'día', 'días')}. Prepare uno nuevo en "
                          "«Configuración» → «Canvas».")
        else:
            result.append("Su token de Canvas venció. Cree uno nuevo y péguelo en «Configuración» → «Canvas».")
    if (system.get("indice") or {}).get("fresh") is False:
        result.append("El índice de búsqueda de este PC está desactualizado: ChatGPT puede no ver lo último. "
                      "Se actualiza con la sincronización diaria.")
    return result


# --- HTML ------------------------------------------------------------------------------
NAV = (("hoy", "Hoy", "/panel"), ("resumen", "Resumen", "/panel/resumen"),
       ("materias", "Materias", "/panel/materias"), ("instrucciones", "Instrucciones", "/panel/instrucciones"),
       ("configuracion", "Configuración", "/"))


def nav(current):
    links = []
    for key, title, href in NAV:
        mark = ' aria-current="page"' if key == current else ""
        links.append(f'<a href="{esc(href)}"{mark}>{esc(title)}</a>')
    return '<nav class="panel-nav">' + " · ".join(links) + "</nav>"


def problem_card(reading, csrf=None):
    """Aviso de un lector que falló, con el camino a «Configuración»."""
    from .pages import csrf_field
    extra = ""
    if reading.stale_index and csrf is not None:
        extra = (f'<form method="post" action="/paso/materias/indice">{csrf_field(csrf)}'
                 '<button>Actualizar el índice</button></form>')
    return (f'<div class="card msg bad" role="status"><p>{esc(reading.problem)}</p>'
            '<p><a href="/paso/materias">Ir a «Configuración» → «Materias»</a></p>' + extra + '</div>')


def names_of(summary):
    return {m["materia_id"]: m.get("nombre") or m["materia_id"] for m in (summary or {}).get("materias", [])}


def items_table(items, names):
    if not items:
        return ""
    rows = []
    for item in items:
        subject = item.get("materia_id")
        label = names.get(subject) or subject or "Sin materia"
        cell = (f'<a href="/panel/materia/{esc(subject)}">{esc(label)}</a>' if subject in names
                else esc(label))
        rows.append(f"<tr><td><strong>{esc(item.get('titulo') or 'Sin título')}</strong><br>"
                    f'<span class="muted">{esc(TYPES.get(item["tipo"], "Pendiente"))}</span></td>'
                    f"<td>{cell}</td><td>{esc(day_text(item.get('fecha')) or 'Sin fecha')}<br>"
                    f'<span class="muted">{esc(item.get("motivo") or "")}</span></td></tr>')
    return ('<table class="panel-table"><thead><tr><th>Qué</th><th>Materia</th><th>Cuándo</th></tr></thead>'
            "<tbody>" + "".join(rows) + "</tbody></table>")


def copy_box(ident, label, text):
    """Cuadro de solo lectura con el texto a copiar y su botón (pages.COPY_SCRIPT lo muestra)."""
    return (f'<div class="copy-box"><label for="{esc(ident)}">{esc(label)}</label>'
            f'<textarea id="{esc(ident)}" class="copiar" readonly rows="12">{esc(text)}</textarea>'
            f'<button type="button" class="copiar-boton" data-copiar="{esc(ident)}" hidden>Copiar</button></div>')
