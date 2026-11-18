"""Horario de clases y grabaciones desde Google Calendar (odd/tasks/clases-automaticas.md, S5).

Solo lectura, con el permiso opcional de Calendar del inicio de sesión del asistente
(`capture.sources.auth.load_calendar_credentials`). Lee los eventos del calendario principal de
las últimas tres semanas y la próxima, reconoce de qué materia es cada uno por su título
(«Sesión Sistemas Operativos II», «4146_GESTION DE BASES DE DATOS») y guarda su fecha, su hora y
las grabaciones adjuntas en `procesamiento/clases-automaticas/calendario.json`. Si Calendar falla,
se conserva el horario anterior.
"""
import re
import unicodedata
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from capture.models import CaptureError

from . import class_state
from .class_state import FOLDER, MEDIA  # noqa: F401 - FOLDER también se usa desde fuera

ZONE = ZoneInfo("America/Bogota")
STATE = "calendario.json"
LAST = "calendario-estado.json"  # resultado de la última lectura: el asistente lo muestra sin conectarse
PAST_DAYS, NEXT_DAYS = 21, 8
MAX_PAGES = 5
SMALL = {"de", "del", "la", "las", "el", "los", "y", "e", "en", "a"}
ALIASES = {"ingles": "english"}  # «Inglés III» aparece en el calendario como «English III»


def _words(text):
    plain = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode().casefold()
    return [w for w in re.split(r"[^a-z0-9]+", plain) if w]


def _subjects(runtime):
    """{materia_id: nombre} del semestre activo."""
    from academic_access import today as access_today
    from academic_store import store
    catalog = store.read_catalog(Path(runtime) / store.DB_NAME, ("01 - Materias", "05 - Semestres"))
    active = set(access_today._active_subjects(catalog))
    return {str(r["materia_id"]): str(r.get("nombre") or "") for r in catalog["01 - Materias"]
            if str(r.get("materia_id")) in active}


def match(summary, subjects):
    """La materia cuyo nombre (sin tildes ni palabras cortas) está completo en el título; None si no hay una sola."""
    words = set(_words(summary))
    found = []
    for ident, name in subjects.items():
        needed = [ALIASES.get(w, w) for w in _words(name) if w not in SMALL]
        if needed and all(w in words for w in needed):
            found.append((len(needed), ident))
    found.sort(reverse=True)
    if not found or (len(found) > 1 and found[0][0] == found[1][0]):
        return None
    return found[0][1]


def _local(moment):
    """(fecha, hora) en Bogotá de un evento («dateTime» con zona, o «date» de todo el día)."""
    if "dateTime" in moment:
        value = datetime.fromisoformat(moment["dateTime"].replace("Z", "+00:00")).astimezone(ZONE)
        return value.date(), value.strftime("%H:%M")
    return date.fromisoformat(moment["date"]), None


def events(runtime):
    found = (class_state.read(runtime, STATE) or {}).get("eventos")
    return [e for e in found if isinstance(e, dict)] if isinstance(found, list) else []


def _save(runtime, found):
    class_state.write(runtime, STATE, {"actualizado_utc": class_state.stamp(), "eventos": found})


def _service(runtime):
    from capture.sources import auth
    credentials = auth.load_calendar_credentials(runtime)
    if credentials is None:
        return None
    from googleapiclient.discovery import build
    return build("calendar", "v3", credentials=credentials, cache_discovery=False)


def last(runtime):
    """Resultado de la última lectura ({estado, mensaje?}); None si nunca se leyó."""
    return class_state.read(runtime, LAST)


def fetch(runtime, **kw):
    """Lee el calendario y guarda las clases reconocidas; devuelve un resumen con su `estado`."""
    result = _fetch(Path(runtime), **kw)
    try:
        class_state.write(runtime, LAST, result)
    except (CaptureError, OSError):
        pass  # es solo un aviso para el asistente
    return result


def _class(item, subjects):
    """La clase de un evento del calendario; None si no es de una materia o dura todo el día."""
    subject = match(item.get("summary"), subjects)
    if subject is None or "dateTime" not in (item.get("start") or {}):
        return None
    when, begins = _local(item["start"])
    try:
        ends = _local(item["end"])[1]
    except Exception:  # noqa: BLE001 - la hora de fin es opcional
        ends = None
    attached = item.get("attachments")
    recordings = [{"file_id": a["fileId"], "nombre": a.get("title")} for a in (attached if isinstance(attached, list) else [])
                  if isinstance(a, dict) and a.get("fileId") and str(a.get("mimeType") or "").startswith(MEDIA)]
    return {"materia_id": subject, "fecha": when.isoformat(), "inicio": begins, "fin": ends,
            "titulo": item.get("summary"), "grabaciones": recordings}


def _fetch(runtime, *, api=None, on=None):
    if api is None:
        try:
            api = _service(runtime)
        except CaptureError as exc:
            return {"estado": "sin_permiso", "mensaje": str(exc)}
        except Exception:  # noqa: BLE001 - sin la biblioteca o sin conexión: se intenta mañana
            return {"estado": "error", "mensaje": "No se pudo abrir Google Calendar."}
        if api is None:
            return {"estado": "sin_sesion"}
    day = on or datetime.now(ZONE).date()
    start = datetime.combine(day - timedelta(days=PAST_DAYS), datetime.min.time(), ZONE)
    end = datetime.combine(day + timedelta(days=NEXT_DAYS), datetime.min.time(), ZONE)
    subjects = _subjects(runtime)
    found, token = [], None
    try:
        for _ in range(MAX_PAGES):
            options = {"calendarId": "primary", "timeMin": start.isoformat(), "timeMax": end.isoformat(),
                       "singleEvents": True, "orderBy": "startTime", "maxResults": 250}
            if token:
                options["pageToken"] = token
            page = api.events().list(**options).execute()
            for item in page.get("items") or []:
                try:
                    found.append(_class(item, subjects))
                except Exception:  # noqa: BLE001 - un evento dañado no impide leer los demás
                    continue
            token = page.get("nextPageToken")
            if not token:
                break
    except Exception:  # noqa: BLE001 - se conserva el horario anterior
        return {"estado": "error", "mensaje": "Google Calendar no respondió; se intentará en la próxima sincronización."}
    found = [event for event in found if event]
    _save(runtime, found)
    return {"estado": "ok", "eventos": len(found), "grabaciones": sum(len(e["grabaciones"]) for e in found)}
