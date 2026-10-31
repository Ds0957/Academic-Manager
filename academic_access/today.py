"""`today`: lista determinista de qué hacer primero (solo lectura).

Cruza actividades, tareas personales, clases sin procesar y materias sin movimiento de la
base publicada. Se valida contra `workbook_version`, no contra el índice de conocimiento.
"""
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from academic_store import records, store

from . import core

LOCAL_ZONE = ZoneInfo("America/Bogota")
DEFAULT_DAYS = 14
MAX_DAYS = 60
CLASS_WINDOW = 21
NEGLECT_WINDOW = 14
TYPES = ("actividad_vencida", "actividad_proxima", "tarea", "clase_sin_procesar",
         "materia_descuidada", "tarea_sin_fecha")
# Valores que escribe canvas_sync.workbook_activities._states para entregada/calificada.
DONE_TRACK = ("entregada", "calificada")
DONE_DELIVERY = "entregada"
DONE_GRADE = "calificada"
OPEN_TASKS = ("pendiente", "en_progreso")


def local_today():
    return datetime.now(LOCAL_ZONE).date()


def parse_date(text):
    try:
        if len(text) != 10:
            raise ValueError
        return date.fromisoformat(text)
    except (TypeError, ValueError):
        raise core.AccessError("INVALID_ARGUMENT", "fecha debe tener el formato YYYY-MM-DD.") from None


def _local_date(value):
    """Serial de Excel (hora local de Bogotá) o texto ISO → fecha local; vacío → None."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if not 1 <= value <= 2958465:
            raise core.AccessError("INVALID_SOURCE", "Fecha en la base fuera de intervalo válido.")
        return (datetime(1899, 12, 30) + timedelta(days=value)).date()
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        raise core.AccessError("INVALID_SOURCE", "Fecha no reconocible en la base.") from None


def _utc_local_date(stamp):
    """Marca ISO-8601 UTC de las tablas propias → fecha local de Bogotá."""
    if not stamp:
        return None
    try:
        moment = datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        raise core.AccessError("INVALID_SOURCE", "Marca de tiempo no reconocible en la base.") from None
    return moment.astimezone(LOCAL_ZONE).date()


def _text(value):
    return None if value is None or value == "" else str(value)


def _window(delta, days):
    """Prioridad de un vencimiento futuro (delta ≥ 0 días) o None si queda fuera del horizonte."""
    if delta > days:
        return None
    return 1 if delta <= 2 else 2 if delta <= 7 else 3


def _when(delta):
    if delta == 0:
        return "hoy"
    if delta == 1:
        return "mañana"
    return f"en {delta} días"


def _ago(delta):
    return "hace 1 día" if delta == 1 else f"hace {delta} días"


def _active_subjects(catalog):
    active = [r for r in catalog["05 - Semestres"]
              if str(r.get("estado") or "").strip().casefold() == "activo"]
    if len(active) != 1:
        raise core.AccessError("INVALID_SOURCE", "La base debe tener un único semestre activo.")
    semester = str(active[0]["semestre_id"])
    return sorted(str(r["materia_id"]) for r in catalog["01 - Materias"]
                  if r.get("materia_id") and str(r.get("semestre")) == semester)


def _activities(rows, tracking, today, days):
    items = []
    for row in rows:
        aid = _text(row.get("actividad_id"))
        due = _local_date(row.get("fecha_entrega"))
        if aid is None or due is None:
            continue
        done = ((tracking.get(aid) or {}).get("estado") in DONE_TRACK
                or row.get("estado_entrega") == DONE_DELIVERY
                or row.get("estado_calificacion") == DONE_GRADE)
        if done:
            continue
        delta = (due - today).days
        base = {"fecha": due.isoformat(), "materia_id": _text(row.get("materia_id")),
                "actividad_id": aid, "titulo": _text(row.get("titulo"))}
        if delta < 0:
            close = _local_date(row.get("fecha_cierre"))
            if close is not None and close < today:
                continue
            items.append({"tipo": "actividad_vencida", "prioridad": 0,
                          "motivo": f"Venció {_ago(-delta)} y aún no consta como entregada",
                          **base})
            continue
        priority = _window(delta, days)
        if priority is not None:
            items.append({"tipo": "actividad_proxima", "prioridad": priority,
                          "motivo": f"Se entrega {_when(delta)}", **base})
    return items


def _tasks(rows, today, days):
    items = []
    for row in rows:
        if row["estado"] not in OPEN_TASKS:
            continue
        base = {"materia_id": row["materia_id"], "tarea_id": row["tarea_id"], "titulo": row["titulo"]}
        deadline = _local_date(row["fecha_limite"])
        if deadline is None:
            items.append({"tipo": "tarea_sin_fecha", "prioridad": 6,
                          "motivo": "Tarea abierta sin fecha límite", "fecha": None, **base})
            continue
        delta = (deadline - today).days
        priority = 0 if delta < 0 else _window(delta, days)
        if priority is None:
            continue
        motivo = (f"Tarea vencida {_ago(-delta)}" if delta < 0 else f"Tarea con fecha límite {_when(delta)}")
        items.append({"tipo": "tarea", "prioridad": priority, "motivo": motivo,
                      "fecha": deadline.isoformat(), **base})
    return items


def _classes(rows, today):
    items, seen = [], {}
    for row in rows:
        cid = _text(row.get("clase_id"))
        when = _local_date(row.get("fecha"))
        subject = _text(row.get("materia_id"))
        if cid is None or when is None or when > today:
            continue
        seen[subject] = max(seen.get(subject, when), when)
        if not row.get("procesada") and (today - when).days <= CLASS_WINDOW:
            items.append({"tipo": "clase_sin_procesar", "prioridad": 4,
                          "motivo": f"Clase {_ago((today - when).days) if when < today else 'de hoy'} sin procesar",
                          "fecha": when.isoformat(), "materia_id": subject, "clase_id": cid,
                          "titulo": _text(row.get("titulo"))})
    return items, seen


def _neglected(subjects, names, last_class, notes, tracking, activity_subject, today):
    last = dict(last_class)

    def touch(subject, when):
        if subject is not None and when is not None and when <= today:
            last[subject] = max(last.get(subject, when), when)

    for row in notes:
        touch(row["materia_id"], _utc_local_date(row["creado_utc"]))
    for row in tracking:
        touch(activity_subject.get(row["actividad_id"]), _utc_local_date(row["actualizado_utc"]))
    items = []
    for subject in subjects:
        when = last.get(subject)
        if when is not None and (today - when).days <= NEGLECT_WINDOW:
            continue
        items.append({"tipo": "materia_descuidada", "prioridad": 5,
                      "motivo": f"Sin clases, notas ni seguimiento en los últimos {NEGLECT_WINDOW} días",
                      "fecha": when.isoformat() if when else None, "materia_id": subject,
                      "titulo": names.get(subject)})
    return items


def _key(item):
    ident = item.get("actividad_id") or item.get("tarea_id") or item.get("clase_id") or item["materia_id"]
    return (item["prioridad"], item["fecha"] is None, item["fecha"] or "", ident)


def today(runtime, *, on=None, days=DEFAULT_DAYS):
    if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= MAX_DAYS:
        raise core.AccessError("INVALID_ARGUMENT", f"dias debe estar entre 1 y {MAX_DAYS}.")
    day = parse_date(on) if on is not None else local_today()
    try:
        actual = core.workbook_version(runtime)
    except core.SyncError as exc:
        raise core.AccessError("SOURCE_UNAVAILABLE", "La base publicada no está disponible o no valida.",
                               action="Revisar la tarea diaria y la integridad de la base local.") from exc
    path = runtime / store.DB_NAME
    catalog = store.read_catalog(path, ("01 - Materias", "02 - Actividades", "03 - Clases",
                                        "05 - Semestres"))
    subjects = _active_subjects(catalog)
    names = {str(r["materia_id"]): _text(r.get("nombre")) for r in catalog["01 - Materias"]
             if r.get("materia_id")}
    tracking = records.read(path, "seguimiento")
    activity_subject = {str(r["actividad_id"]): _text(r.get("materia_id"))
                        for r in catalog["02 - Actividades"] if r.get("actividad_id")}
    # Solo cuenta el semestre activo: lo de semestres cerrados ya no se puede hacer.
    # Las tareas personales sin materia no pertenecen a ningún semestre y siempre cuentan.
    current = set(subjects)
    class_items, last_class = _classes(
        [r for r in catalog["03 - Clases"] if _text(r.get("materia_id")) in current], day)
    open_tasks = [t for t in records.read(path, "tareas")
                  if t.get("materia_id") is None or t["materia_id"] in current]
    items = (_activities([r for r in catalog["02 - Actividades"] if _text(r.get("materia_id")) in current],
                         {r["actividad_id"]: r for r in tracking}, day, days)
             + _tasks(open_tasks, day, days)
             + class_items
             + _neglected(subjects, names, last_class, records.read(path, "notas"), tracking,
                          activity_subject, day))
    items.sort(key=_key)
    return {"fecha": day.isoformat(), "dias": days, "fechas_zona": "America/Bogota",
            "resumen": {kind: sum(i["tipo"] == kind for i in items) for kind in TYPES},
            "items": items, "workbook_sha256": actual}
